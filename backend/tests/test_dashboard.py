from fastapi.testclient import TestClient

from app.main import create_app
from app.engines import StubDataRiskEngine
from test_passive import capture


def test_dashboard_empty_and_unknown(tmp_path):
    with TestClient(create_app(tmp_path / "fds.db")) as client:
        assert client.get("/api/v1/dashboard/events").json() == {"events": [], "total": 0, "limit": 50, "offset": 0}
        assert client.get("/api/v1/dashboard/explanation", params={"capture_id": "missing"}).status_code == 404
        assert client.get("/api/v1/dashboard/events?limit=201").status_code == 422


def test_dashboard_pending_and_window_identity(tmp_path):
    with TestClient(create_app(tmp_path / "fds.db")) as client:
        client.post("/api/v1/ingest/captures", json=capture(text="PRIVATE_PROMPT_CANARY"))
        response = client.get("/api/v1/dashboard/events")
        row = response.json()["events"][0]
        assert row["score"] is None and row["confidence"] is None
        assert row["status"] == "pending"
        assert row["session_id"] == "session-cap-1"
        assert row["started_at"] == row["ended_at"]
        assert len(row["network_reasons"]) == 12
        assert len({r["id"] for r in row["network_reasons"]}) == 12
        assert {r["window_minutes"] for r in row["network_reasons"]} == {5, 60}
        assert all(r["score"] is None for r in row["prompt_reasons"])
        summary = client.get("/api/v1/dashboard/explanation", params={"capture_id": "cap-1"}).json()
        assert summary["source"] == "stored"
        assert "미판정" in summary["text"]
        assert "PRIVATE_PROMPT_CANARY" not in response.text + summary["text"]


def test_engine_scores_do_not_become_fused_score_or_confidence(tmp_path):
    class Complete(StubDataRiskEngine):
        def analyze(self, request):
            result = super().analyze(request)
            return result.model_copy(update={"status": "complete", "score": 85,
                "findings": [f.model_copy(update={"status": "complete", "score": 85}) for f in result.findings]})

    with TestClient(create_app(tmp_path / "fds.db", data_engine=Complete())) as client:
        client.post("/api/v1/ingest/captures", json=capture(text="text"))
        row = client.get("/api/v1/dashboard/events").json()["events"][0]
        assert row["score"] is None and row["confidence"] is None
        assert row["status"] == "pending"
        assert all(r["status"] == "complete" and r["score"] == 85 for r in row["prompt_reasons"])


def test_error_is_visible_without_leaking_exception(tmp_path):
    class Broken:
        def analyze(self, request):
            raise RuntimeError("PRIVATE_EXCEPTION")

    with TestClient(create_app(tmp_path / "fds.db", data_engine=Broken())) as client:
        client.post("/api/v1/ingest/captures", json=capture(text="text"))
        response = client.get("/api/v1/dashboard/events")
        assert response.json()["events"][0]["status"] == "error"
        assert "error" in {r["status"] for r in response.json()["events"][0]["prompt_reasons"]}
        assert "PRIVATE_EXCEPTION" not in response.text


def test_pagination_and_user_filter(tmp_path):
    with TestClient(create_app(tmp_path / "fds.db")) as client:
        for i in range(3):
            client.post("/api/v1/ingest/captures", json=capture(f"cap-{i}"))
        first = client.get("/api/v1/dashboard/events?limit=2").json()
        second = client.get("/api/v1/dashboard/events?limit=2&offset=2").json()
        assert first["total"] == second["total"] == 3
        assert [r["id"] for r in first["events"] + second["events"]] == ["cap-2", "cap-1", "cap-0"]
        assert client.get("/api/v1/dashboard/events?user_id=unknown").json()["total"] == 0
