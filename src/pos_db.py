import os
import sqlite3
from contextlib import contextmanager

DB_NAME = os.getenv("POS_DB_NAME", "pos.db")


@contextmanager
def db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=10)
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
            CREATE TABLE IF NOT EXISTS pos_orders (
                order_id INTEGER PRIMARY KEY,
                items_json TEXT NOT NULL,
                total_cents INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'received'
            )
        """)
