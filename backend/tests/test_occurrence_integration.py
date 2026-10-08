"""반복 횟수 집계를 수집·저장·5분 구간·화면 응답에 연결한 결과를 검증합니다. 점수·등급은 바뀌지 않아야 합니다."""
from app.collection import ingest
from app.dashboard import explain_window, present_window
from app.schemas import DataRiskRequest
from test_prompt_occurrences import make_engine
from test_risk_windows import Data, Network, body, repository, window


def data_result(repo, capture_id):
    return next(r for r in repo.get("passive_assessment", capture_id)["results"] if r["engine"] == "data")


def test_ingest_stores_counts_without_changing_score(tmp_path):
    repo, engine = repository(tmp_path), make_engine()
    plain = engine.analyze(DataRiskRequest(
        user_id="a", text="INJECTION. INJECTION. 업무."))
    ingest(repo, body("c1", "INJECTION. INJECTION. 업무."), engine, Network())
    stored = data_result(repo, "c1")
    # 반복 반영: 인젝션 2회 → 10 + (1 - 0.6²) × 60 = 48.4 (1회일 때 34)
    assert plain.score == 34 and stored["score"] == 48.4
    assert stored["scoring_policy"] == "prompt-product-base10-repeat3-v2"
    assert stored["occurrence_status"] == "ok" and stored["sentence_count"] == 3
    counts = {f["code"]: f["occurrence_count"] for f in stored["findings"]}
    assert counts == {"AI_steal": 0, "prompt_injection": 2, "abuse_act": 0, "token_waste_repeat": 0}
    # 문장 위치·확률 상세는 저장하지 않습니다.
    assert "sentence_detections" not in stored and "occurrences" not in stored


def test_window_sums_counts_and_discloses_how_many_were_counted(tmp_path):
    repo, engine, net = repository(tmp_path), make_engine(), Network(score=10)
    first = ingest(repo, body("w1", "INJECTION. INJECTION."), engine, net)
    ingest(repo, body("w2", "INJECTION. ABUSE."), engine, net)
    group = window(repo, first)
    assert group["prompt_occurrence_counts"] == {"AI_steal": 0, "prompt_injection": 3, "abuse_act": 1, "token_waste_repeat": 0}
    assert group["prompt_occurrence_capture_count"] == 2
    # 통합 점수는 여전히 프롬프트 최고 점수 + 네트워크 × 0.4
    assert group["score"] == round(group["prompt_max_score"] + 10 * .4, 2)
    event = present_window(group)
    assert event.prompt_occurrence_counts["prompt_injection"] == 3
    text = explain_window(group).text
    assert "프롬프트 인젝션 3회" in text and "2/2건 집계" in text and "점수에 반영됩니다" in text


def test_engines_without_counting_and_failed_counting_stay_null(tmp_path):
    repo, net = repository(tmp_path), Network(score=10)
    first = ingest(repo, body("n1", "20"), Data(), net)  # 횟수 집계를 지원하지 않는 엔진
    group = window(repo, first)
    assert group["prompt_occurrence_counts"] is None and group["prompt_occurrence_capture_count"] == 0
    assert "occurrence_counts" not in group["prompt_scores"][0]
    assert data_result(repo, "n1")["occurrence_status"] is None

    # 문장 분석만 실패: 기존 판정·점수는 유지, 횟수는 0이 아니라 None
    second = ingest(repo, body("f1", "INJECTION. SENTENCE_FAILURE.", user="b"), make_engine(), net)
    stored = data_result(repo, "f1")
    assert stored["status"] == "complete" and stored["score"] == 34  # 횟수 실패 → 1회로 보고 기존 점수
    assert stored["scoring_policy"] == "prompt-product-base10-v1"
    assert stored["occurrence_status"] == "error" and stored["occurrence_error_code"] == "occurrence_analysis_failed"
    assert all(f["occurrence_count"] is None for f in stored["findings"])
    group = window(repo, second)
    assert group["prompt_occurrence_counts"] is None
    assert group["prompt_scores"][0]["occurrence_status"] == "error"


def test_repeat_scoring_caps_at_three_and_combines_labels():
    from app.schemas import Finding
    from app.scoring import prompt_score
    def f(code, n):
        return Finding(code=code, name=code, status="complete", detected=n > 0, probability=.9 if n else .1,
                       threshold=.45, reason="t", occurrence_count=n)
    base = [f("AI_steal", 0), f("abuse_act", 0), f("token_waste_repeat", 0)]
    assert prompt_score(base + [f("prompt_injection", 1)]) == 34.0
    assert prompt_score(base + [f("prompt_injection", 3)]) == 57.0
    assert prompt_score(base + [f("prompt_injection", 9)]) == 57.0  # 3회 상한
    no_counts = [Finding(code=c, name=c, status="complete", detected=c == "prompt_injection",
                         probability=.9 if c == "prompt_injection" else .1, threshold=.45, reason="t")
                 for c in ("AI_steal", "prompt_injection", "abuse_act", "token_waste_repeat")]
    assert prompt_score(no_counts) == 34.0  # 횟수 없음 → 기존 정책과 같음
