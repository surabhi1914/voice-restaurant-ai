import json
import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

from src.models.pos import POSOrder, POSStatusUpdate
from src.pos_db import db_connection, initialize_database

app = FastAPI(title="Mock Voice Restaurant API")
load_dotenv()  # Load environment variables from .env file
# Temporary in-memory storage for menu items and orders
# pos_orders = {}
initialize_database()
WEBHOOK_SECRET = os.getenv("POS_WEBHOOK_SECRET")
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")


def normalize(items):
    return sorted(
        (
            item["item_id"],
            item["quantity"],
            tuple(sorted(item.get("modifications", []))),
        )
        for item in items
    )


@app.post("/pos/orders", status_code=201)
def receive_order(order: POSOrder):
    items = [item.model_dump() for item in order.items]

    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            "SELECT * FROM pos_orders WHERE order_id = ?", (order.order_id,)
        ).fetchone()

        if existing:
            if (
                normalize(items) != normalize(json.loads(existing["items_json"]))
                or order.total_cents != existing["total_cents"]
            ):
                raise HTTPException(
                    status_code=409, detail="Order ID exists with different data"
                )

            return {
                "pos_order_id": order.order_id,
                "status": existing["status"],
                "already_exists": True,
            }

        conn.execute(
            """
            INSERT INTO pos_orders
            (order_id, items_json, total_cents)
            VALUES (?, ?, ?)
            """,
            (order.order_id, json.dumps(items), order.total_cents),
        )

    return {
        "pos_order_id": order.order_id,
        "status": "received",
        "already_exists": False,
    }


@app.get("/pos/orders/{order_id}")
def get_pos_order(order_id: int):
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM pos_orders WHERE order_id = ?", (order_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="POS order not found")

    return {
        "pos_order_id": row["order_id"],
        "items": json.loads(row["items_json"]),
        "total_cents": row["total_cents"],
        "status": row["status"],
    }


@app.patch("/pos/orders/{order_id}/status")
def update_pos_status(order_id: int, update: POSStatusUpdate):

    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            "SELECT status FROM pos_orders WHERE order_id = ?", (order_id,)
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="POS order not found")

        if row["status"] in ("accepted", "rejected"):
            if row["status"] != update.status:
                raise HTTPException(status_code=409, detail="Order already finalized")

            return {
                "pos_order_id": order_id,
                "status": row["status"],
                "webhook_delivered": True,
            }

    try:
        response = httpx.post(
            f"{BACKEND_URL}/webhooks/pos",
            json={"order_id": order_id, "status": update.status},
            headers={"X-Webhook-Secret": WEBHOOK_SECRET},
            timeout=5.0,
        )
        response.raise_for_status()

    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Restaurant backend unavailable")

    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Webhook rejected: HTTP {error.response.status_code}",
        )

    with db_connection() as conn:
        conn.execute(
            "UPDATE pos_orders SET status = ? WHERE order_id = ?",
            (update.status, order_id),
        )

    return {
        "pos_order_id": order_id,
        "status": update.status,
        "webhook_delivered": True,
    }


@app.get("/health")
def health():
    with db_connection() as conn:
        conn.execute("SELECT 1").fetchone()

    return {"status": "healthy", "service": "restaurant-backend"}
