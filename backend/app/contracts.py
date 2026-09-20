"""Shared contracts between the independent Gateway, model wrapper and FDS."""
from typing import Literal

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from .schemas import Count, Identifier, Model, RiskResult


class ChatRequest(Model):
    # Demo identity only. A deployed Gateway must derive this from verified SSO.
    user_id: Identifier
    device_id: Identifier
    model: Identifier
    text: str = Field(min_length=1, max_length=16384)
    input_origin: Literal["direct_user", "external_document", "tool_output"] = "direct_user"


class ModelRequest(Model):
    model: Identifier
    text: str = Field(min_length=1, max_length=16384)
    input_origin: Literal["direct_user", "external_document", "tool_output"] = "direct_user"


class ModelReply(Model):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    text: str = Field(max_length=65536)
    mode: Literal["demo", "model"]


class GatewayAudit(Model):
    id: Identifier
    user_id: Identifier
    device_id: Identifier
    session_id: Identifier
    model: Identifier
    started_at: AwareDatetime
    ended_at: AwareDatetime
    request_bytes: Count
    response_bytes: Count = 0
    policy_action: Literal["allow", "block"]
    policy_reason: Literal["model_allowed", "model_not_allowed", "request_too_large"]
    outcome: Literal["forwarding", "completed", "blocked", "upstream_error"]
    fds_delivery: Literal["pending", "delivered", "failed"] = "pending"

    @model_validator(mode="after")
    def consistent_audit(self):
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be >= started_at")
        if (self.policy_action == "block") != (self.outcome == "blocked"):
            raise ValueError("Blocked policy must have blocked outcome")
        if (self.policy_action == "allow") != (self.policy_reason == "model_allowed"):
            raise ValueError("Policy action and reason disagree")
        return self


class GatewayReply(Model):
    request_id: str
    action: Literal["allow", "block"]
    reason: str
    outcome: str
    answer: str | None = None
    model_mode: Literal["demo", "model"] | None = None
    # This is a dispatch status at response time, not an FDS verdict.
    fds_delivery: Literal["pending"] = "pending"


class FDSIngest(Model):
    audit: GatewayAudit
    text: str = Field(min_length=1, max_length=16384)
    input_origin: Literal["direct_user", "external_document", "tool_output"]

    @model_validator(mode="after")
    def consistent_payload(self):
        if len(self.text.encode("utf-8")) != self.audit.request_bytes:
            raise ValueError("Prompt byte count must match the Gateway audit")
        if self.audit.outcome == "forwarding":
            raise ValueError("FDS accepts finalized requests only")
        return self


class Assessment(Model):
    id: Identifier
    user_id: Identifier
    session_id: Identifier
    policy_action: Literal["allow", "block"]
    status: Literal["pending", "complete", "error"]
    fusion_status: Literal["pending"] = "pending"
    final_grade: Literal["unassessed"] = "unassessed"
    reason: str = "통합 위험 등급 정책 연결 대기. Gateway 정책 결정과 FDS 위험 판단은 별개입니다."
    results: list[RiskResult]
