"""사용자·단말별 고정 5분 위험 구간(RiskWindow) 집계·통합 검증."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

from fastapi.testclient import TestClient

from app.collection import ingest
from app.contracts import EventIngest
from app.main import create_app
from app.repository import Repository
from app.schemas import Finding, RiskResult
from app.windows import refresh_dirty, window_slot


class Data:
    def analyze(self, request):
        if request.text == "error":
            raise RuntimeError("private error")
        score = float(request.text)
        return RiskResult(user_id=request.user_id, engine="data", engine_version="test", status="complete",
                          score=score, score_max=60, findings=[Finding(code="prompt", name="prompt", status="complete", score=score, reason="test")])


class Network:
    def __init__(self, score=70):
        self.windows, self.score = [], score

    def analyze(self, window):
        self.windows.append(window)
        score = self.score(window) if callable(self.score) else self.score
        return RiskResult(user_id=window.user_id, engine="network", engine_version="test", status="complete",
                          score=score, findings=[Finding(code="network_score", name="network", status="complete", score=score, reason="test")])


def body(name, score="20", user="a", at="2026-10-04T10:01:00+09:00", device="pc", sent=100):
    return EventIngest(id=name, session_id="s-" + name, user_id=user, device_id=device, occurred_at=at,
                      destination="ai.internal", provider="internal", bytes_sent=sent,
                      prompt={"text": score} if score is not None else None).as_capture()


def repository(tmp_path):
    repo = Repository(tmp_path / "windows.db")
    repo.initialize()
    return repo


def window(repo, response):
    return repo.get("risk_window", response["risk_window_id"])


def test_same_user_max_prompt_not_sum_and_boundary(tmp_path):
    repo, net = repository(tmp_path), Network()
    first = ingest(repo, body("a1", "5"), Data(), net)
    ingest(repo, body("a2", "50"), Data(), net)
    ingest(repo, body("a3", "20", at="2026-10-04T10:04:59.999999+09:00"), Data(), net)
    group = window(repo, first)
    assert group["score"] == 78 and group["final_grade"] == "danger"  # 50 + 70×0.4, 합계 75·평균 25 아님
    assert group["prompt_max_score"] == 50 and group["prompt_source_capture_id"] == "a2"
    assert group["user_id"] == "a" and group["device_id"] == "pc" and group["scope"] == "user_device"
    assert group["capture_count"] == 3 and group["network_contribution"] == 28
    assert net.windows[-1].scope == "user_device" and net.windows[-1].user_id == "a"
    assert net.windows[-1].features.request_count == 3 and net.windows[-1].features.bytes_sent == 300
    assert first["score"] is None and first["scoring_scope"] == "prompt_only" and first["company_window_id"] is None
    following = ingest(repo, body("a4", "0", at="2026-10-04T10:05:00+09:00"), Data(), net)
    assert following["risk_window_id"] != first["risk_window_id"]
    assert net.windows[-1].features.request_count == 1
    assert net.windows[-1].model_features.user_request_count_observed_1h == 4
    assert window(repo, first)["score"] == 78
    before = len(net.windows)
    ingest(repo, body("a1", "5"), Data(), net)  # 같은 관측 재전송: 중복 집계·재분석 없음
    assert len(net.windows) == before


def test_other_users_and_devices_do_not_mix(tmp_path):
    repo, net = repository(tmp_path), Network()
    a = ingest(repo, body("a1", "50", "alice", sent=900_000), Data(), net)
    b = ingest(repo, body("b1", "0", "bob", sent=100), Data(), net)
    a_laptop = ingest(repo, body("a2", "0", "alice", device="laptop"), Data(), net)
    assert len({a["risk_window_id"], b["risk_window_id"], a_laptop["risk_window_id"]}) == 3
    bob = window(repo, b)
    # bob 구간에는 alice의 프롬프트·전송량이 섞이지 않습니다.
    assert bob["prompt_max_score"] == 0 and bob["capture_count"] == 1 and bob["score"] == 28
    bob_features = next(w for w in net.windows if w.user_id == "bob")
    assert bob_features.features.bytes_sent == 100 and bob_features.model_features.user_upload_bytes_observed_1h == 100
    # 같은 사용자의 다른 단말: 5분 피처는 단말별, 최근 1시간 이력은 사용자 기준(학습 정의와 같음)
    laptop = net.windows[-1]
    assert laptop.device_id == "laptop" and laptop.features.request_count == 1
    assert laptop.model_features.user_request_count_observed_1h == 2
    # 노트북 기록이 alice의 1시간 이력을 바꾸므로 같은 시간대 PC 구간은 재계산 대상이 됩니다.
    assert window(repo, a)["score"] is None
    refresh_dirty(repo, net)
    assert window(repo, a)["score"] == 78 and window(repo, b)["score"] == 28


def test_missing_error_and_valid_zero_are_distinct(tmp_path):
    repo, net = repository(tmp_path), Network()
    event = ingest(repo, body("zero", "0"), Data(), net)
    assert window(repo, event)["score"] == 28
    ingest(repo, body("missing", None), Data(), net)
    group = window(repo, event)
    assert group["score"] is None and group["final_grade"] is None
    assert group["prompt_missing_count"] == 1 and group["prompt_max_score"] == 0
    ingest(repo, body("failed", "error"), Data(), net)
    group = window(repo, event)
    assert group["fusion_status"] == "error" and group["prompt_error_count"] == 1
    assert group["score"] is None


def test_late_arrival_recomputes_own_and_following_history_of_same_user(tmp_path):
    repo, net = repository(tmp_path), Network()
    later = ingest(repo, body("later", "5", at="2026-10-04T10:05:00+09:00"), Data(), net)
    other = ingest(repo, body("other", "5", "z", at="2026-10-04T10:05:00+09:00"), Data(), net)
    old = ingest(repo, body("late", "50"), Data(), net)
    assert window(repo, later)["score"] is None          # 같은 사용자 이후 구간은 재계산 대기
    assert window(repo, other)["score"] == 33            # 다른 사용자 구간은 영향 없음
    refresh_dirty(repo, net)
    assert window(repo, later)["score"] == 33
    assert net.windows[-1].model_features.user_request_count_observed_1h == 2
    assert window(repo, old)["score"] == 78


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
            second = ingest(repo, body("second", "50"), Data(), net)
        finally:
            release.set()
        job.result()
    group = window(repo, second)
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
            event = ingest(repo, body("fast", "5"), Data(), net)
            group = window(repo, event)
            assert group["score"] is None and group["prompt_missing_count"] == 1
            revision = repo.revision()
        finally:
            release.set()
        job.result()
    assert window(repo, event)["score"] == 78
    assert repo.revision() > revision


def test_closed_window_api_and_company_slot_summary(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    net = Network(lambda w: 70 if w.user_id == "a" else 0)
    at = datetime(2026, 10, 4, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("app.windows.now", lambda: at)
    response = ingest(repo, body("one", "50"), Data(), net)
    ingest(repo, body("two", "10", "b"), Data(), net)
    ingest(repo, body("three", None, "c"), Data(), net)
    group_id = response["risk_window_id"]
    assert repo.get("risk_window", group_id)["phase"] == "open"
    monkeypatch.setattr("app.window_repository.now", lambda: at + timedelta(minutes=4))
    revision = repo.revision()
    repo.close_risk_windows()
    assert repo.get("risk_window", group_id)["phase"] == "closed"
    assert repo.revision() > revision
    with TestClient(create_app(repo.path, data_engine=Data(), network_engine=net)) as client:
        rows = client.get("/api/v1/dashboard/risk-windows").json()["events"]
        assert {r["user"] for r in rows} == {"a", "b", "c"} and all(r["scope"] == "user_device" for r in rows)
        top = next(r for r in rows if r["id"] == group_id)
        assert top["score"] == 78 and top["prompt_source_capture_id"] == "one" and top["network_reasons"]
        same_slot = client.get("/api/v1/dashboard/risk-windows", params={"start": top["window_start"]}).json()
        assert same_slot["total"] == 3
        assert client.get("/api/v1/dashboard/risk-windows", params={"user_id": "b"}).json()["total"] == 1
        assert client.get("/api/v1/risk-windows/" + group_id).json()["capture_count"] == 1
        slot = client.get("/api/v1/dashboard/company-slots").json()["slots"][0]
        # 회사 요약은 합산·평균이 아니라 사용자 구간 등급을 세고 최고 등급 구간을 가리킵니다.
        assert slot["window_count"] == 3 and slot["user_count"] == 3
        assert slot["graded_window_count"] == 2 and slot["pending_window_count"] == 1
        assert slot["grade_counts"] == {"normal": 1, "caution": 0, "warning": 0, "danger": 1}
        assert slot["top_window_id"] == group_id and slot["top_user_id"] == "a" and slot["top_score"] == 78
        assert slot["window_ids"][0] == group_id
        text = client.get("/api/v1/dashboard/explanation", params={"window_id": group_id}).json()["text"]
        assert "사용자 a · 단말 pc" in text and "최고" in text
        summary = client.get("/api/v1/dashboard/summary").json()
        assert summary["graded_event_count"] == 0 and summary["risk_window_count"] == 3
        assert summary["graded_window_count"] == 2 and summary["average_risk_score"] == (78 + 10) / 2


def test_equivalent_timezone_slot_and_distinct_users():
    kst = datetime.fromisoformat("2026-10-04T10:01:00+09:00")
    utc = datetime.fromisoformat("2026-10-04T01:04:59+00:00")
    assert window_slot("a", "pc", kst).id == window_slot("a", "pc", utc).id
    assert window_slot("a", "pc", kst).id != window_slot("b", "pc", kst).id != window_slot("a", "laptop", kst).id
    # 구분자가 들어간 ID도 충돌하지 않습니다.
    assert window_slot("a:b", "c", kst).id != window_slot("a", "b:c", kst).id


def test_migrate_legacy_preserves_prompt_evidence_and_company_records(tmp_path):
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
        processing_state="finished", status="complete", fusion_status="complete", final_grade="danger", score=90,
        company_window_id="company-20261004T010000Z", results=[data]))
    with repo.connection() as conn:  # 이전 버전 회사 구간 기록
        conn.execute("INSERT INTO records VALUES ('company_assessment', 'company-20261004T010000Z', 'company', ?)",
                     ('{"id": "company-20261004T010000Z", "start": "2026-10-04T01:00:00Z", "end": "2026-10-04T01:05:00Z", "scope": "company"}',))
    repo.migrate_risk_windows()
    refresh_dirty(repo, net)
    individual = repo.get("passive_assessment", "legacy")
    group = repo.get("risk_window", individual["risk_window_id"])
    assert individual["score"] is None and individual["final_grade"] is None
    assert individual["company_window_id"] == "company-20261004T010000Z"  # 이전 연결 보존
    assert individual["results"][0]["id"] == data.id and repo.get("risk", data.id) is not None
    assert group["score"] == 78 and group["prompt_source_capture_id"] == "legacy" and group["user_id"] == "a"
    assert repo.get("company_assessment", "company-20261004T010000Z") is not None  # 삭제하지 않음
    revision = repo.revision()
    repo.migrate_risk_windows()
    refresh_dirty(repo, net)
    assert repo.revision() == revision and len(repo.list("risk_window")) == 1
    with TestClient(create_app(repo.path, data_engine=Data(), network_engine=net)) as client:
        assert client.get("/api/v1/company-windows/company-20261004T010000Z").json()["scope"] == "company"


def test_session_spanning_boundary_uses_same_time_for_prompt_and_network(tmp_path):
    repo, net = repository(tmp_path), Network()
    observed = body("spanning", "20", at="2026-10-04T10:05:00+09:00")
    observed.session.started_at -= timedelta(minutes=1)
    observed.session.ended_at += timedelta(minutes=1)
    event = ingest(repo, observed, Data(), net)
    group = window(repo, event)
    assert group["start"].startswith("2026-10-04T01:05:00")
    assert net.windows[-1].features.bytes_sent == 100 and net.windows[-1].features.request_count == 1
    assert group["score"] == 48


def test_network_error_keeps_prompt_evidence_without_fused_score(tmp_path):
    repo = repository(tmp_path)
    class Broken:
        def analyze(self, window):
            raise RuntimeError("private network failure")
    event = ingest(repo, body("failure", "50"), Data(), Broken())
    group = window(repo, event)
    assert group["score"] is None and group["fusion_status"] == "error"
    assert group["prompt_max_score"] == 50 and group["prompt_source_capture_id"] == "failure"
    assert group["override"] is False and group["network_score"] is None
