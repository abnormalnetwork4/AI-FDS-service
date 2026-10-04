"""트래픽 경로 밖에서 수집한 관측 자료와 분석 결과의 계약."""
from typing import Literal
import hashlib
from pydantic import AwareDatetime, ConfigDict, Field, model_validator
from .schemas import AIUsageEvent, Count, Identifier, Model, NetworkSession, RiskResult


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


class EventIngest(Model):
    """진행 중 통신에서 발생한 관측 한 건. 바이트는 누적값이 아닌 이번 관측의 증가량입니다."""
    id: Identifier
    session_id: Identifier
    user_id: Identifier
    device_id: Identifier
    occurred_at: AwareDatetime
    destination: str = Field(min_length=1, max_length=253)
    bytes_sent: Count = 0
    bytes_received: Count = 0
    source: Literal["application_log", "collector", "packet_capture", "flow_export"] = "application_log"
    provider: str | None = Field(default=None, min_length=1, max_length=100)
    channel: Literal["web", "api"] = "api"
    prompt: PromptObservation | None = None

    @model_validator(mode="after")
    def prompt_requires_application_event(self):
        if self.prompt is not None and (self.source != "application_log" or self.provider is None):
            raise ValueError("Prompt requires an application_log event with provider")
        return self

    def as_capture(self):
        # 세션은 계속 유지될 수 있으므로 관측마다 고유 저장 ID를 부여합니다.
        observation_id = "observation-" + hashlib.sha256(self.id.encode()).hexdigest()
        session = NetworkSession(
            id=observation_id, parent_session_id=self.session_id, observation_kind="event",
            user_id=self.user_id, device_id=self.device_id, started_at=self.occurred_at,
            ended_at=self.occurred_at, destination=self.destination, source=self.source,
            bytes_sent=self.bytes_sent, bytes_received=self.bytes_received,
        )
        event = AIUsageEvent(id="usage-" + hashlib.sha256(self.id.encode()).hexdigest(),
            session_id=observation_id, user_id=self.user_id, device_id=self.device_id,
            occurred_at=self.occurred_at, provider=self.provider, channel=self.channel,
            request_bytes=self.bytes_sent) if self.provider is not None else None
        return CaptureIngest(id=self.id, session=session, ai_event=event, prompt=self.prompt)


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
