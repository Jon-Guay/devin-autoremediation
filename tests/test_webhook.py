"""Webhook server tests with mocked GitHub + Devin HTTP (no external calls)."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import httpx
import pytest
import respx
from starlette.testclient import TestClient

import routes
from main import app

DEVIN_V3_SESSIONS = "https://api.devin.ai/v3/organizations/org-test/sessions"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_webhook_state() -> None:
    routes.store._sessions.clear()
    routes._in_flight.clear()
    routes.store._write()


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_webhook_no_firing_alerts_skips_downstream(client: TestClient) -> None:
    payload = {"alerts": [{"status": "resolved"}]}
    with respx.mock:
        gh = respx.get(url__regex=r"https://api\.github\.com/repos/.+/issues(\?.*)?$").mock(
            return_value=httpx.Response(200, json=[])
        )
        dv = respx.post(DEVIN_V3_SESSIONS).mock(
            return_value=httpx.Response(200, json={"session_id": "x", "url": "y"})
        )
        r = client.post("/webhook", json=payload)

    assert r.status_code == 200
    assert r.json() == {"status": "no_firing_alerts"}
    assert not gh.called
    assert not dv.called


def test_webhook_invalid_payload(client: TestClient) -> None:
    r = client.post(
        "/webhook",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_webhook_invalid_signature(client: TestClient) -> None:
    payload = {"alerts": [{"status": "firing"}]}
    body = json.dumps(payload).encode()

    with patch.object(routes, "WEBHOOK_SECRET", "sekrit"):
        r = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Grafana-Signature": "sha256=deadbeef",
            },
        )

    assert r.status_code == 401


@respx.mock
def test_webhook_firing_triggers_devin_for_open_issues(client: TestClient) -> None:
    gh = respx.get(url__regex=r"https://api\.github\.com/repos/test/test/issues(\?.*)?$").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 42,
                    "title": "Bug",
                    "body": "Fix me",
                    "html_url": "https://github.com/test/test/issues/42",
                }
            ],
        )
    )
    dv = respx.post(DEVIN_V3_SESSIONS).mock(
        return_value=httpx.Response(
            200,
            json={
                "session_id": "sess_abc",
                "url": "https://app.devin.ai/sessions/sess_abc",
                "status": "new",
            },
        )
    )

    r = client.post("/webhook", json={"alerts": [{"status": "firing"}]})

    assert r.status_code == 200
    assert r.json() == {"status": "accepted"}
    assert gh.called
    assert dv.call_count == 1

    sess = client.get("/sessions").json()
    assert len(sess) == 1
    assert sess[0]["issue_number"] == 42
    assert sess[0]["session_id"] == "sess_abc"


@respx.mock
def test_webhook_idempotent_skips_second_devin_call(client: TestClient) -> None:
    issue_json = [
        {
            "number": 7,
            "title": "T",
            "body": "b",
            "html_url": "https://github.com/test/test/issues/7",
        }
    ]
    respx.get(url__regex=r"https://api\.github\.com/repos/test/test/issues(\?.*)?$").mock(
        return_value=httpx.Response(200, json=issue_json)
    )
    dv = respx.post(DEVIN_V3_SESSIONS).mock(
        return_value=httpx.Response(
            200,
            json={
                "session_id": "sess_one",
                "url": "https://app.devin.ai/sessions/sess_one",
                "status": "new",
            },
        )
    )

    payload = {"alerts": [{"status": "firing"}]}
    assert client.post("/webhook", json=payload).status_code == 200
    assert client.post("/webhook", json=payload).status_code == 200

    assert dv.call_count == 1
