import asyncio
import importlib
import sqlite3

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main


@pytest.fixture
def setup(tmp_path, monkeypatch):
    import src.db as restaurant_db

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POS_WEBHOOK_SECRET", "test-secret")

    main = importlib.import_module("main")

    # Isolate the restaurant database
    test_db = str(tmp_path / "test.db")
    monkeypatch.setattr(restaurant_db, "DB_NAME", test_db)
    monkeypatch.setattr(main, "DB_NAME", test_db)

    monkeypatch.setattr(main, "WEBHOOK_SECRET", "test-secret")

    main.initialize_database()
    main.add_pos_sync_column()

    return TestClient(main.app), main


def test_invalid_customization(setup):
    client, _ = setup
    response = client.post(
        "/check-order",
        json={
            "items": [{"item_id": 1, "quantity": 1, "modifications": ["extra bacon"]}]
        },
    )
    assert response.status_code == 422


def test_customized_order(setup):
    client, _ = setup

    response = client.post(
        "/orders",
        json={
            "request_id": "custom-001",
            "items": [
                {"item_id": 1, "quantity": 1, "modifications": ["no onions"]},
                {"item_id": 1, "quantity": 1, "modifications": ["extra cheese"]},
            ],
        },
    )
    assert response.status_code == 201

    order = response.json()
    assert order["total"] == 18.0
    assert len(order["items"]) == 2
    assert order["items"][0]["modifications"] == ["extra cheese"]
    assert order["items"][1]["modifications"] == ["no onions"]


def test_inventory_and_duplicate_protection(setup):
    client, _ = setup

    payload = {"request_id": "duplicate-001", "items": [{"item_id": 1, "quantity": 2}]}

    first = client.post("/orders", json=payload)
    second = client.post("/orders", json=payload)

    assert first.status_code == 201
    assert second.json()["already_exists"] == True

    assert first.json()["order_id"] == second.json()["order_id"]

    menu = client.get("/menu").json()["items"]
    burger = next(item for item in menu if item["id"] == 1)

    assert burger["stock"] == 48


def test_webhook_authentication(setup):
    client, _ = setup
    created = client.post(
        "/orders",
        json={"request_id": "webhook-001", "items": [{"item_id": 1, "quantity": 1}]},
    )

    order_id = created.json()["order_id"]

    event = {"order_id": order_id, "status": "accepted"}
    unauthorized = client.post("/webhooks/pos", json=event)

    assert unauthorized.status_code == 401

    authorized = client.post(
        "/webhooks/pos", json=event, headers={"X-Webhook-Secret": "test-secret"}
    )

    assert authorized.status_code == 200
    assert authorized.json()["status"] == "accepted"


def test_pos_failure_preserves_order(setup, monkeypatch):
    client, main = setup

    created = client.post(
        "/orders",
        json={
            "request_id": "pos-failure-001",
            "items": [{"item_id": 1, "quantity": 1}],
        },
    )

    order_id = created.json()["order_id"]

    # Simulate POS being unavailable
    def fake_send_to_pos(payload, pos_url):
        raise HTTPException(status_code=503, detail="POS unavailable")

    monkeypatch.setattr(main, "send_to_pos", fake_send_to_pos)

    response = client.post(f"/orders/{order_id}/sync-pos")

    assert response.status_code == 503

    with main.db_connection() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()

    assert order is not None
    assert order["pos_sync_status"] == "pending"


def test_pos_recovers_after_temporary_failure(monkeypatch):
    attempts = []

    def fake_post(url, json, timeout):
        attempts.append(url)

        if len(attempts) < 3:
            raise httpx.ConnectError("POS unavailable")

        return httpx.Response(
            201, json={"status": "received"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda _: None)

    result = main.send_to_pos(
        {"order_id": 123, "items": [], "total_cents": 0}, "http://pos:8001"
    )

    assert len(attempts) == 3
    assert result["status"] == "received"
    assert all(url == "http://pos:8001/pos/orders" for url in attempts)


def test_pos_stays_unavailable(monkeypatch):
    attempts = []

    def fake_post(url, json, timeout):
        attempts.append(url)
        raise httpx.ConnectError("POS unavailable")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda _: None)

    with pytest.raises(HTTPException) as error:
        main.send_to_pos(
            {"order_id": 123, "items": [], "total_cents": 0}, "http://pos:8001"
        )

    assert error.value.status_code == 503
    assert len(attempts) == 3


