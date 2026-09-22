"""Run local Ollama chat, showing tool execution without cloud API keys."""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_DIR / ".env")
sys.path.insert(0, str(PROJECT_DIR / "src"))

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from react_agent.graph import create_graph
from react_agent.runtime_context import AgentRuntimeContext
from react_agent.security import private_state_file
from react_agent.services.responses import safe_text
from react_agent.utils import get_message_text


def uses_demo_data(messages) -> bool:
    """Show provenance deterministically even when the LLM omits the warning."""
    for message in messages:
        if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
            continue
        try:
            payload = json.loads(message.content)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        data = payload.get("data")
        records = data if isinstance(data, list) else [data]
        if any(
            isinstance(record, dict)
            and (
                record.get("is_demo") is True
                or record.get("delivered_at_source") == "local_demo"
            )
            for record in records
        ):
            return True
    return False


def tool_outcome(message: ToolMessage) -> str:
    """Display result status without exposing tool arguments or private records."""
    if message.status == "error":
        return "failed (tool_execution_error)"
    try:
        payload = (
            json.loads(message.content) if isinstance(message.content, str) else None
        )
    except (ValueError, TypeError):
        return "completed (unstructured result)"
    if isinstance(payload, dict) and payload.get("ok") is True:
        return "success"
    if isinstance(payload, dict) and payload.get("ok") is False:
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        safe_code = (
            code
            if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code)
            else "unknown_error"
        )
        return f"failed ({safe_code})"
    return "completed"


