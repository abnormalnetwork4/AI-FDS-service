# 모델 추론 전 사용할 행동 통계와 관리자 조회용 통계를 계산합니다.
from datetime import timedelta, timezone
from uuid import uuid4

from .repository import Repository
from .schemas import (AIUsageEvent, BehaviorFeatures, BehaviorWindow, DashboardSummary, NetworkModelFeatures,
                      NetworkSession, RiskResult, WindowRequest)


def build_window(repo: Repository, request: WindowRequest) -> BehaviorWindow:
    # 수동 집계 API용 함수: 기존 기록 읽기 → compute_window()로 계산 → 스냅샷 저장.
    sessions, events = repo.observation_snapshot(request.user_id)
    return repo.save("window", compute_window(request, sessions, events))


def compute_window(request: WindowRequest, sessions: list[NetworkSession], events: list[AIUsageEvent], *, company=False) -> BehaviorWindow:
    # 계산만 수행하고 DB를 수정하지 않습니다. 자동 수집 경로에서도 재사용합니다.
    start = request.start.astimezone(timezone.utc)
    end = start + timedelta(minutes=request.duration_minutes)
    user_sessions = [s for s in sessions if company or s.user_id == request.user_id]
    user_events = [e for e in events if company or e.user_id == request.user_id]
    # 같은 사용자·단말에서 구간 시작 이상, 구간 끝 미만인 기록만 고릅니다.
    # 세션이 구간을 가로질러도 전체 전송량을 시작 시각에 귀속하는 기본 집계 방식입니다.
    sessions = [s for s in user_sessions if (company or s.device_id == request.device_id) and start <= s.started_at < end]
    events = [e for e in user_events if (company or e.device_id == request.device_id) and start <= e.occurred_at < end]
    window = BehaviorWindow(
        id=str(uuid4()), user_id=request.user_id, device_id=request.device_id, scope="company" if company else "user_device",
        start=start, end=end, duration_minutes=request.duration_minutes,
        features=BehaviorFeatures(
            # 모델 입력용 숫자들입니다. 위험 점수나 탐지 결과가 아니라 관찰한 행동의 통계입니다.
            session_count=len({(s.user_id, s.device_id, s.parent_session_id or s.id) for s in sessions}), request_count=len(events),
            bytes_sent=sum(s.bytes_sent for s in sessions),
            bytes_received=sum(s.bytes_received for s in sessions),
            distinct_destinations=len({s.destination for s in sessions}),
            blocked_connections=len({(s.user_id, s.device_id, s.parent_session_id or s.id) for s in sessions if s.connection_action == "block"}),
            # 경로가 확인되지 않은 캡처를 우회 접속으로 추정하지 않습니다.
            direct_connections=len({(s.user_id, s.device_id, s.parent_session_id or s.id) for s in sessions if s.via_gateway is False}),
            unknown_gateway_connections=len({(s.user_id, s.device_id, s.parent_session_id or s.id) for s in sessions if s.via_gateway is None}),
            unapproved_ai_requests=sum(e.approved_destination is False for e in events),
            blocked_ai_requests=sum(e.policy_action == "block" for e in events),
            file_count=sum(e.file_count for e in events),
        ),
        model_features=model_features(sessions, events, user_sessions, user_events, end, request.duration_minutes),
    )
    return window


# 업무시간(한국 시간 08:00~19:00). 학습 데이터 실행 시각과 off_hours_fraction 값으로 추정한 경계입니다.
KST = timezone(timedelta(hours=9))
BUSINESS_HOURS = (8, 19)


def switches(values):
    values = [v for v in values if v is not None]
    return sum(a != b for a, b in zip(values, values[1:]))


