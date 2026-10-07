from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient

from app.collection import ingest
from app.contracts import CaptureIngest
from app.engines import StubDataRiskEngine, StubNetworkRiskEngine
from app.main import create_app
from app.repository import Repository
from app.schemas import now


def capture(name="cap-1", at=None, text=None):
    time = (at or now()).isoformat()
    body = {"id": name, "session": {
        "id": "session-" + name, "user_id": "employee-1", "device_id": "pc-1",
        "started_at": time, "ended_at": time, "destination": "local-ai.internal",
        "bytes_sent": 1234, "bytes_received": 5678, "source": "packet_capture",
    }}
    if text is not None:
        body["ai_event"] = {"id": "event-" + name, "session_id": "session-" + name,
                            "user_id": "employee-1", "device_id": "pc-1", "occurred_at": time,
                            "provider": "internal-ai", "channel": "api"}
        body["prompt"] = {"text": text, "input_origin": "external_document", "source": "application_log"}
    return body


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "fds.db")) as client:
        yield client


def test_metadata_only_analysis_never_calls_data_model(client):
    class UnexpectedData:
        def analyze(self, request):
            pytest.fail("Data model must not be called without a prompt")

    client.app.state.data_engine = UnexpectedData()
    response = client.post("/api/v1/ingest/captures", json=capture())
    assert response.status_code == 200
    result = response.json()
    assert result["processing_state"] == "finished"
    assert result["status"] == "pending"
    assert result["final_grade"] is None
    assert result["results"][0]["findings"][0]["code"] == "prompt_unavailable"
    assert len(result["results"]) == 1
    windows = client.get("/api/v1/behavior-windows").json()
    assert {w["duration_minutes"] for w in windows} == {5}
    assert all(w["features"]["direct_connections"] == 0 for w in windows)
    assert all(w["features"]["unknown_gateway_connections"] == 1 for w in windows)
    assert all(w["features"]["bytes_sent"] == 1234 for w in windows)
    assert client.get("/api/v1/captures").json()[0]["prompt_status"] == "unavailable"
    assert "policy_action" not in result


def test_prompt_whitespace_preserved_but_not_persisted(tmp_path):
    original = "    PRIVATE_PROMPT_CANARY\n"
    class Data(StubDataRiskEngine):
        def analyze(self, request):
            assert request.text == original
            assert request.input_origin == "external_document"
            return super().analyze(request)

    path = tmp_path / "fds.db"
    with TestClient(create_app(path, data_engine=Data())) as client:
        response = client.post("/api/v1/ingest/captures", json=capture(text=original))
        assert response.status_code == 200
        assert response.json()["results"][0]["engine_version"] == "stub-v1"
    assert b"PRIVATE_PROMPT_CANARY" not in path.read_bytes()


@pytest.mark.parametrize("text, expected", [("x" * 20000, "too_large"), ("   \n", "empty")])
def test_unanalyzable_prompt_still_records_observation(client, text, expected):
    result = client.post("/api/v1/ingest/captures", json=capture(text=text))
    assert result.status_code == 200
    assert result.json()["results"][0]["findings"][0]["code"] == "prompt_" + expected
    assert client.get("/api/v1/captures").json()[0]["prompt_status"] == expected
    assert client.get("/api/v1/dashboard/summary").json()["network_session_count"] == 1


def test_retry_and_conflicting_content(client):
    body = capture()
    first = client.post("/api/v1/ingest/captures", json=body)
    assert first.status_code == 200
    second = client.post("/api/v1/ingest/captures", json=body)
    assert first.json() == second.json()
    assert len(client.get("/api/v1/risks").json()) == 2
    body["session"]["bytes_sent"] += 1
    assert client.post("/api/v1/ingest/captures", json=body).status_code == 409


def test_invalid_link_and_atomic_recording(client):
    invalid = capture(text="test")
    invalid["ai_event"]["device_id"] = "other"
    assert client.post("/api/v1/ingest/captures", json=invalid).status_code == 422
    assert client.get("/api/v1/captures").json() == []
    assert client.post("/api/v1/ingest/captures", json=capture()).status_code == 200
    collision = capture("cap-2")
    collision["session"]["id"] = "session-cap-1"
    assert client.post("/api/v1/ingest/captures", json=collision).status_code == 409
    assert len(client.get("/api/v1/captures").json()) == 1


