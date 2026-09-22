"""Customer-scoped, read-only order services with structured outcomes."""
# security layer between the user layer and the database layer

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..data import db
from ..security import UnsafeRecord, validate_items

logger = logging.getLogger(__name__)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}


def _public_order(order: dict[str, Any]) -> dict[str, Any]:
    validate_items(order.get("items"))
    # Exclude address, email, name and internal seed metadata from model context.
    result = {
        key: order[key]
        for key in (
            "order_id",
            "items",
            "status",
            "total_cents",
            "currency",
            "tracking_number",
            "delivered_at",
        )
    }
    result["delivered_at_source"] = order.get("delivered_at_source")
    return result


class OrderService:
    """Use an identity supplied by the host application, not the model.

    This scopes access but does not authenticate the caller. An exposed API must
    derive customer_email from a verified session, not a client-editable context.
    """

    def __init__(self, customer_email: str, db_path: str | Path):
        self.customer_email = (
            customer_email.strip().lower() if isinstance(customer_email, str) else ""
        )
        self.db_path = db_path

    def lookup_my_orders(self) -> dict[str, Any]:
        """List the runtime customer's orders; the caller supplies no identity."""
        return self.lookup_orders_by_email(self.customer_email)

    def lookup_order(self, order_id: str) -> dict[str, Any]:
        """Get a customer's order without revealing another customer's existence."""
        if not self.customer_email:
            return _error(
                "identity_required", "A trusted customer identity is required."
            )
        if not isinstance(order_id, str):
            return _error("invalid_argument", "Provide a valid order ID.")
        key = order_id.strip().upper()
        if not re.fullmatch(r"ORD-[0-9]{1,20}", key):
            return _error("invalid_argument", "Provide an order ID such as ORD-1001.")
        try:
            order = db.lookup_order_for_customer(key, self.customer_email, self.db_path)
            if order is None:
                return _error("not_found", "No accessible order was found.")
            return {"ok": True, "data": _public_order(order), "error": None}
        except UnsafeRecord:
            return _error(
                "unsafe_record", "Order metadata requires manual verification."
            )
        except (sqlite3.Error, json.JSONDecodeError, KeyError, TypeError) as exc:
            # Do not log SQL parameters, database paths or exception text containing PII.
            logger.error("Order lookup failed: %s", type(exc).__name__)
            return _error(
                "backend_error", "Order service is unavailable. Please try later."
            )

    def lookup_orders_by_email(self, email: str) -> dict[str, Any]:
        """Get orders only when the supplied email matches the trusted identity."""
        if not self.customer_email:
            return _error(
                "identity_required", "A trusted customer identity is required."
            )
        if not isinstance(email, str):
            return _error("invalid_argument", "Provide a valid email address.")
        key = email.strip().lower()
        if (
            not key
            or len(key) > 254
            or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", key)
        ):
            return _error("invalid_argument", "Provide a valid email address.")
        if key != self.customer_email:
            return _error(
                "forbidden", "Only the current customer's orders can be queried."
            )
        try:
            orders = db.lookup_orders_by_email(self.customer_email, self.db_path)
            return {
                "ok": True,
                "data": [_public_order(o) for o in orders],
                "error": None,
            }
        except UnsafeRecord:
            return _error(
                "unsafe_record", "Order metadata requires manual verification."
            )
        except (sqlite3.Error, json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.error("Order list lookup failed: %s", type(exc).__name__)
            return _error(
                "backend_error", "Order service is unavailable. Please try later."
            )
