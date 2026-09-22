"""Shipment business access scoped to an application-supplied customer."""

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..data import db

logger = logging.getLogger(__name__)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}


class ShipmentService:
    """Identity must come from a verified session in an exposed application."""

    def __init__(self, customer_email: str, db_path: str | Path):
        self.customer_email = customer_email.strip().lower()
        self.db_path = db_path

    def track_shipment(self, tracking_number: str) -> dict[str, Any]:
        """Read a snapshot; never infer an ETA or reveal another customer's data."""
        if not self.customer_email:
            return _error(
                "identity_required", "A trusted customer identity is required."
            )
        if not isinstance(tracking_number, str):
            return _error("invalid_argument", "Provide a valid tracking number.")
        key = tracking_number.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{2,63}", key):
            return _error("invalid_argument", "Provide a valid tracking number.")
        try:
            result = db.lookup_shipment_for_customer(
                key, self.customer_email, self.db_path
            )
            if result is None:
                return _error("not_found", "No accessible shipment was found.")
            if result["shipment"] is None:
                return _error(
                    "data_unavailable",
                    "This order has a tracking number but no shipment snapshot is available.",
                )
            shipment = result["shipment"]
            shipment["is_demo"] = shipment["data_source"] == "local_demo"
            return {"ok": True, "data": shipment, "error": None}
        except (sqlite3.Error, KeyError, TypeError) as exc:
            logger.error("Shipment lookup failed: %s", type(exc).__name__)
            return _error(
                "backend_error", "Shipment service is unavailable. Please try later."
            )
