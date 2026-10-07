"""저장된 사후 분석 결과를 화면에 맞게 변환합니다. 새로운 위험 판정을 만들지 않습니다."""
from typing import Literal

from pydantic import BaseModel, Field

from .contracts import Assessment, CompanyAssessment
from .schemas import Finding, NetworkSession


class Evidence(BaseModel):
    id: str
    code: str
    label: str
    detail: str
    status: Literal["pending", "complete", "error"]
    score: float | None
    detected: bool | None = None
    probability: float | None = None
    threshold: float | None = None
    detection_method: Literal["threshold", "argmax"] | None = None
    window_minutes: int | None = None


class DashboardEvent(BaseModel):
    id: str  # 캡처 ID: 하나의 분석 묶음을 선택하는 키
    session_id: str
    user: str
    device_id: str
    destination: str
    started_at: str
    ended_at: str
    status: Literal["pending", "complete", "error"]
    processing_state: str
    observation_kind: str
    reason: str
    grade: Literal["normal", "caution", "warning", "danger"] | None = None
    score: float | None = None
    confidence: None = None
    override: bool = False
    override_reasons: list[str] = Field(default_factory=list)
    fusion_status: Literal["pending", "complete", "error"] = "pending"
    network_reasons: list[Evidence]
    prompt_reasons: list[Evidence]
    scope: Literal["event", "company"] = "event"
    phase: Literal["open", "closed"] | None = None
    capture_count: int = 0
    prompt_max_score: float | None = None
    prompt_source_capture_id: str | None = None
    prompt_source_user_id: str | None = None
    network_score: float | None = None
    network_contribution: float | None = None
    prompt_missing_count: int = 0
    prompt_error_count: int = 0


class DashboardPage(BaseModel):
    events: list[DashboardEvent]
    total: int
    limit: int
    offset: int


class Explanation(BaseModel):
    text: str
    source: Literal["stored"] = "stored"


def present(raw, session_raw, windows):
    assessment = Assessment.model_validate(raw)
    session = NetworkSession.model_validate(session_raw)
    groups = {"data": [], "network": []}
    for result in assessment.results:
        duration = windows.get(result.window_id, {}).get("duration_minutes")
        # 엔진 전체 오류에 개별 항목이 없더라도 화면에서 오류를 확인할 수 있게 합니다.
        findings = result.findings or [Finding(
            code=result.engine, name=f"{result.engine} engine", status=result.status,
            score=result.score, reason="엔진 결과를 확인해 주세요.",
        )]
        for index, finding in enumerate(findings):
            groups[result.engine].append(Evidence(
                id=f"{result.id}:{index}", code=finding.code,
                label=finding.name + (f" · {duration}분 집계" if duration else ""),
                detail=finding.reason, status=finding.status, score=finding.score,
                window_minutes=duration, detected=finding.detected,
                probability=finding.probability, threshold=finding.threshold,
                detection_method=finding.detection_method,
            ))
    return DashboardEvent(
        id=assessment.id, session_id=session.parent_session_id or assessment.session_id, user=assessment.user_id,
        device_id=session.device_id, destination=session.destination,
        started_at=session.started_at.isoformat(), ended_at=session.ended_at.isoformat(),
        status=assessment.fusion_status if assessment.fusion_status == "complete" else ("error" if assessment.status == "error" else "pending"),
        grade=assessment.final_grade, score=assessment.score, confidence=assessment.confidence,
        override=assessment.override, override_reasons=assessment.override_reasons, fusion_status=assessment.fusion_status,
        processing_state=assessment.processing_state, observation_kind=session.observation_kind, reason=assessment.reason,
        network_reasons=groups["network"], prompt_reasons=groups["data"],
    )


