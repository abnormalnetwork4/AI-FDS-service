"""시연 스크립트와 사용자·단말 구간의 공개 필드(prompt_scores, network_score_breakdown) 검증."""
import importlib.util
from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.collection import ingest
from app.windows import window_slot
from app.contracts import EventIngest
from app.main import create_app
from app.repository import Repository
from app.schemas import Finding, NetworkScoreBreakdown, RiskResult

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "video_demo.py"
spec = importlib.util.spec_from_file_location("video_demo", SCRIPT)
video_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video_demo)


class Data:
    """프롬프트 원문이 숫자면 그 값을, 'error'면 예외를, 그 외에는 0점을 반환하는 시험용 엔진."""
    def analyze(self, request):
        if request.text == "error":
            raise RuntimeError("private error")
        try:
            score = float(request.text)
        except ValueError:
            score = 0.0
        return RiskResult(user_id=request.user_id, engine="data", engine_version="test", status="complete",
                          score=score, score_max=60,
                          findings=[Finding(code="prompt", name="prompt", status="complete", score=score, reason="test")])


BREAKDOWN = NetworkScoreBreakdown(base_score=50, probability_score=18, retry_score=0, repeat_score=None,
                                  total_score=68, threat="N1", threat_name="비정상 대량 업로드",
                                  predicted_class="N1", model_probability=0.9, retry_count=0)


class Network:
    def __init__(self, score=68, breakdown=BREAKDOWN):
        self.score, self.breakdown = score, breakdown

    def analyze(self, window):
        return RiskResult(user_id=window.user_id, engine="network", engine_version="test", status="complete",
                          score=self.score, score_breakdown=self.breakdown,
                          findings=[Finding(code="network_score", name="network", status="complete",
                                            score=self.score, reason="test")])


def capture(name, text, user="a", at="2026-10-01T10:01:00+09:00"):
    return EventIngest(id=name, session_id="s-" + name, user_id=user, device_id="pc", occurred_at=at,
                       destination="ai.internal", provider="internal", bytes_sent=100,
                       prompt={"text": text} if text is not None else None).as_capture()


def repository(tmp_path):
    repo = Repository(tmp_path / "demo.db")
    repo.initialize()
    return repo


def test_four_scenarios_use_four_distinct_five_minute_windows():
    day = date(2026, 10, 1)
    seen_ids, seen_windows = set(), []
    for scenario in video_demo.SCENARIOS:
        start = video_demo.slot_start(day, scenario["minute"])
        bodies = video_demo.build_bodies(scenario, start)
        assert bodies, scenario["name"]
        slots = {window_slot(b["user_id"], b["device_id"], datetime.fromisoformat(b["occurred_at"])).id for b in bodies}
        # 모든 이벤트가 하나의 구간에, 그리고 스크립트가 예상한 구간 ID에 들어가야 합니다.
        assert slots == {video_demo.window_id(start)}
        seen_windows.append(slots.pop())
        for b in bodies:
            assert b["id"].startswith("video-demo-") and b["id"] not in seen_ids
            seen_ids.add(b["id"])
            assert "score" not in b and "grade" not in b  # 점수·등급을 보내지 않음
            EventIngest.model_validate(b)  # API 계약 형식
    assert len(set(seen_windows)) == 4
    assert [s["target"] for s in video_demo.SCENARIOS] == ["normal", "caution", "warning", "danger"]


def test_max_prompt_only_not_sum_or_mean_and_all_scores_disclosed(tmp_path):
    repo, net = repository(tmp_path), Network()
    first = ingest(repo, capture("p1", "5", "a"), Data(), net)
    ingest(repo, capture("p2", "40", "a"), Data(), net)
    ingest(repo, capture("p3", "20", "a"), Data(), net)
    group = repo.get("risk_window", first["risk_window_id"])
    assert group["prompt_max_score"] == 40           # 합계 65, 평균 21.67이 아님
    assert group["prompt_source_capture_id"] == "p2" and group["prompt_source_user_id"] == "a"
    assert group["score"] == 40 + 68 * 0.4 == 67.2 and group["final_grade"] == "warning"
    assert group["capture_count"] == 3 and group["prompt_complete_count"] == 3
    assert group["prompt_scores"] == [
        {"capture_id": "p1", "user_id": "a", "score": 5.0, "status": "complete"},
        {"capture_id": "p2", "user_id": "a", "score": 40.0, "status": "complete"},
        {"capture_id": "p3", "user_id": "a", "score": 20.0, "status": "complete"},
    ]


def test_missing_and_error_prompts_are_not_zero(tmp_path):
    repo, net = repository(tmp_path), Network()
    event = ingest(repo, capture("ok", "10"), Data(), net)
    ingest(repo, capture("missing", None), Data(), net)
    ingest(repo, capture("failed", "error"), Data(), net)
    group = repo.get("risk_window", event["risk_window_id"])
    assert group["score"] is None and group["final_grade"] is None and group["fusion_status"] == "error"
    assert (group["prompt_complete_count"], group["prompt_missing_count"], group["prompt_error_count"]) == (1, 1, 1)
    by_id = {p["capture_id"]: p for p in group["prompt_scores"]}
    assert by_id["missing"] == {"capture_id": "missing", "user_id": "a", "score": None, "status": "pending"}
    assert by_id["failed"] == {"capture_id": "failed", "user_id": "a", "score": None, "status": "error"}


