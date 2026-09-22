"""Single-worker, local portfolio MVP. Bearer tokens represent demo identities.

No public registration, arbitrary customer email, write endpoint or raw model
trace. Sessions and request receipts are durable; failed runs are not replayed.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import aiosqlite
from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from react_agent.api_security import (
    MAX_SESSIONS_PER_CUSTOMER,
    MAX_TURNS_PER_SESSION,
    IngressLimits,
    RateLimiter,
)
from react_agent.paths import PROJECT_ROOT
from react_agent.runtime_context import AgentRuntimeContext
from react_agent.security import private_state_file
from react_agent.utils import get_message_text

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def safe_input(cls, value):
        if any(
            unicodedata.category(c) in ("Cs", "Cc") and c not in "\n\r\t" for c in value
        ):
            raise ValueError("Unsupported control characters")
        return value


class SessionOutput(BaseModel):
    session_id: str
    mode: str = "read_only_demo"


class AnswerOutput(BaseModel):
    request_id: str
    answer: str
    is_demo: bool = True


def create_app(*, graph_factory=None) -> FastAPI:
    """Factory allows smoke checks to substitute a bounded deterministic graph."""

    @asynccontextmanager
    async def lifespan(app):
        identities = json.loads(os.environ.get("DEMO_API_IDENTITIES", "{}"))
        if (
            not isinstance(identities, dict)
            or not identities
            or len(identities) > 25
            or any(
                not isinstance(token, str)
                or not 32 <= len(token) <= 128
                or not token.isascii()
                or not isinstance(email, str)
                or "@" not in email
                for token, email in identities.items()
            )
        ):
            raise RuntimeError(
                "Configure DEMO_API_IDENTITIES: {32+ char token: demo email}."
            )
        app.state.identities = {
            hashlib.sha256(token.encode()).digest(): email
            for token, email in identities.items()
        }
        app.state.user_limiter = RateLimiter()
        context = AgentRuntimeContext(read_only=True)
        path = Path(
            os.environ.get("API_STATE_DB", str(PROJECT_ROOT / "var/api_state.db"))
        ).resolve()
        if path in {
            Path(context.orders_db_path).resolve(),
            Path(context.checkpoint_db_path).resolve(),
        }:
            raise RuntimeError("API state must use a separate database.")
        if not Path(context.orders_db_path).is_file():
            raise RuntimeError(
                "Orders database missing; initialize demo data explicitly."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        private_state_file(path)
        async with (
            aiosqlite.connect(path) as db,
            AsyncSqliteSaver.from_conn_string(str(path)) as saver,
        ):
            await db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS api_sessions (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_runs (
                    session_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    digest TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
                    PRIMARY KEY(session_id, request_id)
                );
                UPDATE api_runs SET status='failed' WHERE status='running';
            """)
            await db.commit()
            await saver.setup()
            if graph_factory is None:
                from react_agent.graph import create_graph

                factory = create_graph
            else:
                factory = graph_factory
            app.state.agent = factory(saver)
            app.state.db = db
            # Single-flight protects local Ollama and serializes DB receipts.
            app.state.gate = asyncio.Lock()
            yield

    app = FastAPI(title="Customer Support MVP", version="0.1.0", lifespan=lifespan)
    # Only local demo hosts; testserver supports the standalone ASGI harness.
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
    )
    app.add_middleware(IngressLimits)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Pydantic errors normally echo input; never reflect credentials/payloads.
        return JSONResponse({"detail": {"code": "invalid_request"}}, status_code=422)

    async def identity(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ):
        if (
            credentials
            and credentials.scheme.lower() == "bearer"
            and len(credentials.credentials) <= 128
        ):
            key = hashlib.sha256(credentials.credentials.encode()).digest()
            email = app.state.identities.get(key)
            if email:
                context = AgentRuntimeContext(customer_email=email, read_only=True)
                if not app.state.user_limiter.allow(context.identity_scope(), 20):
                    raise HTTPException(
                        429,
                        detail={"code": "customer_rate_limited"},
                        headers={"Retry-After": "60"},
                    )
                return context
        raise HTTPException(
            401, detail={"code": "unauthorized"}, headers={"WWW-Authenticate": "Bearer"}
        )

    async def owned_session(session_id, context):
        async with app.state.db.execute(
            "SELECT scope FROM api_sessions WHERE id=?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] != context.identity_scope():
            raise HTTPException(404, detail={"code": "session_not_found"})

    @app.get("/health/live")
    async def live():
        # Liveness only: this does not claim Ollama or RAG is ready.
        return {"status": "alive", "mode": "read_only_demo"}

    @app.post("/v1/sessions", response_model=SessionOutput, status_code=201)
    async def session(context=Depends(identity)):
        if app.state.gate.locked():
            raise HTTPException(
                429, detail={"code": "busy"}, headers={"Retry-After": "5"}
            )
        async with app.state.gate:
            async with app.state.db.execute(
                "SELECT count(*) FROM api_sessions WHERE scope=?",
                (context.identity_scope(),),
            ) as cursor:
                if (await cursor.fetchone())[0] >= MAX_SESSIONS_PER_CUSTOMER:
                    raise HTTPException(409, detail={"code": "session_limit_reached"})
            session_id = uuid4().hex
            await app.state.db.execute(
                "INSERT INTO api_sessions VALUES (?,?)",
                (session_id, context.identity_scope()),
            )
            await app.state.db.commit()
        return SessionOutput(session_id=session_id)

    @app.post("/v1/sessions/{session_id}/messages", response_model=AnswerOutput)
    async def message(session_id: str, body: MessageInput, context=Depends(identity)):
        await owned_session(session_id, context)
        if app.state.gate.locked():
            raise HTTPException(
                429, detail={"code": "busy"}, headers={"Retry-After": "5"}
            )
        async with app.state.gate:
            db = app.state.db
            digest = hashlib.sha256(body.text.encode()).hexdigest()
            async with db.execute(
                "SELECT digest,status,result FROM api_runs WHERE session_id=? AND request_id=?",
                (session_id, body.request_id),
            ) as cursor:
                receipt = await cursor.fetchone()
            if receipt:
                if receipt[0] != digest:
                    raise HTTPException(409, detail={"code": "request_id_conflict"})
                if receipt[1] == "completed":
                    return AnswerOutput.model_validate_json(receipt[2])
                raise HTTPException(
                    409, detail={"code": "run_failed_create_new_session"}
                )
            async with db.execute(
                "SELECT 1 FROM api_runs WHERE session_id=? AND status!='completed'",
                (session_id,),
            ) as cursor:
                if await cursor.fetchone():
                    raise HTTPException(
                        409, detail={"code": "session_failed_create_new_session"}
                    )
            config = {
                "configurable": {"thread_id": "api_" + session_id},
                "recursion_limit": 20,
            }
            snapshot = await app.state.agent.aget_state(config)
            previous_message_count = len(snapshot.values.get("messages", []))
            if (
                sum(
                    isinstance(m, HumanMessage)
                    for m in snapshot.values.get("messages", [])
                )
                >= MAX_TURNS_PER_SESSION
            ):
                raise HTTPException(409, detail={"code": "turn_limit_reached"})
            if (
                snapshot.next
                or snapshot.values.get("customer_scope", context.identity_scope())
                != context.identity_scope()
            ):
                raise HTTPException(409, detail={"code": "session_not_ready"})
            await db.execute(
                "INSERT INTO api_runs VALUES (?,?,?,'running',NULL)",
                (session_id, body.request_id, digest),
            )
            await db.commit()
            started = time.monotonic()
            try:
                async with asyncio.timeout(180):
                    result = await app.state.agent.ainvoke(
                        {
                            "messages": [
                                HumanMessage(
                                    content=body.text, id="api_user_" + uuid4().hex
                                )
                            ]
                        },
                        context=context,
                        config=config,
                    )
                messages = result.get("messages", [])
                if (
                    result.get("__interrupt__")
                    or not messages
                    or not isinstance(messages[-1], AIMessage)
                    or messages[-1].tool_calls
                ):
                    raise RuntimeError("No validated final answer.")
                if (
                    messages[-1].additional_kwargs.get("response_source")
                    != "validated_renderer"
                ):
                    raise RuntimeError("Refusing provisional model output.")
                answer = AnswerOutput(
                    request_id=body.request_id, answer=get_message_text(messages[-1])
                )
                await db.execute(
                    "UPDATE api_runs SET status='completed',result=? WHERE session_id=? AND request_id=?",
                    (answer.model_dump_json(), session_id, body.request_id),
                )
                await db.commit()
                new_messages = messages[previous_message_count:]
                tool_calls = sum(
                    len(message.tool_calls)
                    for message in new_messages
                    if isinstance(message, AIMessage)
                )
                policy_data = (result.get("policy_evidence") or {}).get("data") or {}
                logger.info(
                    "agent_run_completed session=%s request=%s duration_ms=%d "
                    "intent=%s policy_status=%s tool_calls=%d",
                    session_id,
                    body.request_id,
                    round((time.monotonic() - started) * 1000),
                    result.get("request_intent", "unknown"),
                    (policy_data.get("sufficiency") or {}).get("status", "none"),
                    tool_calls,
                )
                return answer
            except BaseException as error:
                await db.execute(
                    "UPDATE api_runs SET status='failed' WHERE session_id=? AND request_id=?",
                    (session_id, body.request_id),
                )
                await db.commit()
                # Do not log prompts, tokens, emails or potentially private exceptions.
                logger.warning(
                    "agent_run_failed type=%s request=%s duration_ms=%d",
                    type(error).__name__,
                    body.request_id,
                    round((time.monotonic() - started) * 1000),
                )
                if isinstance(error, asyncio.CancelledError):
                    raise
                if not isinstance(error, Exception):
                    raise
                raise HTTPException(
                    504 if isinstance(error, TimeoutError) else 503,
                    detail={"code": "agent_unavailable", "request_id": body.request_id},
                ) from None

    return app
