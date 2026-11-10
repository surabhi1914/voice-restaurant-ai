import json
import os
import secrets

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException

from src.db import ALLOWED_MODIFICATIONS, db_connection, initialize_database
from src.helper_functions import send_to_pos
from src.models.order import CreateOrderRequest, OrderRequest
from src.models.pos import POSWebhook

load_dotenv()  # Load environment variables from .env file
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

app = FastAPI(title="Voice Restaurant API")

DB_NAME = os.getenv("DB_NAME", "restaurant.db")
POS_URL = os.getenv("POS_URL", "http://127.0.0.1:8001")
initialize_database()


def add_pos_sync_column():
    with db_connection() as conn:
        columns = conn.execute("PRAGMA table_info(orders)").fetchall()

        column_names = [col["name"] for col in columns]

        if "pos_sync_status" not in column_names:
            conn.execute("""
                ALTER TABLE orders
                ADD COLUMN pos_sync_status TEXT
                NOT NULL DEFAULT 'pending'
            """)


add_pos_sync_column()


@app.get("/menu")
def get_menu():
    with db_connection() as conn:
        rows = conn.execute("SELECT * FROM menu ORDER BY id").fetchall()

    return {
        "items": [
            {
                "id": row["id"],
                "name": row["name"],
                "price": row["price_cents"] / 100,
                "stock": row["stock"],
                "allowed_modifications": ALLOWED_MODIFICATIONS.get(row["id"], []),
            }
            for row in rows
        ]
    }


@app.post("/check-order")
def check_order(order: OrderRequest):
    checked_items = []
    total_cents = 0
    quantities = {}

    with db_connection() as conn:
        for item in order.items:
            row = conn.execute(
                "SELECT * FROM menu WHERE id = ?", (item.item_id,)
            ).fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"Menu item {item.item_id} not found"
                )

            # Check whether modifications are allowed
            allowed = ALLOWED_MODIFICATIONS.get(item.item_id, [])

            for modification in item.modifications:
                if modification not in allowed:
                    raise HTTPException(
                        status_code=422,
                        detail=f"'{modification}' is not allowed for {row['name']}",
                    )

            # Add quantities across all versions of this item
            quantities[item.item_id] = quantities.get(item.item_id, 0) + item.quantity

            subtotal_cents = row["price_cents"] * item.quantity
            total_cents += subtotal_cents

            checked_items.append(
                {
                    "item_id": item.item_id,
                    "name": row["name"],
                    "quantity": item.quantity,
                    "modifications": item.modifications,
                    "unit_price": row["price_cents"] / 100,
                    "subtotal": subtotal_cents / 100,
                }
            )

        # Check combined inventory requirements
        for item_id, quantity in quantities.items():
            row = conn.execute(
                "SELECT name, stock FROM menu WHERE id = ?", (item_id,)
            ).fetchone()

            if quantity > row["stock"]:
                raise HTTPException(
                    status_code=409,
                    detail=f"Only {row['stock']} units of {row['name']} available",
                )

    return {"valid": True, "items": checked_items, "total": total_cents / 100}


