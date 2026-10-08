"""Out-of-path FDS: 관측 자료 저장 → 행동 집계 → 사후 분석. 원본 통신을 호출하거나 차단하지 않습니다."""
import hashlib
import json

from .contracts import Assessment, CaptureIngest, CaptureRecord
from .repository import Repository
from .schemas import OPTIONAL_EVENT_FIELDS, DataRiskRequest, Finding, RiskResult
from .windows import refresh_window


# PromptRiskResult에서 저장할 요약 필드. 문장별 위치·확률 상세는 저장하지 않습니다(화면에 쓰지 않고 크기만 늘어남).
OCCURRENCE_FIELDS = ("occurrence_status", "occurrence_analysis_version", "sentence_count")


def occurrence_summary(prompt_result):
    """엔진 전용 반복 횟수 결과를 공통 RiskResult로 줄입니다. 점수·판정·확률은 그대로 둡니다."""
    raw = prompt_result.model_dump(mode="json")
    error = raw.pop("occurrence_error", None)
    status = raw.get("occurrence_status")
    summary = {k: v for k, v in raw.items() if k in RiskResult.model_fields}
    summary.update({k: raw.get(k) for k in OCCURRENCE_FIELDS})
    if status == "not_analyzed":
        summary.update(occurrence_status=None, occurrence_analysis_version=None)
    summary["occurrence_error_code"] = error["code"] if status == "error" and error else None
    return summary


def analyze_safely(engine, value, user_id, engine_name, event_id, window_id=None):
    try:
        if engine_name == "data" and callable(getattr(engine, "analyze_with_occurrences", None)):
            # 반복 횟수 집계를 지원하는 프롬프트 엔진. 횟수 분석이 실패해도 엔진이 기존 판정·점수를 그대로 돌려줍니다.
            raw = occurrence_summary(engine.analyze_with_occurrences(value, request_id=event_id))
        else:
            raw = engine.analyze(value)
        result = RiskResult.model_validate(raw)
        if result.user_id != user_id or result.engine != engine_name:
            raise ValueError("Engine returned mismatched identity")
        return result.model_copy(update={"source_event_id": event_id, "window_id": window_id})
    except Exception:
        # 예외에 원문이 포함될 수 있으므로 상세 예외 문자열을 저장하지 않습니다.
        return RiskResult(user_id=user_id, engine=engine_name, engine_version="adapter-error",
                          source_event_id=event_id, window_id=window_id, status="error",
                          findings=[Finding(code="engine_error", name="분석 실패", status="error",
                                            reason="엔진 실행 실패. 어댑터 상태 확인 필요")])


def prompt_status(body: CaptureIngest):
    if body.prompt is None:
        return "unavailable"
    if not body.prompt.text.strip():
        return "empty"
    if len(body.prompt.text) > 16384:
        return "too_large"
    return "available"


def data_analysis(body, engine, availability):
    if availability != "available":
        # 프롬프트 부재나 모델 입력 상한 초과는 통신 차단 사유가 아니라 분석 불가 사유입니다.
        return RiskResult(user_id=body.session.user_id, engine="data", engine_version="not-invoked",
                          source_event_id=body.id, status="pending",
                          findings=[Finding(code="prompt_" + availability, name="프롬프트 분석 대기",
                                            reason={"unavailable": "패킷 메타데이터만 수집됨. 별도 프롬프트 로그 필요",
                                                    "empty": "비어 있는 프롬프트 관측",
                                                    "too_large": "현재 Data 모델 입력 상한 16384자 초과"}[availability])])
    return analyze_safely(engine, DataRiskRequest(user_id=body.session.user_id, text=body.prompt.text,
                                                input_origin=body.prompt.input_origin),
                          body.session.user_id, "data", body.id)


def ingest(repo: Repository, body: CaptureIngest, data_engine, network_engine):
    payload = body.model_dump(mode="json")
    # 기존 캡처의 재전송 지문은 새 선택 필드 추가 이후에도 동일하게 유지합니다.
    if payload["session"]["parent_session_id"] is None:
        payload["session"].pop("parent_session_id")
    if payload["session"]["observation_kind"] == "session":
        payload["session"].pop("observation_kind")
    if payload["ai_event"] is not None:
        for field in OPTIONAL_EVENT_FIELDS:
            if payload["ai_event"][field] is None:
                payload["ai_event"].pop(field)
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    session = body.session
    availability = prompt_status(body)
    capture = CaptureRecord(id=body.id, user_id=session.user_id, device_id=session.device_id,
                            session_id=session.id, ai_event_id=body.ai_event.id if body.ai_event else None,
                            source=session.source, prompt_status=availability)
    initial = Assessment(id=body.id, user_id=session.user_id, session_id=session.id)
    # 모델 실행보다 먼저 관측 자료를 확정합니다. 분석 중 들어온 다음 수집도 이 기록을 집계할 수 있습니다.
    # 같은 ID·내용은 재전송으로 처리하며, 앞선 처리 중 중단됐다면 같은 자료로 분석을 다시 시도합니다.
    existing = repo.record_capture(fingerprint, capture, initial, session, body.ai_event)
    if existing["processing_state"] != "finished":
        result = data_analysis(body, data_engine, availability)
        repo.publish_result(body.id, "data", result)
    # 재전송에서도 중단됐던 구간 분석은 재개합니다. 같은 입력 버전은 다시 저장하지 않습니다.
    if existing.get("risk_window_id"):
        refresh_window(repo, existing["risk_window_id"], network_engine)
    return repo.get("passive_assessment", body.id)