def configure_terminal_input() -> None:
    """Enable Unicode-aware line editing, without changing terminal profiles."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    try:
        import readline
    except ImportError:
        print(
            "Line editing unavailable: readline is missing from this Python.",
            file=sys.stderr,
        )
        return
    backend = getattr(readline, "backend", "")
    if backend == "editline" or "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind -e")
        readline.parse_and_bind("bind ^H ed-delete-prev-char")
        readline.parse_and_bind("bind ^? ed-delete-prev-char")
    else:
        readline.parse_and_bind("set editing-mode emacs")
        readline.parse_and_bind(r'"\C-h": backward-delete-char')
        readline.parse_and_bind(r'"\C-?": backward-delete-char')
        readline.parse_and_bind(r'"\C-u": kill-whole-line')
        readline.parse_and_bind(r'"\e[3~": delete-char')


def collect_approvals(interrupts) -> Command:
    """Only terminal input can authorize a write; model text cannot approve it."""
    responses = {}
    for pending in interrupts:
        draft = pending.value
        if not isinstance(draft, dict) or draft.get("action") != "initiate_return":
            raise ValueError("Unsupported approval action.")
        order_id = draft["order_id"]
        print("\n待确认：整单退货申请（只创建申请，不退款）", flush=True)
        print(f"订单：{order_id}\n原因：{draft['reason']}")
        print("商品：", safe_text(json.dumps(draft["items"], ensure_ascii=False)))
        print(
            f"政策：{safe_text(draft['policy_id'])} / {safe_text(draft['policy_version'])}"
        )
        print(f"退货截止：{draft['deadline']}\n确认有效期至：{draft['expires_at']}")
        if draft["is_demo"]:
            print("注意：这是模拟政策/签收数据。")
        answer = input(f"输入「确认 {order_id}」提交，其他输入取消：").strip()
        responses[pending.id] = {
            "decision": "approve" if answer == f"确认 {order_id}" else "reject",
            "fingerprint": draft["fingerprint"],
            "expires_at": draft["expires_at"],
        }
    return Command(resume=responses)


async def execute_turn(
    agent, incoming, context, config, emitted: int, *, trace: bool = False
) -> None:
    turn_started = time.monotonic()
    previous_update = turn_started
    while True:
        pending = ()
        async for update in agent.astream(
            incoming, context=context, config=config, stream_mode="updates"
        ):
            if trace:
                now = time.monotonic()
                node_names = [name for name in update if name != "__interrupt__"]
                print(
                    "Trace:",
                    f"nodes={','.join(node_names) or 'interrupt'}",
                    f"step_ms={round((now - previous_update) * 1000)}",
                    flush=True,
                )
                previous_update = now
            if "understand_request" in update:
                route = update["understand_request"]
                print(
                    "Graph route:",
                    "policy retrieval" if route.get("needs_policy_search") else "ReAct",
                    flush=True,
                )
                if trace and route.get("policy_topics"):
                    print(
                        "Trace:",
                        "policy_topics=" + ",".join(route["policy_topics"]),
                        flush=True,
                    )
            if "retrieve_policy" in update:
                evidence = update["retrieve_policy"].get("policy_evidence") or {}
                data = evidence.get("data") or {}
                print(
                    "Graph retrieval:",
                    f"{len(data.get('matches', []))} matches"
                    if evidence.get("ok")
                    else "failed",
                    flush=True,
                )
            if "__interrupt__" in update:
                pending = update["__interrupt__"]
        snapshot = await agent.aget_state(config)
        result = dict(snapshot.values)
        result["__interrupt__"] = pending
        messages = result.get("messages", [])
        for message in messages[emitted:]:
            if isinstance(message, AIMessage) and message.tool_calls:
                print(
                    "Tools requested:", ", ".join(c["name"] for c in message.tool_calls)
                )
            elif isinstance(message, ToolMessage):
                print("Tool result:", message.name or "tool", tool_outcome(message))
        emitted = len(messages)
        pending = result.get("__interrupt__", ())
        if pending:
            incoming = collect_approvals(pending)
            continue
        if (
            not messages
            or not isinstance(messages[-1], AIMessage)
            or messages[-1].tool_calls
        ):
            raise RuntimeError("Agent stopped without a final answer.")
        policy_data = (result.get("policy_evidence") or {}).get("data") or {}
        if uses_demo_data(messages) or policy_data.get("is_demo") is True:
            print(
                "Demo notice: This conversation uses synthetic shipment, delivery or policy data; not real carrier/merchant information."
            )
        print("Assistant:", get_message_text(messages[-1]))
        if trace:
            print(
                "Trace:",
                f"turn_ms={round((time.monotonic() - turn_started) * 1000)}",
                flush=True,
            )
        return


async def run(args: argparse.Namespace) -> None:
    configure_terminal_input()
    context = AgentRuntimeContext()
    if args.customer_email:
        context.customer_email = args.customer_email
    if args.model:
        context.model = args.model
    if args.orders_db:
        context.orders_db_path = args.orders_db
    if args.checkpoint_db:
        context.checkpoint_db_path = args.checkpoint_db
    if not context.customer_email:
        raise ValueError("Set CUSTOMER_EMAIL or pass --customer-email for the demo.")
    if not args.question and sys.stdin.isatty():
        print("输入提示：退格删除字符，左右键移动，Ctrl+U 清空当前行，exit 退出。")
    checkpoint_path = Path(context.checkpoint_db_path).resolve()
    if checkpoint_path == Path(context.orders_db_path).resolve():
        raise ValueError("Checkpoint and business databases must be different files.")
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    private_state_file(checkpoint_path)
    thread_id = args.thread_id or uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", thread_id):
        raise ValueError("Invalid thread ID.")
    print(
        f"Conversation ID: {thread_id} (resume with --thread-id {thread_id})",
        flush=True,
    )
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        agent = create_graph(saver)
        snapshot = await agent.aget_state(config)
        scope = snapshot.values.get("customer_scope")
        if scope and scope != context.identity_scope():
            raise PermissionError(
                "Conversation belongs to a different customer/database."
            )
        if snapshot.next:
            if args.question:
                raise ValueError(
                    "Resume the pending conversation without --question first."
                )
            pending = [item for task in snapshot.tasks for item in task.interrupts]
            incoming = collect_approvals(pending) if pending else None
            await execute_turn(
                agent,
                incoming,
                context,
                config,
                len(snapshot.values.get("messages", [])),
                trace=args.trace,
            )
        while True:
            question = args.question or input("You (exit to quit): ").strip()
            if question.lower() in {"exit", "quit"}:
                break
            if not question:
                continue
            snapshot = await agent.aget_state(config)
            print("Thinking...", flush=True)
            await execute_turn(
                agent,
                {"messages": [("user", question)]},
                context,
                config,
                len(snapshot.values.get("messages", [])) + 1,
                trace=args.trace,
            )
            if args.question:
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--question", help="Run one question instead of interactive chat"
    )
    parser.add_argument(
        "--customer-email", help="Local demo identity; not authentication"
    )
    parser.add_argument(
        "--model", help="Override provider/model, e.g. ollama/qwen3:1.7b"
    )
    parser.add_argument(
        "--thread-id", help="Resume a saved conversation for the same demo identity"
    )
    parser.add_argument("--orders-db", help="Override business SQLite database path")
    parser.add_argument(
        "--checkpoint-db", help="Override conversation SQLite database path"
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Print per-node and whole-turn latency without prompts or tool payloads",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (KeyboardInterrupt, EOFError):
        print("\nBye.")
    except Exception as exc:
        print(
            f"Agent failed ({type(exc).__name__}). Check Ollama, model and database configuration.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