@app.post("/orders", status_code=201)
def create_order(order: CreateOrderRequest):
    quantities = {}
    requested_items = []

    # Normalize modifications and retain separate order lines
    for item in order.items:
        modifications = sorted(item.modifications)

        if len(modifications) != len(set(modifications)):
            raise HTTPException(
                status_code=422, detail="Duplicate modifications are not allowed"
            )

        requested_items.append(
            {
                "item_id": item.item_id,
                "quantity": item.quantity,
                "modifications": modifications,
            }
        )

        quantities[item.item_id] = quantities.get(item.item_id, 0) + item.quantity

    # Normalize order lines for idempotency comparison
    requested_items.sort(
        key=lambda x: (x["item_id"], tuple(x["modifications"]), x["quantity"])
    )

    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        # 1. Check whether this request was processed before
        existing = conn.execute(
            "SELECT * FROM orders WHERE request_id = ?", (order.request_id,)
        ).fetchone()

        if existing:
            existing_items = json.loads(existing["items_json"])

            existing_normalized = sorted(
                [
                    {
                        "item_id": item["item_id"],
                        "quantity": item["quantity"],
                        "modifications": sorted(item.get("modifications", [])),
                    }
                    for item in existing_items
                ],
                key=lambda x: (x["item_id"], tuple(x["modifications"]), x["quantity"]),
            )

            if existing_normalized != requested_items:
                raise HTTPException(
                    status_code=409,
                    detail="Request ID already used for a different order",
                )

            return {
                "order_id": existing["id"],
                "status": existing["status"],
                "items": existing_items,
                "total": existing["total_cents"] / 100,
                "already_exists": True,
            }

        # 2. Validate menu items, modifications, and inventory
        menu_rows = {}

        for item_id, quantity in quantities.items():
            row = conn.execute("SELECT * FROM menu WHERE id = ?", (item_id,)).fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"Menu item {item_id} not found"
                )

            if quantity > row["stock"]:
                raise HTTPException(
                    status_code=409,
                    detail=f"Only {row['stock']} units of {row['name']} available",
                )

            menu_rows[item_id] = row

        saved_items = []
        total_cents = 0

        for item in requested_items:
            row = menu_rows[item["item_id"]]
            allowed = ALLOWED_MODIFICATIONS.get(item["item_id"], [])

            for modification in item["modifications"]:
                if modification not in allowed:
                    raise HTTPException(
                        status_code=422,
                        detail=f"'{modification}' is not allowed for {row['name']}",
                    )

            subtotal_cents = row["price_cents"] * item["quantity"]
            total_cents += subtotal_cents

            saved_items.append(
                {
                    "item_id": item["item_id"],
                    "name": row["name"],
                    "quantity": item["quantity"],
                    "modifications": item["modifications"],
                    "unit_price": row["price_cents"] / 100,
                    "subtotal": subtotal_cents / 100,
                }
            )

        # 3. Reduce inventory inside the same transaction
        for item_id, quantity in quantities.items():
            conn.execute(
                "UPDATE menu SET stock = stock - ? WHERE id = ?", (quantity, item_id)
            )

        # 4. Save the complete order
        cursor = conn.execute(
            """
            INSERT INTO orders
                (items_json, total_cents, request_id)
            VALUES (?, ?, ?)
            """,
            (json.dumps(saved_items), total_cents, order.request_id),
        )

        order_id = cursor.lastrowid

    return {
        "order_id": order_id,
        "status": "confirmed",
        "items": saved_items,
        "total": total_cents / 100,
        "already_exists": False,
    }


@app.post("/orders/{order_id}/sync-pos")
def sync_order_to_pos(order_id: int):

    # 1. Retrieve the saved order
    with db_connection() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()

    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # 2. Prepare the POS request
    items = json.loads(order["items_json"])

    payload = {
        "order_id": order["id"],
        "items": [
            {
                "item_id": item["item_id"],
                "quantity": item["quantity"],
                "modifications": item.get("modifications", []),
            }
            for item in items
        ],
        "total_cents": order["total_cents"],
    }

    pos_result = send_to_pos(payload, POS_URL)

    # Save successful synchronization
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE orders
            SET pos_sync_status = 'synced'
            WHERE id = ?
            """,
            (order_id,),
        )

    return {
        "restaurant_order_id": order_id,
        "pos_sync_status": "synced",
        "pos_response": pos_result,
    }


@app.post("/webhooks/pos")
def receive_pos_webhook(
    event: POSWebhook, x_webhook_secret: str | None = Header(default=None)
):
    if not x_webhook_secret or not secrets.compare_digest(
        x_webhook_secret, WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    with db_connection() as conn:
        order = conn.execute(
            "SELECT id, status FROM orders WHERE id = ?", (event.order_id,)
        ).fetchone()

        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # A repeated webhook should not change anything
        if order["status"] == event.status:
            return {
                "order_id": event.order_id,
                "status": event.status,
                "already_processed": True,
            }

        # Don't allow conflicting final statuses
        if order["status"] in ("accepted", "rejected"):
            raise HTTPException(
                status_code=409, detail="Order already has a final status"
            )

        conn.execute(
            "UPDATE orders SET status = ? WHERE id = ?", (event.status, event.order_id)
        )

    return {
        "order_id": event.order_id,
        "status": event.status,
        "already_processed": False,
    }
