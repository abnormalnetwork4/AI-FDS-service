"""정해진 분류 출력으로 집계·오류·기존 판정 보존을 검증합니다. 탐지 성능 평가가 아닙니다."""
from threading import Lock

import pytest
from pydantic import ValidationError

from app.prompt_engine import LABELS, RegressionDataRiskEngine
from app.prompt_occurrences import (
    OCCURRENCE_MAX_CHARACTERS, OccurrenceAnalysisLimitError, PromptRiskResult, split_prompt_sentences,
)
from app.schemas import DataRiskRequest, RiskResult


class ProbabilityMatrix:
    def __init__(self, probabilities):
        self.probabilities = probabilities

    def __getitem__(self, position):
        row, column = position
        value = self.probabilities[row]
        return value if column == 1 else 1 - value


class MarkerVectorizer:
    def __init__(self):
        self.calls = []

    def transform(self, texts):
        self.calls.append(list(texts))
        return texts


class MarkerClassifier:
    def __init__(self, label):
        self.label = label

    def predict_proba(self, texts):
        marker = dict(zip(LABELS, ("DISTILL", "INJECTION", "ABUSE", "WASTE")))[self.label]
        values = []
        for text in texts:
            if "WHOLE_FAILURE" in text or (len(texts) > 1 and "SENTENCE_FAILURE" in text):
                raise RuntimeError("PRIVATE_CLASSIFIER_FAILURE " + text)
            value = .9 if marker in text else .1
            if text.startswith("SAFE_QUOTE"):
                value = .1
            if self.label == "prompt_injection" and "WIDE" in text and "CONTEXT" in text:
                value = .9
            if "EXACT_THRESHOLD" in text:
                value = .45
            values.append(value)
        return ProbabilityMatrix(values)


def make_engine():
    # 사용자가 제공한 모델 대신 고정된 확률을 주입해 집계 계약만 검증합니다.
    engine = object.__new__(RegressionDataRiskEngine)
    engine.model_version = "controlled-occurrence-test"
    engine.thresholds = dict.fromkeys(LABELS, .45)
    engine.vectorizer = MarkerVectorizer()
    engine.classifiers = {label: MarkerClassifier(label) for label in LABELS}
    engine._lock = Lock()
    return engine


def analyze(text, request_id="request-1", engine=None):
    return (engine or make_engine()).analyze_with_occurrences(
        DataRiskRequest(user_id="user-1", text=text), request_id=request_id,
    )


@pytest.mark.parametrize("text,expected", [
    ("안녕하세요. 다음 업무입니다!", ["안녕하세요.", "다음 업무입니다!"]),
    ('"가. 나!" 를 인용합니다. 다음.', ['"가. 나!" 를 인용합니다.', '다음.']),
    ("‘가. 나!’ 인용. 다음.", ["‘가. 나!’ 인용.", "다음."]),
    ("`a.b` 코드. 다음.", ["`a.b` 코드.", "다음."]),
    ("```js\na.b();\nc.d();\n```\n다음.", ["```js\na.b();\nc.d();\n```", "다음."]),
    ("~~~\na.b\nc.d\n~~~\n다음.", ["~~~\na.b\nc.d\n~~~", "다음."]),
    ("https://example.com/a.b 확인. 다음.", ["https://example.com/a.b 확인.", "다음."]),
    ("name@example.com 주소. 다음.", ["name@example.com 주소.", "다음."]),
    ("3.14 값. 다음.", ["3.14 값.", "다음."]),
    ("Dr. Kim과 U.S. 자료. 다음.", ["Dr. Kim과 U.S. 자료.", "다음."]),
    ("ＡＩ 업무。 다음！ 끝？", ["ＡＩ 업무。", "다음！", "끝？"]),
])
def test_sentence_boundaries_preserve_protected_content(text, expected):
    spans = split_prompt_sentences(text)
    assert [text[span["start"]:span["end"]] for span in spans] == expected


def test_repeated_identical_sentences_count_separate_positions():
    engine = make_engine()
    result = analyze("  😀 INJECTION.\r\n INJECTION.  ", engine=engine)
    assert result.occurrence_status == "ok"
    assert result.label_occurrence_counts == dict(AI_steal=0, prompt_injection=2, abuse_act=0, token_waste_repeat=0)
    assert result.label_sentence_counts == result.label_occurrence_counts
    assert [finding.occurrence_count for finding in result.findings] == [0, 2, 0, 0]
    assert [item.start for item in result.occurrences] == [2, 17]
    assert len({item.event_id for item in result.occurrences}) == 2
    assert result.score == 34.0
    assert all(finding.probability == (.9 if finding.code == "prompt_injection" else .1) for finding in result.findings)
    assert engine.vectorizer.calls[0] == ["😀 INJECTION.\n INJECTION."]


def test_sentence_hits_do_not_change_whole_prompt_normal_decision():
    result = analyze('SAFE_QUOTE 업무 설명.\n"INJECTION. INJECTION." 인용.')
    assert result.label_sentence_counts["prompt_injection"] == 1
    assert result.label_occurrence_counts == dict.fromkeys(LABELS, 0)
    assert result.occurrences == []
    assert not any(finding.detected for finding in result.findings)
    assert result.score == 0


