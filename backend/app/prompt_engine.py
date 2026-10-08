"""검증된 TF-IDF와 네 분류기를 로딩합니다. 기본은 Regression.ipynb의 로지스틱 모델입니다."""
import hashlib
import json
import math
import unicodedata
from importlib.metadata import version
from pathlib import Path
from threading import Lock

from pydantic import TypeAdapter

from .engines import DATA_CATEGORIES
from .schemas import DataRiskRequest, Finding, Identifier, RiskResult
from .scoring import PROMPT_POLICY, PROMPT_REPEAT_POLICY, prompt_score
from .prompt_occurrences import (
    OCCURRENCE_ANALYSIS_VERSION, OccurrenceAnalysisLimitError, PromptRiskResult, analyze_sentence_occurrences,
)

LABELS = tuple(DATA_CATEGORIES)
DESCRIPTIONS = {
    "AI_steal": "응답 수집을 통한 모델 학습·복제 목적 분류. 무단 추출을 확정하지 않습니다.",
    "prompt_injection": "기존 지시의 무시·변조 유도 분류. 공격 성공을 뜻하지 않습니다.",
    "abuse_act": "명시적 개인·업무 외 목적 분류. 실제 업무 맥락·회사 정책 위반을 확정하지 않습니다.",
    "token_waste_repeat": "끝없는 반복 등 토큰 낭비 의도 분류. 실제 소비량을 측정하지 않습니다.",
}
ORIGINS = {"direct_user": "직접 입력", "external_document": "외부 문서", "tool_output": "도구 출력"}


