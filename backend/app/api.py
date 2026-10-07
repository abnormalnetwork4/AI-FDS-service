# FDS의 HTTP 진입점과 조회 주소를 모은 파일입니다. @router.post/get이 URL과 파이썬 함수를 연결합니다.
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from . import services, dashboard
from .collection import analyze_safely, ingest
from .contracts import Assessment, CaptureIngest, CaptureRecord, EventIngest, CompanyAssessment, RiskWindow
from .live import changes
from .repository import Repository
from .schemas import AIUsageEvent, BehaviorWindow, DashboardSummary, DataRiskRequest, NetworkSession, RiskResult, WindowRequest

router = APIRouter(prefix="/api/v1")


def repository(request: Request) -> Repository:
    return request.app.state.repository


# Depends가 각 요청에 앱의 저장소 객체를 전달합니다. 매 API마다 DB 설정을 새로 작성할 필요가 없습니다.
Repo = Annotated[Repository, Depends(repository)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.post("/network-sessions", response_model=NetworkSession, status_code=201, tags=["수집"])
def create_session(body: NetworkSession, repo: Repo):
    # 외부 수집기가 개별 네트워크 기록을 등록하는 API입니다. 이 함수 자체가 패킷을 캡처하지는 않습니다.
    return repo.save("session", body)


@router.get("/network-sessions", response_model=list[NetworkSession], tags=["수집"])
def list_sessions(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("session", user_id, limit, offset)


@router.post("/ai-usage-events", response_model=AIUsageEvent, status_code=201, tags=["수집"])
def create_event(body: AIUsageEvent, repo: Repo):
    # 이미 저장된 세션과 사용자·단말·시간이 맞는지 확인한 후 이벤트를 저장합니다.
    return repo.save_event(body)


@router.get("/ai-usage-events", response_model=list[AIUsageEvent], tags=["수집"])
def list_events(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("event", user_id, limit, offset)


@router.post("/behavior-windows", response_model=BehaviorWindow, status_code=201, tags=["행동 집계"])
def create_window(body: WindowRequest, repo: Repo):
    # 원하는 구간을 직접 집계하는 API입니다. 캡처 수집 경로에서는 collection.py가 자동 집계합니다.
    return services.build_window(repo, body)


@router.get("/behavior-windows", response_model=list[BehaviorWindow], tags=["행동 집계"])
def list_windows(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("window", user_id, limit, offset)


@router.post("/data-risk/analyze", response_model=RiskResult, status_code=201, tags=["위험 분석"])
def analyze_data(body: DataRiskRequest, request: Request, repo: Repo):
    result = analyze_safely(request.app.state.data_engine, body, body.user_id, "data", None)
    # 모델에 원문을 전달하되 저장소에는 분석 결과만 넣습니다.
    return repo.save("risk", result)


@router.post("/network-risk/analyze/{window_id}", response_model=RiskResult, status_code=201, tags=["위험 분석"])
def analyze_network(window_id: str, request: Request, repo: Repo):
    raw = repo.get("window", window_id)
    if raw is None:
        raise HTTPException(404, "Behavior window not found")
    result = request.app.state.network_engine.analyze(BehaviorWindow.model_validate(raw))
    return repo.save("risk", result)


@router.get("/risks", response_model=list[RiskResult], tags=["위험 분석"])
def list_risks(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("risk", user_id, limit, offset)


@router.get("/risks/{risk_id}", response_model=RiskResult, tags=["위험 분석"])
def get_risk(risk_id: str, repo: Repo):
    result = repo.get("risk", risk_id)
    if result is None:
        raise HTTPException(404, "Risk result not found")
    return result


@router.get("/dashboard/summary", response_model=DashboardSummary, tags=["대시보드"])
def summary(repo: Repo, user_id: str | None = None):
    return services.dashboard(repo, user_id)


@router.get("/dashboard/events", response_model=dashboard.DashboardPage, tags=["대시보드"])
def dashboard_events(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    pairs, windows, total = repo.dashboard_page(user_id, limit, offset)
    return dashboard.DashboardPage(events=[dashboard.present(a, s, windows) for a, s in pairs],
                                   total=total, limit=limit, offset=offset)


@router.get("/dashboard/explanation", response_model=dashboard.Explanation, tags=["대시보드"])
def dashboard_explanation(repo: Repo, capture_id: str | None = None, window_id: str | None = None):
    if window_id is not None:
        raw = repo.get("risk_window", window_id) or repo.get("company_assessment", window_id)
        if raw is None:
            raise HTTPException(404, "Risk window not found")
        return dashboard.explain_window(raw)
    if capture_id is None:
        raise HTTPException(422, "Provide window_id or capture_id")
    # 브라우저가 만든 근거가 아니라 저장된 분석 결과만 요약합니다.
    raw = repo.get("passive_assessment", capture_id)
    if raw is None:
        raise HTTPException(404, "Assessment not found")
    return dashboard.explain(raw)


@router.get("/dashboard/risk-windows", response_model=dashboard.DashboardPage, tags=["대시보드"])
def risk_window_dashboard(repo: Repo, limit: Limit = 50, offset: Offset = 0, user_id: str | None = None,
                          start: str | None = None, date: date | None = None):
    # start는 구간 시작 시각(UTC ISO 문자열, 응답의 window_start)입니다. 같은 시간대의 다른 사용자 구간 조회에 씁니다.
    # date는 한국 시간(KST) 기준 날짜(YYYY-MM-DD)입니다.
    rows, total = repo.window_page(limit, offset, user_id, start, date)
    return dashboard.DashboardPage(events=[dashboard.present_window(row) for row in rows], total=total, limit=limit, offset=offset)


@router.get("/dashboard/users", response_model=dashboard.UserSummaryPage, tags=["대시보드"])
def dashboard_users(repo: Repo, date: date | None = None):
    # 사용자 선택 목록과 사용자별 등급 분포. date는 KST 날짜이며 없으면 전체 기간입니다.
    return dashboard.UserSummaryPage(users=repo.user_summaries(date), date=date.isoformat() if date else None)


@router.get("/dashboard/dates", response_model=list[dashboard.WindowDate], tags=["대시보드"])
def dashboard_dates(repo: Repo):
    return repo.window_dates()


@router.get("/dashboard/company-slots", response_model=dashboard.CompanySlotPage, tags=["대시보드"])
def company_slots(repo: Repo, limit: Limit = 50, offset: Offset = 0, date: date | None = None):
    slots, total = repo.company_slots(limit, offset, date)
    return dashboard.CompanySlotPage(slots=slots, total=total, limit=limit, offset=offset)


@router.get("/risk-windows", response_model=list[RiskWindow], tags=["사용자 5분 통합"])
def risk_windows(repo: Repo, limit: Limit = 50, offset: Offset = 0, user_id: str | None = None,
                 date: date | None = None):
    return repo.window_page(limit, offset, user_id, day=date)[0]


@router.get("/risk-windows/{window_id}", response_model=RiskWindow, tags=["사용자 5분 통합"])
def risk_window(window_id: str, repo: Repo):
    raw = repo.get("risk_window", window_id)
    if raw is None:
        raise HTTPException(404, "Risk window not found")
    return raw


@router.get("/company-windows", response_model=list[CompanyAssessment], tags=["이전 버전"], deprecated=True)
def company_windows(repo: Repo, limit: Limit = 50, offset: Offset = 0):
    # v0.6~0.7의 회사 전체 합산 구간. 새로 만들지 않으며 보존된 기록 조회용입니다.
    return repo.legacy_company_page(limit, offset)[0]


@router.get("/company-windows/{window_id}", response_model=CompanyAssessment, tags=["이전 버전"], deprecated=True)
def company_window(window_id: str, repo: Repo):
    raw = repo.get("company_assessment", window_id)
    if raw is None:
        raise HTTPException(404, "Company window not found")
    return raw


@router.get("/dashboard/stream", tags=["대시보드"])
async def dashboard_stream(request: Request, repo: Repo):
    return StreamingResponse(changes(repo, request), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/ingest/events", response_model=Assessment, tags=["이벤트 분석 수집"])
def ingest_event(body: EventIngest, request: Request, repo: Repo):
    # 통신 종료 시각 없이 관측 발생 시각만 받습니다. 원본 AI 요청을 전달하는 API가 아닙니다.
    return ingest(repo, body.as_capture(), request.app.state.data_engine, request.app.state.network_engine)


@router.post("/ingest/captures", response_model=Assessment, tags=["사후 분석 수집"])
def ingest_capture(body: CaptureIngest, request: Request, repo: Repo):
    # 미러링/캡처 수집기가 관측 자료의 복사본을 전송하는 API입니다. 원본 AI 요청 경로와 무관합니다.
    return ingest(repo, body, request.app.state.data_engine, request.app.state.network_engine)


@router.get("/captures", response_model=list[CaptureRecord], tags=["사후 분석 수집"])
def list_captures(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("capture", user_id, limit, offset)


@router.get("/assessments", response_model=list[Assessment], tags=["통합 분석 조회"])
def list_assessments(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("passive_assessment", user_id, limit, offset)


@router.get("/assessments/{event_id}", response_model=Assessment, tags=["통합 분석 조회"])
def get_assessment(event_id: str, repo: Repo):
    # 수집기가 부여한 캡처 ID로 분석 묶음을 조회합니다.
    result = repo.get("passive_assessment", event_id)
    if result is None:
        raise HTTPException(404, "Assessment not found")
    return result