def test_prior_capture_visible_while_its_model_is_running(tmp_path):
    repo = Repository(tmp_path / "fds.db")
    repo.initialize()
    entered, release = Event(), Event()
    class SlowData(StubDataRiskEngine):
        def analyze(self, request):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("Test coordination timed out")
            return super().analyze(request)
    time = now()
    first = CaptureIngest.model_validate(capture("first", time, "first prompt"))
    second = CaptureIngest.model_validate(capture("second", time + timedelta(seconds=1), "second prompt"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        first_job = pool.submit(ingest, repo, first, SlowData(), StubNetworkRiskEngine())
        try:
            assert entered.wait(timeout=5)
            assert len(repo.list("session")) == 1
            assert repo.get("passive_assessment", "first")["processing_state"] == "processing"
            result = ingest(repo, second, StubDataRiskEngine(), StubNetworkRiskEngine())
            window = repo.get("risk_window", result["risk_window_id"])
            for risk in [r for r in window["results"] if r["engine"] == "network"]:
                assert repo.get("window", risk["window_id"])["features"]["request_count"] == 2
        finally:
            release.set()
        assert first_job.result()["processing_state"] == "finished"


def test_engine_failure_preserves_observed_capture(client):
    class Broken(StubDataRiskEngine):
        def analyze(self, request):
            raise RuntimeError("PRIVATE_EXCEPTION")
    client.app.state.data_engine = Broken()
    response = client.post("/api/v1/ingest/captures", json=capture(text="text"))
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert "PRIVATE_EXCEPTION" not in response.text
    assert len(client.get("/api/v1/captures").json()) == 1


def test_no_gateway_or_model_execution_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/api/v1/chat", "/generate", "/api/v1/ingest/gateway", "/api/v1/gateway-audits"):
        assert path not in paths
    assert client.get("/health").json()["mode"] == "out-of-path"


def test_restart_keeps_captures_and_retry_result(tmp_path):
    path = tmp_path / "fds.db"
    body = capture()
    with TestClient(create_app(path)) as client:
        first = client.post("/api/v1/ingest/captures", json=body).json()
    with TestClient(create_app(path)) as client:
        assert client.get("/api/v1/assessments/" + body["id"]).json() == first
        assert client.post("/api/v1/ingest/captures", json=body).json() == first


def test_concurrent_retransmission_commits_one_result(tmp_path):
    repo = Repository(tmp_path / "fds.db")
    repo.initialize()
    barrier = Barrier(2)
    class WaitingData(StubDataRiskEngine):
        def analyze(self, request):
            barrier.wait(timeout=5)
            return super().analyze(request)
    body = CaptureIngest.model_validate(capture(text="same prompt"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(ingest, repo, body, WaitingData(), StubNetworkRiskEngine()) for _ in range(2)]
        results = [job.result() for job in jobs]
    assert results[0] == results[1]
    assert len(repo.list("session")) == 1
    assert len(repo.list("risk")) == 2
    assert len(repo.list("window")) == 1


def test_retry_resumes_after_recording_but_before_analysis(tmp_path, monkeypatch):
    import app.collection as collection
    repo = Repository(tmp_path / "fds.db")
    repo.initialize()
    body = CaptureIngest.model_validate(capture())
    original = collection.data_analysis
    def interrupted(*args, **kwargs):
        raise RuntimeError("Simulated interruption after capture commit")
    monkeypatch.setattr(collection, "data_analysis", interrupted)
    with pytest.raises(RuntimeError):
        ingest(repo, body, StubDataRiskEngine(), StubNetworkRiskEngine())
    assert len(repo.list("session")) == 1
    assert repo.get("passive_assessment", body.id)["processing_state"] == "processing"
    monkeypatch.setattr(collection, "data_analysis", original)
    result = ingest(repo, body, StubDataRiskEngine(), StubNetworkRiskEngine())
    assert result["processing_state"] == "finished"
    assert len(repo.list("session")) == 1
