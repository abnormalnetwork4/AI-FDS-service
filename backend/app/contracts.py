"""Shared contracts between the independent Gateway, model wrapper and FDS."""
# 서버끼리 주고받는 JSON의 모양을 정의합니다. Model은 이 프로젝트의 데이터 검증 기반 클래스입니다.
# 필드 이름과 타입이 맞지 않는 HTTP 입력은 FastAPI가 검증 단계에서 422로 거절합니다.
from typing import Literal

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from .schemas import Count, Identifier, Model, RiskResult


class ChatRequest(Model):
    # 채팅 화면 → Gateway. 현재 사용자 ID는 데모 입력값이며 실제 서비스에서는 검증된 SSO 정보로 대체해야 합니다.
    user_id: Identifier
    device_id: Identifier
    model: Identifier
    text: str = Field(min_length=1, max_length=16384)  # 직원이 입력한 프롬프트. 여기의 상한은 글자 수입니다.
    input_origin: Literal["direct_user", "external_document", "tool_output"] = "direct_user"


class ModelRequest(Model):
    # Gateway → 사내 생성형 AI. 자체 정의한 계약이므로 실제 모델 API에 맞춘 연결 코드가 필요합니다.
    model: Identifier
    text: str = Field(min_length=1, max_length=16384)
    input_origin: Literal["direct_user", "external_document", "tool_output"] = "direct_user"


class ModelReply(Model):
    # 사내 생성형 AI → Gateway. mode로 통신 테스트용 데모 답변과 실제 모델 답변을 구분합니다.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    text: str = Field(max_length=65536)
    mode: Literal["demo", "model"]


class GatewayAudit(Model):
    # 원문 대신 누가 언제 어떤 모델을 요청했고 어떻게 처리됐는지를 저장하는 기록입니다.
    # policy_action은 허용/차단 결정, outcome은 실제 호출 결과, fds_delivery는 분석 자료 전달 상태입니다.
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
    # Gateway → 채팅 화면. 차단이나 모델 오류 시 answer는 None이며 request_id로 처리 내역을 조회합니다.
    request_id: str
    action: Literal["allow", "block"]
    reason: str
    outcome: str
    answer: str | None = None
    model_mode: Literal["demo", "model"] | None = None
    # 응답 시점에는 FDS 전달을 예약한 상태입니다. FDS의 위험 판정 결과를 뜻하지 않습니다.
    fds_delivery: Literal["pending"] = "pending"


class FDSIngest(Model):
    # Gateway → FDS. 로그와 원문을 함께 받아 분석하지만 DB에는 원문을 보관하지 않습니다.
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
    # 한 요청의 Data 1개·Network 2개 결과 묶음입니다. final_grade를 계산하는 통합 정책은 아직 미연결입니다.
    id: Identifier
    user_id: Identifier
    session_id: Identifier
    policy_action: Literal["allow", "block"]
    status: Literal["pending", "complete", "error"]
    fusion_status: Literal["pending"] = "pending"
    final_grade: Literal["unassessed"] = "unassessed"
    reason: str = "통합 위험 등급 정책 연결 대기. Gateway 정책 결정과 FDS 위험 판단은 별개입니다."
    results: list[RiskResult]
