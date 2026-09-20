"""FDS collection path; never makes a Gateway enforcement decision."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from .contracts import Assessment, FDSIngest
from .repository import ConflictError, Repository
from .schemas import AIUsageEvent, DataRiskRequest, Finding, NetworkSession, RiskResult, WindowRequest
from .services import compute_window


def analyze_safely(engine, value, user_id, engine_name, event_id, window_id=None):
    try:
        result = RiskResult.model_validate(engine.analyze(value))
        if result.user_id != user_id or result.engine != engine_name:
            raise ValueError("Engine returned mismatched identity")
        return result.model_copy(update={"source_event_id": event_id, "window_id": window_id})
    except Exception:
        # Do not include exception text: model failures can echo prompt content.
        return RiskResult(user_id=user_id, engine=engine_name, engine_version="adapter-error",
                          source_event_id=event_id, window_id=window_id, status="error",
                          findings=[Finding(code="engine_error", name="분석 실패", status="error",
                                            reason="엔진 실행 실패. 어댑터 상태 확인 필요")])


def ingest(repo: Repository, body: FDSIngest, data_engine, network_engine):
    canonical = body.model_dump(mode="json")
    canonical["audit"].pop("fds_delivery")
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    audit = body.audit
    receipt = repo.get("receipt", audit.id)
    if receipt:
        if receipt["fingerprint"] != fingerprint:
            raise ConflictError("Event ID reused with different content")
        return repo.get("assessment", audit.id)

    # Application estimates, not captured network packets. Failed forwarding is unknown.
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
    # Build trailing windows as of this request, including the request itself.
    end = audit.started_at + timedelta(microseconds=1)
    windows = [compute_window(WindowRequest(user_id=audit.user_id, device_id=audit.device_id,
                                            start=end - timedelta(minutes=minutes), duration_minutes=minutes),
                              [*sessions, session], [*events, event]) for minutes in (5, 60)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        data_job = pool.submit(analyze_safely, data_engine,
                               DataRiskRequest(user_id=audit.user_id, text=body.text, input_origin=body.input_origin),
                               audit.user_id, "data", audit.id)
        # Keep calls to a single Network adapter sequential (models may not be reentrant).
        network_job = pool.submit(lambda: [analyze_safely(network_engine, w, audit.user_id, "network", audit.id, w.id)
                                          for w in windows])
        results = [data_job.result(), *network_job.result()]
    statuses = {r.status for r in results}
    assessment = Assessment(id=audit.id, user_id=audit.user_id, session_id=audit.session_id,
                            policy_action=audit.policy_action,
                            status="error" if "error" in statuses else "pending" if "pending" in statuses else "complete",
                            results=results)
    return repo.save_ingest(fingerprint, assessment,
                           [("gateway_audit", audit), ("session", session), ("event", event),
                            *[("window", w) for w in windows], *[("risk", r) for r in results]])
