"""Model adapters. Stub output intentionally carries no risk score."""
from typing import Protocol

from .schemas import BehaviorWindow, DataRiskRequest, Finding, RiskResult

DATA_CATEGORIES = {
    "sensitive_data": "민감정보 유출",
    "non_business": "업무 목적 외 오남용",
    "model_extraction": "토큰 자원 낭비 / 모델 추출",
    "prompt_manipulation": "모델 교란 / Prompt Injection",
}
NETWORK_CATEGORIES = {
    "N1": "비정상 대량 업로드",
    "N2": "저속·분산 누적 전송",
    "N3": "자동화·요청 폭주",
    "N4": "미승인 AI 목적지",
    "N5": "Gateway·Proxy 우회",
    "N6": "차단 후 우회·재시도",
}


class DataRiskEngine(Protocol):
    def analyze(self, request: DataRiskRequest) -> RiskResult: ...


class NetworkRiskEngine(Protocol):
    def analyze(self, window: BehaviorWindow) -> RiskResult: ...


def pending_findings(categories):
    return [Finding(code=code, name=name, reason="모델 어댑터 연결 대기")
            for code, name in categories.items()]


class StubDataRiskEngine:
    def analyze(self, request: DataRiskRequest) -> RiskResult:
        # The adapter must preserve input_origin; external text is not a user instruction.
        return RiskResult(
            user_id=request.user_id, engine="data", engine_version="stub-v1",
            status="pending", findings=pending_findings(DATA_CATEGORIES),
        )


class StubNetworkRiskEngine:
    def analyze(self, window: BehaviorWindow) -> RiskResult:
        return RiskResult(
            user_id=window.user_id, engine="network", engine_version="stub-v1",
            window_id=window.id, status="pending", findings=pending_findings(NETWORK_CATEGORIES),
        )
