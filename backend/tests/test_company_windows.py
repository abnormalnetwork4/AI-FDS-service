from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

from fastapi.testclient import TestClient

from app.collection import ingest
from app.company import company_slot, refresh_dirty
from app.contracts import EventIngest
from app.main import create_app
from app.repository import Repository
from app.schemas import Finding, RiskResult


class Data:
    def analyze(self, request):
        if request.text == "error":
            raise RuntimeError("private error")
        score = float(request.text)
        return RiskResult(user_id=request.user_id, engine="data", engine_version="test", status="complete",
                          score=score, score_max=60, findings=[Finding(code="prompt", name="prompt", status="complete", score=score, reason="test")])


class Network:
    def __init__(self):
        self.windows = []

    def analyze(self, window):
        self.windows.append(window)
        return RiskResult(user_id=window.user_id, engine="network", engine_version="test", status="complete",
                          score=70, findings=[Finding(code="network_score", name="network", status="complete", score=70, reason="test")])


def body(name, score="20", user="a", at="2026-10-04T10:01:00+09:00"):
    return EventIngest(id=name, session_id="same-session-id", user_id=user, device_id="pc", occurred_at=at,
                      destination="ai.internal", provider="internal", bytes_sent=100,
                      prompt={"text": score} if score is not None else None).as_capture()


def repository(tmp_path):
    repo = Repository(tmp_path / "company.db")
    repo.initialize()
    return repo


def test_all_users_max_not_sum_and_boundary(tmp_path):
    repo, net = repository(tmp_path), Network()
    first = ingest(repo, body("a", "5"), Data(), net)
    ingest(repo, body("b", "50", "b"), Data(), net)
    ingest(repo, body("c", "20", "c", "2026-10-04T10:04:59.999999+09:00"), Data(), net)
    group = repo.get("company_assessment", first["company_window_id"])
    assert group["score"] == 78 and group["final_grade"] == "danger"
    assert group["prompt_max_score"] == 50 and group["prompt_source_capture_id"] == "b"
    assert group["capture_count"] == 3 and group["network_contribution"] == 28
    assert net.windows[-1].features.request_count == 3
    assert net.windows[-1].features.bytes_sent == 300
    assert net.windows[-1].features.session_count == 3  # same session ID across users must not collapse
    assert net.windows[-1].scope == "company"
    assert first["score"] is None and first["scoring_scope"] == "prompt_only"
    following = ingest(repo, body("next", "0", "d", "2026-10-04T10:05:00+09:00"), Data(), net)
    assert following["company_window_id"] != first["company_window_id"]
    assert net.windows[-1].features.request_count == 1
    assert net.windows[-1].model_features.user_request_count_observed_1h == 4
    assert repo.get("company_assessment", first["company_window_id"])["score"] == 78
    # exact replay neither counts twice nor re-runs network
    before = len(net.windows)
    ingest(repo, body("a", "5"), Data(), net)
    assert len(net.windows) == before


def test_missing_error_and_valid_zero_are_distinct(tmp_path):
    repo, net = repository(tmp_path), Network()
    event = ingest(repo, body("zero", "0"), Data(), net)
    assert repo.get("company_assessment", event["company_window_id"])["score"] == 28
    ingest(repo, body("missing", None, "b"), Data(), net)
    group = repo.get("company_assessment", event["company_window_id"])
    assert group["score"] is None and group["final_grade"] is None
    assert group["prompt_missing_count"] == 1 and group["prompt_max_score"] == 0
    ingest(repo, body("failed", "error", "c"), Data(), net)
    group = repo.get("company_assessment", event["company_window_id"])
    assert group["fusion_status"] == "error" and group["prompt_error_count"] == 1
    assert group["score"] is None


def test_late_arrival_recomputes_own_and_following_history(tmp_path):
    repo, net = repository(tmp_path), Network()
    later = ingest(repo, body("later", "5", at="2026-10-04T10:05:00+09:00"), Data(), net)
    old = ingest(repo, body("late", "50", "b"), Data(), net)
    assert repo.get("company_assessment", later["company_window_id"])["score"] is None
    refresh_dirty(repo, net)
    later_group = repo.get("company_assessment", later["company_window_id"])
    assert later_group["score"] == 33
    assert net.windows[-1].model_features.user_request_count_observed_1h == 2
    assert repo.get("company_assessment", old["company_window_id"])["score"] == 78


def test_slow_old_network_cannot_overwrite_new_snapshot(tmp_path):
    repo = repository(tmp_path)
    entered, release = Event(), Event()
    class Slow(Network):
        def analyze(self, window):
            if window.features.request_count == 1:
                entered.set()
                assert release.wait(5)
            return super().analyze(window)
    net = Slow()
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(ingest, repo, body("first", "5"), Data(), net)
        try:
            assert entered.wait(5)
            second = ingest(repo, body("second", "50", "b"), Data(), net)
        finally:
            release.set()
        job.result()
    group = repo.get("company_assessment", second["company_window_id"])
    assert group["score"] == 78 and group["network_revision"] == group["revision"] == 2
    network = next(r for r in group["results"] if r["engine"] == "network")
    assert repo.get("window", network["window_id"])["features"]["request_count"] == 2


