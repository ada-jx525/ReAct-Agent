"""Initialize SQLite using the existing sample orders, without overwriting rows."""

import argparse
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

if __package__:
    from .db import DEFAULT_DB_PATH
else:
    from db import DEFAULT_DB_PATH


orders = [
    # ----------------------------
    # 原始项目风格的基础订单
    # ----------------------------
    (
        "ORD-1001",
        "James Wilson",
        "james@example.com",
        [
            {
                "product_name": "Wireless Headphones",
                "sku": "SKU-501",
                "quantity": 1,
                "price": 79.99,
            },
            {
                "product_name": "USB-C Cable",
                "sku": "SKU-102",
                "quantity": 2,
                "price": 12.99,
            },
        ],
        "delivered",
        105.97,
        12,
        "742 Evergreen Terrace, Springfield, IL 62701",
        "FDX-78901234",
    ),
    (
        "ORD-1002",
        "Sarah Chen",
        "sarah@example.com",
        [
            {
                "product_name": "Mechanical Keyboard",
                "sku": "SKU-201",
                "quantity": 1,
                "price": 149.99,
            }
        ],
        "shipped",
        149.99,
        4,
        "221B Baker Street, London, NW1 6XE",
        "UPS-45678901",
    ),
    (
        "ORD-1003",
        "Marcus Rivera",
        "marcus@example.com",
        [
            {
                "product_name": '27" 4K Monitor',
                "sku": "SKU-305",
                "quantity": 1,
                "price": 499.99,
            }
        ],
        "confirmed",
        499.99,
        0,
        "350 Fifth Avenue, New York, NY 10118",
        None,
    ),
    (
        "ORD-1004",
        "Emily Foster",
        "emily@example.com",
        [
            {
                "product_name": "Wireless Mouse",
                "sku": "SKU-410",
                "quantity": 1,
                "price": 49.99,
            },
            {
                "product_name": "XL Mousepad",
                "sku": "SKU-411",
                "quantity": 1,
                "price": 19.99,
            },
        ],
        "cancelled",
        69.98,
        3,
        "1 Infinite Loop, Cupertino, CA 95014",
        None,
    ),
    (
        "ORD-1005",
        "David Kim",
        "david@example.com",
        [
            {
                "product_name": "Wireless Charger",
                "sku": "SKU-601",
                "quantity": 1,
                "price": 39.99,
            }
        ],
        "delivered",
        39.99,
        5,
        "1600 Amphitheatre Pkwy, Mountain View, CA 94043",
        "DHL-11223344",
    ),
    (
        "ORD-1006",
        "Olivia Brown",
        "olivia@example.com",
        [
            {
                "product_name": "Tablet Stand",
                "sku": "SKU-701",
                "quantity": 1,
                "price": 29.99,
            },
            {
                "product_name": "Screen Protector",
                "sku": "SKU-702",
                "quantity": 2,
                "price": 9.99,
            },
        ],
        "shipped",
        49.97,
        2,
        "4059 Mt Lee Dr, Hollywood, CA 90068",
        "UPS-99887766",
    ),
    (
        "ORD-1007",
        "Alex Turner",
        "alex@example.com",
        [
            {
                "product_name": "1TB Portable SSD",
                "sku": "SKU-801",
                "quantity": 1,
                "price": 129.99,
            }
        ],
        "confirmed",
        129.99,
        0,
        "10 Downing Street, London, SW1A 2AA",
        None,
    ),
    (
        "ORD-1008",
        "Sophia Martinez",
        "sophia@example.com",
        [
            {
                "product_name": "Smartwatch Pro",
                "sku": "SKU-901",
                "quantity": 1,
                "price": 299.99,
            }
        ],
        "delivered",
        299.99,
        16,
        "5th Ave & 59th St, New York, NY 10022",
        "FDX-55667788",
    ),
    (
        "ORD-1009",
        "Daniel Park",
        "daniel@example.com",
        [
            {
                "product_name": "Bluetooth Speaker",
                "sku": "SKU-1001",
                "quantity": 1,
                "price": 89.99,
            }
        ],
        "delivered",
        89.99,
        1,
        "100 Broadway, New York, NY 10005",
        "DHL-99001122",
    ),
    (
        "ORD-1010",
        "Rachel Green",
        "rachel@example.com",
        [
            {
                "product_name": "Laptop Sleeve",
                "sku": "SKU-1101",
                "quantity": 1,
                "price": 34.99,
            }
        ],
        "shipped",
        34.99,
        6,
        "90 Bedford St, New York, NY 10014",
        "FDX-33445566",
    ),
    (
        "ORD-1011",
        "James Wilson",
        "james@example.com",
        [
            {
                "product_name": "HD Webcam",
                "sku": "SKU-1201",
                "quantity": 1,
                "price": 89.99,
            }
        ],
        "shipped",
        89.99,
        1,
        "742 Evergreen Terrace, Springfield, IL 62701",
        "UPS-11223344",
    ),
    (
        "ORD-1012",
        "Grace Patel",
        "grace@example.com",
        [
            {
                "product_name": "Wireless Earbuds",
                "sku": "SKU-502",
                "quantity": 2,
                "price": 79.99,
            },
            {
                "product_name": "Phone Case",
                "sku": "SKU-1301",
                "quantity": 1,
                "price": 24.99,
            },
            {
                "product_name": "Car Charger",
                "sku": "SKU-1302",
                "quantity": 1,
                "price": 19.99,
            },
        ],
        "delivered",
        204.96,
        7,
        "555 Market St, San Francisco, CA 94105",
        "DHL-77889900",
    ),
    # ----------------------------
    # 我们自己扩展的订单
    # ----------------------------
    (
        "ORD-1013",
        "James Wilson",
        "james@example.com",
        [
            {
                "product_name": "Noise Cancelling Earbuds",
                "sku": "SKU-1401",
                "quantity": 1,
                "price": 129.99,
            }
        ],
        "delivered",
        129.99,
        3,
        "742 Evergreen Terrace, Springfield, IL 62701",
        "UPS-22334455",
    ),
    (
        "ORD-1014",
        "Sarah Chen",
        "sarah@example.com",
        [
            {
                "product_name": "USB-C Hub",
                "sku": "SKU-1402",
                "quantity": 1,
                "price": 59.99,
            }
        ],
        "delivered",
        59.99,
        2,
        "221B Baker Street, London, NW1 6XE",
        "DHL-22334455",
    ),
    (
        "ORD-1015",
        "David Kim",
        "david@example.com",
        [
            {
                "product_name": "65W USB-C Charger",
                "sku": "SKU-1403",
                "quantity": 1,
                "price": 45.99,
            }
        ],
        "delivered",
        45.99,
        21,
        "1600 Amphitheatre Pkwy, Mountain View, CA 94043",
        "FDX-22334455",
    ),
    (
        "ORD-1016",
        "Grace Patel",
        "grace@example.com",
        [
            {
                "product_name": "Phone Tripod",
                "sku": "SKU-1404",
                "quantity": 1,
                "price": 31.99,
            }
        ],
        "processing",
        31.99,
        1,
        "555 Market St, San Francisco, CA 94105",
        None,
    ),
    (
        "ORD-1017",
        "Marcus Rivera",
        "marcus@example.com",
        [
            {
                "product_name": "Gaming Headset",
                "sku": "SKU-1405",
                "quantity": 1,
                "price": 109.99,
            }
        ],
        "delivered",
        109.99,
        6,
        "350 Fifth Avenue, New York, NY 10118",
        "UPS-33445577",
    ),
    (
        "ORD-1018",
        "Emily Foster",
        "emily@example.com",
        [
            {
                "product_name": "Ergonomic Keyboard",
                "sku": "SKU-1406",
                "quantity": 1,
                "price": 119.99,
            }
        ],
        "delivered",
        119.99,
        30,
        "1 Infinite Loop, Cupertino, CA 95014",
        "DHL-33445577",
    ),
    (
        "ORD-1019",
        "Olivia Brown",
        "olivia@example.com",
        [
            {
                "product_name": "Portable Power Bank",
                "sku": "SKU-1407",
                "quantity": 1,
                "price": 49.99,
            }
        ],
        "cancelled",
        49.99,
        2,
        "4059 Mt Lee Dr, Hollywood, CA 90068",
        None,
    ),
    (
        "ORD-1020",
        "Sophia Martinez",
        "sophia@example.com",
        [
            {
                "product_name": "Fitness Tracker",
                "sku": "SKU-1408",
                "quantity": 1,
                "price": 79.99,
            }
        ],
        "delivered",
        79.99,
        4,
        "5th Ave & 59th St, New York, NY 10022",
        "UPS-44556677",
    ),
    (
        "ORD-1021",
        "Daniel Park",
        "daniel@example.com",
        [
            {
                "product_name": "Mini Bluetooth Speaker",
                "sku": "SKU-1409",
                "quantity": 2,
                "price": 39.99,
            }
        ],
        "delivered",
        79.98,
        8,
        "100 Broadway, New York, NY 10005",
        "FDX-44556677",
    ),
    (
        "ORD-1022",
        "Rachel Green",
        "rachel@example.com",
        [
            {
                "product_name": "Laptop Cooling Pad",
                "sku": "SKU-1410",
                "quantity": 1,
                "price": 42.99,
            }
        ],
        "shipped",
        42.99,
        2,
        "90 Bedford St, New York, NY 10014",
        "UPS-55667788",
    ),
    (
        "ORD-1023",
        "Alex Turner",
        "alex@example.com",
        [
            {
                "product_name": "2TB Portable SSD",
                "sku": "SKU-1411",
                "quantity": 1,
                "price": 199.99,
            }
        ],
        "delivered",
        199.99,
        5,
        "10 Downing Street, London, SW1A 2AA",
        "DHL-55667788",
    ),
    (
        "ORD-1024",
        "Grace Patel",
        "grace@example.com",
        [
            {
                "product_name": "Wireless Gaming Mouse",
                "sku": "SKU-1412",
                "quantity": 1,
                "price": 69.99,
            }
        ],
        "delivered",
        69.99,
        14,
        "555 Market St, San Francisco, CA 94105",
        "FDX-66778899",
    ),
    (
        "ORD-1025",
        "James Wilson",
        "james@example.com",
        [
            {
                "product_name": "Smart Home Camera",
                "sku": "SKU-1413",
                "quantity": 1,
                "price": 99.99,
            }
        ],
        "delivered",
        99.99,
        1,
        "742 Evergreen Terrace, Springfield, IL 62701",
        "UPS-66778899",
    ),
    (
        "ORD-1026",
        "Sarah Chen",
        "sarah@example.com",
        [
            {
                "product_name": "Portable Monitor",
                "sku": "SKU-1414",
                "quantity": 1,
                "price": 229.99,
            }
        ],
        "confirmed",
        229.99,
        0,
        "221B Baker Street, London, NW1 6XE",
        None,
    ),
    (
        "ORD-1027",
        "Marcus Rivera",
        "marcus@example.com",
        [
            {
                "product_name": "Gaming Controller",
                "sku": "SKU-1415",
                "quantity": 1,
                "price": 64.99,
            }
        ],
        "delivered",
        64.99,
        9,
        "350 Fifth Avenue, New York, NY 10118",
        "DHL-77881122",
    ),
    (
        "ORD-1028",
        "Olivia Brown",
        "olivia@example.com",
        [
            {
                "product_name": "USB Microphone",
                "sku": "SKU-1416",
                "quantity": 1,
                "price": 89.99,
            }
        ],
        "delivered",
        89.99,
        3,
        "4059 Mt Lee Dr, Hollywood, CA 90068",
        "UPS-77881122",
    ),
    (
        "ORD-1029",
        "Emily Foster",
        "emily@example.com",
        [
            {
                "product_name": "Smart LED Desk Lamp",
                "sku": "SKU-1417",
                "quantity": 1,
                "price": 54.99,
            }
        ],
        "shipped",
        54.99,
        1,
        "1 Infinite Loop, Cupertino, CA 95014",
        "FDX-88992211",
    ),
    (
        "ORD-1030",
        "Daniel Park",
        "daniel@example.com",
        [
            {
                "product_name": "Wi-Fi Smart Plug",
                "sku": "SKU-1418",
                "quantity": 3,
                "price": 19.99,
            }
        ],
        "delivered",
        59.97,
        2,
        "100 Broadway, New York, NY 10005",
        "DHL-88992211",
    ),
]


