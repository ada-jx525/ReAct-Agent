"""Paired real retrieval diagnostics; no index rebuild or business DB writes.

This is a development set: literal coverage is not general answer accuracy.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from evaluate_retrieval import CASES, assess

from react_agent.knowledge import store as store_module
from react_agent.knowledge.retrieval import RECOVERY_QUERIES, retrieve_evidence
from react_agent.knowledge.store import DEFAULT_INDEX, PolicyStore
from react_agent.knowledge.sufficiency import missing_coverage

WINDOW_MARKERS = [
    (
        "return_policy_general.md",
        ["14 × 24 hours from the recorded delivery timestamp"],
    ),
    (
        "return_policy_general.md",
        ["Contacting support alone does not reserve or extend the window."],
    ),
]
EXTRA_CASES = [
    ("deadline_original", "收到商品16天，之前联系过客服，能退吗？", WINDOW_MARKERS),
    (
        "deadline_paraphrase",
        "签收已经超过两周，之前找过客服说要退货，还能申请吗？",
        WINDOW_MARKERS,
    ),
    (
        "deadline_contact_first",
        "我在退货期限内咨询过客服，但是没提交申请，现在过期了怎么办？",
        WINDOW_MARKERS,
    ),
    (
        "deadline_english",
        "Can I return an item after 16 days if I contacted support earlier?",
        WINDOW_MARKERS,
    ),
]


def main(args):
    rows = []
    inputs = list(
        dict.fromkeys(
            "task: search result | query: " + question
            for question in [q for _, q, _ in CASES + EXTRA_CASES]
            + list(RECOVERY_QUERIES.values())
        )
    )
    # One batch avoids repeatedly cold-loading Ollama during diagnostics.
    vectors = dict(zip(inputs, PolicyStore().embed(inputs)))
    for strategy in ("adjacent", "parent_child"):
        index = (
            DEFAULT_INDEX
            if strategy == "adjacent"
            else DEFAULT_INDEX.with_name("policies-faiss-parent-child")
        )
        with patch.dict(
            os.environ,
            {
                "KNOWLEDGE_STRATEGY": strategy,
                "KNOWLEDGE_INDEX_PATH": str(index),
                "RERANK_ENABLED": "true",
            },
        ):
            store = PolicyStore()
            original_embed = store.embed

            def cached_embed(texts):
                missing = list(
                    dict.fromkeys(text for text in texts if text not in vectors)
                )
                if missing:
                    vectors.update(zip(missing, original_embed(missing)))
                return [vectors[text] for text in texts]

            store.embed = cached_embed
            original_rerank = store_module.rerank_candidates
            trace = []

            def traced_rerank(query, candidates):
                ranked = original_rerank(query, candidates)
                trace.append(
                    {
                        "query": query,
                        "vector_candidates": [
                            {
                                "source": c["source_file"],
                                "chunk_index": c["chunk_index"],
                                "cosine": round(s, 6),
                            }
                            for c, _, s in candidates
                        ],
                        "reranked": [
                            {
                                "source": c["source_file"],
                                "chunk_index": c["chunk_index"],
                                "rerank_logit": c["rerank_score"],
                            }
                            for c, _, _ in ranked
                        ],
                    }
                )
                return ranked

            with patch.object(store_module, "rerank_candidates", traced_rerank):
                for name, question, requirements in CASES + EXTRA_CASES:
                    trace.clear()
                    baseline = store.safe_search(question)
                    baseline_trace = list(trace)
                    trace.clear()
                    recovered = retrieve_evidence(question, store)
                    row = {
                        "strategy": strategy,
                        "case": name,
                        "question": question,
                        "before": assess(baseline, requirements),
                        "after": assess(recovered, requirements),
                        "baseline_trace": baseline_trace,
                        "recovery_trace": list(trace),
                        "recovery": (recovered.get("data") or {}).get(
                            "retrieval_recovery"
                        ),
                    }
                    for mode, result in [("before", baseline), ("after", recovered)]:
                        row[mode]["missing_topics"] = missing_coverage(
                            question, (result.get("data") or {}).get("matches", [])
                        )
                    rows.append(row)
                    print(
                        f"{strategy} {name}: {row['before']['status']} -> {row['after']['status']}",
                        flush=True,
                    )
    summary = {}
    for strategy in ("adjacent", "parent_child"):
        subset = [r for r in rows if r["strategy"] == strategy]
        known = [
            r
            for r in subset
            if r["case"] not in ("unknown_address", "unknown_warranty")
        ]
        summary[strategy] = {
            mode: {
                "complete": sum(r[mode]["status"] == "complete" for r in known),
                "known_total": len(known),
                "off_target_source_groups": sum(
                    r[mode]["off_target_source_groups"] for r in known
                ),
                "evidence_characters": sum(
                    r[mode]["evidence_characters"] for r in known
                ),
            }
            for mode in ("before", "after")
        }
    deadline_cases = {"support_deadline", *(name for name, _, _ in EXTRA_CASES)}
    acceptance = {
        "deadline_cases_complete": all(
            r["after"]["status"] == "complete"
            for r in rows
            if r["case"] in deadline_cases
        ),
        "no_complete_case_regression": all(
            r["after"]["status"] == "complete"
            for r in rows
            if r["before"]["status"] == "complete"
        ),
        "unknown_topics_not_expanded": all(
            r["recovery"]["search_count"] == 1
            for r in rows
            if r["case"] in ("unknown_address", "unknown_warranty")
        ),
    }
    report = {
        "scope": "paired_real_retrieval_development_not_answer_accuracy",
        "summary": summary,
        "acceptance": acceptance,
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(acceptance, ensure_ascii=False))
    if not all(acceptance.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT / "knowledge/results/retrieval_recovery_evaluation.json",
    )
    main(parser.parse_args())