def test_slow_prompt_prevents_premature_max_and_updates_sse_revision(tmp_path):
    repo, net = repository(tmp_path), Network()
    entered, release = Event(), Event()
    class Slow(Data):
        def analyze(self, request):
            entered.set()
            assert release.wait(5)
            return super().analyze(request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(ingest, repo, body("slow", "50"), Slow(), net)
        try:
            assert entered.wait(5)
            event = ingest(repo, body("fast", "5", "b"), Data(), net)
            group = repo.get("company_assessment", event["company_window_id"])
            assert group["score"] is None and group["prompt_missing_count"] == 1
            revision = repo.revision()
        finally:
            release.set()
        job.result()
    assert repo.get("company_assessment", event["company_window_id"])["score"] == 78
    assert repo.revision() > revision


def test_closed_window_and_api_restart_pagination(tmp_path, monkeypatch):
    repo, net = repository(tmp_path), Network()
    at = datetime(2026, 10, 4, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("app.company.now", lambda: at)
    response = ingest(repo, body("one", "50"), Data(), net)
    group_id = response["company_window_id"]
    assert repo.get("company_assessment", group_id)["phase"] == "open"
    monkeypatch.setattr("app.company_repository.now", lambda: at + timedelta(minutes=4))
    revision = repo.revision()
    repo.close_company_windows()
    assert repo.get("company_assessment", group_id)["phase"] == "closed"
    assert repo.revision() > revision
    with TestClient(create_app(repo.path, data_engine=Data(), network_engine=net)) as client:
        row = client.get("/api/v1/dashboard/company-windows?limit=1").json()["events"][0]
        assert row["scope"] == "company" and row["score"] == 78
        assert row["prompt_source_capture_id"] == "one" and row["network_reasons"]
        assert client.get("/api/v1/company-windows/" + group_id).json()["capture_count"] == 1
        assert client.get("/api/v1/dashboard/company-windows?offset=1").json()["events"] == []
        assert "최고" in client.get("/api/v1/dashboard/explanation", params={"window_id": group_id}).json()["text"]
        summary = client.get("/api/v1/dashboard/summary").json()
        assert summary["graded_event_count"] == 0 and summary["graded_window_count"] == 1
        assert summary["average_risk_score"] == 78


def test_equivalent_timezone_slot():
    assert company_slot(datetime.fromisoformat("2026-10-04T10:01:00+09:00")).id == company_slot(datetime.fromisoformat("2026-10-04T01:04:59+00:00")).id


def test_migrate_legacy_preserves_prompt_evidence_and_clears_personal_grade(tmp_path):
    from app.contracts import Assessment, CaptureRecord
    repo, net = repository(tmp_path), Network()
    observed = body("legacy", "50")
    data = Data().analyze(type("Input", (), {"text": "50", "user_id": "a"})())
    repo.save("session", observed.session)
    repo.save("event", observed.ai_event)
    repo.save("risk", data)
    repo.save("capture", CaptureRecord(id=observed.id, user_id="a", device_id="pc", session_id=observed.session.id,
                                       ai_event_id=observed.ai_event.id, source="application_log", prompt_status="available"))
    repo.save("passive_assessment", Assessment(id=observed.id, user_id="a", session_id=observed.session.id,
        processing_state="finished", status="complete", fusion_status="complete", final_grade="danger", score=90, results=[data]))
    repo.migrate_company_windows()
    refresh_dirty(repo, net)
    individual = repo.get("passive_assessment", "legacy")
    group = repo.get("company_assessment", individual["company_window_id"])
    assert individual["score"] is None and individual["final_grade"] is None
    assert individual["results"][0]["id"] == data.id and repo.get("risk", data.id) is not None
    assert group["score"] == 78 and group["prompt_source_capture_id"] == "legacy"
    revision = repo.revision()
    repo.migrate_company_windows()
    refresh_dirty(repo, net)
    assert repo.revision() == revision and len(repo.list("company_assessment")) == 1


def test_session_spanning_boundary_uses_same_time_for_prompt_and_network(tmp_path):
    repo, net = repository(tmp_path), Network()
    observed = body("spanning", "20", at="2026-10-04T10:05:00+09:00")
    observed.session.started_at -= timedelta(minutes=1)
    observed.session.ended_at += timedelta(minutes=1)
    event = ingest(repo, observed, Data(), net)
    group = repo.get("company_assessment", event["company_window_id"])
    assert group["start"].startswith("2026-10-04T01:05:00")
    assert net.windows[-1].features.bytes_sent == 100 and net.windows[-1].features.request_count == 1
    assert group["score"] == 48


def test_network_error_keeps_prompt_evidence_without_fused_score(tmp_path):
    repo = repository(tmp_path)
    class Broken:
        def analyze(self, window):
            raise RuntimeError("private network failure")
    event = ingest(repo, body("failure", "50"), Data(), Broken())
    group = repo.get("company_assessment", event["company_window_id"])
    assert group["score"] is None and group["fusion_status"] == "error"
    assert group["prompt_max_score"] == 50 and group["prompt_source_capture_id"] == "failure"
    assert group["override"] is False and group["network_score"] is None