def initialize_database(db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """Create the orders table and insert missing samples; return inserted count.

    The sample's integer age is deliberately not interpreted as delivery age.
    Existing orders are preserved, including any later business updates.
    """
    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                email TEXT NOT NULL COLLATE NOCASE,
                items_json TEXT NOT NULL,
                status TEXT NOT NULL,
                total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
                currency TEXT NOT NULL DEFAULT 'USD',
                sample_age_days INTEGER NOT NULL CHECK (sample_age_days >= 0),
                shipping_address TEXT NOT NULL,
                tracking_number TEXT,
                delivered_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(email)")
        inserted = 0
        for (
            order_id,
            name,
            email,
            items,
            status,
            total,
            age,
            address,
            tracking,
        ) in orders:
            cursor = conn.execute(
                """
                INSERT INTO orders (
                    order_id, customer_name, email, items_json, status,
                    total_cents, sample_age_days, shipping_address, tracking_number
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO NOTHING
            """,
                (
                    order_id,
                    name,
                    email.strip().lower(),
                    json.dumps(items, ensure_ascii=False),
                    status,
                    int((Decimal(str(total)) * 100).quantize(Decimal("1"))),
                    age,
                    address,
                    tracking,
                ),
            )
            inserted += cursor.rowcount
    return inserted


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    count = initialize_database(args.db_path)
    print(f"Database: {args.db_path.resolve()} | inserted orders: {count}")
