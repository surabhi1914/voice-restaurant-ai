import sqlite3
from contextlib import contextmanager

DB_NAME = "restaurant.db"

INITIAL_MENU = [
    (1, "Chicken Burger", 900, 50),
    (2, "Veggie Burger", 800, 8),
    (3, "French Fries", 400, 15),
    (4, "Coke", 250, 20),
    (5, "Pizza", 1200, 6),
    (6, "Sprite", 1000, 12),
    (7, "Ice Cream", 500, 10),
    (
        8,
        "Coffee",
        300,
        25,
    ),
    (9, "Tea", 200, 30),
    (10, "Cake", 700, 5),
]


ALLOWED_MODIFICATIONS = {
    1: ["no onions", "no cheese", "extra cheese"],
    2: ["no onions", "no cheese", "extra cheese"],
    3: ["no salt", "extra salt"],
    4: ["no ice", "extra ice"],
    5: ["extra cheese", "no cheese"],
    6: ["no ice", "extra ice"],
    7: ["no toppings", "extra toppings"],
    8: ["no ice", "extra ice"],
    9: ["no ice", "extra ice"],
    10: ["no frosting", "extra frosting"],
}


@contextmanager
def db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database():
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS menu (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
                stock INTEGER NOT NULL CHECK(stock >= 0)
            )
        """)

        conn.executemany(
            """
            INSERT OR IGNORE INTO menu
            (id, name, price_cents, stock)
            VALUES (?, ?, ?, ?)
        """,
            INITIAL_MENU,
        )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                items_json TEXT NOT NULL,
                total_cents INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'confirmed',
                request_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
