from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:@-]+$")]
Count = Annotated[int, Field(ge=0, strict=True)]
Score = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


def now() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NetworkSession(Model):
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
    user_id: Identifier
    device_id: Identifier
    start: AwareDatetime
    duration_minutes: Literal[5, 60] = 5


class BehaviorFeatures(Model):
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
    id: str
    user_id: Identifier
    device_id: Identifier
    start: AwareDatetime
    end: AwareDatetime
    duration_minutes: Literal[5, 60]
    features: BehaviorFeatures
    created_at: AwareDatetime = Field(default_factory=now)


class DataRiskRequest(Model):
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