def explain(raw):
    assessment = Assessment.model_validate(raw)
    labels = {"pending": "미분석", "complete": "분석 완료", "error": "오류"}
    grade_labels = {"normal": "정상", "caution": "주의", "warning": "경고", "danger": "위험"}
    verdict = (f"통합 등급 {grade_labels[assessment.final_grade]}, {assessment.score:g}/100점."
               if assessment.final_grade is not None and assessment.score is not None
               else "통합 등급은 미판정이며 안전 판정을 뜻하지 않습니다.")
    if assessment.scoring_scope == "prompt_only":
        verdict = f"개인 통합 등급은 미판정(산정 대상 아님). 회사 구간 {assessment.company_window_id}에서 통합 결과를 조회합니다."
    lines = [f"캡처 {assessment.id}: 처리 {'중' if assessment.processing_state == 'processing' else '종료'}. {verdict}"]
    lines.extend(assessment.override_reasons)
    for result in assessment.results:
        score = f", 엔진 점수 {result.score:g}/{result.score_max}" if result.score is not None else ""
        lines.append(f"{result.engine} ({result.engine_version}): {labels[result.status]}{score}.")
        for finding in result.findings:
            classification = ""
            if finding.detected is not None:
                classification = (f"{'탐지' if finding.detected else '미탐지'}, "
                                  f"모델 예측 확률 {finding.probability:.1%} (위험도 점수 아님). ")
            lines.append(f"{finding.name}: {classification}{finding.reason}")
    lines.append("저장된 결과의 요약이며 외부 AI 호출이나 자동 차단은 수행하지 않습니다.")
    return Explanation(text="\n".join(lines))


def present_company(raw):
    group = CompanyAssessment.model_validate(raw)
    evidence = {"data": [], "network": []}
    for result in group.results:
        for index, finding in enumerate(result.findings):
            evidence[result.engine].append(Evidence(
                id=f"{result.id}:{index}", code=finding.code, label=finding.name,
                detail=finding.reason, status=finding.status, score=finding.score,
                detected=finding.detected, probability=finding.probability, threshold=finding.threshold,
                detection_method=finding.detection_method, window_minutes=5))
    return DashboardEvent(
        id=group.id, session_id=group.id, user="회사 전체", device_id="전체 단말", destination="전체 AI 사용 기록",
        started_at=group.start.isoformat(), ended_at=group.end.isoformat(), scope="company", phase=group.phase,
        status=group.status, fusion_status=group.fusion_status, observation_kind="company_window",
        processing_state="processing" if group.network_revision != group.revision else "finished",
        reason=group.reason, grade=group.final_grade, score=group.score,
        override=group.override, override_reasons=group.override_reasons,
        capture_count=group.capture_count, prompt_max_score=group.prompt_max_score,
        prompt_source_capture_id=group.prompt_source_capture_id, prompt_source_user_id=group.prompt_source_user_id,
        network_score=group.network_score, network_contribution=group.network_contribution,
        prompt_missing_count=group.prompt_missing_count, prompt_error_count=group.prompt_error_count,
        network_reasons=evidence["network"], prompt_reasons=evidence["data"])


def explain_company(raw):
    group = CompanyAssessment.model_validate(raw)
    phase = "진행 중인 구간의 잠정 결과" if group.phase == "open" else "종료된 구간의 결과(지연 수신 시 갱신)"
    lines = [f"회사 전체 {group.start.isoformat()} ~ {group.end.isoformat()} (끝 시각 제외). {phase}.", group.reason]
    if group.score is not None:
        lines.append(f"프롬프트 최고 {group.prompt_max_score:g}/60 + 네트워크 {group.network_score:g} × 0.4 = {group.score:g}/100점. 등급: {group.final_grade}.")
    if group.prompt_source_capture_id:
        lines.append(f"최고 점수 근거 캡처: {group.prompt_source_capture_id}, 사용자: {group.prompt_source_user_id}. 개인의 통합 위험 등급을 뜻하지 않습니다.")
    lines.extend(group.override_reasons)
    for result in group.results:
        lines.extend(f"{f.name}: {f.reason}" for f in result.findings)
    return Explanation(text="\n".join(lines))
