"""Bounded, coverage-guided query expansion above the existing FAISS search.

Expansion contains search topics, never an answer or a chosen source file.
Evidence must still pass independent assessment against the ORIGINAL question.
"""

import os

from .store import PolicyStore
from .sufficiency import (
    AUTHORITATIVE_POLICY_IDS,
    coverage_requirements,
    missing_coverage,
)

# Governed bilingual domain vocabulary; no order IDs, policy facts or filenames.
RECOVERY_QUERIES = {
    "application_window": "return application deadline contacting customer support window 退货申请期限 联系客服",
    "refund_receipt_vs_posting": "return warehouse receipt refund issued funds posted payment stages 仓库签收 退款到账 状态区别",
    "in_ear_hygiene_condition": "in-ear earbuds opened hygiene seal change of mind defect 耳塞 拆封 卫生封条 无理由退货 质量问题",
    "shipping_responsibility": "return shipping costs responsibility confirmed wrong item defect 退货运费 责任确认 错发商品",
    "refund_destination": "refund original payment method mixed payment unrelated account 退款 原支付方式 混合支付 他人账户",
    "product_safety_transport": "swollen battery burning smell stop use dangerous goods shipping restrictions 电池鼓包 焦味 停止使用 危险品 运输限制",
}
MAX_RECOVERY_SEARCHES = 2
MAX_EVIDENCE_PASSAGES = 5


def _unique(passages):
    positions, result = {}, []
    for passage in passages:
        key = passage["chunk_id"]
        if key not in positions:
            positions[key] = len(result)
            result.append(passage)
        elif result[positions[key]]["text"] in passage["text"]:
            # Adjacent expansion can return a larger view of the same anchor.
            # Keep one identity and only replace it with a genuine superset.
            result[positions[key]] = passage
    return result


def retrieve_evidence(
    question: str,
    store: PolicyStore | None = None,
    policy_topics: list[str] | None = None,
) -> dict:
    """Search once, then at most twice for missing governed evidence topics.

    Search/model failures never trigger a permissive fallback. Unknown topics do
    not cause speculative expansion. Added passages retain original provenance.
    """
    store = store or PolicyStore()
    evidence = store.safe_search(question)
    if evidence.get("ok") is not True:
        return evidence
    data = evidence["data"]
    matches = list(data["matches"])
    initial = missing_coverage(question, matches, policy_topics)
    remaining = list(initial)
    attempts = []
    try:
        budget = int(os.environ.get("KNOWLEDGE_CONTEXT_CHAR_BUDGET", "6000"))
        if not 1800 <= budget <= 20000:
            raise ValueError
    except ValueError:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "invalid_configuration",
                "message": "Context character budget must be 1800-20000.",
            },
        }
    required_topics = [
        topic for topic, _ in coverage_requirements(question, policy_topics)
    ]
    governed_missing = [
        topic
        for topic in required_topics
        if topic in AUTHORITATIVE_POLICY_IDS
        and not any(
            passage.get("policy_id") == AUTHORITATIVE_POLICY_IDS[topic]
            and topic not in missing_coverage(question, [passage], policy_topics)
            for passage in matches
        )
    ]
    recovery_topics = list(dict.fromkeys(initial + governed_missing))
    for topic in recovery_topics:
        needs_authoritative = topic in governed_missing
        if (
            topic not in remaining and not needs_authoritative
        ) or topic not in RECOVERY_QUERIES:
            continue
        if len(attempts) == MAX_RECOVERY_SEARCHES:
            break
        query = RECOVERY_QUERIES[topic]
        recovered = store.safe_search(query)
        attempt = {"topic": topic, "query": query, "applied": False}
        attempts.append(attempt)
        if recovered.get("ok") is not True:
            attempt["error"] = recovered.get("error")
            continue
        additions = list(recovered["data"]["matches"])
        if needs_authoritative:
            additions = [
                passage
                for passage in additions
                if passage.get("policy_id") == AUTHORITATIVE_POLICY_IDS[topic]
            ]
            if not additions:
                continue
        improved = missing_coverage(
            question, _unique(matches + additions), policy_topics
        )
        if not needs_authoritative and len(improved) >= len(remaining):
            continue
        # Remove unnecessary additions, rather than fill context with all hits.
        if not needs_authoritative:
            for passage in list(reversed(additions)):
                reduced = list(additions)
                reduced.remove(passage)
                if (
                    missing_coverage(
                        question, _unique(matches + reduced), policy_topics
                    )
                    == improved
                ):
                    additions = reduced
        combined = _unique(additions + matches)
        bounded, characters = [], 0
        for passage in combined:
            if len(bounded) == MAX_EVIDENCE_PASSAGES:
                break
            if characters + len(passage["text"]) <= budget:
                bounded.append(passage)
                characters += len(passage["text"])
        bounded_missing = missing_coverage(question, bounded, policy_topics)
        # A context limit must not replace one missing topic with another.
        authoritative_present = any(
            passage.get("policy_id") == AUTHORITATIVE_POLICY_IDS.get(topic)
            for passage in bounded
        )
        if (needs_authoritative and authoritative_present) or set(
            bounded_missing
        ) < set(remaining):
            matches, remaining = bounded, bounded_missing
            attempt["applied"] = True
    return {
        **evidence,
        "data": {
            **data,
            "matches": matches,
            "no_evidence": not matches,
            "retrieval_recovery": {
                "initial_missing_topics": initial,
                "remaining_missing_topics": remaining,
                "attempts": attempts,
                "search_count": 1 + len(attempts),
                "final_passage_count": len(matches),
                "final_context_characters": sum(len(p["text"]) for p in matches),
            },
        },
    }
