# 모델 추론 전 사용할 행동 통계와 관리자 조회용 통계를 계산합니다.
from datetime import timedelta, timezone
from uuid import uuid4

from .repository import Repository
from .schemas import AIUsageEvent, BehaviorFeatures, BehaviorWindow, DashboardSummary, NetworkSession, RiskResult, WindowRequest


def build_window(repo: Repository, request: WindowRequest) -> BehaviorWindow:
    # 수동 집계 API용 함수: 기존 기록 읽기 → compute_window()로 계산 → 스냅샷 저장.
    sessions = [NetworkSession.model_validate(row) for row in repo.list("session", request.user_id)]
    events = [AIUsageEvent.model_validate(row) for row in repo.list("event", request.user_id)]
    return repo.save("window", compute_window(request, sessions, events))


def compute_window(request: WindowRequest, sessions: list[NetworkSession], events: list[AIUsageEvent]) -> BehaviorWindow:
    # 계산만 수행하고 DB를 수정하지 않습니다. 자동 수집 경로에서도 재사용합니다.
    start = request.start.astimezone(timezone.utc)
    end = start + timedelta(minutes=request.duration_minutes)
    # 같은 사용자·단말에서 구간 시작 이상, 구간 끝 미만인 기록만 고릅니다.
    # 세션이 구간을 가로질러도 전체 전송량을 시작 시각에 귀속하는 기본 집계 방식입니다.
    sessions = [s for s in sessions if s.user_id == request.user_id and s.device_id == request.device_id and start <= s.started_at < end]
    events = [e for e in events if e.user_id == request.user_id and e.device_id == request.device_id and start <= e.occurred_at < end]
    window = BehaviorWindow(
        id=str(uuid4()), user_id=request.user_id, device_id=request.device_id,
        start=start, end=end, duration_minutes=request.duration_minutes,
        features=BehaviorFeatures(
            # 모델 입력용 숫자들입니다. 위험 점수나 탐지 결과가 아니라 관찰한 행동의 통계입니다.
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
    return window


def dashboard(repo: Repository, user_id: str | None) -> DashboardSummary:
    # 미연결/실패 결과를 0점으로 넣지 않고, 실제 점수가 있는 완료 결과만 평균에 반영합니다.
    # 이 평균은 엔진 호출별 단순 평균이며 두 엔진의 통합 위험 등급은 아닙니다.
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
