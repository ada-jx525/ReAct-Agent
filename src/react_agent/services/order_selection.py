"""Application-owned order selection; model/tool outputs never select an order."""

import re
from typing import Any

from langchain_core.messages import HumanMessage


def selected_order(messages, previous: str = "") -> str:
    latest = next(
        (str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )
    ids = set(
        re.findall(r"(?i)(?<![a-z0-9])ord[\s-]*([0-9]{1,20})(?![a-z0-9])", latest)
    )
    if ids:
        return f"ORD-{next(iter(ids))}" if len(ids) == 1 else ""
    # Explicit referential follow-up only; unrelated questions clear selection.
    if previous and re.search(
        r"这个订单|这笔订单|该订单|this order|that order", latest, re.I
    ):
        return previous
    return ""


def selection_error(state: Any, order_id: str, identity_scope: str):
    def value(name, default=None):
        return (
            state.get(name, default)
            if isinstance(state, dict)
            else getattr(state, name, default)
        )

    if value("customer_scope", "") != identity_scope:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "forbidden",
                "message": "Conversation identity does not match.",
            },
        }
    chosen = value("selected_order_id", "")
    if not chosen:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "order_selection_required",
                "message": "请明确要检查或申请的唯一订单号；不会从订单列表自动选择。",
            },
        }
    if not isinstance(order_id, str) or order_id.strip().upper() != chosen:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "order_selection_mismatch",
                "message": "工具订单号与用户明确选中的订单不一致，未执行检查或申请。",
            },
        }
    return None
