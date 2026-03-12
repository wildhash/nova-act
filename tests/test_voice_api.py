# Copyright 2026 Amazon Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Integration tests for the Nova Clerk FastAPI endpoints."""

from __future__ import annotations


def test_health(fastapi_client):
    resp = fastapi_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_start_session_mock(fastapi_client):
    resp = fastapi_client.post(
        "/api/voice/session/start",
        json={"mode": "mock"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"]
    assert data["mode"] == "mock"
    assert data["state"] == "created"


def test_start_session_real(fastapi_client):
    resp = fastapi_client.post(
        "/api/voice/session/start",
        json={"mode": "real"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "real"


def test_get_session(fastapi_client):
    start = fastapi_client.post(
        "/api/voice/session/start", json={"mode": "mock"}
    ).json()
    resp = fastapi_client.get(f"/api/voice/session/{start['session_id']}")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == start["session_id"]


def test_get_session_not_found(fastapi_client):
    resp = fastapi_client.get("/api/voice/session/does-not-exist")
    assert resp.status_code == 404


def test_stop_session(fastapi_client):
    start = fastapi_client.post(
        "/api/voice/session/start", json={"mode": "mock"}
    ).json()
    resp = fastapi_client.post(
        "/api/voice/session/stop",
        json={"session_id": start["session_id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "stopped"


def test_stop_session_not_found(fastapi_client):
    resp = fastapi_client.post(
        "/api/voice/session/stop",
        json={"session_id": "ghost"},
    )
    assert resp.status_code == 404


def test_list_runs_empty(fastapi_client):
    resp = fastapi_client.get("/api/runs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_run_not_found(fastapi_client):
    resp = fastapi_client.get("/api/runs/nonexistent")
    assert resp.status_code == 404


def test_get_run_receipts_not_found(fastapi_client):
    resp = fastapi_client.get("/api/runs/nonexistent/receipts")
    assert resp.status_code == 404
