from datetime import timedelta, timezone
from uuid import uuid4

from .repository import Repository
from .schemas import AIUsageEvent, BehaviorFeatures, BehaviorWindow, DashboardSummary, NetworkSession, RiskResult, WindowRequest


def build_window(repo: Repository, request: WindowRequest) -> BehaviorWindow:
    start = request.start.astimezone(timezone.utc)
    end = start + timedelta(minutes=request.duration_minutes)
    # Snapshot semantics: sessions are attributed to the window containing their start.
    sessions = [NetworkSession.model_validate(row) for row in repo.list("session", request.user_id)]
    sessions = [s for s in sessions if s.device_id == request.device_id and start <= s.started_at < end]
    events = [AIUsageEvent.model_validate(row) for row in repo.list("event", request.user_id)]
    events = [e for e in events if e.device_id == request.device_id and start <= e.occurred_at < end]
    window = BehaviorWindow(
        id=str(uuid4()), user_id=request.user_id, device_id=request.device_id,
        start=start, end=end, duration_minutes=request.duration_minutes,
        features=BehaviorFeatures(
            session_count=len(sessions), request_count=len(events),
            bytes_sent=sum(s.bytes_sent for s in sessions),
            bytes_received=sum(s.bytes_received for s in sessions),
            distinct_destinations=len({s.destination for s in sessions}),
            blocked_connections=sum(s.connection_action == "block" for s in sessions),
            direct_connections=sum(not s.via_gateway for s in sessions),
            unapproved_ai_requests=sum(e.approved_destination is False for e in events),
            blocked_ai_requests=sum(e.policy_action == "block" for e in events),
            file_count=sum(e.file_count for e in events),
        ),
    )
    return repo.save("window", window)


def dashboard(repo: Repository, user_id: str | None) -> DashboardSummary:
    results = [RiskResult.model_validate(row) for row in repo.list("risk", user_id)]
    scores = [r.score for r in results if r.status == "complete" and r.score is not None]
    return DashboardSummary(
        network_session_count=len(repo.list("session", user_id)),
        ai_event_count=len(repo.list("event", user_id)),
        behavior_window_count=len(repo.list("window", user_id)),
        analysis_count=len(results),
        pending_analysis_count=sum(r.status == "pending" for r in results),
        scored_analysis_count=len(scores),
        average_risk_score=round(sum(scores) / len(scores), 2) if scores else None,
    )
