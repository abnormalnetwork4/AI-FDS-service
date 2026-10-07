"""고정 5분 회사 집계. 이벤트 시간 기준 [start, end), 한 DB는 한 회사 전용."""
from datetime import timedelta, timezone

from .contracts import CompanyAssessment
from .schemas import WindowRequest, now
from .services import compute_window


def company_slot(at):
    at = at.astimezone(timezone.utc)
    start = at.replace(minute=at.minute // 5 * 5, second=0, microsecond=0)
    end = start + timedelta(minutes=5)
    return CompanyAssessment(id="company-" + start.strftime("%Y%m%dT%H%M%SZ"),
                             start=start, end=end, phase="closed" if now() >= end else "open")


def compute_company_window(group, sessions, events):
    # 캡처에 AI 로그가 있으면 요청 발생 시각에 전송량을 귀속합니다.
    # 로그 없는 세션은 시작 시각에 귀속하며 긴 세션의 바이트를 임의로 분할하지 않습니다.
    event_times = {e.session_id: e.occurred_at for e in events}
    aligned = [s.model_copy(update={"started_at": event_times.get(s.id, s.started_at)}) for s in sessions]
    request = WindowRequest(user_id="company", device_id="all", start=group.start)
    return compute_window(request, aligned, events, company=True)


def refresh_company(repo, window_id, engine):
    from .collection import analyze_safely
    # 입력 버전이 바뀐 추론은 저장하지 않습니다. 다음 반복/백그라운드 작업이 최신 입력을 처리합니다.
    for _ in range(3):
        snapshot = repo.company_snapshot(window_id)
        if snapshot is None:
            return
        group, sessions, events = snapshot
        window = compute_company_window(group, sessions, events)
        result = analyze_safely(engine, window, "company", "network", None, window.id)
        if repo.publish_company_network(window_id, group.revision, result, window):
            return


def refresh_dirty(repo, engine):
    for group in repo.list("company_assessment"):
        if group["revision"] != group["network_revision"]:
            refresh_company(repo, group["id"], engine)
    repo.close_company_windows()
