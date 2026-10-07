"""트래픽 경로 밖에서 수집한 관측 자료와 분석 결과의 계약."""
from typing import Literal
import hashlib
from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator
from .schemas import AIUsageEvent, Count, Identifier, Model, NetworkScoreBreakdown, NetworkSession, RiskResult


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
    # Network 모델 입력용 선택 항목입니다. 패킷 수는 바이트와 같이 이번 관측의 증가량입니다.
    packets_sent: Count = 0
    packets_received: Count = 0
    # 아래는 AI 사용 로그 항목이라 provider가 있을 때만 받습니다.
    request_bytes: Count | None = None  # HTTP 요청 본문 크기. 생략 시 bytes_sent 사용
    file_count: Count = 0
    process_name: str | None = Field(default=None, max_length=255)
    tenant: str | None = Field(default=None, min_length=1, max_length=100)
    completed_at: AwareDatetime | None = None
    retry_after_block: bool | None = None

    @model_validator(mode="after")
    def prompt_requires_application_event(self):
        if self.prompt is not None and (self.source != "application_log" or self.provider is None):
            raise ValueError("Prompt requires an application_log event with provider")
        app_fields = (self.request_bytes, self.process_name, self.tenant, self.completed_at, self.retry_after_block)
        if self.provider is None and (self.file_count or any(v is not None for v in app_fields)):
            raise ValueError("AI usage fields require provider")
        return self

    def as_capture(self):
        # 세션은 계속 유지될 수 있으므로 관측마다 고유 저장 ID를 부여합니다.
        observation_id = "observation-" + hashlib.sha256(self.id.encode()).hexdigest()
        session = NetworkSession(
            id=observation_id, parent_session_id=self.session_id, observation_kind="event",
            user_id=self.user_id, device_id=self.device_id, started_at=self.occurred_at,
            ended_at=self.occurred_at, destination=self.destination, source=self.source,
            bytes_sent=self.bytes_sent, bytes_received=self.bytes_received,
            packets_sent=self.packets_sent, packets_received=self.packets_received,
        )
        event = AIUsageEvent(id="usage-" + hashlib.sha256(self.id.encode()).hexdigest(),
            session_id=observation_id, user_id=self.user_id, device_id=self.device_id,
            occurred_at=self.occurred_at, provider=self.provider, channel=self.channel,
            request_bytes=self.bytes_sent if self.request_bytes is None else self.request_bytes,
            file_count=self.file_count, process_name=self.process_name, tenant=self.tenant,
            completed_at=self.completed_at, retry_after_block=self.retry_after_block,
        ) if self.provider is not None else None
        return CaptureIngest(id=self.id, session=session, ai_event=event, prompt=self.prompt)


class Assessment(Model):
    id: Identifier
    user_id: Identifier
    session_id: Identifier
    scoring_scope: Literal["legacy_event", "prompt_only"] = "legacy_event"
    # 이 관측이 속한 사용자·단말 5분 위험 구간 ID입니다.
    risk_window_id: str | None = None
    # 이전 버전(회사 전체 합산) 구간 ID. 기존 기록 보존용이며 새 수집에서는 채우지 않습니다.
    company_window_id: str | None = None
    processing_state: Literal["processing", "finished"] = "processing"
    status: Literal["pending", "complete", "error"] = "pending"
    fusion_status: Literal["pending", "complete", "error"] = "pending"
    final_grade: Literal["normal", "caution", "warning", "danger"] | None = None
    score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    confidence: None = None
    override: bool = False
    override_reasons: list[str] = Field(default_factory=list)
    scoring_policy: str | None = None
    reason: str = "분석 결과를 기다리는 중입니다. 통합 등급은 미판정입니다."
    results: list[RiskResult] = Field(default_factory=list)

    @field_validator("final_grade", mode="before")
    @classmethod
    def legacy_unassessed(cls, value):
        return None if value == "unassessed" else value


class PromptScore(Model):
    """5분 구간에 속한 개별 프롬프트 결과 한 건. 최고 점수 선택 근거를 숨기지 않기 위해 모두 공개합니다."""
    capture_id: str
    user_id: str
    # complete일 때만 점수가 있습니다. pending(누락·미분석)·error는 0점이 아니라 None입니다.
    score: float | None = None
    status: Literal["pending", "complete", "error"]


class WindowResult(Model):
    """고정 5분 구간 결과의 공통 필드. 통합 점수 = 구간 프롬프트 최고 점수 + 네트워크 점수 × 0.4."""
    id: str
    start: AwareDatetime
    end: AwareDatetime
    duration_minutes: Literal[5] = 5
    phase: Literal["open", "closed"] = "open"
    revision: int = 0
    network_revision: int = -1
    capture_count: int = 0
    prompt_complete_count: int = 0
    prompt_missing_count: int = 0
    prompt_error_count: int = 0
    prompt_max_score: float | None = None
    prompt_source_capture_id: str | None = None
    prompt_source_user_id: str | None = None
    # 구간 안 모든 프롬프트 결과(capture_id 오름차순). 통합 점수에는 이 중 최고 점수 한 건만 반영합니다.
    prompt_scores: list[PromptScore] = Field(default_factory=list)
    network_score: float | None = None
    network_contribution: float | None = None
    # 최신 네트워크 결과의 점수 구성. 모델 보고서에 근거가 없던 과거 결과는 None입니다.
    network_score_breakdown: NetworkScoreBreakdown | None = None
    status: Literal["pending", "complete", "error"] = "pending"
    fusion_status: Literal["pending", "complete", "error"] = "pending"
    final_grade: Literal["normal", "caution", "warning", "danger"] | None = None
    score: float | None = None
    confidence: None = None
    override: bool = False
    override_reasons: list[str] = Field(default_factory=list)
    reason: str = "5분 구간 분석 대기"
    results: list[RiskResult] = Field(default_factory=list)


class RiskWindow(WindowResult):
    """한 사용자·한 단말의 고정 5분 구간 위험도. 네트워크 모델 학습 단위(사용자별 5분 창)와 같습니다.

    점수는 보안 담당자의 검토 우선순위용이며 위반을 확정하지 않습니다.
    """
    user_id: Identifier
    device_id: Identifier
    scope: Literal["user_device"] = "user_device"
    scoring_policy: str = "userdevice5m-promptmax60-network40-v3"


class CompanyAssessment(WindowResult):
    """이전 버전(v0.6~0.7)의 회사 전체 합산 구간. 기존 기록 조회용이며 새로 만들거나 갱신하지 않습니다."""
    user_id: Identifier = "company"
    scope: Literal["company"] = "company"
    scoring_policy: str = "company5m-promptmax60-network40-v2"