def test_whole_prompt_fallback_is_explicit_and_not_a_sentence_hit():
    result = analyze("WIDE. CONTEXT.")
    assert result.label_sentence_counts["prompt_injection"] == 0
    assert result.label_occurrence_counts["prompt_injection"] == 1
    occurrence = result.occurrences[0]
    assert occurrence.source == "whole_prompt_fallback"
    assert occurrence.sentence_index is None
    assert (occurrence.start, occurrence.end) == (0, len("WIDE. CONTEXT."))


def test_exact_threshold_and_multiple_labels():
    boundary = analyze("EXACT_THRESHOLD INJECTION.")
    assert not any(finding.detected for finding in boundary.findings)
    assert boundary.label_occurrence_counts == dict.fromkeys(LABELS, 0)
    mixed = analyze("DISTILL INJECTION. ABUSE. WASTE. INJECTION.")
    assert mixed.label_occurrence_counts == dict(AI_steal=1, prompt_injection=2, abuse_act=1, token_waste_repeat=1)
    assert mixed.score == 56.2  # 횟수는 기존 점수에 재가산하지 않습니다.


def test_reprocessing_ids_are_stable_but_requests_positions_and_raw_text_are_distinct():
    text = "INJECTION. INJECTION."
    ids = [item.event_id for item in analyze(text).occurrences]
    assert [item.event_id for item in analyze(text).occurrences] == ids
    assert all(item.event_id not in ids for item in analyze(text, "request-2").occurrences)
    assert all(item.event_id not in ids for item in analyze(" " + text).occurrences)
    assert analyze(text, None).occurrences[0].event_id != analyze(text, None).occurrences[0].event_id


def test_batching_keeps_all_65_occurrences():
    engine = make_engine()
    result = analyze("INJECTION. " * 65, engine=engine)
    assert result.label_occurrence_counts["prompt_injection"] == 65
    assert [len(batch) for batch in engine.vectorizer.calls] == [1, 64, 1]


@pytest.mark.parametrize("text,code", [
    ("INJECTION. " * 257, "occurrence_limit_exceeded"),
    ("INJECTION. SENTENCE_FAILURE.", "occurrence_analysis_failed"),
])
def test_auxiliary_failure_keeps_whole_classification_and_null_counts(text, code):
    result = analyze(text)
    assert result.status == "complete" and result.occurrence_status == "error"
    assert result.findings[1].detected and result.findings[1].probability == .9
    assert result.score == 34
    assert result.occurrence_error.code == code
    assert result.label_occurrence_counts is None and result.label_sentence_counts is None
    assert result.sentence_count is None and result.sentence_detections is None and result.occurrences is None
    assert all(finding.occurrence_count is None for finding in result.findings)
    assert "PRIVATE_CLASSIFIER_FAILURE" not in result.model_dump_json()
    assert text not in result.model_dump_json()


def test_character_limit_and_whole_model_failure():
    with pytest.raises(OccurrenceAnalysisLimitError):
        split_prompt_sentences("x" * (OCCURRENCE_MAX_CHARACTERS + 1))
    with pytest.raises(RuntimeError, match="PRIVATE_CLASSIFIER_FAILURE"):
        analyze("WHOLE_FAILURE")


@pytest.mark.parametrize("mutation", ["counts", "duplicate", "gating", "finding", "null", "fractional"])
def test_schema_rejects_inconsistent_or_unverified_counts(mutation):
    payload = analyze("INJECTION. INJECTION.").model_dump()
    if mutation == "counts":
        payload["label_occurrence_counts"]["prompt_injection"] = 1
    elif mutation == "duplicate":
        payload["occurrences"][1]["event_id"] = payload["occurrences"][0]["event_id"]
    elif mutation == "gating":
        payload["sentence_detections"][0]["counted_flags"]["prompt_injection"] = False
    elif mutation == "finding":
        payload["findings"][1]["occurrence_count"] = 0
    elif mutation == "null":
        payload["occurrence_status"] = "error"
        payload["occurrence_error"] = {"code": "occurrence_analysis_failed", "message": "error"}
    elif mutation == "fractional":
        payload["label_occurrence_counts"]["prompt_injection"] = 2.5
    with pytest.raises(ValidationError):
        PromptRiskResult.model_validate(payload)


def test_existing_analyze_remains_compatible_without_implicit_sentence_analysis():
    engine = make_engine()
    old = engine.analyze(DataRiskRequest(user_id="user-1", text="INJECTION. INJECTION."))
    assert type(old) is RiskResult
    assert len(engine.vectorizer.calls) == 1
    assert "occurrence_status" not in old.model_dump()
    assert all("occurrence_count" not in finding.model_dump() for finding in old.findings)
    restored = RiskResult.model_validate_json(old.model_dump_json())
    assert restored.score == 34 and restored.findings[1].detected


@pytest.mark.parametrize("request_id", ["", "space id", "x" * 129])
def test_explicit_reprocessing_id_must_follow_the_existing_identifier_contract(request_id):
    with pytest.raises(ValidationError):
        analyze("INJECTION.", request_id)