def test_pos_idempotency_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("POS_WEBHOOK_SECRET", "test-secret")

    pos = importlib.import_module("mock_pos")

    from src import pos_db

    db_path = tmp_path / "test_pos.db"

    monkeypatch.setattr(pos_db, "DB_NAME", str(db_path))

    pos.initialize_database()
    client = TestClient(pos.app)
    payload = {
        "order_id": 1001,
        "items": [{"item_id": 1, "quantity": 2, "modifications": ["no onions"]}],
        "total_cents": 1800,
    }

    # First delivery
    first = client.post("/pos/orders", json=payload)

    assert first.status_code == 201
    assert first.json()["already_exists"] is False

    # Duplicate delivery
    second = client.post("/pos/orders", json=payload)

    assert second.status_code == 201
    assert second.json()["already_exists"] is True

    # Verify only one record exists
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM pos_orders WHERE order_id = ?", (1001,)
        ).fetchone()[0]

    assert count == 1

    # Verify data survives closing and reopening SQLite
    retrieved = client.get("/pos/orders/1001")

    assert retrieved.status_code == 200
    assert retrieved.json()["total_cents"] == 1800
    assert retrieved.json()["items"][0]["modifications"] == ["no onions"]

    # A conflicting retry must be rejected
    conflicting_payload = {**payload, "total_cents": 1900}

    conflict = client.post("/pos/orders", json=conflicting_payload)

    assert conflict.status_code == 409


def test_recovery_worker_syncs_pending_order(setup, monkeypatch):
    client, main = setup

    # Create a pending restaurant order
    response = client.post(
        "/orders",
        json={
            "request_id": "recovery-worker-test-001",
            "items": [{"item_id": 1, "quantity": 1}],
        },
    )

    assert response.status_code == 201
    order_id = response.json()["order_id"]

    # Simulate a successful POS acknowledgment
    def fake_send_to_pos(payload, pos_url):
        assert payload["order_id"] == order_id
        return {"status": "received"}

    monkeypatch.setattr(main, "send_to_pos", fake_send_to_pos)

    # Stop the infinite recovery loop after one scan
    async def stop_after_scan(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(main.asyncio, "sleep", stop_after_scan)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.recover_pending_orders())

    # Verify the database was updated
    with main.db_connection() as conn:
        order = conn.execute(
            "SELECT pos_sync_status FROM orders WHERE id = ?", (order_id,)
        ).fetchone()

    assert order["pos_sync_status"] == "synced"


def test_backend_health(setup):
    client, _ = setup

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["service"] == "restaurant-backend"


def test_order_idempotency_benchmark(setup):
    import statistics
    import time

    client, main = setup

    payload = {
        "request_id": "benchmark-idempotency-001",
        "items": [{"item_id": 1, "quantity": 1}],
    }

    # Create the original order
    response = client.post("/orders", json=payload)
    assert response.status_code in (200, 201)

    latencies = []
    total_retries = 100
    successful_retries = 0

    # Simulate repeated submissions of the same order
    for _ in range(total_retries):
        start = time.perf_counter()
        response = client.post("/orders", json=payload)
        elapsed = (time.perf_counter() - start) * 1000

        latencies.append(elapsed)

        if response.status_code in (200, 201):
            successful_retries += 1

    # Verify only one database record exists
    with main.db_connection() as conn:
        order_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE request_id = ?
            """,
            (payload["request_id"],),
        ).fetchone()[0]

    assert order_count == 1
    assert successful_retries == total_retries

    print("\nORDER IDEMPOTENCY BENCHMARK")
    print("-----------------------------")
    print(f"Retries: {total_retries}")
    print(f"Successful retries: {successful_retries}")
    print(f"Database orders: {order_count}")
    print(f"Mean latency: {statistics.mean(latencies):.2f} ms")
    print(f"p50: {statistics.median(latencies):.2f} ms")
    print(f"p95: {sorted(latencies)[94]:.2f} ms")
    print(f"Duplicate prevention: {100 if order_count == 1 else 0}%")


def test_concurrent_order_idempotency(setup):
    import time
    from concurrent.futures import ThreadPoolExecutor

    client, main = setup

    payload = {
        "request_id": "concurrent-order-001",
        "items": [{"item_id": 1, "quantity": 1}],
    }

    total_requests = 10

    def submit_order(_):
        start = time.perf_counter()

        response = client.post("/orders", json=payload)

        latency_ms = (time.perf_counter() - start) * 1000

        return response.status_code, latency_ms

    # Submit 10 requests concurrently
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(submit_order, range(total_requests)))

    status_codes = [result[0] for result in results]
    latencies = [result[1] for result in results]

    with main.db_connection() as conn:
        order_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE request_id = ?
            """,
            (payload["request_id"],),
        ).fetchone()[0]

    print("\nCONCURRENT ORDER BENCHMARK")
    print("-----------------------------")
    print(f"Concurrent requests: {total_requests}")
    print(f"Status codes: {status_codes}")
    print(f"Database orders: {order_count}")
    print(f"Average latency: {sum(latencies) / len(latencies):.2f} ms")
    print(f"Maximum latency: {max(latencies):.2f} ms")

    assert order_count == 1
    assert all(status in (200, 201) for status in status_codes)


