"""Seed explicitly synthetic shipment snapshots without changing existing data."""

import argparse
import sqlite3
from pathlib import Path

if __package__:
    from .db import DEFAULT_DB_PATH
else:
    from db import DEFAULT_DB_PATH

# These are demo snapshots, not carrier API responses or live locations.
SAMPLE_SHIPMENTS = [
    ("FDX-78901234", "FedEx", "delivered", "Springfield, IL"),
    ("UPS-45678901", "UPS", "in_transit", "London → Stansted"),
    ("DHL-11223344", "DHL", "delivered", "Mountain View, CA"),
    ("UPS-99887766", "UPS", "out_for_delivery", "Hollywood, CA"),
]
SNAPSHOT_TIME = "2026-09-12T08:00:00+00:00"


def initialize_shipments(db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """Add missing snapshots and their single known event, preserving updates."""
    path = Path(db_path).resolve()
    # Require an existing orders database; do not create an accidental empty DB.
    with sqlite3.connect(path.as_uri() + "?mode=rw", uri=True) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("SELECT order_id FROM orders LIMIT 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shipments (
                tracking_number TEXT PRIMARY KEY,
                carrier TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN (
                    'label_created', 'in_transit', 'out_for_delivery',
                    'delivered', 'exception', 'returned'
                )),
                current_location TEXT,
                updated_at TEXT NOT NULL,
                estimated_delivery_at TEXT,
                data_source TEXT NOT NULL DEFAULT 'local_demo'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shipment_events (
                tracking_number TEXT NOT NULL REFERENCES shipments(tracking_number),
                event_id TEXT NOT NULL,
                status TEXT NOT NULL,
                location TEXT,
                occurred_at TEXT NOT NULL,
                description TEXT NOT NULL,
                PRIMARY KEY (tracking_number, event_id)
            )
        """)
        inserted = 0
        for tracking, carrier, status, location in SAMPLE_SHIPMENTS:
            cursor = conn.execute(
                """
                INSERT INTO shipments (
                    tracking_number, carrier, status, current_location, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tracking_number) DO NOTHING
            """,
                (tracking, carrier, status, location, SNAPSHOT_TIME),
            )
            if cursor.rowcount:
                inserted += 1
                conn.execute(
                    """
                    INSERT INTO shipment_events (
                        tracking_number, event_id, status, location,
                        occurred_at, description
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        tracking,
                        "demo-snapshot-1",
                        status,
                        location,
                        SNAPSHOT_TIME,
                        "Synthetic demo snapshot; earlier events are not available.",
                    ),
                )
    return inserted


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    count = initialize_shipments(args.db_path)
    print(f"Database: {args.db_path.resolve()} | inserted demo shipments: {count}")
