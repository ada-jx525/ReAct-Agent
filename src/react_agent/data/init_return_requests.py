"""Create the return application table; no sample applications are inserted."""

import argparse
import sqlite3
from pathlib import Path

if __package__:
    from .db import DEFAULT_DB_PATH
else:
    from db import DEFAULT_DB_PATH


def initialize_return_requests(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Require existing orders/policies, preserve all current applications."""
    path = Path(db_path).resolve()
    with sqlite3.connect(path.as_uri() + "?mode=rw", uri=True) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("SELECT order_id FROM orders LIMIT 1")
        conn.execute("SELECT policy_id FROM return_policies LIMIT 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS return_requests (
                request_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL UNIQUE REFERENCES orders(order_id),
                customer_email TEXT NOT NULL COLLATE NOCASE,
                reason TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 500),
                status TEXT NOT NULL CHECK (status = 'submitted'),
                return_scope TEXT NOT NULL CHECK (return_scope = 'whole_order'),
                policy_id TEXT NOT NULL REFERENCES return_policies(policy_id),
                policy_version TEXT NOT NULL,
                approval_fingerprint TEXT NOT NULL UNIQUE CHECK (length(approval_fingerprint) = 64),
                order_snapshot_json TEXT NOT NULL,
                policy_snapshot_json TEXT NOT NULL,
                is_demo INTEGER NOT NULL CHECK (is_demo IN (0, 1)),
                approved_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    initialize_return_requests(args.db_path)
    print(f"Return application table ready: {args.db_path.resolve()}")
