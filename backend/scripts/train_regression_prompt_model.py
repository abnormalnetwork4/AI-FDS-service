"""검토한 Regression.ipynb로 한 번 학습해 서버용 모델을 저장합니다."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import joblib

SOURCE_SHA256 = "1d39c6d918ef93e47e2c846d138eea1366591607e3d88915343335c830b576ae"


def train(source: Path, output: Path):
    source, output = source.resolve(), output.resolve()
    raw = (source / "Regression.ipynb").read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("검토한 Regression.ipynb와 다릅니다. 변경 내용을 먼저 확인하세요.")
    if output.exists():
        raise FileExistsError("새 출력 폴더를 지정하세요. 기존 모델은 덮어쓰지 않습니다.")
    if version("scikit-learn") != "1.7.2":
        raise ValueError("원본과 같은 scikit-learn==1.7.2 환경이 필요합니다.")
    notebook = json.loads(raw)
    scope = {"SOURCE_DIRECTORY": source, "__name__": "regression_training"}
    # 설정, 데이터 검증, 학습·예측 정의만 실행합니다. 원본 출력 파일은 쓰지 않습니다.
    for index in (1, 3, 5):
        code = "".join(notebook["cells"][index]["source"])
        if index == 1:
            code = code.replace("PROJECT_DIR = locate_project()", "PROJECT_DIR = SOURCE_DIRECTORY")
        exec(compile(code, f"Regression.ipynb:cell-{index}", "exec"), scope)
    print("승인 CSV의 해시·수량·라벨·중복 검사 중", flush=True)
    records, data_summary = scope["prepare_training_data"]()
    print(f"검사 통과: {len(records):,}건. TF-IDF 및 네 분류기 학습 시작", flush=True)
    with scope["threadpool_limits"](limits=scope["NUMERICAL_THREADS"]):
        bundle = scope["train_regression"](records, verbose=True)
    output.mkdir(parents=True)
    joblib.dump(bundle["vectorizer"], output / "vectorizer.joblib", compress=3)
    for label, classifier in bundle["classifiers"].items():
        joblib.dump(classifier, output / f"{label}.joblib", compress=3)
    names = ["vectorizer.joblib", *(f"{label}.joblib" for label in scope["RISK_LABELS"])]
    hashes = {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in names}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    data_summary["training_file"] = "dataset/integrated_train.csv"
    data_summary["sha256"] = scope["INTEGRATED_TRAIN_APPROVAL"]["sha256"]
    bundle["summary"]["versions"] = {key: version(package) for key, package in (
        ("sklearn", "scikit-learn"), ("numpy", "numpy"), ("scipy", "scipy"), ("joblib", "joblib"))}
    manifest = {
        "format_version": 2, "model_type": "logistic_regression",
        "model_version": "regression-" + digest[:16],
        "source_model_version": scope["FRONTEND_MODEL_VERSION"],
        "source_notebook_sha256": SOURCE_SHA256,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "labels": list(scope["RISK_LABELS"]), "thresholds": bundle["thresholds"],
        "tfidf_parameters": scope["REGRESSION_TFIDF_PARAMS"],
        "files": hashes, "training": bundle["summary"], "data": data_summary,
        "limitations": "합성·AI 잠정 라벨 포함 시범 모델. 정확도 평가·위험 점수 산출은 미실시.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "model_version": manifest["model_version"],
                      "training_rows": len(records)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Regression.ipynb와 dataset이 있는 폴더")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2] / "model" / "prompt" / "regression")
    args = parser.parse_args()
    train(args.source, args.output)
