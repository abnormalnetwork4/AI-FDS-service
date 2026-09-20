"""FDS collection path; never makes a Gateway enforcement decision."""
# 분석 경로의 중심: 자료 연계 → 최근 행동 집계 → 두 엔진 실행 → 요청별 결과 저장.
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from .contracts import Assessment, FDSIngest
from .repository import ConflictError, Repository
from .schemas import AIUsageEvent, DataRiskRequest, Finding, NetworkSession, RiskResult, WindowRequest
from .services import compute_window


def analyze_safely(engine, value, user_id, engine_name, event_id, window_id=None):
    # 어댑터가 반환한 데이터 형식과 사용자·엔진 정보를 확인하고 원래 요청 ID를 붙입니다.
    try:
        result = RiskResult.model_validate(engine.analyze(value))
        if result.user_id != user_id or result.engine != engine_name:
            raise ValueError("Engine returned mismatched identity")
        return result.model_copy(update={"source_event_id": event_id, "window_id": window_id})
    except Exception:
        # 예외 메시지에 프롬프트가 섞일 수 있어 그대로 저장하지 않습니다. 실패는 정상/0점이 아니라 error입니다.
        return RiskResult(user_id=user_id, engine=engine_name, engine_version="adapter-error",
                          source_event_id=event_id, window_id=window_id, status="error",
                          findings=[Finding(code="engine_error", name="분석 실패", status="error",
                                            reason="엔진 실행 실패. 어댑터 상태 확인 필요")])


def ingest(repo: Repository, body: FDSIngest, data_engine, network_engine):
    # 1. 같은 요청이 다시 전송되어도 분석 결과가 중복 저장되지 않도록 내용의 지문을 만듭니다.
    # 전송 상태는 처리하면서 바뀌므로 지문에서 제외합니다. 지문으로 원문을 복구할 수는 없습니다.
    canonical = body.model_dump(mode="json")
    canonical["audit"].pop("fds_delivery")
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    audit = body.audit
    receipt = repo.get("receipt", audit.id)
    if receipt:
        if receipt["fingerprint"] != fingerprint:
            raise ConflictError("Event ID reused with different content")
        return repo.get("assessment", audit.id)

    # 2. 한 요청에서 통신 기록(session)과 AI 사용 문맥(event)을 만들고 같은 사용자·세션으로 묶습니다.
    # 바이트 수는 앱에서 추정한 텍스트 크기입니다. 실제 패킷 계측값이 아니며 실패한 요청의 전송량은 미상입니다.
    session = NetworkSession(
        id=audit.session_id, user_id=audit.user_id, device_id=audit.device_id,
        started_at=audit.started_at, ended_at=audit.ended_at, destination=audit.model,
        bytes_sent=audit.request_bytes if audit.outcome == "completed" else 0,
        bytes_received=audit.response_bytes, via_gateway=True,
        connection_action=audit.policy_action, source="gateway_application",
    )
    event = AIUsageEvent(
        id=audit.id, session_id=audit.session_id, prompt_event_id=audit.id,
        user_id=audit.user_id, device_id=audit.device_id, occurred_at=audit.started_at,
        provider="internal-ai", channel="api", request_bytes=audit.request_bytes,
        approved_destination=audit.policy_reason != "model_not_allowed", policy_action=audit.policy_action,
    )
    sessions = [NetworkSession.model_validate(row) for row in repo.list("session", audit.user_id)]
    events = [AIUsageEvent.model_validate(row) for row in repo.list("event", audit.user_id)]
    # 3. 최근 5분/1시간은 분석 대상 범위입니다. 해당 시간이 끝날 때까지 기다리지 않습니다.
    # 집계 구간은 끝 시각을 제외하므로 1마이크로초를 더해 이번 요청의 시작 시각도 포함시킵니다.
    end = audit.started_at + timedelta(microseconds=1)
    windows = [compute_window(WindowRequest(user_id=audit.user_id, device_id=audit.device_id,
                                            start=end - timedelta(minutes=minutes), duration_minutes=minutes),
                              [*sessions, session], [*events, event]) for minutes in (5, 60)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        # 4. Data 엔진에는 원문(body.text), Network 엔진에는 집계된 숫자(window.features)가 전달됩니다.
        # 두 종류의 분석을 동시에 시작합니다. 엔진 객체는 main.py의 create_app()에서 설정합니다.
        data_job = pool.submit(analyze_safely, data_engine,
                               DataRiskRequest(user_id=audit.user_id, text=body.text, input_origin=body.input_origin),
                               audit.user_id, "data", audit.id)
        # 같은 Network 모델을 동시에 호출하지 않도록 이 작업 안에서는 5분 → 1시간 순서로 처리합니다.
        network_job = pool.submit(lambda: [analyze_safely(network_engine, w, audit.user_id, "network", audit.id, w.id)
                                          for w in windows])
        results = [data_job.result(), *network_job.result()]
    statuses = {r.status for r in results}
    # 5. 세 분석 결과를 같은 요청 아래 모읍니다. status는 처리 상태이고 최종 위험 등급은 아직 미판정입니다.
    assessment = Assessment(id=audit.id, user_id=audit.user_id, session_id=audit.session_id,
                            policy_action=audit.policy_action,
                            status="error" if "error" in statuses else "pending" if "pending" in statuses else "complete",
                            results=results)
    # 중간에 일부 기록만 저장되지 않도록 하나의 트랜잭션으로 반영합니다. body.text 자체는 저장하지 않습니다.
    return repo.save_ingest(fingerprint, assessment,
                           [("gateway_audit", audit), ("session", session), ("event", event),
                            *[("window", w) for w in windows], *[("risk", r) for r in results]])