def test_pos_recovery_after_outage(setup, monkeypatch):
    import asyncio

    from fastapi import HTTPException

    client, main = setup

    response = client.post(
        "/orders",
        json={
            "request_id": "recovery-benchmark-001",
            "items": [{"item_id": 1, "quantity": 1}],
        },
    )

    assert response.status_code in (200, 201)
    order_id = response.json()["order_id"]

    attempts = 0

    def fake_send_to_pos(payload, pos_url):
        nonlocal attempts
        attempts += 1

        # First attempt: POS is unavailable
        if attempts == 1:
            raise HTTPException(
                status_code=503,
                detail="POS temporarily unavailable",
            )

        # Second attempt: POS has recovered
        return {"status": "received"}

    monkeypatch.setattr(
        main,
        "send_to_pos",
        fake_send_to_pos,
    )

    sleep_calls = 0

    async def fake_sleep(seconds):
        nonlocal sleep_calls
        sleep_calls += 1

        # Permit one retry, then stop the worker
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(
        main.asyncio,
        "sleep",
        fake_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.recover_pending_orders())

    with main.db_connection() as conn:
        order = conn.execute(
            "SELECT pos_sync_status FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()

    print("\nPOS RECOVERY BENCHMARK")
    print("--------------------------")
    print(f"POS attempts: {attempts}")
    print(f"Final status: {order['pos_sync_status']}")

    assert attempts == 2
    assert order["pos_sync_status"] == "synced"


@pytest.mark.parametrize("failures_before_recovery", range(10))
def test_multiple_pos_recovery_scenarios(
    setup,
    monkeypatch,
    failures_before_recovery,
):
    import asyncio

    from fastapi import HTTPException

    client, main = setup

    response = client.post(
        "/orders",
        json={
            "request_id": (f"recovery-scenario-{failures_before_recovery}"),
            "items": [{"item_id": 1, "quantity": 1}],
        },
    )

    assert response.status_code in (200, 201)
    order_id = response.json()["order_id"]

    attempts = 0

    def fake_send_to_pos(payload, pos_url):
        nonlocal attempts
        attempts += 1

        if attempts <= failures_before_recovery:
            raise HTTPException(
                status_code=503,
                detail="Simulated POS outage",
            )

        return {"status": "received"}

    monkeypatch.setattr(
        main,
        "send_to_pos",
        fake_send_to_pos,
    )

    async def fake_sleep(seconds):
        # Stop after the worker successfully retries.
        if attempts > failures_before_recovery:
            raise asyncio.CancelledError()

    monkeypatch.setattr(
        main.asyncio,
        "sleep",
        fake_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.recover_pending_orders())

    with main.db_connection() as conn:
        status = conn.execute(
            """
            SELECT pos_sync_status
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        ).fetchone()["pos_sync_status"]

    assert attempts == failures_before_recovery + 1
    assert status == "synced"

    print(
        f"Failures: {failures_before_recovery}, "
        f"Attempts: {attempts}, "
        f"Final status: {status}"
    )
