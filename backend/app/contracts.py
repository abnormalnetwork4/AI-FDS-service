"""트래픽 경로 밖에서 수집한 관측 자료와 분석 결과의 계약."""
from typing import Literal
from pydantic import ConfigDict, Field, model_validator
from .schemas import AIUsageEvent, Identifier, Model, NetworkSession, RiskResult


class PromptObservation(Model):
    # HTTPS 패킷에서 원문을 추측하지 않습니다. 앱 사용 로그 등에서 확보한 경우에만 보냅니다.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    text: str = Field(max_length=131072)
    input_origin: Literal["direct_user", "external_document", "tool_output"] = "direct_user"
    source: Literal["application_log"] = "application_log"


class CaptureIngest(Model):
    # 수집기가 부여한 안정적인 ID입니다. 재전송 시 같은 ID를 사용합니다.
    id: Identifier
    session: NetworkSession
    ai_event: AIUsageEvent | None = None
    prompt: PromptObservation | None = None

    @model_validator(mode="after")
    def validate_links(self):
        if self.session.source == "gateway_application":
            raise ValueError("Passive ingestion requires collector or packet/flow observations")
        event = self.ai_event
        if event is not None:
            if (event.session_id, event.user_id, event.device_id) != (self.session.id, self.session.user_id, self.session.device_id):
                raise ValueError("AI event must reference the same session, user and device")
            if not self.session.started_at <= event.occurred_at <= self.session.ended_at:
                raise ValueError("AI event must occur within the session")
        if self.prompt is not None and event is None:
            raise ValueError("Prompt observations require a linked AI usage event")
        return self


class CaptureRecord(Model):
    # 원문 없이 관측 자료의 연결과 수집 상태만 저장합니다.
    id: Identifier
    user_id: Identifier
    device_id: Identifier
    session_id: Identifier
    ai_event_id: Identifier | None
    source: str
    prompt_status: Literal["available", "unavailable", "too_large", "empty"]


class Assessment(Model):
    id: Identifier
    user_id: Identifier
    session_id: Identifier
    processing_state: Literal["processing", "finished"] = "processing"
    status: Literal["pending", "complete", "error"] = "pending"
    fusion_status: Literal["pending"] = "pending"
    final_grade: Literal["unassessed"] = "unassessed"
    reason: str = "사후 분석 결과입니다. 통합 등급 정책은 미연결이며 통신 허용·차단을 수행하지 않습니다."
    results: list[RiskResult] = Field(default_factory=list)
