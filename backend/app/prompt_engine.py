"""All_in_one의 TF-IDF → 네 XGBoost 분류기 → 임계값 비교를 서버에서 재사용합니다."""
import hashlib
import json
import math
import unicodedata
from importlib.metadata import version
from pathlib import Path
from threading import Lock

from .engines import DATA_CATEGORIES
from .schemas import DataRiskRequest, Finding, RiskResult

LABELS = tuple(DATA_CATEGORIES)
DESCRIPTIONS = {
    "AI_steal": "응답 수집을 통한 모델 학습·복제 목적 분류. 무단 추출을 확정하지 않습니다.",
    "prompt_injection": "기존 지시의 무시·변조 유도 분류. 공격 성공을 뜻하지 않습니다.",
    "abuse_act": "명시적 개인·업무 외 목적 분류. 실제 업무 맥락·회사 정책 위반을 확정하지 않습니다.",
    "token_waste_repeat": "끝없는 반복 등 토큰 낭비 의도 분류. 실제 소비량을 측정하지 않습니다.",
}
ORIGINS = {"direct_user": "직접 입력", "external_document": "외부 문서", "tool_output": "도구 출력"}


class AllInOneDataRiskEngine:
    def __init__(self, directory: Path):
        # 서버가 관리하는 모델만 로딩합니다. 요청으로 모델 경로·파일을 받지 않습니다.
        import joblib
        from xgboost import XGBClassifier

        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["format_version"] != 1 or manifest["labels"] != list(LABELS):
            raise ValueError("Unsupported prompt model format or labels")
        files = {"vectorizer.joblib", *(f"{label}.ubj" for label in LABELS)}
        if set(manifest["files"]) != files:
            raise ValueError("Incomplete prompt model files")
        for name in files:
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != manifest["files"][name]:
                raise ValueError("Prompt model checksum mismatch")
        for package, key in (("scikit-learn", "sklearn"), ("xgboost", "xgboost")):
            if version(package) != manifest["training"]["versions"][key]:
                raise ValueError("Install requirements.txt matching the prompt model versions")
        self.model_version = manifest["model_version"]
        self.thresholds = {label: float(manifest["thresholds"][label]) for label in LABELS}
        if not all(math.isfinite(v) and 0 < v < 1 for v in self.thresholds.values()):
            raise ValueError("Invalid prompt model thresholds")
        self.vectorizer = joblib.load(directory / "vectorizer.joblib")
        self.classifiers = {}
        for label in LABELS:
            classifier = XGBClassifier()
            classifier.load_model(directory / f"{label}.ubj")
            # 추론 시 CPU 과점유를 줄입니다. 학습 설정과 판정 기준은 변경하지 않습니다.
            classifier.set_params(n_jobs=1)
            if list(classifier.classes_) != [0, 1] or classifier.n_features_in_ != len(self.vectorizer.vocabulary_):
                raise ValueError("Mismatched prompt model features or classes")
            self.classifiers[label] = classifier
        self._lock = Lock()

    def analyze(self, request: DataRiskRequest) -> RiskResult:
        # 원본 normalize_prompt와 같은 처리입니다. 문맥이나 새 규칙은 추가하지 않습니다.
        text = unicodedata.normalize("NFKC", request.text).replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            raise ValueError("Prompt must not be blank")
        with self._lock:
            features = self.vectorizer.transform([text])
            probabilities = {label: float(self.classifiers[label].predict_proba(features)[0, 1]) for label in LABELS}
        findings = [Finding(
            code=label, name=DATA_CATEGORIES[label], status="complete",
            detected=probabilities[label] > self.thresholds[label],
            probability=probabilities[label], threshold=self.thresholds[label],
            reason=f"{ORIGINS[request.input_origin]}의 내용 분류. {DESCRIPTIONS[label]} 라벨 설명이며 개별 판단 근거는 미제공.",
        ) for label in LABELS]
        return RiskResult(user_id=request.user_id, engine="data", engine_version=self.model_version,
                          input_origin=request.input_origin, status="complete", findings=findings)


def configured_data_engine():
    import os
    from .engines import StubDataRiskEngine

    mode = os.getenv("PROMPT_ENGINE", "all-in-one")
    if mode == "stub":
        return StubDataRiskEngine()
    if mode != "all-in-one":
        raise ValueError("PROMPT_ENGINE must be all-in-one or stub")
    default = Path(__file__).resolve().parents[2] / "model" / "prompt" / "all-in-one"
    return AllInOneDataRiskEngine(Path(os.getenv("PROMPT_MODEL_DIR", str(default))))
