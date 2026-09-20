# FDS의 HTTP 진입점과 조회 주소를 모은 파일입니다. @router.post/get이 URL과 파이썬 함수를 연결합니다.
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import services
from .collection import ingest
from .contracts import Assessment, FDSIngest, GatewayAudit
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
    # 원하는 구간을 직접 집계하는 API입니다. Gateway 자료를 받는 경로에서는 collection.py가 자동 집계합니다.
    return services.build_window(repo, body)


@router.get("/behavior-windows", response_model=list[BehaviorWindow], tags=["행동 집계"])
def list_windows(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("window", user_id, limit, offset)


@router.post("/data-risk/analyze", response_model=RiskResult, status_code=201, tags=["위험 분석"])
def analyze_data(body: DataRiskRequest, request: Request, repo: Repo):
    result = request.app.state.data_engine.analyze(body)
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


@router.post("/ingest/gateway", response_model=Assessment, tags=["Gateway 연동"])
def ingest_gateway(body: FDSIngest, request: Request, repo: Repo):
    # Gateway의 HTTPFDSSink가 호출하는 주소입니다. 프롬프트는 body.text에 들어 있습니다.
    # 수집부터 두 엔진 실행·저장까지의 구체적인 동작은 collection.py의 ingest()에 위임합니다.
    return ingest(repo, body, request.app.state.data_engine, request.app.state.network_engine)


@router.get("/gateway-audits", response_model=list[GatewayAudit], tags=["Gateway 연동"])
def list_gateway_audits(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("gateway_audit", user_id, limit, offset)


@router.get("/assessments", response_model=list[Assessment], tags=["통합 분석 조회"])
def list_assessments(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("assessment", user_id, limit, offset)


@router.get("/assessments/{event_id}", response_model=Assessment, tags=["통합 분석 조회"])
def get_assessment(event_id: str, repo: Repo):
    # Gateway에서 받은 request_id를 event_id로 넣으면 같은 요청의 분석 묶음을 조회합니다.
    result = repo.get("assessment", event_id)
    if result is None:
        raise HTTPException(404, "Assessment not found")
    return result
