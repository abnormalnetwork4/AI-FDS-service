import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.prompt_engine import AllInOneDataRiskEngine, RegressionDataRiskEngine, LABELS, configured_data_engine
from app.schemas import DataRiskRequest, Finding, RiskResult
from test_live import observation


MODEL_ROOT = Path(__file__).resolve().parents[2] / "model" / "prompt"
ENGINE_TYPES = {"all-in-one": AllInOneDataRiskEngine, "regression": RegressionDataRiskEngine}


@pytest.fixture(scope="module", params=list(ENGINE_TYPES))
def engine(request):
    return ENGINE_TYPES[request.param](MODEL_ROOT / request.param)


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
        assert 0 <= result["score"] <= 60
        assert result["score_max"] == 60
        assert [f["code"] for f in result["findings"]] == list(LABELS)
        assert all(f["score"] is None and f["threshold"] == engine.thresholds[f["code"]]
                   and f["detected"] == (f["probability"] > f["threshold"]) for f in result["findings"])
        assert client.post("/api/v1/ingest/events", json=body).json() == reply.json()
        row = client.get("/api/v1/dashboard/events").json()["events"][0]
        assert row["score"] is None and row["status"] == "pending"
        assert [f["probability"] for f in row["prompt_reasons"]] == [f["probability"] for f in result["findings"]]
        assert all(f["status"] == "pending" for f in row["network_reasons"])
        summary = client.get("/api/v1/dashboard/summary").json()
        assert summary["scored_analysis_count"] == 1 and summary["average_risk_score"] is None
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
        assert health["data_engine"] == "RegressionDataRiskEngine"
        assert health["prompt_model_version"].startswith("regression-")
        assert client.post("/api/v1/data-risk/analyze", json={"user_id": "u", "text": "업무 회의록을 요약해줘"}).json()["status"] == "complete"
    monkeypatch.setenv("PROMPT_MODEL_DIR", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        with TestClient(create_app(tmp_path / "missing.db")):
            pass


@pytest.mark.parametrize("mode", ENGINE_TYPES)
def test_corrupt_artifact_rejected_before_loading(tmp_path, mode):
    source = MODEL_ROOT / mode
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name in manifest["files"]:
        (tmp_path / name).write_bytes(b"broken")
    with pytest.raises(ValueError, match="checksum"):
        ENGINE_TYPES[mode](tmp_path)


@pytest.fixture(scope="module")
def regression():
    return RegressionDataRiskEngine(MODEL_ROOT / "regression")


def test_regression_matches_supplied_notebook_predictions(regression):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "regression_reference.json").read_text(encoding="utf-8"))
    assert regression.thresholds == dict.fromkeys(LABELS, .45)
    for case in fixture["cases"]:
        result = regression.analyze(DataRiskRequest(user_id="reference", text=case["text"]))
        assert 0 <= result.score <= 60
        assert (not any(f.detected for f in result.findings)) == case["normal"]
        for finding, reference in zip(result.findings, case["predictions"], strict=True):
            assert finding.code == reference["label"]
            assert finding.detected == reference["detected"]
            # 원본 체크포인트는 없으며 제공 JSON과 재학습 확률은 최대 약 1.29%p 차이가 납니다.
            # 여기서는 원본 탐지 판정을 비교하고, 같은 모델의 확률 일치는 아래에서 별도로 검사합니다.


def test_regression_matches_original_inference_with_same_checkpoint(regression):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "regression_retrained_reference.json").read_text(encoding="utf-8"))
    assert fixture["model_version"] == regression.model_version
    for case in fixture["cases"]:
        result = regression.analyze(DataRiskRequest(user_id="same-checkpoint", text=case["text"]))
        for finding, reference in zip(result.findings, case["predictions"], strict=True):
            assert finding.code == reference["label"]
            assert finding.detected == reference["detected"]
            assert finding.probability == pytest.approx(reference["probability"], abs=1e-12, rel=0)


def test_new_default_with_real_network_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("PROMPT_ENGINE")
    monkeypatch.delenv("NETWORK_ENGINE")
    with TestClient(create_app(tmp_path / "both-models.db")) as client:
        health = client.get("/health").json()
        assert health["data_engine"] == "RegressionDataRiskEngine"
        assert health["network_engine"] == "XGBoostNetworkRiskEngine"
        body = observation()
        body["prompt"] = {"text": "업무 회의록을 요약해줘."}
        response = client.post("/api/v1/ingest/events", json=body)
        assert response.status_code == 200
        results = response.json()["results"]
        assert next(r for r in results if r["engine"] == "data")["status"] == "complete"
        group = client.get("/api/v1/risk-windows/" + response.json()["risk_window_id"]).json()
        assert any(r["status"] == "complete" and r["score"] is not None for r in group["results"] if r["engine"] == "network")
        row = client.get("/api/v1/dashboard/risk-windows").json()["events"][0]
        assert all(f["threshold"] == .45 for f in row["prompt_reasons"])
        assert any(f["status"] == "complete" for f in row["network_reasons"])
        assert row["score"] is not None and row["confidence"] is None
        assert row["grade"] in {"normal", "caution", "warning", "danger"}


def test_regression_exact_cutoff_and_invalid_probability(regression, monkeypatch):
    import numpy as np
    class Fixed:
        def __init__(self, probability):
            self.probability = probability
        def predict_proba(self, features):
            return np.array([[1 - self.probability, self.probability]])
    probabilities = [.45, .45000001, .44999999, .9]
    monkeypatch.setattr(regression, "classifiers", dict(zip(LABELS, map(Fixed, probabilities))))
    request = DataRiskRequest(user_id="boundary", text="업무 회의록")
    result = regression.analyze(request)
    assert [f.detected for f in result.findings] == [False, True, False, True]
    for invalid in [float("nan"), float("inf"), -0.1, 1.1]:
        monkeypatch.setitem(regression.classifiers, LABELS[0], Fixed(invalid))
        with pytest.raises(ValidationError):
            regression.analyze(request)


def test_regression_normalization_matches_notebook(regression):
    normalized = regression.analyze(DataRiskRequest(user_id="normalization", text="AI 업무\n회의록"))
    raw = regression.analyze(DataRiskRequest(user_id="normalization", text="  ＡＩ 업무\r\n회의록 \r"))
    assert [f.probability for f in raw.findings] == [f.probability for f in normalized.findings]


def test_explicit_legacy_mode_remains_available(monkeypatch):
    monkeypatch.setenv("PROMPT_ENGINE", "all-in-one")
    assert type(configured_data_engine()) is AllInOneDataRiskEngine


def test_regression_rejects_legacy_artifacts_and_version_mismatch(monkeypatch):
    with pytest.raises(ValueError, match="format"):
        RegressionDataRiskEngine(MODEL_ROOT / "all-in-one")
    monkeypatch.setattr("app.prompt_engine.version", lambda package: "incompatible")
    with pytest.raises(ValueError, match="versions"):
        RegressionDataRiskEngine(MODEL_ROOT / "regression")


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
