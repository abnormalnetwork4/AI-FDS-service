from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.prompt_engine import AllInOneDataRiskEngine, LABELS
from app.schemas import Finding, RiskResult
from test_live import observation


@pytest.fixture(scope="module")
def engine():
    return AllInOneDataRiskEngine(Path(__file__).resolve().parents[1] / "models" / "all-in-one")


def test_actual_model_event_api_persistence_and_dashboard(engine, tmp_path):
    path = tmp_path / "model.db"
    body = observation()
    text = "회사 업무용으로 회의 안건을 요약해줘. PRIVATE_MODEL_CANARY"
    body["prompt"] = {"text": text, "input_origin": "external_document"}
    with TestClient(create_app(path, data_engine=engine)) as client:
        reply = client.post("/api/v1/ingest/events", json=body)
        assert reply.status_code == 200
        result = reply.json()["results"][0]
        assert result["engine"] == "data" and result["status"] == "complete"
        assert result["engine_version"] == engine.model_version
        assert result["input_origin"] == "external_document"
        assert result["score"] is None
        assert [f["code"] for f in result["findings"]] == list(LABELS)
        assert all(f["score"] is None and f["detected"] == (f["probability"] > .5) for f in result["findings"])
        assert client.post("/api/v1/ingest/events", json=body).json() == reply.json()
        row = client.get("/api/v1/dashboard/events").json()["events"][0]
        assert row["score"] is None and row["status"] == "pending"
        assert [f["probability"] for f in row["prompt_reasons"]] == [f["probability"] for f in result["findings"]]
        assert all(f["status"] == "pending" for f in row["network_reasons"])
        summary = client.get("/api/v1/dashboard/summary").json()
        assert summary["scored_analysis_count"] == 0 and summary["average_risk_score"] is None
        explanation = client.get("/api/v1/dashboard/explanation?capture_id=live-1").json()["text"]
        assert "모델 예측 확률" in explanation and "위험도 점수 아님" in explanation
        assert "PRIVATE_MODEL_CANARY" not in str(row) + explanation
    assert b"PRIVATE_MODEL_CANARY" not in path.read_bytes()
    with TestClient(create_app(path, data_engine=engine)) as client:
        assert client.get("/api/v1/assessments/live-1").json()["results"][0] == result


def test_default_startup_loads_model_and_missing_model_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("PROMPT_ENGINE")
    with TestClient(create_app(tmp_path / "default.db")) as client:
        health = client.get("/health").json()
        assert health["data_engine"] == "AllInOneDataRiskEngine"
        assert health["prompt_model_version"].startswith("all-in-one-")
        assert client.post("/api/v1/data-risk/analyze", json={"user_id": "u", "text": "업무 회의록을 요약해줘"}).json()["status"] == "complete"
    monkeypatch.setenv("PROMPT_MODEL_DIR", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        with TestClient(create_app(tmp_path / "missing.db")):
            pass


def test_corrupt_artifact_rejected_before_loading(tmp_path):
    import json
    source = Path(__file__).resolve().parents[1] / "models" / "all-in-one"
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name in manifest["files"]:
        (tmp_path / name).write_bytes(b"broken")
    with pytest.raises(ValueError, match="checksum"):
        AllInOneDataRiskEngine(tmp_path)


def test_classification_without_fabricated_score_and_exact_threshold():
    finding = Finding(code="x", name="x", status="complete", detected=False, probability=.5, threshold=.5, reason="label definition")
    result = RiskResult(user_id="u", engine="data", engine_version="test", status="complete", findings=[finding])
    assert result.score is None and finding.score is None
    for update in ({"detected": True}, {"probability": float("nan")}, {"threshold": None}, {"status": "pending"}):
        with pytest.raises(ValidationError):
            Finding.model_validate(finding.model_dump() | update)
    with pytest.raises(ValidationError):
        RiskResult(user_id="u", engine="data", engine_version="test", status="complete", findings=[])


def test_standalone_model_failure_is_error_without_prompt_leak(tmp_path):
    class Broken:
        def analyze(self, request):
            raise RuntimeError(request.text)
    with TestClient(create_app(tmp_path / "failed.db", data_engine=Broken())) as client:
        result = client.post("/api/v1/data-risk/analyze", json={"user_id": "u", "text": "PRIVATE_EXCEPTION"})
        assert result.json()["status"] == "error"
        assert "PRIVATE_EXCEPTION" not in result.text
