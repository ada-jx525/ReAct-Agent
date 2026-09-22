"""Paired retrieval-only evaluation; never builds an index or changes policies.

Labels check literal evidence coverage, not generated-answer correctness.
The cases are a diagnostic/development set, not an independent final benchmark.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

from react_agent.knowledge.store import PolicyStore

# Each requirement is (source file, alternative literal evidence markers).
CASES = [
    (
        "opened_preference",
        "耳塞拆开了，但一次没用，能因为不喜欢退吗？",
        [
            ("electronics_return_policy.md", ["通常不接受无理由退货"]),
            ("electronics_return_policy.md", ["未使用也不自动构成例外"]),
        ],
    ),
    (
        "opened_defect",
        "蓝牙耳塞到手两天，充不进电，拆封了是不是就不给退？",
        [
            ("electronics_return_policy.md", ["充电或配对失败"]),
            ("electronics_return_policy.md", ["质量问题例外不等于已获批准"]),
        ],
    ),
    (
        "application_refund",
        "已经提交退货申请，为什么钱还没到账？",
        [
            ("refund_and_payment_policy.md", ["创建退货申请不等于退货批准"]),
        ],
    ),
    (
        "warehouse_refund",
        "我寄回去的包裹仓库签收了，是不是退款已经到账了？",
        [
            (
                "refund_and_payment_policy.md",
                [
                    "Distinguish application submitted, return approved, return received, refund issued and funds posted."
                ],
            ),
            (
                "refund_and_payment_policy.md",
                ["Do not infer a refund from carrier delivery"],
            ),
        ],
    ),
    (
        "wrong_item_shipping",
        "商家发错型号了，寄回去还需要我承担邮费吗？",
        [
            ("return_shipping_policy.md", ["通常由公司承担合理标准退货运费"]),
            ("return_shipping_policy.md", ["wrong item"]),
            ("return_shipping_policy.md", ["客户报告质量问题不等于已确认"]),
        ],
    ),
    (
        "support_deadline",
        "我收到商品16天了，之前找客服说过要退，这样算没有超期吗？",
        [
            (
                "return_policy_general.md",
                ["Contacting support alone does not reserve or extend the window."],
            ),
            (
                "return_policy_general.md",
                ["14 × 24 hours from the recorded delivery timestamp"],
            ),
        ],
    ),
    (
        "posting_date",
        "我这笔订单的退款会在明天下午到账吗？",
        [
            (
                "refund_and_payment_policy.md",
                ["no guaranteed processing duration or posting date"],
            ),
        ],
    ),
    (
        "expired_label",
        "退货用的预付面单过了有效期，已批准的退货也作废了吗？",
        [
            (
                "return_shipping_policy.md",
                [
                    "An expired label does not automatically invalidate an approved return."
                ],
            ),
            ("return_shipping_policy.md", ["separate from the order"]),
        ],
    ),
    ("unknown_warranty", "保修期具体是几年？", []),
    ("unknown_address", "退货仓库详细地址和收件人电话是什么？", []),
]


def assess(result, requirements):
    if not result["ok"]:
        return {"status": "error", "error": result["error"]}
    matches = result["data"]["matches"]
    hits = [
        any(
            m["source_file"] == source
            and any(marker in m["text"] for marker in markers)
            for m in matches
        )
        for source, markers in requirements
    ]
    if requirements:
        status = "complete" if all(hits) else "partial" if any(hits) else "miss"
    else:
        status = "correct_empty" if not matches else "unsupported_retrieval"
    expected_sources = {source for source, _ in requirements}
    return {
        "status": status,
        "requirements_hit": sum(hits),
        "requirements_total": len(hits),
        # Source-level noise proxy; same-source irrelevant passages may remain.
        "off_target_source_groups": sum(
            m["source_file"] not in expected_sources for m in matches
        ),
        "evidence_characters": sum(len(m["text"]) for m in matches),
        "candidate_count": result["data"]["candidate_count"],
        "relevant_count": result["data"]["relevant_count"],
        "matches": matches,
    }


def main():
    store = PolicyStore()
    # Cache exactly the same real query embeddings for both production paths.
    inputs = ["task: search result | query: " + q for _, q, _ in CASES]
    vectors = store.embed(inputs)
    cached = dict(zip(inputs, vectors))
    store.embed = lambda texts: [cached[text] for text in texts]
    output = {
        "scope": "retrieval_only_development_set",
        "top_k": 3,
        "comparison": "existing relative-cutoff/top-k vs existing cross-encoder/top-k",
        "cases": [],
        "summary": {},
    }
    for name, query, requirements in CASES:
        row = {"id": name, "query": query, "expected_requirements": requirements}
        for mode, enabled in [("without_rerank", "false"), ("with_rerank", "true")]:
            with patch.dict(os.environ, {"RERANK_ENABLED": enabled}):
                row[mode] = assess(store.safe_search(query, top_k=3), requirements)
        output["cases"].append(row)
        print(
            name
            + ": "
            + row["without_rerank"]["status"]
            + " -> "
            + row["with_rerank"]["status"],
            file=sys.stderr,
            flush=True,
        )
    for mode in ("without_rerank", "with_rerank"):
        counts = {}
        for row in output["cases"]:
            status = row[mode]["status"]
            counts[status] = counts.get(status, 0) + 1
        output["summary"][mode] = counts
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
