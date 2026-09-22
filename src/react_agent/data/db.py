"""SQLite order queries. Internal data access, not authenticated Agent tools.

Do not expose these functions directly to customers without authorization checks.
An email address or order identifier is a search key, not proof of identity.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

if __package__:
    from ..paths import PROJECT_ROOT
else:
    # Preserve direct execution of the existing database initializer scripts.
    PROJECT_ROOT = Path(
        os.environ.get("REACT_AGENT_PROJECT_ROOT", Path(__file__).resolve().parents[3])
    ).resolve()

DEFAULT_DB_PATH = Path(
    os.environ.get(
        "ORDERS_DB_PATH", PROJECT_ROOT / "src" / "react_agent" / "data" / "orders.db"
    )
).resolve()


def get_active_return_policy(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    """Read the single active versioned policy; fail rather than guess defaults."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM return_policies WHERE active = 1").fetchone()
    if row is None:
        return None
    policy = dict(row)
    policy["conditions"] = json.loads(policy.pop("conditions_json"))
    return policy


@contextmanager
def _connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(db_path).resolve()
    # Read-only mode prevents accidentally creating an empty database on a typo.
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _decode_order(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["items"] = json.loads(result.pop("items_json"))
    return result


def lookup_order(
    order_id: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    """Return an order dictionary or None if absent; reject blank identifiers."""
    key = order_id.strip().upper()
    if not key:
        raise ValueError("order_id must not be blank")
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (key,),
        ).fetchone()
    return _decode_order(row) if row is not None else None


def lookup_shipment_for_customer(
    tracking_number: str,
    customer_email: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    """Return a scoped snapshot; None means no accessible order has this number.

    An accessible order with no carrier snapshot returns {'shipment': None}.
    """
    with _connect(db_path) as conn:
        accessible = conn.execute(
            "SELECT 1 FROM orders WHERE tracking_number = ? AND email = ? LIMIT 1",
            (tracking_number, customer_email),
        ).fetchone()
        if accessible is None:
            return None
        row = conn.execute(
            """
            SELECT s.* FROM shipments s
            WHERE s.tracking_number = ? AND EXISTS (
                SELECT 1 FROM orders o
                WHERE o.tracking_number = s.tracking_number AND o.email = ?
            )
        """,
            (tracking_number, customer_email),
        ).fetchone()
        if row is None:
            return {"shipment": None}
        shipment = dict(row)
        events = conn.execute(
            """
            SELECT status, location, occurred_at, description
            FROM shipment_events WHERE tracking_number = ?
            ORDER BY occurred_at, event_id
        """,
            (tracking_number,),
        ).fetchall()
        shipment["events"] = [dict(event) for event in events]
    return {"shipment": shipment}


def lookup_orders_by_email(
    email: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Return all matching orders in order-ID order, or an empty list."""
    key = email.strip().lower()
    if not key:
        raise ValueError("email must not be blank")
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE email = ? ORDER BY order_id",
            (key,),
        ).fetchall()
    return [_decode_order(row) for row in rows]


def lookup_order_for_customer(
    order_id: str,
    customer_email: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    """Scope order access in SQL; missing and other customers' orders look alike."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ? AND email = ?",
            (order_id, customer_email),
        ).fetchone()
    return _decode_order(row) if row is not None else None
