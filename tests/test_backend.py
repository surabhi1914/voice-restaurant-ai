import importlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # use seperate db for every test
    """
    SN: Monkey patching is a programming technique where you dynamically modify or replace code at runtime, typically by changing methods, functions, or attributes of existing classes or modules without altering their original source code.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POS_WEBHOOK_URL", "test-secret")

    main = importlib.import_module("main")
    monkeypatch.setattr(main, "DB_NAME", str(tmp_path / "test.db"))

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
    def fake_send_to_pos(payload):
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