class AllInOneDataRiskEngine:
    def __init__(self, directory: Path, *, expected_format=1):
        # 서버가 관리하는 모델만 로딩합니다. 요청으로 모델 경로·파일을 받지 않습니다.
        import joblib
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["format_version"] != expected_format or manifest["labels"] != list(LABELS):
            raise ValueError("Unsupported prompt model format or labels")
        regression = expected_format == 2
        if regression and manifest.get("model_type") != "logistic_regression":
            raise ValueError("Unsupported prompt model type")
        extension = "joblib" if regression else "ubj"
        files = {"vectorizer.joblib", *(f"{label}.{extension}" for label in LABELS)}
        if set(manifest["files"]) != files:
            raise ValueError("Incomplete prompt model files")
        for name in files:
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != manifest["files"][name]:
                raise ValueError("Prompt model checksum mismatch")
        packages = [("scikit-learn", "sklearn")]
        if not regression:
            packages.append(("xgboost", "xgboost"))
        for package, key in packages:
            if version(package) != manifest["training"]["versions"][key]:
                raise ValueError("Install requirements.txt matching the prompt model versions")
        self.model_version = manifest["model_version"]
        self.thresholds = {label: float(manifest["thresholds"][label]) for label in LABELS}
        if not all(math.isfinite(v) and 0 < v < 1 for v in self.thresholds.values()):
            raise ValueError("Invalid prompt model thresholds")
        self.vectorizer = joblib.load(directory / "vectorizer.joblib")
        self.classifiers = {}
        for label in LABELS:
            if regression:
                from sklearn.linear_model import LogisticRegression
                classifier = joblib.load(directory / f"{label}.joblib")
                if not isinstance(classifier, LogisticRegression):
                    raise ValueError("Expected LogisticRegression classifier")
            else:
                from xgboost import XGBClassifier
                classifier = XGBClassifier()
                # XGBoost의 네이티브 파일 열기 대신 Python으로 한글 경로를 처리합니다.
                classifier.load_model(bytearray((directory / f"{label}.ubj").read_bytes()))
                # 추론 시 CPU 과점유를 줄입니다. 학습 설정과 판정 기준은 변경하지 않습니다.
                classifier.set_params(n_jobs=1)
            if list(classifier.classes_) != [0, 1] or classifier.n_features_in_ != len(self.vectorizer.vocabulary_):
                raise ValueError("Mismatched prompt model features or classes")
            self.classifiers[label] = classifier
        self._lock = Lock()

    def _predict_many(self, prompts):
        # 원본 normalize_prompt와 같은 처리입니다. 문맥이나 새 규칙은 추가하지 않습니다.
        cleaned = [unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n").strip()
                   for text in prompts]
        if not cleaned or any(not text for text in cleaned):
            raise ValueError("Prompt must not be blank")
        with self._lock:
            features = self.vectorizer.transform(cleaned)
            matrices = {label: self.classifiers[label].predict_proba(features) for label in LABELS}
        outputs = []
        for index in range(len(cleaned)):
            probabilities = {label: float(matrices[label][index, 1]) for label in LABELS}
            if any(not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities.values()):
                raise ValueError("Prompt model returned invalid probabilities")
            outputs.append(dict(probabilities=probabilities,
                                risk_flags={label: probabilities[label] > self.thresholds[label] for label in LABELS}))
        return tuple(outputs)

    def analyze(self, request: DataRiskRequest) -> RiskResult:
        probabilities = self._predict_many([request.text])[0]["probabilities"]
        findings = [Finding(
            code=label, name=DATA_CATEGORIES[label], status="complete",
            detected=probabilities[label] > self.thresholds[label],
            probability=probabilities[label], threshold=self.thresholds[label],
            detection_method="threshold",
            reason=f"{ORIGINS[request.input_origin]}의 내용 분류. {DESCRIPTIONS[label]} 라벨 설명이며 개별 판단 근거는 미제공.",
        ) for label in LABELS]
        return RiskResult(user_id=request.user_id, engine="data", engine_version=self.model_version,
                          input_origin=request.input_origin, status="complete", findings=findings,
                          score=prompt_score(findings), score_max=60, scoring_policy=PROMPT_POLICY)

    def analyze_with_occurrences(self, request: DataRiskRequest, *, request_id: str | None = None) -> PromptRiskResult:
        """백엔드에서 명시적으로 연결할 반복 집계 진입점입니다. 기존 analyze 계약은 유지합니다."""
        if request_id is not None:
            request_id = TypeAdapter(Identifier).validate_python(request_id)
        result = self.analyze(request)
        payload = result.model_dump()
        payload["source_event_id"] = request_id
        try:
            analysis = analyze_sentence_occurrences(
                request.text, result.findings, self._predict_many, request_id or result.id,
            )
            counted_findings = [finding.model_dump() | {"occurrence_count": analysis["label_occurrence_counts"][finding.code]}
                                for finding in result.findings]
            # 반복 횟수를 점수에 반영합니다(같은 곱셈식, 라벨당 최대 3회). 탐지 여부·확률은 바꾸지 않습니다.
            score = prompt_score([Finding.model_validate(f) for f in counted_findings])
            return PromptRiskResult.model_validate(payload | analysis | {
                "findings": counted_findings, "score": score, "scoring_policy": PROMPT_REPEAT_POLICY})
        except OccurrenceAnalysisLimitError:
            error = {"code": "occurrence_limit_exceeded", "message": "문장 분석의 길이 또는 문장 수 제한을 초과함."}
        except Exception:
            # 보조 분석 오류는 기존 전체 판정·점수를 유지하며 원문·예외 상세를 노출하지 않습니다.
            error = {"code": "occurrence_analysis_failed", "message": "반복 횟수 분석 실패. 0회 판정이 아님."}
        return PromptRiskResult.model_validate(payload | {
            "occurrence_status": "error", "occurrence_analysis_version": OCCURRENCE_ANALYSIS_VERSION,
            "occurrence_error": error,
        })


class RegressionDataRiskEngine(AllInOneDataRiskEngine):
    def __init__(self, directory: Path):
        super().__init__(directory, expected_format=2)


def configured_data_engine():
    import os
    from .engines import StubDataRiskEngine

    mode = os.getenv("PROMPT_ENGINE", "regression")
    if mode == "stub":
        return StubDataRiskEngine()
    engines = {"regression": RegressionDataRiskEngine, "all-in-one": AllInOneDataRiskEngine}
    if mode not in engines:
        raise ValueError("PROMPT_ENGINE must be regression, all-in-one or stub")
    default = Path(__file__).resolve().parents[2] / "model" / "prompt" / mode
    return engines[mode](Path(os.getenv("PROMPT_MODEL_DIR", str(default))))
