"""Deterministic status/window checks; these do not approve refunds or write data."""

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..data import db

logger = logging.getLogger(__name__)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}


def _policy_data(policy: dict[str, Any]) -> dict[str, Any]:
    return {**policy, "is_demo": policy["data_source"] == "local_demo"}


def evaluate_eligibility(
    order: dict[str, Any],
    policy: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Evaluate using aware UTC times; unknown information is never a rejection."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Evaluation time must have a timezone")
    now = now.astimezone(UTC)
    days = policy["window_days"]
    if (
        not isinstance(days, int)
        or not 1 <= days <= 3650
        or policy["required_status"] != "delivered"
    ):
        raise ValueError("Invalid return policy")
    data = {
        "order_id": order["order_id"],
        "eligible": None,
        "decision": "insufficient_information",
        "reason_code": None,
        "reason": None,
        "policy_id": policy["policy_id"],
        "policy_version": policy["version"],
        "window_days": days,
        "checked_at": now.isoformat(),
        "delivered_at": order.get("delivered_at"),
        "deadline": None,
        "delivery_data_source": order.get("delivered_at_source"),
        "is_demo": policy["data_source"] == "local_demo"
        or order.get("delivered_at_source") == "local_demo",
        "scope": "Order status and return application window only; not refund approval.",
    }
    if order["status"] != policy["required_status"]:
        data.update(
            eligible=False,
            decision="ineligible",
            reason_code="order_not_delivered",
            reason="Only delivered orders can use this return flow; cancellation is a separate process.",
        )
        return data
    raw = order.get("delivered_at")
    if not raw:
        data.update(
            reason_code="delivery_time_missing",
            reason="Delivery time is missing; manual verification is required.",
        )
        return data
    try:
        delivered = datetime.fromisoformat(raw)
        if delivered.tzinfo is None or delivered.utcoffset() is None:
            raise ValueError("Missing timezone")
        delivered = delivered.astimezone(UTC)
    except (ValueError, TypeError):
        data.update(
            reason_code="delivery_time_invalid",
            reason="Delivery time is invalid or has no timezone; manual verification is required.",
        )
        return data
    if delivered > now:
        data.update(
            reason_code="delivery_time_in_future",
            reason="Delivery time is in the future; manual verification is required.",
        )
        return data
    deadline = delivered + timedelta(days=days)
    data["deadline"] = deadline.isoformat()
    if now <= deadline:
        data.update(
            eligible=True,
            decision="eligible_to_apply",
            reason_code="within_return_window",
            reason="The order is delivered and within the application window. No return has been submitted or refund approved.",
        )
    else:
        data.update(
            eligible=False,
            decision="ineligible",
            reason_code="return_window_expired",
            reason="The standard return application window has expired; this does not decide warranty rights.",
        )
    return data


class ReturnService:
    """Read-only policy access and customer-scoped eligibility evaluation."""

    def __init__(self, customer_email: str, db_path: str | Path):
        self.customer_email = customer_email.strip().lower()
        self.db_path = db_path

    def get_return_policy(self) -> dict[str, Any]:
        """Policy is public; do not silently substitute a hardcoded policy."""
        try:
            policy = db.get_active_return_policy(self.db_path)
            if policy is None:
                return _error(
                    "policy_unavailable", "No active return policy is configured."
                )
            return {"ok": True, "data": _policy_data(policy), "error": None}
        except (sqlite3.Error, json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.error("Return policy lookup failed: %s", type(exc).__name__)
            return _error("backend_error", "Return policy service is unavailable.")

    def check_return_eligibility(self, order_id: str) -> dict[str, Any]:
        """Use stored order facts, never LLM-supplied delivery dates or decisions."""
        if not self.customer_email:
            return _error(
                "identity_required", "A trusted customer identity is required."
            )
        key = order_id.strip().upper()
        if not re.fullmatch(r"ORD-[0-9]{1,20}", key):
            return _error("invalid_argument", "Provide an order ID such as ORD-1001.")
        try:
            order = db.lookup_order_for_customer(key, self.customer_email, self.db_path)
            if order is None:
                return _error("not_found", "No accessible order was found.")
            policy = db.get_active_return_policy(self.db_path)
            if policy is None:
                return _error(
                    "policy_unavailable", "No active return policy is configured."
                )
            result = evaluate_eligibility(order, policy, datetime.now(UTC))
            return {"ok": True, "data": result, "error": None}
        except (sqlite3.Error, ValueError, KeyError, TypeError, OverflowError) as exc:
            logger.error("Return eligibility check failed: %s", type(exc).__name__)
            return _error("backend_error", "Return eligibility service is unavailable.")