def test_network_breakdown_is_copied_from_result_not_invented(tmp_path):
    repo = repository(tmp_path)
    event = ingest(repo, capture("n1", "0"), Data(), Network())
    group = repo.get("risk_window", event["risk_window_id"])
    assert group["network_score_breakdown"] == BREAKDOWN.model_dump(mode="json")
    assert group["network_score_breakdown"]["total_score"] == group["network_score"] == 68
    assert group["network_score_breakdown"]["repeat_score"] is None
    # 근거가 없는 엔진 결과(이전 버전)는 None으로 두고 만들어 내지 않습니다.
    other = ingest(repo, capture("n2", "0", at="2026-10-01T10:31:00+09:00"), Data(), Network(breakdown=None))
    assert repo.get("risk_window", other["risk_window_id"])["network_score_breakdown"] is None


def test_dashboard_exposes_prompt_scores_breakdown_and_scope_note(tmp_path):
    with TestClient(create_app(tmp_path / "ui.db", data_engine=Data(), network_engine=Network())) as client:
        body = video_demo.build_bodies(dict(name="t", events=[video_demo.req(
            10, "30", sent=100, received=100, packets_sent=1, packets_received=1)]),
            video_demo.slot_start(date(2026, 10, 1), 0))[0]
        wid = client.post("/api/v1/ingest/events", json=body).json()["risk_window_id"]
        raw = client.get(f"/api/v1/risk-windows/{wid}").json()
        assert raw["prompt_scores"][0]["score"] == 30 and raw["network_score_breakdown"]["base_score"] == 50
        row = client.get("/api/v1/dashboard/risk-windows").json()["events"][0]
        assert row["prompt_complete_count"] == 1 and row["prompt_scores"] == raw["prompt_scores"]
        assert row["network_score_breakdown"] == raw["network_score_breakdown"]
        text = client.get(f"/api/v1/dashboard/explanation?window_id={wid}").json()["text"]
        assert "한 사용자·한 단말의 5분 구간 위험도" in text and "최고 점수 한 건만" in text
        assert "기본점수 50 + 모델 확률 점수 18 + 재시도 가산점 0" in text


class ClientApi:
    """스크립트의 Api.call과 같은 형태로 TestClient를 감쌉니다(실제 네트워크 사용 안 함)."""
    def __init__(self, client):
        self.client = client

    def call(self, method, path, body=None):
        response = self.client.request(method, path, json=body)
        return response.status_code, response.json()


def test_script_reports_mismatch_instead_of_false_success(tmp_path, capsys):
    # 시험용 엔진은 프롬프트 0점·네트워크 0점이라 모든 구간이 normal입니다. caution~danger는 목표와 달라야 합니다.
    engine = Network(score=0, breakdown=None)
    with TestClient(create_app(tmp_path / "s.db", data_engine=Data(), network_engine=engine)) as client:
        code = video_demo.main(["--date", "2026-10-01", "--timeout", "0"], api=ClientApi(client))
        out = capsys.readouterr().out
        assert code == 1
        assert out.count("목표와 실제 결과가 다름") == 3 and out.count("목표와 실제 결과가 같음") == 1
        assert "시나리오: danger" in out and "목표 등급              : danger" in out
        assert "실제 등급              : normal" in out
        assert "모두 목표 등급과 실제 등급이 같았습니다" not in out
        # 같은 날짜 재실행은 기존 구간과 섞이지 않도록 중단합니다.
        assert video_demo.main(["--date", "2026-10-01"], api=ClientApi(client)) == 3


def test_verdict_and_summary_never_treat_null_as_success(capsys):
    assert video_demo.verdict("danger", "danger")[0] == "match"
    assert video_demo.verdict("danger", "warning") == ("mismatch", "목표와 실제 결과가 다름")
    assert video_demo.verdict("normal", None)[0] == "mismatch"
    ok = dict(name="normal", target="normal", actual="normal", state="match", window_id="w", score=0)
    assert video_demo.summarize([ok]) == 0
    pending = dict(ok, actual=None, state="mismatch", score=None)
    assert video_demo.summarize([ok, pending]) == 1


def test_colleague_option_creates_separate_user_windows(tmp_path, capsys):
    with TestClient(create_app(tmp_path / "c.db", data_engine=Data(), network_engine=Network(score=0, breakdown=None))) as client:
        video_demo.main(["--date", "2026-10-01", "--timeout", "0", "--with-colleague"], api=ClientApi(client))
        out = capsys.readouterr().out
        windows = client.get("/api/v1/risk-windows?limit=50").json()
        assert len(windows) == 8 and {w["user_id"] for w in windows} == {"demo-user", "colleague-user"}
        demo = [w for w in windows if w["user_id"] == "demo-user"]
        assert sorted(w["capture_count"] for w in demo) == sorted(len(s["events"]) for s in video_demo.SCENARIOS)
        assert "사용자 2명" in out and "회사 점수 아님" in out
