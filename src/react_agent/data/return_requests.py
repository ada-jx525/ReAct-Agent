"""Return repository: parameterized queries and serialized SQLite writes."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .db import _connect, _decode_order


def load_context(conn: sqlite3.Connection, order_id: str, email: str) -> dict[str, Any]:
    """Read ownership, order, policy and application using one snapshot."""
    row = conn.execute(
        "SELECT * FROM orders WHERE order_id = ? AND email = ?",
        (order_id, email),
    ).fetchone()
    if row is None:
        return {"order": None, "policy": None, "existing": None}
    existing = conn.execute(
        "SELECT * FROM return_requests WHERE order_id = ? AND customer_email = ?",
        (order_id, email),
    ).fetchone()
    row_policy = conn.execute(
        "SELECT * FROM return_policies WHERE active = 1"
    ).fetchone()
    policy = dict(row_policy) if row_policy is not None else None
    if policy is not None:
        policy["conditions"] = json.loads(policy.pop("conditions_json"))
    return {
        "order": _decode_order(row),
        "policy": policy,
        "existing": dict(existing) if existing is not None else None,
    }


def read_context(order_id: str, email: str, db_path: str | Path) -> dict[str, Any]:
    with _connect(db_path) as conn:
        conn.execute("BEGIN")
        return load_context(conn, order_id, email)


@contextmanager
def transaction(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(
        Path(db_path).resolve().as_uri() + "?mode=rw",
        uri=True,
        timeout=10,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_request(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO return_requests (
            request_id, order_id, customer_email, reason, status, return_scope,
            policy_id, policy_version, approval_fingerprint, order_snapshot_json,
            policy_snapshot_json, is_demo, approved_at, created_at
        ) VALUES (
            :request_id, :order_id, :customer_email, :reason, :status, :return_scope,
            :policy_id, :policy_version, :approval_fingerprint, :order_snapshot_json,
            :policy_snapshot_json, :is_demo, :approved_at, :created_at
        )
    """,
        row,
    )
