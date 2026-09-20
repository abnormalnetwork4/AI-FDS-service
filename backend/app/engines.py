"""Model adapters. Stub output intentionally carries no risk score."""
# FDS 위험 탐지 모델의 연결 지점입니다. 직원에게 답변을 생성하는 사내 AI와는 다른 모델입니다.
# 실제 어댑터는 모델 로딩 → 학습 때와 동일한 전처리 → 예측 → RiskResult 변환을 구현해야 합니다.
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
    # request.text가 프롬프트입니다. 내용을 분석한 결과를 공통 형식 RiskResult로 반환하는 약속입니다.
    def analyze(self, request: DataRiskRequest) -> RiskResult: ...


class NetworkRiskEngine(Protocol):
    # window.features는 최근 요청 수·전송량 등의 숫자입니다. 학습 시 사용한 특징 순서·정규화를 맞춰야 합니다.
    def analyze(self, window: BehaviorWindow) -> RiskResult: ...


def pending_findings(categories):
    # 아직 모델이 없는 항목을 명시합니다. pending과 score=None을 '위험 없음'으로 해석하면 안 됩니다.
    return [Finding(code=code, name=name, reason="모델 어댑터 연결 대기")
            for code, name in categories.items()]


class StubDataRiskEngine:
    def analyze(self, request: DataRiskRequest) -> RiskResult:
        # 현재는 모델 없는 자리 표시자(Stub)입니다. 실제 어댑터에서도 input_origin을 유지해야 합니다.
        # 외부 문서나 도구 출력에 적힌 내용을 사용자가 직접 내린 지시로 취급하지 않습니다.
        return RiskResult(
            user_id=request.user_id, engine="data", engine_version="stub-v1",
            status="pending", findings=pending_findings(DATA_CATEGORIES),
        )


class StubNetworkRiskEngine:
    def analyze(self, window: BehaviorWindow) -> RiskResult:
        # N1~N6의 결과 자리를 만들고 어느 Window를 분석하려 했는지 남깁니다. 실제 예측은 수행하지 않습니다.
        return RiskResult(
            user_id=window.user_id, engine="network", engine_version="stub-v1",
            window_id=window.id, status="pending", findings=pending_findings(NETWORK_CATEGORIES),
        )
