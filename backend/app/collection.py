"""Out-of-path FDS: 관측 자료 저장 → 행동 집계 → 사후 분석. 원본 통신을 호출하거나 차단하지 않습니다."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from .contracts import Assessment, CaptureIngest, CaptureRecord
from .repository import Repository
from .schemas import DataRiskRequest, Finding, RiskResult, WindowRequest
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
    fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
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
    # 완료된 세션을 관측한 시점을 기준으로 최근 5분/1시간을 봅니다. 세션은 시작 시각에 귀속합니다.
    end = session.ended_at + timedelta(microseconds=1)
    windows = [compute_window(WindowRequest(user_id=session.user_id, device_id=session.device_id,
                                            start=end - timedelta(minutes=minutes), duration_minutes=minutes),
                              sessions, events) for minutes in (5, 60)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        data_job = pool.submit(data_analysis, body, data_engine, availability)
        network_job = pool.submit(lambda: [analyze_safely(network_engine, w, session.user_id, "network", body.id, w.id)
                                          for w in windows])
        results = [data_job.result(), *network_job.result()]
    statuses = {result.status for result in results}
    assessment = Assessment(id=body.id, user_id=session.user_id, session_id=session.id,
                            processing_state="finished",
                            status="error" if "error" in statuses else "pending" if "pending" in statuses else "complete",
                            results=results)
    return repo.finish_capture(assessment, [("window", w) for w in windows] + [("risk", r) for r in results])
