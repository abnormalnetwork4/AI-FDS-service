from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.contracts import FDSIngest, GatewayAudit, ModelReply
from app.gateway import GatewaySettings, create_gateway
from app.main import create_app
from app.schemas import now


class RecordingModel:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def generate(self, body):
        self.calls.append(body)
        if self.error:
            raise self.error
        return ModelReply(text="DEMO_ANSWER_PRIVATE", mode="demo")


class RecordingSink:
    def __init__(self, error=None):
        self.bodies = []
        self.error = error

    def send(self, body):
        self.bodies.append(body)
        if self.error:
            raise self.error


def chat(**overrides):
    return {"user_id": "user-1", "device_id": "device-1", "model": "local-demo",
            "text": "PRIVATE_PROMPT_CANARY", "input_origin": "external_document", **overrides}


def envelope(event_id="request-1", when=None):
    time = when or now()
    return FDSIngest(audit=GatewayAudit(
        id=event_id, user_id="user-1", device_id="device-1", session_id="session-" + event_id,
        model="local-demo", started_at=time, ended_at=time, request_bytes=4,
        policy_action="allow", policy_reason="model_allowed", outcome="completed"),
        text="test", input_origin="direct_user")


def test_gateway_to_fds_correlates_both_paths(tmp_path):
    model = RecordingModel()
    gateway_path, fds_path = tmp_path / "gateway.db", tmp_path / "fds.db"
    with TestClient(create_app(fds_path)) as fds:
        class Sink:
            def send(self, body):
                response = fds.post("/api/v1/ingest/gateway", json=body.model_dump(mode="json"))
                assert response.status_code == 200

        with TestClient(create_gateway(gateway_path, model_client=model, fds_sink=Sink())) as gateway:
            response = gateway.post("/api/v1/chat", json=chat())
            assert response.status_code == 200
            result = response.json()
            assert result["answer"] == "DEMO_ANSWER_PRIVATE"
            assert result["model_mode"] == "demo"
            assert model.calls[0].input_origin == "external_document"
            request_id = result["request_id"]
            audit = gateway.get(f"/api/v1/requests/{request_id}").json()
            assert audit["fds_delivery"] == "delivered"
            assessment = fds.get(f"/api/v1/assessments/{request_id}").json()
            assert assessment["session_id"] == audit["session_id"]
            assert assessment["status"] == "pending"
            assert assessment["final_grade"] == "unassessed"
            assert len(assessment["results"]) == 3
            assert {r["source_event_id"] for r in assessment["results"]} == {request_id}
            windows = fds.get("/api/v1/behavior-windows").json()
            assert {w["duration_minutes"] for w in windows} == {5, 60}
            assert all(w["features"]["request_count"] == 1 for w in windows)
            assert fds.get("/api/v1/network-sessions").json()[0]["source"] == "gateway_application"
    for path in (gateway_path, fds_path):
        assert b"PRIVATE_PROMPT_CANARY" not in path.read_bytes()
        assert b"DEMO_ANSWER_PRIVATE" not in path.read_bytes()


@pytest.mark.parametrize("body, reason", [
    (chat(model="unapproved"), "model_not_allowed"),
    (chat(text="가" * 11), "request_too_large"),
])
def test_policy_blocks_without_model_call(tmp_path, body, reason):
    model, sink = RecordingModel(), RecordingSink()
    with TestClient(create_gateway(tmp_path / "gateway.db", settings=GatewaySettings(max_prompt_bytes=30),
                                   model_client=model, fds_sink=sink)) as gateway:
        response = gateway.post("/api/v1/chat", json=body)
        assert response.status_code == 403
        assert response.json()["reason"] == reason
        assert response.json()["answer"] is None
        assert model.calls == []
        assert sink.bodies[0].audit.outcome == "blocked"


