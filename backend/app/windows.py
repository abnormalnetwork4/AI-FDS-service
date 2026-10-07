"""사용자·단말별 고정 5분 위험 구간. 이벤트 시간 기준 [start, end).

네트워크 모델은 사용자별 5분 창으로 학습했습니다(test_features.csv의 window_id: ..._user_001_w02).
그래서 판정 단위도 (사용자, 단말, 5분)입니다. 회사 화면은 이 결과를 시간대별로 요약만 합니다.
"""
import hashlib
from datetime import timedelta, timezone

from .contracts import RiskWindow
from .schemas import WindowRequest, now
from .services import compute_window


def window_slot(user_id, device_id, at):
    at = at.astimezone(timezone.utc)
    start = at.replace(minute=at.minute // 5 * 5, second=0, microsecond=0)
    end = start + timedelta(minutes=5)
    # 사용자·단말 ID에 구분자가 들어가도 충돌하지 않도록 해시를 씁니다. 원래 ID는 필드로 보관합니다.
    key = hashlib.sha256(f"{user_id}\0{device_id}".encode()).hexdigest()[:16]
    return RiskWindow(id=f"window-{start:%Y%m%dT%H%M%SZ}-{key}", user_id=user_id, device_id=device_id,
                      start=start, end=end, phase="closed" if now() >= end else "open")


def compute_risk_window(group, sessions, events):
    # AI 로그가 있으면 요청 발생 시각에 전송량을 귀속합니다. 긴 세션의 바이트를 임의로 분할하지 않습니다.
    # compute_window(company=False)가 같은 사용자의 최근 1시간 이력과 같은 단말의 5분 기록을 고릅니다.
    event_times = {e.session_id: e.occurred_at for e in events}
    aligned = [s.model_copy(update={"started_at": event_times.get(s.id, s.started_at)}) for s in sessions]
    request = WindowRequest(user_id=group.user_id, device_id=group.device_id, start=group.start)
    return compute_window(request, aligned, events)


def refresh_window(repo, window_id, engine):
    from .collection import analyze_safely
    # 입력 버전이 바뀐 추론은 저장하지 않습니다. 다음 반복/백그라운드 작업이 최신 입력을 처리합니다.
    for _ in range(3):
        snapshot = repo.window_snapshot(window_id)
        if snapshot is None:
            return
        group, sessions, events = snapshot
        window = compute_risk_window(group, sessions, events)
        result = analyze_safely(engine, window, group.user_id, "network", None, window.id)
        if repo.publish_window_network(window_id, group.revision, result, window):
            return


def refresh_dirty(repo, engine):
    for group in repo.list("risk_window"):
        if group["revision"] != group["network_revision"]:
            refresh_window(repo, group["id"], engine)
    repo.close_risk_windows()
