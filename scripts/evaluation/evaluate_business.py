"""Frozen business acceptance cases: real local graph, isolated DBs/checkpoints.

Checks observable contracts and literal evidence anchors, not semantic accuracy.
Fault cases deliberately substitute a failed component. No retry/tuning in runner.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from importlib import import_module, metadata
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from react_agent.data.db import DEFAULT_DB_PATH
from react_agent.graph import create_graph
from react_agent.runtime_context import AgentRuntimeContext


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count_requests(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT count(*) FROM return_requests").fetchone()[0]


def prepare_database(path, case):
    with (
        sqlite3.connect(DEFAULT_DB_PATH.as_uri() + "?mode=ro", uri=True) as source,
        sqlite3.connect(path) as target,
    ):
        source.backup(target)
        # Stabilize clock-sensitive cases ONLY in temporary copies.
        delivered = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        if case.get("fixture") == "expired":
            delivered = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        if case.get("fixture") == "missing_delivery":
            delivered = None
        target.execute(
            "UPDATE orders SET status='delivered',delivered_at=?,delivered_at_source='local_demo' WHERE order_id='ORD-1001'",
            (delivered,),
        )
        # Only this synthetic fixture's old applications are removed in its copy.
        target.execute("DELETE FROM return_requests WHERE order_id='ORD-1001'")
        target.commit()


def fault_context(case):
    graph_module = import_module("react_agent.graph")
    if case.get("fault") == "model":
        return patch.object(
            graph_module,
            "load_chat_model",
            side_effect=RuntimeError("Synthetic model outage"),
        )
    if case.get("fault") == "retrieval":
        return patch.object(
            graph_module.PolicyStore,
            "safe_search",
            return_value={
                "ok": False,
                "data": None,
                "error": {
                    "code": "retrieval_unavailable",
                    "message": "Synthetic retrieval outage",
                },
            },
        )
    return nullcontext()


def inspect_turn(result, previous_count):
    messages = result.get("messages", [])[previous_count:]
    calls = [
        call
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    outputs = []
    for message in messages:
        if isinstance(message, ToolMessage):
            try:
                outputs.append(
                    {"name": message.name, "result": json.loads(message.content)}
                )
            except (ValueError, TypeError):
                outputs.append({"name": message.name, "invalid_json": True})
    pending = result.get("__interrupt__", [])
    final = messages[-1] if messages else None
    evidence = result.get("policy_evidence") or {}
    data = evidence.get("data") or {}
    return {
        "selected": result.get("selected_order_id", ""),
        "intent": result.get("request_intent", "unknown"),
        "policy_topics": sorted(result.get("policy_topics", [])),
        "tools": [{"name": call["name"], "args": call["args"]} for call in calls],
        "tool_results": outputs,
        "status": (data.get("sufficiency") or {}).get("status"),
        "sources": sorted({item["source_file"] for item in data.get("matches", [])}),
        "evidence_diagnostics": [
            {
                "question": item["question"],
                "status": (
                    (item["evidence"].get("data") or {}).get("sufficiency") or {}
                ).get("status"),
                "assessment_error": (item["evidence"].get("data") or {}).get(
                    "assessment_error"
                ),
                "coverage_failures": (item["evidence"].get("data") or {}).get(
                    "coverage_failures"
                ),
            }
            for item in data.get("question_results", [])
        ],
        "interrupted": bool(pending),
        "answer": str(final.content)
        if isinstance(final, AIMessage) and not final.tool_calls and not pending
        else "",
        "validated": bool(
            isinstance(final, AIMessage)
            and not final.tool_calls
            and final.additional_kwargs.get("response_source") == "validated_renderer"
            and not final.additional_kwargs.get("validation_errors")
        ),
    }


async def run_case(root, case, args, email):
    started = time.monotonic()
    row = {
        "id": case["id"],
        "category": case["category"],
        "language": case.get("language", "unspecified"),
        "questions": case["turns"],
        "rubric": case["rubric"],
        "pass": False,
        "failures": [],
        "turn_results": [],
        "manual_review": "pending",
    }
    database = root / (case["id"] + ".db")
    prepare_database(database, case)
    baseline = count_requests(database)
    initial_hash = digest(database)
    context = AgentRuntimeContext(
        model=args.model, customer_email=email, orders_db_path=str(database)
    )
    config = {"configurable": {"thread_id": case["id"]}, "recursion_limit": 20}
    try:
        with fault_context(case):
            async with AsyncSqliteSaver.from_conn_string(
                str(root / (case["id"] + "-checkpoint.db"))
            ) as saver:
                graph = create_graph(saver)
                previous_count = 0
                async with asyncio.timeout(args.timeout):
                    for question in case["turns"]:
                        result = await graph.ainvoke(
                            {"messages": [HumanMessage(content=question)]},
                            context=context,
                            config=config,
                        )
                        row["turn_results"].append(inspect_turn(result, previous_count))
                        previous_count = len(result.get("messages", []))
                    if case.get("approval"):
                        pending = result.get("__interrupt__", [])
                        if not pending:
                            row["failures"].append("approval_interrupt_missing")
                        else:
                            if count_requests(database) != baseline:
                                row["failures"].append("write_before_confirmation")
                            draft = pending[0].value
                            decision = {
                                "decision": case["approval"],
                                "fingerprint": draft["fingerprint"],
                                "expires_at": draft["expires_at"],
                            }
                            result = await graph.ainvoke(
                                Command(resume={pending[0].id: decision}),
                                context=context,
                                config=config,
                            )
                            row["turn_results"].append(
                                inspect_turn(result, previous_count)
                            )
                    elif result.get("__interrupt__"):
                        row["failures"].append("unrequested_interrupt")
        final = row["turn_results"][-1]
        # The final user turn is scored; approval resume is additionally inspected.
        scored = (
            row["turn_results"][-2]
            if case.get("approval") and len(row["turn_results"]) > 1
            else final
        )
        expected = case["expect"]
        tool_names = [item["name"] for item in scored["tools"]]
        if not final["validated"]:
            row["failures"].append("no_validated_final")
        for name in expected.get("tools", []):
            if name not in tool_names:
                row["failures"].append("required_tool_missing:" + name)
        forbidden = set(expected.get("forbidden_tools", []))
        if not case.get("approval"):
            forbidden.add("initiate_return")
        for name in forbidden.intersection(tool_names):
            row["failures"].append("forbidden_tool:" + name)
        if "selected" in expected and scored["selected"] != expected["selected"]:
            row["failures"].append("wrong_order_selection")
        if "intent" in expected and scored["intent"] != expected["intent"]:
            row["failures"].append("wrong_intent:" + scored["intent"])
        for topic in expected.get("policy_topics", []):
            if topic not in scored["policy_topics"]:
                row["failures"].append("policy_topic_missing:" + topic)
        if "status" in expected and scored["status"] != expected["status"]:
            row["failures"].append("policy_status:" + str(scored["status"]))
        for source in expected.get("sources", []):
            if source not in scored["sources"]:
                row["failures"].append("required_source_missing:" + source)
        for group in expected.get("answer_groups", []):
            if not any(marker.lower() in final["answer"].lower() for marker in group):
                row["failures"].append("answer_anchor_missing:" + "|".join(group))
        if "eligible" in expected:
            results = [
                item.get("result", {})
                for item in scored["tool_results"]
                if item["name"] == "check_return_eligibility"
            ]
            if not any(
                value.get("ok") is True
                and (value.get("data") or {}).get("eligible") is expected["eligible"]
                for value in results
            ):
                row["failures"].append("eligibility_result_wrong_or_missing")
        row["created_requests"] = count_requests(database) - baseline
        if row["created_requests"] != expected.get("created", 0):
            row["failures"].append("unexpected_write_count")
        if not case.get("approval") and digest(database) != initial_hash:
            row["failures"].append("unexpected_business_db_change")
        row["pass"] = not row["failures"]
    except Exception as error:
        row["error_type"] = type(error).__name__
        row["failures"].append("execution_error")
    row["seconds"] = round(time.monotonic() - started, 2)
    return row


async def evaluate(args):
    dataset = json.loads(args.dataset.read_text())
    cases = [
        case for case in dataset["cases"] if not args.cases or case["id"] in args.cases
    ]
    if not cases or (args.cases and set(args.cases) - {case["id"] for case in cases}):
        raise ValueError("Unknown or empty case selection")
    originals = {str(DEFAULT_DB_PATH): digest(DEFAULT_DB_PATH)}
    originals.update(
        {
            str(path): digest(path)
            for path in (PROJECT / "knowledge/policies").glob("*.md")
        }
    )
    source_digest = hashlib.sha256(
        "".join(
            str(path.relative_to(PROJECT)) + digest(path)
            for path in sorted((PROJECT / "src").rglob("*.py"))
        ).encode()
    ).hexdigest()
    report = {
        "scope": "business_acceptance_contracts_and_literal_anchors_not_semantic_accuracy",
        "dataset_version": dataset["version"],
        "dataset_sha256": digest(args.dataset),
        "source_sha256": source_digest,
        "checked_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "strategy": "adjacent",
        "versions": {
            name: metadata.version(name)
            for name in [
                "langgraph",
                "langchain-ollama",
                "torch",
                "sentence-transformers",
            ]
        },
        "cases": [],
        "summary": {},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        rows = report["cases"]
        languages = sorted({row["language"] for row in rows})
        categories = sorted({row["category"] for row in rows})
        report["summary"] = {
            "passed": sum(row["pass"] for row in rows),
            "completed": len(rows),
            "planned": len(cases),
            "original_db_and_policies_unchanged": all(
                digest(Path(path)) == value for path, value in originals.items()
            ),
            "manual_semantic_review": "pending",
            "by_language": {
                language: {
                    "passed": sum(
                        row["pass"] for row in rows if row["language"] == language
                    ),
                    "completed": sum(row["language"] == language for row in rows),
                }
                for language in languages
            },
            "by_category": {
                category: {
                    "passed": sum(
                        row["pass"] for row in rows if row["category"] == category
                    ),
                    "completed": sum(row["category"] == category for row in rows),
                }
                for category in categories
            },
        }
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    with (
        tempfile.TemporaryDirectory(prefix="react-business-eval-") as temporary,
        patch.dict(
            os.environ,
            {
                "KNOWLEDGE_STRATEGY": "adjacent",
                "KNOWLEDGE_INDEX_PATH": str(PROJECT / "knowledge/index/policies-faiss"),
            },
        ),
    ):
        for case in cases:
            print("START " + case["id"] + " " + case["turns"][-1], flush=True)
            row = await run_case(Path(temporary), case, args, dataset["customer_email"])
            report["cases"].append(row)
            save()
            print(
                json.dumps(
                    {key: row[key] for key in ["id", "pass", "failures", "seconds"]},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    save()
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return int(
        not all(row["pass"] for row in report["cases"])
        or not report["summary"]["original_db_and_policies_unchanged"]
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT / "knowledge/evaluation/business_acceptance_v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT / "knowledge/results/business_acceptance.json",
    )
    parser.add_argument("--model", default="ollama/qwen3:4b-instruct-2507-q4_K_M")
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Whole scenario including turns/resume, seconds",
    )
    parser.add_argument("--cases", nargs="+")
    raise SystemExit(asyncio.run(evaluate(parser.parse_args())))
