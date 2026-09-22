"""Prepare immutable approvals and create whole-order applications after approval."""

import hashlib
import json
import logging
import re
import sqlite3
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..data import return_requests as repository
from ..runtime_context import AgentRuntimeContext
from ..security import UnsafeRecord, validate_items
from .returns import evaluate_eligibility

logger = logging.getLogger(__name__)


def _error(code: str, message: str, data=None) -> dict[str, Any]:
    return {"ok": False, "data": data, "error": {"code": code, "message": message}}


def _public_request(row: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: row[key]
        for key in (
            "request_id",
            "order_id",
            "reason",
            "status",
            "return_scope",
            "policy_id",
            "policy_version",
            "approved_at",
            "created_at",
        )
    }
    result["is_demo"] = bool(row["is_demo"])
    return result


class ReturnRequestService:
    """This internal service does not authenticate callers or grant approval.

    Only an application-controlled interrupt handler may supply the approved
    fingerprint. Exposed APIs must authenticate the approver before resuming.
    """

    def __init__(self, customer_email: str, db_path: str | Path):
        self.email = customer_email.strip().lower()
        self.db_path = db_path
        self.scope = AgentRuntimeContext(
            customer_email=self.email, orders_db_path=str(db_path)
        ).identity_scope()

    def _validate(self, order_id: str, reason: str):
        if not isinstance(order_id, str) or not isinstance(reason, str):
            return (
                None,
                None,
                _error("invalid_argument", "Order ID and reason must be text."),
            )
        if not self.email:
            return (
                None,
                None,
                _error("identity_required", "A trusted customer identity is required."),
            )
        order_id = order_id.strip().upper()
        reason = unicodedata.normalize("NFC", reason.strip())
        if not re.fullmatch(r"ORD-[0-9]{1,20}", order_id):
            return (
                None,
                None,
                _error("invalid_argument", "Provide an order ID such as ORD-1001."),
            )
        if not 1 <= len(reason) <= 500 or any(
            unicodedata.category(c).startswith("C") for c in reason
        ):
            return (
                None,
                None,
                _error(
                    "invalid_argument",
                    "Reason must be 1-500 characters without control characters.",
                ),
            )
        return order_id, reason, None

    def _prepare(
        self, context: dict[str, Any], reason: str, now: datetime
    ) -> dict[str, Any]:
        order, policy, existing = (
            context["order"],
            context["policy"],
            context["existing"],
        )
        if order is None:
            return _error("not_found", "No accessible order was found.")
        if existing is not None:
            return {
                "ok": True,
                "data": {
                    "created": False,
                    "already_exists": True,
                    "request": _public_request(existing),
                    "is_demo": bool(existing["is_demo"]),
                },
                "error": None,
            }
        if policy is None:
            return _error(
                "policy_unavailable", "No active return policy is configured."
            )
        try:
            validate_items(order.get("items"))
        except UnsafeRecord:
            return _error(
                "unsafe_record", "Order metadata requires manual verification."
            )
        eligibility = evaluate_eligibility(order, policy, now)
        if eligibility["eligible"] is not True:
            code = (
                "eligibility_unknown"
                if eligibility["eligible"] is None
                else "not_eligible"
            )
            return _error(
                code,
                eligibility["reason"],
                {"eligibility": eligibility, "is_demo": eligibility["is_demo"]},
            )
        order_snapshot = {
            key: order.get(key)
            for key in (
                "order_id",
                "status",
                "items",
                "total_cents",
                "currency",
                "delivered_at",
                "delivered_at_source",
            )
        }
        bound = {
            "customer_scope": self.scope,
            "order": order_snapshot,
            "policy": policy,
            "reason": reason,
            "return_scope": "whole_order",
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                bound, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        return {
            "ok": True,
            "data": {
                "action": "initiate_return",
                "order_id": order["order_id"],
                "reason": reason,
                "return_scope": "whole_order",
                "items": order["items"],
                "policy_id": policy["policy_id"],
                "policy_version": policy["version"],
                "deadline": eligibility["deadline"],
                "fingerprint": fingerprint,
                "expires_at": (now + timedelta(minutes=10)).isoformat(),
                "is_demo": eligibility["is_demo"],
                "created": False,
                "order_snapshot": order_snapshot,
                "policy_snapshot": policy,
            },
            "error": None,
        }

    def prepare_return(self, order_id: str, reason: str) -> dict[str, Any]:
        """Read-only: no draft/application is inserted before approval."""
        order_id, reason, error = self._validate(order_id, reason)
        if error:
            return error
        try:
            context = repository.read_context(order_id, self.email, self.db_path)
            return self._prepare(context, reason, datetime.now(UTC))
        except (sqlite3.Error, ValueError, KeyError, TypeError, OverflowError) as exc:
            logger.error("Return preparation failed: %s", type(exc).__name__)
            return _error("backend_error", "Return application service is unavailable.")

    def submit_return(
        self, order_id: str, reason: str, approved_fingerprint: str
    ) -> dict[str, Any]:
        """Recheck facts under a write lock and insert once; no approved=True bypass."""
        order_id, reason, error = self._validate(order_id, reason)
        if error:
            return error
        try:
            with repository.transaction(self.db_path) as conn:
                now = datetime.now(UTC)
                prepared = self._prepare(
                    repository.load_context(conn, order_id, self.email), reason, now
                )
                if not prepared["ok"] or prepared["data"].get("already_exists"):
                    return prepared
                draft = prepared["data"]
                if draft["fingerprint"] != approved_fingerprint:
                    return _error(
                        "approval_stale",
                        "Order, reason or policy changed; a new confirmation is required.",
                    )
                row = {
                    "request_id": "RET-" + uuid4().hex.upper(),
                    "order_id": order_id,
                    "customer_email": self.email,
                    "reason": reason,
                    "status": "submitted",
                    "return_scope": "whole_order",
                    "policy_id": draft["policy_id"],
                    "policy_version": draft["policy_version"],
                    "approval_fingerprint": approved_fingerprint,
                    "order_snapshot_json": json.dumps(
                        draft["order_snapshot"], ensure_ascii=False
                    ),
                    "policy_snapshot_json": json.dumps(
                        draft["policy_snapshot"], ensure_ascii=False
                    ),
                    "is_demo": int(draft["is_demo"]),
                    "approved_at": now.isoformat(),
                    "created_at": now.isoformat(),
                }
                repository.insert_request(conn, row)
            return {
                "ok": True,
                "data": {
                    "created": True,
                    "already_exists": False,
                    "request": _public_request(row),
                    "is_demo": draft["is_demo"],
                },
                "error": None,
            }
        except (sqlite3.Error, ValueError, KeyError, TypeError, OverflowError) as exc:
            logger.error("Return submission failed: %s", type(exc).__name__)
            return _error("backend_error", "Return application service is unavailable.")