@pytest.mark.parametrize("error, status", [(httpx.ReadTimeout("private details"), 504), (ValueError("private details"), 502)])
def test_upstream_errors_are_audited(tmp_path, error, status):
    sink = RecordingSink()
    with TestClient(create_gateway(tmp_path / "gateway.db", model_client=RecordingModel(error), fds_sink=sink)) as gateway:
        response = gateway.post("/api/v1/chat", json=chat())
        assert response.status_code == status
        assert "private details" not in response.text
        assert sink.bodies[0].audit.outcome == "upstream_error"


def test_fds_failure_does_not_turn_successful_generation_into_error(tmp_path):
    with TestClient(create_gateway(tmp_path / "gateway.db", model_client=RecordingModel(),
                                   fds_sink=RecordingSink(RuntimeError("offline")))) as gateway:
        response = gateway.post("/api/v1/chat", json=chat())
        assert response.status_code == 200
        audit = gateway.get("/api/v1/requests/" + response.json()["request_id"]).json()
        assert audit["outcome"] == "completed"
        assert audit["fds_delivery"] == "failed"


def test_collection_retry_and_conflicting_event(tmp_path):
    with TestClient(create_app(tmp_path / "fds.db")) as fds:
        body = envelope().model_dump(mode="json")
        first = fds.post("/api/v1/ingest/gateway", json=body)
        second = fds.post("/api/v1/ingest/gateway", json=body)
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert fds.get("/api/v1/dashboard/summary").json()["analysis_count"] == 3
        body["text"] = "diff"
        assert fds.post("/api/v1/ingest/gateway", json=body).status_code == 409
        assert len(fds.get("/api/v1/assessments").json()) == 1


def test_collection_validation_and_atomic_conflict(tmp_path):
    with TestClient(create_app(tmp_path / "fds.db")) as fds:
        body = envelope().model_dump(mode="json")
        body["audit"]["request_bytes"] = 99
        assert fds.post("/api/v1/ingest/gateway", json=body).status_code == 422
        assert fds.get("/api/v1/network-sessions").json() == []
        assert fds.post("/api/v1/ingest/gateway", json=envelope().model_dump(mode="json")).status_code == 200
        conflicting = envelope("request-2").model_dump(mode="json")
        conflicting["audit"]["session_id"] = "session-request-1"
        assert fds.post("/api/v1/ingest/gateway", json=conflicting).status_code == 409
        assert len(fds.get("/api/v1/gateway-audits").json()) == 1
        assert len(fds.get("/api/v1/risks").json()) == 3


def test_rolling_windows_include_recent_history(tmp_path):
    moment = now()
    with TestClient(create_app(tmp_path / "fds.db")) as fds:
        for name, minutes in [("old", 61), ("hour", 10), ("recent", 1), ("current", 0)]:
            response = fds.post("/api/v1/ingest/gateway", json=envelope(name, moment - timedelta(minutes=minutes)).model_dump(mode="json"))
            assert response.status_code == 200
        windows = fds.get("/api/v1/behavior-windows").json()
        latest = {w["duration_minutes"]: w for w in windows[:2]}
        assert latest[5]["features"]["request_count"] == 2
        assert latest[60]["features"]["request_count"] == 3


def test_engine_failure_is_visible_and_other_engine_keeps_results(tmp_path):
    class BrokenData:
        def analyze(self, request):
            raise RuntimeError(request.text)

    with TestClient(create_app(tmp_path / "fds.db", data_engine=BrokenData())) as fds:
        response = fds.post("/api/v1/ingest/gateway", json=envelope().model_dump(mode="json"))
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "error"
        assert [r["status"] for r in result["results"]] == ["error", "pending", "pending"]
        assert result["results"][0]["findings"][0]["reason"] != "test"
        assert result["final_grade"] == "unassessed"


def test_gateway_rejects_arbitrary_upstream_fields(tmp_path):
    model = RecordingModel()
    with TestClient(create_gateway(tmp_path / "gateway.db", model_client=model, fds_sink=RecordingSink())) as gateway:
        assert gateway.post("/api/v1/chat", json=chat(upstream_url="http://example.test")).status_code == 422
        assert not model.calls