def model_features(sessions, events, user_sessions, user_events, end, minutes) -> NetworkModelFeatures:
    # 학습 데이터(v3 behavior window)와 같은 정의입니다. 원본 v0 로그로 재계산해 behavior_windows.csv와 대조했습니다.
    # 요청 단위 값은 AI 사용 이벤트, 송수신량은 네트워크 세션에서 계산합니다.
    events = sorted(events, key=lambda e: (e.occurred_at, e.id))
    destination = {s.id: s.destination for s in user_sessions}  # 이벤트의 세션은 구간 이전에 시작했을 수 있음
    times = [e.occurred_at for e in events]
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    iat_mean = sum(gaps) / len(gaps) if gaps else None
    iat_std = (sum((g - iat_mean) ** 2 for g in gaps) / len(gaps)) ** 0.5 if gaps else None  # 모표준편차(ddof=0)
    # 동시 요청 수: 요청~응답 구간이 겹치는 최대 개수. 응답 시각이 없으면 요청 시점 하나로 봅니다.
    spans = [(e.occurred_at, e.completed_at or e.occurred_at) for e in events]
    peak = max((1 + sum(o_start <= s < o_end for j, (o_start, o_end) in enumerate(spans) if j != i)
                for i, (s, _) in enumerate(spans)), default=0)
    upload, download = sum(s.bytes_sent for s in sessions), sum(s.bytes_received for s in sessions)
    # 최근 1시간은 전달된 집계 범위의 전체 기록입니다. 회사 집계에서는 모든 사용자 기록을 포함합니다.
    since = end - timedelta(hours=1)
    first = min([s.started_at for s in user_sessions if s.started_at < end] + [e.occurred_at for e in user_events if e.occurred_at < end], default=end)
    coverage = min(3600.0, max(0.0, (end - first).total_seconds()))
    off_hours = [not BUSINESS_HOURS[0] <= t.astimezone(KST).hour < BUSINESS_HOURS[1] for t in times]
    return NetworkModelFeatures(
        provider_count=len({e.provider for e in events}),
        session_count=len({(s.user_id, s.device_id, s.parent_session_id or s.id) for s in sessions}),
        request_count=len(events),
        upload_bytes=upload, download_bytes=download,
        upload_packets=sum(s.packets_sent for s in sessions),
        download_packets=sum(s.packets_received for s in sessions),
        request_body_bytes=sum(e.request_bytes for e in events),
        file_count=sum(e.file_count for e in events),
        max_request_bytes=max((e.request_bytes for e in events), default=None),
        iat_mean_s=iat_mean, iat_std_s=iat_std,
        iat_cv=iat_std / iat_mean if iat_mean else None,
        peak_concurrency=peak,
        destination_switch_count=switches([destination.get(e.session_id) for e in events]),
        tenant_switch_count=switches([e.tenant for e in events]),
        declared_process_switch_count=switches([e.process_name for e in events]),
        request_rate_per_min=len(events) / minutes,
        upload_download_ratio=upload / download if download else None,
        off_hours_fraction=sum(off_hours) / len(off_hours) if off_hours else None,
        user_upload_bytes_observed_1h=sum(s.bytes_sent for s in user_sessions if since <= s.started_at < end),
        user_request_count_observed_1h=sum(since <= e.occurred_at < end for e in user_events),
        history_coverage_seconds_1h=coverage,
        history_complete_1h=float(coverage >= 3600),
        retry_count_after_block=sum(bool(e.retry_after_block) for e in events),
    )


def dashboard(repo: Repository, user_id: str | None) -> DashboardSummary:
    # 60점/100점 엔진을 섞어 평균내지 않습니다. 유효한 통합 점수가 있는 이벤트만 집계합니다.
    results = [RiskResult.model_validate(row) for row in repo.list("risk", user_id)]
    scored = [r for r in results if r.status == "complete" and r.score is not None]
    company_rows = repo.list("company_assessment") if user_id is None else []
    scores = [r["score"] for r in company_rows
              if r.get("fusion_status") == "complete" and r.get("score") is not None]
    return DashboardSummary(
        network_session_count=len({(s["user_id"], s["device_id"], s.get("parent_session_id") or s["id"])
                                   for s in repo.list("session", user_id)}),
        ai_event_count=len(repo.list("event", user_id)),
        behavior_window_count=len(repo.list("window", user_id)),
        analysis_count=len(results),
        pending_analysis_count=sum(r.status == "pending" for r in results),
        scored_analysis_count=len(scored), graded_event_count=0,
        company_window_count=len(company_rows), graded_window_count=len(scores),
        average_risk_score=round(sum(scores) / len(scores), 2) if scores else None,
    )
