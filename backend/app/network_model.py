"""model/network 의 XGBoost 네트워크 위험 모델을 NetworkRiskEngine 계약에 연결하는 어댑터."""
# 모델·점수 규칙의 원본은 저장소 루트의 model/network/ 입니다(inference.py, risk_scoring.py, artifacts/).
# 이 파일은 BehaviorWindow → 학습과 같은 24개 피처 표 → 점수·근거 → RiskResult 변환만 담당합니다.
import hashlib
import importlib.util
import os
import sys
from pathlib import Path
from threading import Lock

from .engines import NETWORK_CATEGORIES
from .schemas import BehaviorWindow, Finding, RiskResult

DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "model" / "network"


def load_inference(model_dir: Path):
    # model/network/inference.py 를 패키지 설치 없이 불러옵니다. inference.py가 risk_scoring.py를 함께 불러옵니다.
    spec = importlib.util.spec_from_file_location("network_inference", model_dir / "inference.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class XGBoostNetworkRiskEngine:
    def __init__(self, model_dir: Path | None = None):
        model_dir = Path(model_dir or os.getenv("NETWORK_MODEL_DIR", DEFAULT_MODEL_DIR))
        import pandas as pd  # 모델 의존성은 실제 엔진을 사용할 때만 불러옵니다.
        self.pd = pd
        self.inference = load_inference(model_dir)
        self.model = self.inference.NetworkRiskEngine.load(model_dir / "artifacts")
        digest = hashlib.sha256((model_dir / "artifacts" / "xgb_network_model.json").read_bytes()).hexdigest()
        self.version = "xgb-network-" + digest[:12]
        # 여러 수집 요청이 동시에 분석할 수 있으므로 Booster 호출을 직렬화합니다.
        self.lock = Lock()

    def pending(self, window, reason):
        return RiskResult(user_id=window.user_id, engine="network", engine_version=self.version,
                          window_id=window.id, status="pending",
                          findings=[Finding(code="network_model", name="네트워크 모델 미판정", reason=reason)])

    def analyze(self, window: BehaviorWindow) -> RiskResult:
        if window.duration_minutes != 5:
            # 학습은 5분 구간만 사용했습니다. 최근 1시간 누적은 5분 결과의 입력 피처로 반영됩니다.
            return self.pending(window, "5분 구간 전용 모델. 60분 누적은 5분 분석의 1시간 이력 피처로 반영")
        features = window.model_features
        if features is None or features.request_count == 0:
            # 학습 데이터는 모든 요청에 AI 사용 로그가 있었습니다. 패킷 메타데이터만으로는 입력이 부족합니다.
            return self.pending(window, "구간 내 AI 사용 로그 없음. 요청 단위 피처를 계산할 수 없어 미판정")
        frame = self.pd.DataFrame([{"window_id": window.id, "window_start": window.start.isoformat(),
                                    **features.model_dump()}]).astype({name: float for name in self.model.features})
        with self.lock:
            report = self.model.explain(frame)[0]
            _, matrix = self.model._matrix(frame)
            proba = dict(zip(self.model.classes, self.model.booster.predict(matrix)[0].tolist()))
        return RiskResult(user_id=window.user_id, engine="network", engine_version=self.version,
                          window_id=window.id, status="complete", score=report["network_score"],
                          findings=self.findings(report, proba))

    def findings(self, report, proba):
        threat = report["threat"]
        evidence = "; ".join(e["text"] for e in report["model_evidence"])
        rules = "; ".join(report["rule_evidence"])
        b = report["score_breakdown"]
        parts = ("정상 판정 → 0점" if threat == "normal" else
                 f"기본 {b['base_score']:.0f} + 모델 확신도 {b['probability_score']:.1f} + 재시도 {b['retry_score']:.0f}")
        summary = Finding(
            code="network_score", status="complete", score=report["network_score"],
            name=f"네트워크 위험 점수 [{report['network_grade']}] {threat} {report['threat_name']}",
            reason=(f"{parts} (통합 반영 {report['integrated_contribution']:.1f}/40). 모델 근거: {evidence}"
                    + (f". 규칙 근거: {rules}" if rules else "") + f". {report['note']}"),
        )
        # 위협별 항목 점수는 모델 클래스 확률(%)입니다. N5+N6 동시 클래스는 더 심각한 N6에 표시합니다.
        by_code = {code: proba.get(code, 0.0) for code in NETWORK_CATEGORIES}
        by_code["N6"] = proba.get("N5+N6", 0.0)
        items = [Finding(code=code, name=name, status="complete", score=round(100 * by_code[code], 1),
                         detected=(code == threat), probability=float(by_code[code]), detection_method="argmax",
                         reason=f"모델 확률 {by_code[code]:.1%}" + (" · 판정 위협" if code == threat else "")
                                + (" (N5+N6 동시 클래스)" if code == "N6" else ""))
                 for code, name in NETWORK_CATEGORIES.items()]
        return [summary, *items]
