import time

import httpx
from fastapi import HTTPException


def send_to_pos(payload):
    max_attempts = 3

    for attempt in range(max_attempts):
        try:
            response = httpx.post(
                "http://127.0.0.1:8001/pos/orders", json=payload, timeout=3.0
            )

            response.raise_for_status()
            return response.json()

        except httpx.RequestError:
            if attempt == max_attempts - 1:
                raise HTTPException(
                    status_code=503, detail="POS unavailable after 3 attempts"
                )

        except httpx.HTTPStatusError as exc:
            # Invalid requests should not be retried.
            if exc.response.status_code < 500:
                raise HTTPException(status_code=502, detail="POS rejected the order")

            if attempt == max_attempts - 1:
                raise HTTPException(
                    status_code=502, detail="POS failed after 3 attempts"
                )

        time.sleep(0.5 * (2**attempt))


def sync_with_pos(order_id, BACKEND_URL):
    try:
        response = httpx.post(f"{BACKEND_URL}/orders/{order_id}/sync-pos", timeout=15)
        response.raise_for_status()
        return True

    except httpx.HTTPError as error:
        print("POS synchronization failed:", error)
        return False
