"""저장된 사후 분석 결과를 화면에 맞게 변환합니다. 새로운 위험 판정을 만들지 않습니다."""
from typing import Literal

from pydantic import BaseModel

from .contracts import Assessment
from .schemas import Finding, NetworkSession


class Evidence(BaseModel):
    id: str
    code: str
    label: str
    detail: str
    status: Literal["pending", "complete", "error"]
    score: float | None
    window_minutes: int | None = None


class DashboardEvent(BaseModel):
    id: str  # 캡처 ID: 하나의 분석 묶음을 선택하는 키
    session_id: str
    user: str
    device_id: str
    destination: str
    started_at: str
    ended_at: str
    status: Literal["pending", "error"]
    processing_state: str
    observation_kind: str
    reason: str
    score: None = None  # 통합 정책 미연결: 엔진 점수를 임의로 평균내지 않습니다.
    confidence: None = None
    network_reasons: list[Evidence]
    prompt_reasons: list[Evidence]


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
                window_minutes=duration,
            ))
    return DashboardEvent(
        id=assessment.id, session_id=session.parent_session_id or assessment.session_id, user=assessment.user_id,
        device_id=session.device_id, destination=session.destination,
        started_at=session.started_at.isoformat(), ended_at=session.ended_at.isoformat(),
        status="error" if assessment.status == "error" else "pending",
        processing_state=assessment.processing_state, observation_kind=session.observation_kind, reason=assessment.reason,
        network_reasons=groups["network"], prompt_reasons=groups["data"],
    )


def explain(raw):
    assessment = Assessment.model_validate(raw)
    labels = {"pending": "미분석", "complete": "분석 완료", "error": "오류"}
    lines = [f"캡처 {assessment.id}: 처리 {'중' if assessment.processing_state == 'processing' else '종료'}. "
             "통합 등급은 미판정이며 안전 판정을 뜻하지 않습니다."]
    for result in assessment.results:
        score = f", 엔진 점수 {result.score:g}/100" if result.score is not None else ""
        lines.append(f"{result.engine} ({result.engine_version}): {labels[result.status]}{score}.")
        lines.extend(f"{f.name}: {f.reason}" for f in result.findings)
    lines.append("저장된 결과의 요약이며 외부 AI 호출이나 자동 차단은 수행하지 않습니다.")
    return Explanation(text="\n".join(lines))
