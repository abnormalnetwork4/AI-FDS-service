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


Repo = Annotated[Repository, Depends(repository)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.post("/network-sessions", response_model=NetworkSession, status_code=201, tags=["수집"])
def create_session(body: NetworkSession, repo: Repo):
    return repo.save("session", body)


@router.get("/network-sessions", response_model=list[NetworkSession], tags=["수집"])
def list_sessions(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("session", user_id, limit, offset)


@router.post("/ai-usage-events", response_model=AIUsageEvent, status_code=201, tags=["수집"])
def create_event(body: AIUsageEvent, repo: Repo):
    return repo.save_event(body)


@router.get("/ai-usage-events", response_model=list[AIUsageEvent], tags=["수집"])
def list_events(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("event", user_id, limit, offset)


@router.post("/behavior-windows", response_model=BehaviorWindow, status_code=201, tags=["행동 집계"])
def create_window(body: WindowRequest, repo: Repo):
    return services.build_window(repo, body)


@router.get("/behavior-windows", response_model=list[BehaviorWindow], tags=["행동 집계"])
def list_windows(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("window", user_id, limit, offset)


@router.post("/data-risk/analyze", response_model=RiskResult, status_code=201, tags=["위험 분석"])
def analyze_data(body: DataRiskRequest, request: Request, repo: Repo):
    result = request.app.state.data_engine.analyze(body)
    # Store only the result, never the prompt body.
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
    return ingest(repo, body, request.app.state.data_engine, request.app.state.network_engine)


@router.get("/gateway-audits", response_model=list[GatewayAudit], tags=["Gateway 연동"])
def list_gateway_audits(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("gateway_audit", user_id, limit, offset)


@router.get("/assessments", response_model=list[Assessment], tags=["통합 분석 조회"])
def list_assessments(repo: Repo, user_id: str | None = None, limit: Limit = 50, offset: Offset = 0):
    return repo.list("assessment", user_id, limit, offset)


@router.get("/assessments/{event_id}", response_model=Assessment, tags=["통합 분석 조회"])
def get_assessment(event_id: str, repo: Repo):
    result = repo.get("assessment", event_id)
    if result is None:
        raise HTTPException(404, "Assessment not found")
    return result
