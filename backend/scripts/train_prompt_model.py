"""받은 노트북의 기본 학습만 한 번 실행해 서버용 모델을 저장합니다. 원본 파일은 수정하지 않습니다."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib

# 검토한 원본에만 적용합니다. 임의 노트북 전체 실행이나 평가·튜닝·JSON 내보내기는 하지 않습니다.
SOURCE_SHA256 = "8e3132aabc176ba13f3d386e6ac9975565b6ab481a25ec0d69c5ec301d94cd58"


def train(source: Path, output: Path):
    source = source.resolve()
    raw = (source / "All_in_one.ipynb").read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("검토한 All_in_one.ipynb와 다릅니다. 변경 내용을 먼저 확인하세요.")
    if output.exists():
        raise FileExistsError("새 출력 폴더를 지정하세요. 기존 모델은 덮어쓰지 않습니다.")
    notebook = json.loads(raw)
    scope = {"SOURCE_DIRECTORY": source, "__name__": "all_in_one_training"}
    for index in (1, 3, 5, 7, 9):
        code = "".join(notebook["cells"][index]["source"])
        if index == 1:
            code = code.replace("PROJECT_DIR = locate_project()", "PROJECT_DIR = SOURCE_DIRECTORY")
        exec(compile(code, f"All_in_one.ipynb:cell-{index}", "exec"), scope)
    print("원본 기본 데이터 구성 준비 중", flush=True)
    training, testing, data_summary = scope["prepare_datasets"]()
    bundle = scope["train_baseline"](training, testing, scope["MODEL_TFIDF_PARAMS"], scope["MODEL_XGB_PARAMS"])
    output.mkdir(parents=True)
    joblib.dump(bundle["vectorizer"], output / "vectorizer.joblib", compress=3)
    for label, classifier in bundle["classifiers"].items():
        classifier.save_model(output / f"{label}.ubj")
    files = ["vectorizer.joblib", *(f"{label}.ubj" for label in scope["RISK_LABELS"])]
    hashes = {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in files}
    version = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    manifest = {
        "format_version": 1, "model_version": "all-in-one-" + version[:16],
        "source_notebook_sha256": SOURCE_SHA256,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "labels": list(scope["RISK_LABELS"]), "thresholds": scope["MODEL_THRESHOLDS"],
        "files": hashes, "training": bundle["summary"], "data": data_summary,
        "limitations": "AI·규칙 잠정 라벨 기반 시범 모델. 실제 업무 성능·위험 점수·판단 근거는 미검증.",
    }
    # manifest가 마지막에 생성되므로 학습 중인 불완전 폴더는 서버가 로딩하지 않습니다.
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output.resolve()), "model_version": manifest["model_version"],
                      "training_rows": bundle["summary"]["training_rows"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="All_in_one.ipynb와 data_set이 있는 폴더")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2] / "model" / "prompt" / "all-in-one")
    args = parser.parse_args()
    train(args.source, args.output)
