# FDS가 받거나 저장·반환하는 데이터 형식을 정의합니다. 여기의 Model은 AI가 아닌 Pydantic 데이터 클래스입니다.
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

# 공통 입력 규칙: 식별자는 지정 문자만, 개수는 0 이상 정수, 점수는 0~100의 유한한 숫자입니다.
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:@-]+$")]
Count = Annotated[int, Field(ge=0, strict=True)]
Score = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


def now() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    # 정의하지 않은 필드를 거절합니다. 잘못된 필드명을 조용히 무시하지 않도록 하는 설정입니다.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NetworkSession(Model):
    # 한 통신 세션의 사용자·목적지·시간·송수신량입니다. source로 수집기 기록과 Gateway 추정치를 구분합니다.
    id: Identifier
    user_id: Identifier
    device_id: Identifier
    started_at: AwareDatetime
    ended_at: AwareDatetime
    destination: str = Field(min_length=1, max_length=253)
    bytes_sent: Count = 0
    bytes_received: Count = 0
    packets_sent: Count = 0
    packets_received: Count = 0
    via_gateway: bool
    connection_action: Literal["allow", "block", "unknown"] = "unknown"
    source: Literal["collector", "gateway_application"] = "collector"

    @model_validator(mode="after")
    def valid_interval(self):
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be >= started_at")
        return self


class AIUsageEvent(Model):
    # 네트워크 기록에 '어떤 AI를 어떻게 썼는지'를 붙입니다. session_id로 NetworkSession과 연결합니다.
    id: Identifier
    session_id: Identifier
    prompt_event_id: Identifier | None = None
    user_id: Identifier
    device_id: Identifier
    occurred_at: AwareDatetime
    provider: str = Field(min_length=1, max_length=100)
    channel: Literal["web", "api"]
    process_name: str | None = Field(default=None, max_length=255)
    request_bytes: Count = 0
    file_count: Count = 0
    approved_destination: bool | None = None
    policy_action: Literal["allow", "block", "review", "unknown"] = "unknown"


class WindowRequest(Model):
    # duration_minutes는 묶어 볼 시간 범위입니다. 자동 실행 주기나 대기 시간을 설정하는 값이 아닙니다.
    user_id: Identifier
    device_id: Identifier
    start: AwareDatetime
    duration_minutes: Literal[5, 60] = 5


class BehaviorFeatures(Model):
    # 여러 세션/이벤트를 집계한 Network 모델 입력값입니다. 같은 사용자·단말·시간 범위를 기준으로 계산합니다.
    session_count: Count
    request_count: Count
    bytes_sent: Count
    bytes_received: Count
    distinct_destinations: Count
    blocked_connections: Count
    direct_connections: Count
    unapproved_ai_requests: Count
    blocked_ai_requests: Count
    file_count: Count


class BehaviorWindow(Model):
    # 특정 시점에 계산해 저장한 행동 통계입니다. 뒤늦게 들어온 기록으로 자동 갱신되지는 않습니다.
    id: str
    user_id: Identifier
    device_id: Identifier
    start: AwareDatetime
    end: AwareDatetime
    duration_minutes: Literal[5, 60]
    features: BehaviorFeatures
    created_at: AwareDatetime = Field(default_factory=now)


class DataRiskRequest(Model):
    # Data 엔진의 입력 계약입니다. text에는 원문, input_origin에는 그 원문의 출처가 들어갑니다.
    user_id: Identifier
    text: str = Field(min_length=1, max_length=16384)
    input_origin: Literal["direct_user", "external_document", "tool_output"] = "direct_user"

    @field_validator("text")
    @classmethod
    def visible_text(cls, value):
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class Finding(Model):
    # 민감정보나 N1 같은 개별 위험 항목의 처리 상태·점수·근거입니다.
    code: str
    name: str
    status: Literal["pending", "complete", "error"] = "pending"
    score: Score | None = None
    reason: str

    @model_validator(mode="after")
    def score_matches_status(self):
        if (self.status == "complete") != (self.score is not None):
            raise ValueError("Only complete findings must have a score")
        return self


class RiskResult(Model):
    # 엔진 한 번의 실행 결과입니다. source_event_id는 원래 요청, window_id는 행동 집계와 연결하는 번호입니다.
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: Identifier
    engine: Literal["data", "network"]
    engine_version: str
    window_id: str | None = None
    source_event_id: str | None = None
    status: Literal["pending", "complete", "error"]
    score: Score | None = None
    findings: list[Finding]
    created_at: AwareDatetime = Field(default_factory=now)

    @model_validator(mode="after")
    def consistent_result(self):
        # 미완료 분석에 숫자 점수를 붙이거나 일부 항목이 미완료인데 전체 완료로 표시하는 것을 막습니다.
        if (self.status == "complete") != (self.score is not None):
            raise ValueError("Only complete results must have a score")
        if self.status == "complete" and (not self.findings or any(f.status != "complete" for f in self.findings)):
            raise ValueError("Complete results require complete findings")
        return self


class DashboardSummary(Model):
    network_session_count: int
    ai_event_count: int
    behavior_window_count: int
    analysis_count: int
    pending_analysis_count: int
    scored_analysis_count: int
    average_risk_score: Score | None
