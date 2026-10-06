"""Out-of-path FDS: 관측 자료 저장 → 행동 집계 → 사후 분석. 원본 통신을 호출하거나 차단하지 않습니다."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from .contracts import Assessment, CaptureIngest, CaptureRecord
from .repository import Repository
from .schemas import OPTIONAL_EVENT_FIELDS, DataRiskRequest, Finding, RiskResult, WindowRequest
from .services import compute_window


def analyze_safely(engine, value, user_id, engine_name, event_id, window_id=None):
    try:
        result = RiskResult.model_validate(engine.analyze(value))
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
    if existing["processing_state"] == "finished":
        return existing

    sessions, events = repo.observation_snapshot(session.user_id)
    # 이벤트 발생 시점(기존 캡처는 종료 시점)에서 최근 5분/1시간을 즉시 집계합니다.
    # 이벤트는 발생 시각, 기존 완료 세션은 시작 시각에 귀속하며 구간이 찰 때까지 기다리지 않습니다.
    end = session.ended_at + timedelta(microseconds=1)
    windows = [compute_window(WindowRequest(user_id=session.user_id, device_id=session.device_id,
                                            start=end - timedelta(minutes=minutes), duration_minutes=minutes),
                              sessions, events) for minutes in (5, 60)]
    # Data와 두 Network 구간을 각각 실행하고 먼저 끝난 결과부터 저장·화면에 공개합니다.
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(data_analysis, body, data_engine, availability): ("data", None)}
        for window in windows:
            job = pool.submit(analyze_safely, network_engine, window, session.user_id, "network", body.id, window.id)
            jobs[job] = (f"network-{window.duration_minutes:02}", window)
        for job in as_completed(jobs):
            slot, window = jobs[job]
            repo.publish_result(body.id, slot, job.result(), window)
    return repo.get("passive_assessment", body.id)
