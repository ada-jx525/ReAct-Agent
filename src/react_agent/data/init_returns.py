"""Initialize a versioned demo policy and explicit synthetic delivery times."""

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

if __package__:
    from .db import DEFAULT_DB_PATH
else:
    from db import DEFAULT_DB_PATH

# Explicit fixtures, not inferred from orders.sample_age_days or carrier data.
DEMO_DELIVERY_AGES = {
    "ORD-1001": 3,
    "ORD-1013": 10,
    "ORD-1015": 21,
    "ORD-1018": 30,
    "ORD-1024": 14,
}


def initialize_returns(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, int]:
    """Preserve existing dates/policies; seed missing demo delivery dates once."""
    now = datetime.now(UTC)
    path = Path(db_path).resolve()
    with sqlite3.connect(path.as_uri() + "?mode=rw", uri=True) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
        if "delivered_at" not in columns:
            raise RuntimeError("Initialize the orders database first.")
        if "delivered_at_source" not in columns:
            conn.execute("ALTER TABLE orders ADD COLUMN delivered_at_source TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS return_policies (
                policy_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                title TEXT NOT NULL,
                window_days INTEGER NOT NULL CHECK (window_days BETWEEN 1 AND 3650),
                required_status TEXT NOT NULL CHECK (required_status = 'delivered'),
                conditions_json TEXT NOT NULL,
                data_source TEXT NOT NULL,
                active INTEGER NOT NULL CHECK (active IN (0, 1)),
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_return_policy
            ON return_policies(active) WHERE active = 1
        """)
        # Do not replace a user's active policy or reactivate an existing version.
        policy_inserted = 0
        if not conn.execute(
            "SELECT 1 FROM return_policies WHERE active = 1"
        ).fetchone():
            cursor = conn.execute(
                """
                INSERT INTO return_policies (
                    policy_id, version, title, window_days, required_status,
                    conditions_json, data_source, active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_id) DO NOTHING
            """,
                (
                    "standard-demo-14d",
                    "demo-v1",
                    "Demo 14-day return application policy",
                    14,
                    "delivered",
                    json.dumps(
                        [
                            "Apply within 14 x 24 hours of delivery; the deadline is inclusive.",
                            "Shipped, processing, confirmed and cancelled orders cannot use this return flow.",
                            "Missing or invalid delivery times require manual verification.",
                            "Eligibility covers status and time only, not refund approval, item inspection or warranty claims.",
                        ]
                    ),
                    "local_demo",
                    1,
                    now.isoformat(),
                ),
            )
            policy_inserted = cursor.rowcount
        dates_inserted = 0
        for order_id, days in DEMO_DELIVERY_AGES.items():
            cursor = conn.execute(
                """
                UPDATE orders SET delivered_at = ?, delivered_at_source = 'local_demo'
                WHERE order_id = ? AND status = 'delivered' AND delivered_at IS NULL
            """,
                ((now - timedelta(days=days)).isoformat(), order_id),
            )
            dates_inserted += cursor.rowcount
    return {
        "policies_inserted": policy_inserted,
        "delivery_dates_inserted": dates_inserted,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    print(f"Database: {args.db_path.resolve()} | {initialize_returns(args.db_path)}")
