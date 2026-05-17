"""FastAPI endpoint tests — black-box via TestClient."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_state(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    body = r.json()
    assert "capital" in body
    assert "strategies" in body
    assert "positions" in body
    assert "system" in body


def test_positions(client):
    r = client.get("/api/positions")
    assert r.status_code == 200
    body = r.json()
    assert "count" in body
    assert "positions" in body


def test_kill_all_requires_confirm(client):
    r = client.post("/api/kill/all")
    assert r.status_code == 400
    assert "X-Confirm" in r.json()["detail"]


def test_kill_all_wrong_confirm(client):
    r = client.post("/api/kill/all", headers={"X-Confirm": "wrong"})
    assert r.status_code == 400


def test_kill_all_correct_confirm(client):
    r = client.post("/api/kill/all", headers={"X-Confirm": "HALT"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["actions"]) >= 1


def test_kill_strategy_correct_confirm(client):
    r = client.post(
        "/api/kill/polymarket",
        headers={"X-Confirm": "PAUSE-POLY"},
    )
    assert r.status_code == 200


def test_pause_strategy(client):
    r = client.post(
        "/api/pause/liquidation",
        headers={"X-Confirm": "PAUSE-LIQ"},
    )
    assert r.status_code == 200


def test_root_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<!DOCTYPE html>" in r.text or "<!doctype html>" in r.text.lower()
    assert "Trading" in r.text
