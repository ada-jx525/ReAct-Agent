"""Test the data layer without importing the Agent or needing API keys."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parents[2] / "src/react_agent/data"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


db = _load("order_database", DATA_DIR / "db.py")


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "db", db)
    initializer = _load("order_initializer", DATA_DIR / "init_db.py")
    path = tmp_path / "orders.db"
    assert initializer.initialize_database(path) == 30
    return path, initializer


def test_lookup_order(database):
    path, _ = database
    order = db.lookup_order(" ord-1001 ", path)
    assert order["customer_name"] == "James Wilson"
    assert order["total_cents"] == 10597
    assert order["currency"] == "USD"
    assert order["items"][0]["sku"] == "SKU-501"
    assert order["delivered_at"] is None
    assert db.lookup_order("ORD-MISSING", path) is None


def test_email_lookup(database):
    path, _ = database
    result = db.lookup_orders_by_email(" JAMES@EXAMPLE.COM ", path)
    assert [o["order_id"] for o in result] == [
        "ORD-1001", "ORD-1011", "ORD-1013", "ORD-1025",
    ]
    assert db.lookup_orders_by_email("missing@example.com", path) == []
    assert db.lookup_orders_by_email("' OR 1=1 --", path) == []


def test_reinitialization_preserves_updates(database):
    path, initializer = database
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE orders SET status = 'returned' WHERE order_id = 'ORD-1001'")
    assert initializer.initialize_database(path) == 0
    assert db.lookup_order("ORD-1001", path)["status"] == "returned"


def test_blank_inputs(database):
    path, _ = database
    with pytest.raises(ValueError):
        db.lookup_order(" ", path)
    with pytest.raises(ValueError):
        db.lookup_orders_by_email(" ", path)


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        db.lookup_order("ORD-1001", path)
    assert not path.exists()
