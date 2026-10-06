from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.network_model import DEFAULT_MODEL_DIR, XGBoostNetworkRiskEngine
from app.schemas import AIUsageEvent, BehaviorFeatures, BehaviorWindow, NetworkModelFeatures, NetworkSession, WindowRequest
from app.services import compute_window

ARTIFACTS = DEFAULT_MODEL_DIR / "artifacts"
KST = timezone(timedelta(hours=9))


@pytest.fixture(scope="module")
def engine():
    return XGBoostNetworkRiskEngine()


def window_from(row, minutes=5):
    start = datetime.fromisoformat(row["window_start"])
    features = {k: (None if pd.isna(v) else float(v)) for k, v in row.items() if k in NetworkModelFeatures.model_fields}
    return BehaviorWindow(id=row["window_id"], user_id="u", device_id="d", start=start,
                          end=start + timedelta(minutes=minutes), duration_minutes=minutes,
                          features=BehaviorFeatures(session_count=0, request_count=0, bytes_sent=0, bytes_received=0,
                                                    distinct_destinations=0, blocked_connections=0, direct_connections=0,
                                                    unapproved_ai_requests=0, blocked_ai_requests=0, file_count=0),
                          model_features=NetworkModelFeatures(**features))


def test_adapter_matches_notebook_scores_on_test_split(engine):
    # 노트북이 저장한 Test 1,080개 창의 점수와 어댑터 경유 점수가 같아야 합니다.
    features = pd.read_csv(ARTIFACTS / "test_features.csv")
    expected = pd.read_csv(ARTIFACTS / "test_network_scores.csv", encoding="utf-8-sig").set_index("window_id")
    for _, row in features.iterrows():
        result = engine.analyze(window_from(row))
        reference = expected.loc[row["window_id"]]
        assert result.status == "complete"
        assert result.score == pytest.approx(reference.network_score)
        assert f"[{reference.network_grade}]" in result.findings[0].name
        assert f"] {reference.threat} " in result.findings[0].name


def test_finding_layout_and_unscorable_windows(engine):
    row = pd.read_csv(ARTIFACTS / "test_features.csv").iloc[0]
    result = engine.analyze(window_from(row))
    assert [f.code for f in result.findings] == ["network_score", "N1", "N2", "N3", "N4", "N5", "N6"]
    assert all(f.status == "complete" for f in result.findings)
    assert result.engine_version.startswith("xgb-network-")
    assert engine.analyze(window_from(row, minutes=60)).status == "pending"
    empty = row.copy()
    empty["request_count"] = 0
    assert engine.analyze(window_from(empty)).findings[0].code == "network_model"


def test_window_features_follow_training_definitions():
    t0 = datetime(2026, 10, 6, 20, 0, tzinfo=KST)  # 업무시간 외

    def pair(i, at, done, provider, destination, tenant, process, retry=None, body=1000):
        s = NetworkSession(id=f"s{i}", user_id="u", device_id="d", started_at=at, ended_at=done,
                           destination=destination, bytes_sent=body + 500, bytes_received=200,
                           packets_sent=10, packets_received=5)
        e = AIUsageEvent(id=f"e{i}", session_id=f"s{i}", user_id="u", device_id="d", occurred_at=at,
                         completed_at=done, provider=provider, channel="api", request_bytes=body, file_count=1,
                         tenant=tenant, process_name=process, retry_after_block=retry)
        return s, e

    pairs = [pair(0, t0 - timedelta(minutes=50), t0 - timedelta(minutes=50), "alpha", "a.ai", "company", "py"),
             pair(1, t0, t0 + timedelta(seconds=5), "alpha", "a.ai", "company", "py"),
             pair(2, t0 + timedelta(seconds=2), t0 + timedelta(seconds=3), "beta", "b.ai", "personal", "py"),
             pair(3, t0 + timedelta(seconds=10), t0 + timedelta(seconds=11), "alpha", "a.ai", "company", "retry", True, 4000)]
    sessions, events = zip(*pairs)
    window = compute_window(WindowRequest(user_id="u", device_id="d", start=t0, duration_minutes=5),
                            list(sessions), list(events))
    f = window.model_features
    assert (f.request_count, f.session_count, f.provider_count) == (3, 3, 2)
    assert (f.request_body_bytes, f.max_request_bytes, f.file_count) == (6000, 4000, 3)
    assert (f.upload_bytes, f.download_bytes, f.upload_packets) == (7500, 600, 30)
    assert f.iat_mean_s == pytest.approx(5.0) and f.iat_std_s == pytest.approx(3.0) and f.iat_cv == pytest.approx(0.6)
    assert f.peak_concurrency == 2
    assert (f.destination_switch_count, f.tenant_switch_count, f.declared_process_switch_count) == (2, 2, 1)
    assert f.request_rate_per_min == pytest.approx(0.6)
    assert f.upload_download_ratio == pytest.approx(12.5)
    assert f.off_hours_fraction == 1.0
    # 1시간 누적은 구간 밖의 50분 전 요청까지 포함하고, 이력은 첫 관측부터 55분입니다.
    assert (f.user_upload_bytes_observed_1h, f.user_request_count_observed_1h) == (9000, 4)
    assert (f.history_coverage_seconds_1h, f.history_complete_1h) == (3300, 0)
    assert f.retry_count_after_block == 1


def test_ingested_events_are_scored_end_to_end(tmp_path, engine):
    with TestClient(create_app(tmp_path / "fds.db", network_engine=engine)) as client:
        base = datetime(2026, 10, 6, 10, 0, tzinfo=KST)
        for i in range(3):
            body = dict(id=f"ev-{i}", session_id="s", user_id="u1", device_id="pc", destination="alpha.ai.test",
                        occurred_at=(base + timedelta(seconds=40 * i)).isoformat(), provider="alpha",
                        bytes_sent=60000, bytes_received=11000, packets_sent=45, packets_received=45,
                        request_bytes=55000, file_count=1, tenant="company", process_name="python_client",
                        completed_at=(base + timedelta(seconds=40 * i + 1)).isoformat())
            response = client.post("/api/v1/ingest/events", json=body)
            assert response.status_code == 200
            assert client.post("/api/v1/ingest/events", json=body).json() == response.json()  # 재전송
        results = {r["window_id"]: r for r in response.json()["results"] if r["engine"] == "network"}
        windows = {wid: client.app.state.repository.get("window", wid) for wid in results}
        by_minutes = {w["duration_minutes"]: results[wid] for wid, w in windows.items()}
        assert by_minutes[5]["status"] == "complete" and 0 <= by_minutes[5]["score"] <= 100
        assert by_minutes[60]["status"] == "pending"
        assert windows[by_minutes[5]["window_id"]]["model_features"]["request_count"] == 3
        # 프롬프트·AI 로그 없는 패킷 메타데이터만으로는 미판정입니다.
        metadata_only = client.post("/api/v1/ingest/events", json=dict(
            id="meta-1", session_id="m", user_id="u2", device_id="pc2", destination="x.ai",
            occurred_at=base.isoformat(), source="collector", bytes_sent=10)).json()
        assert {r["status"] for r in metadata_only["results"] if r["engine"] == "network"} == {"pending"}
        assert client.post("/api/v1/ingest/events", json=dict(
            id="bad", session_id="m", user_id="u2", device_id="pc2", destination="x.ai",
            occurred_at=base.isoformat(), source="collector", tenant="company")).status_code == 422
