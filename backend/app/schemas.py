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
    # 한 통신 세션의 사용자·목적지·시간·송수신량입니다. source는 관측 자료를 얻은 경로입니다.
    id: Identifier
    parent_session_id: Identifier | None = None
    observation_kind: Literal["session", "event"] = "session"
    user_id: Identifier
    device_id: Identifier
    started_at: AwareDatetime
    ended_at: AwareDatetime
    destination: str = Field(min_length=1, max_length=253)
    bytes_sent: Count = 0
    bytes_received: Count = 0
    packets_sent: Count = 0
    packets_received: Count = 0
    via_gateway: bool | None = None
    connection_action: Literal["allow", "block", "unknown"] = "unknown"
    # gateway_application은 기존 저장 데이터 조회를 위한 값이며 새 캡처 수집 API에서는 거절합니다.
    source: Literal["collector", "packet_capture", "flow_export", "gateway_application", "application_log"] = "collector"

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
    # 아래는 Network 모델 입력용 선택 항목입니다. 미확인이면 생략합니다.
    completed_at: AwareDatetime | None = None  # 응답 완료 시각: 동시 요청 수 계산
    tenant: str | None = Field(default=None, min_length=1, max_length=100)  # 사용 계정 구분(회사/개인 등)
    retry_after_block: bool | None = None  # Gateway 차단 이후 재시도로 기록된 요청

    @model_validator(mode="after")
    def valid_completion(self):
        if self.completed_at is not None and self.completed_at < self.occurred_at:
            raise ValueError("completed_at must be >= occurred_at")
        return self


# 위 세 항목이 None이면 재전송 지문 계산에서 생략합니다. 기존 캡처 재전송 지문이 바뀌지 않게 하기 위함입니다.
OPTIONAL_EVENT_FIELDS = ("completed_at", "tenant", "retry_after_block")


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
    unknown_gateway_connections: Count = 0
    unapproved_ai_requests: Count
    blocked_ai_requests: Count
    file_count: Count


class NetworkModelFeatures(Model):
    # model/network 의 XGBoost 입력 24개입니다. 이름·의미는 학습 데이터(v3 behavior window)와 같습니다.
    # None은 관측 불가(예: 요청 1건이라 간격 없음)이며 모델이 학습 중앙값으로 대체합니다.
    provider_count: float
    session_count: float
    request_count: float
    upload_bytes: float
    download_bytes: float
    upload_packets: float
    download_packets: float
    request_body_bytes: float
    file_count: float
    max_request_bytes: float | None
    iat_mean_s: float | None
    iat_std_s: float | None
    iat_cv: float | None
    peak_concurrency: float
    destination_switch_count: float
    tenant_switch_count: float
    declared_process_switch_count: float
    request_rate_per_min: float
    upload_download_ratio: float | None
    off_hours_fraction: float | None
    user_upload_bytes_observed_1h: float
    user_request_count_observed_1h: float
    history_coverage_seconds_1h: float
    history_complete_1h: float
    # 모델 입력이 아니라 규칙 근거(재시도 가산점)로만 씁니다.
    retry_count_after_block: float = 0


class BehaviorWindow(Model):
    # 특정 시점에 계산해 저장한 행동 통계입니다. 뒤늦게 들어온 기록으로 자동 갱신되지는 않습니다.
    id: str
    user_id: Identifier
    device_id: Identifier
    start: AwareDatetime
    end: AwareDatetime
    duration_minutes: Literal[5, 60]
    features: BehaviorFeatures
    # 이전 버전에서 저장된 Window에는 없습니다.
    model_features: NetworkModelFeatures | None = None
    created_at: AwareDatetime = Field(default_factory=now)


class DataRiskRequest(Model):
    # Data 엔진의 입력 계약입니다. text에는 원문, input_origin에는 그 원문의 출처가 들어갑니다.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
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
