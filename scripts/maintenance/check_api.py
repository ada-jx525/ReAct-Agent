"""Standalone API smoke checks; --real additionally runs a local Ollama turn.

Uses temporary API/checkpoint storage; does not initialize or write business DB.
No pytest, cloud account or additional model download required.
"""

import argparse
import asyncio
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR / "src"))

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from react_agent.api import create_app
from react_agent.data.db import DEFAULT_DB_PATH


class SmokeGraph:
    calls = 0

    async def aget_state(self, config):
        return SimpleNamespace(next=(), values={})

    async def ainvoke(self, incoming, *, context, config):
        assert context.read_only is True
        self.calls += 1
        if incoming["messages"][0].content == "simulate failure":
            raise RuntimeError("private exception should not reach client")
        return {
            "messages": [
                AIMessage(
                    content="模拟校验答案",
                    additional_kwargs={"response_source": "validated_renderer"},
                )
            ]
        }


def check(real: bool):
    before = hashlib.sha256(DEFAULT_DB_PATH.read_bytes()).hexdigest()
    graph = SmokeGraph()
    os.environ["DEMO_API_IDENTITIES"] = (
        '{"'
        + "a" * 40
        + '":"james@example.com","'
        + "b" * 40
        + '":"sarah@example.com"}'
    )
    headers = {"Authorization": "Bearer " + "a" * 40}
    other = {"Authorization": "Bearer " + "b" * 40}
    with tempfile.TemporaryDirectory(prefix="react-api-smoke-") as temp:
        os.environ["API_STATE_DB"] = str(Path(temp) / "api.db")
        app = create_app(graph_factory=lambda saver: graph)
        with TestClient(app) as client:
            assert client.get("/health/live").status_code == 200
            assert client.post("/v1/sessions").status_code == 401
            sid = client.post("/v1/sessions", headers=headers).json()["session_id"]
            url = f"/v1/sessions/{sid}/messages"
            body = {"request_id": "one", "text": "我的订单"}
            assert client.post(url, headers=other, json=body).status_code == 404
            assert (
                client.post(
                    url,
                    headers=headers,
                    json={**body, "customer_email": "sarah@example.com"},
                ).status_code
                == 422
            )
            first = client.post(url, headers=headers, json=body)
            assert first.status_code == 200, first.text
            assert client.post(url, headers=headers, json=body).json() == first.json()
            assert graph.calls == 1
            assert (
                client.post(
                    url, headers=headers, json={**body, "text": "another"}
                ).status_code
                == 409
            )
        # Restart: durable ownership and completed receipts survive.
        with TestClient(create_app(graph_factory=lambda saver: graph)) as client:
            assert client.post(url, headers=headers, json=body).json() == first.json()
            assert graph.calls == 1
            failure = client.post(
                url,
                headers=headers,
                json={"request_id": "two", "text": "simulate failure"},
            )
            assert (
                failure.status_code == 503 and "private exception" not in failure.text
            )
            assert (
                client.post(
                    url, headers=headers, json={"request_id": "three", "text": "retry"}
                ).status_code
                == 409
            )
        print(
            "PASS: auth, ownership, input contract, deduplication, conflict, restart, sanitized failure, fail-closed session"
        )
        if real:
            from langgraph.graph import END, START, StateGraph
            from langgraph.prebuilt import ToolNode

            from react_agent.runtime_context import AgentRuntimeContext
            from react_agent.state import State
            from react_agent.tools import initiate_return

            guard = StateGraph(State, context_schema=AgentRuntimeContext)
            guard.add_node("tools", ToolNode([initiate_return]))
            guard.add_edge(START, "tools")
            guard.add_edge("tools", END)
            blocked = asyncio.run(
                guard.compile().ainvoke(
                    {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "initiate_return",
                                        "args": {
                                            "order_id": "ORD-1001",
                                            "reason": "demo",
                                        },
                                        "id": "guard-check",
                                        "type": "tool_call",
                                    }
                                ],
                            )
                        ]
                    },
                    context=AgentRuntimeContext(
                        customer_email="james@example.com", read_only=True
                    ),
                )
            )
            assert '"read_only"' in blocked["messages"][-1].content
            print("PASS: direct write-tool invocation rejected by host capability")
            os.environ["API_STATE_DB"] = str(Path(temp) / "real.db")
            with TestClient(create_app()) as client:
                sid = client.post("/v1/sessions", headers=headers).json()["session_id"]
                answer = client.post(
                    f"/v1/sessions/{sid}/messages",
                    headers=headers,
                    json={"request_id": "real", "text": "查询我的 ORD-1001 订单信息"},
                )
                assert answer.status_code == 200, answer.text
                assert "ORD-1001" in answer.json()["answer"], answer.text
                print("PASS: real local graph through HTTP API")
                print(answer.json()["answer"])
    assert hashlib.sha256(DEFAULT_DB_PATH.read_bytes()).hexdigest() == before
    print("PASS: business database unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true")
    check(parser.parse_args().real)
