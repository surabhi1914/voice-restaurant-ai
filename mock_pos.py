import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

from src.models.pos import POSOrder, POSStatusUpdate

app = FastAPI(title="Mock Voice Restaurant API")
load_dotenv()  # Load environment variables from .env file
# Temporary in-memory storage for menu items and orders
pos_orders = {}
WEBHOOK_SECRET = os.getenv("POS_WEBHOOK_SECRET")


@app.post("/pos/orders", status_code=201)
def receive_order(order: POSOrder):

    if order.order_id in pos_orders:
        existing = pos_orders[order.order_id]

        def normalize(items):
            return sorted(
                [
                    (
                        item["item_id"],
                        item["quantity"],
                        tuple(sorted(item.get("modifications", []))),
                    )
                    for item in items
                ]
            )

        incoming_items = [item.model_dump() for item in order.items]

        if (
            normalize(incoming_items) != normalize(existing["items"])
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

    pos_orders[order.order_id] = {
        "items": [item.model_dump() for item in order.items],
        "total_cents": order.total_cents,
        "status": "received",
    }

    return {
        "pos_order_id": order.order_id,
        "status": "received",
        "already_exists": False,
    }


@app.get("/pos/orders/{order_id}")
def get_pos_order(order_id: int):

    order = pos_orders.get(order_id)

    if order is None:
        raise HTTPException(status_code=404, detail="POS order not found")

    return {"pos_order_id": order_id, **order}


@app.patch("/pos/orders/{order_id}/status")
def update_pos_status(order_id: int, update: POSStatusUpdate):
    order = pos_orders.get(order_id)

    if order is None:
        raise HTTPException(status_code=404, detail="POS order not found")

    # Prevent changing an already finalized order
    if order["status"] in ("accepted", "rejected") and order["status"] != update.status:
        raise HTTPException(status_code=409, detail="Order already finalized")

    # Notify the restaurant backend
    try:
        response = httpx.post(
            "http://127.0.0.1:8000/webhooks/pos",
            json={"order_id": order_id, "status": update.status},
            headers={"X-Webhook-Secret": WEBHOOK_SECRET},
            timeout=5.0,
        )

        response.raise_for_status()

    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Restaurant backend unavailable")

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail={
                "backend_status": e.response.status_code,
                "backend_response": e.response.text,
            },
        )

    # Update POS only after successful notification
    order["status"] = update.status

    return {
        "pos_order_id": order_id,
        "status": update.status,
        "webhook_delivered": True,
    }
