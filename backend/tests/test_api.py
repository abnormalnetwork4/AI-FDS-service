import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "test.sqlite3")) as client:
        yield client


def session(**overrides):
    return {
        "id": "session-1", "user_id": "user-1", "device_id": "device-1",
        "started_at": "2026-09-20T10:00:00+09:00", "ended_at": "2026-09-20T10:01:00+09:00",
        "destination": "example.test", "bytes_sent": 1024, "bytes_received": 200,
        "via_gateway": True, "connection_action": "allow", **overrides,
    }


def event(**overrides):
    return {
        "id": "event-1", "session_id": "session-1", "user_id": "user-1", "device_id": "device-1",
        "occurred_at": "2026-09-20T10:00:30+09:00", "provider": "example-ai",
        "channel": "api", "request_bytes": 100, "file_count": 1,
        "approved_destination": False, **overrides,
    }


def window(client):
    response = client.post("/api/v1/behavior-windows", json={
        "user_id": "user-1", "device_id": "device-1", "start": "2026-09-20T01:00:00Z",
        "duration_minutes": 5,
    })
    assert response.status_code == 201
    return response.json()


def test_collection_window_analysis_dashboard(client):
    assert client.post("/api/v1/network-sessions", json=session()).status_code == 201
    assert client.post("/api/v1/ai-usage-events", json=event()).status_code == 201
    built = window(client)
    assert built["features"]["bytes_sent"] == 1024
    assert built["features"]["request_count"] == 1
    assert built["features"]["unapproved_ai_requests"] == 1
    response = client.post(f"/api/v1/network-risk/analyze/{built['id']}")
    assert response.status_code == 201
    risk = response.json()
    assert risk["status"] == "pending" and risk["score"] is None
    assert [f["code"] for f in risk["findings"]] == [f"N{i}" for i in range(1, 7)]
    assert client.get(f"/api/v1/risks/{risk['id']}").json() == risk
    summary = client.get("/api/v1/dashboard/summary").json()
    assert summary["network_session_count"] == 1
    assert summary["pending_analysis_count"] == 1
    assert summary["scored_analysis_count"] == 0
    assert summary["average_risk_score"] is None


def test_duplicate_does_not_overwrite(client):
    assert client.post("/api/v1/network-sessions", json=session()).status_code == 201
    assert client.post("/api/v1/network-sessions", json=session(bytes_sent=9999)).status_code == 409
    assert client.get("/api/v1/network-sessions").json()[0]["bytes_sent"] == 1024


def test_event_reference_validation(client):
    assert client.post("/api/v1/ai-usage-events", json=event()).status_code == 422
    client.post("/api/v1/network-sessions", json=session())
    for bad in [event(user_id="other"), event(device_id="other"), event(occurred_at="2026-09-20T11:00:00+09:00")]:
        assert client.post("/api/v1/ai-usage-events", json=bad).status_code == 422
    assert client.get("/api/v1/ai-usage-events").json() == []


@pytest.mark.parametrize("overrides", [
    {"bytes_sent": -1}, {"bytes_sent": 1.5},
    {"started_at": "2026-09-20T10:00:00"},
    {"ended_at": "2026-09-20T09:59:00+09:00"}, {"unexpected": True},
])
def test_invalid_sessions(client, overrides):
    assert client.post("/api/v1/network-sessions", json=session(**overrides)).status_code == 422


def test_half_open_window_and_user_device_isolation(client):
    records = [session(), session(id="end", started_at="2026-09-20T10:05:00+09:00", ended_at="2026-09-20T10:06:00+09:00"),
               session(id="other-user", user_id="other"), session(id="other-device", device_id="other")]
    for record in records:
        assert client.post("/api/v1/network-sessions", json=record).status_code == 201
    assert window(client)["features"]["session_count"] == 1
    assert client.get("/api/v1/dashboard/summary?user_id=other").json()["network_session_count"] == 1


def test_prompt_not_persisted_and_origin_preserved(tmp_path):
    from app.engines import StubDataRiskEngine

    class CaptureEngine(StubDataRiskEngine):
        def analyze(self, request):
            assert request.input_origin == "external_document"
            return super().analyze(request)

    path = tmp_path / "test.sqlite3"
    with TestClient(create_app(path, data_engine=CaptureEngine())) as client:
        response = client.post("/api/v1/data-risk/analyze", json={
            "user_id": "user-1", "text": "PRIVATE_CANARY_1234", "input_origin": "external_document",
        })
        assert response.status_code == 201
        assert len(response.json()["findings"]) == 4
        assert "PRIVATE_CANARY_1234" not in json.dumps(client.get("/api/v1/risks").json())
    assert b"PRIVATE_CANARY_1234" not in path.read_bytes()


def test_restart_persistence(tmp_path):
    path = tmp_path / "test.sqlite3"
    with TestClient(create_app(path)) as client:
        client.post("/api/v1/network-sessions", json=session())
    with TestClient(create_app(path)) as client:
        assert client.get("/health").status_code == 200
        assert len(client.get("/api/v1/network-sessions").json()) == 1


def test_missing_resources_empty_summary_and_pagination(client):
    assert client.post("/api/v1/network-risk/analyze/missing").status_code == 404
    assert client.get("/api/v1/risks/missing").status_code == 404
    assert client.get("/api/v1/dashboard/summary").json()["average_risk_score"] is None
    assert client.get("/api/v1/risks?limit=201").status_code == 422
    client.post("/api/v1/network-sessions", json=session())
    assert client.get("/api/v1/network-sessions?offset=1").json() == []
    assert client.get("/openapi.json").status_code == 200
