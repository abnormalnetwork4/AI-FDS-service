"""같은 학습 모델을 원본 노트북과 서버로 추론해 비교합니다. 재학습하지 않습니다."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.prompt_engine import RegressionDataRiskEngine
from app.schemas import DataRiskRequest
from train_regression_prompt_model import SOURCE_SHA256


def check(source, model_dir, output=None):
    raw = (source / "Regression.ipynb").read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("검토한 원본 노트북과 다릅니다.")
    scope = {"SOURCE_DIRECTORY": source.resolve(), "__name__": "regression_parity"}
    notebook = json.loads(raw)
    for index in (1, 3, 5):
        code = "".join(notebook["cells"][index]["source"])
        if index == 1:
            code = code.replace("PROJECT_DIR = locate_project()", "PROJECT_DIR = SOURCE_DIRECTORY")
        exec(compile(code, f"Regression.ipynb:cell-{index}", "exec"), scope)
    engine = RegressionDataRiskEngine(model_dir)
    bundle = dict(vectorizer=engine.vectorizer, classifiers=engine.classifiers, thresholds=engine.thresholds)
    reference_path = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "regression_reference.json"
    supplied = json.loads(reference_path.read_text(encoding="utf-8"))
    prompts = [case["text"] for case in supplied["cases"]]
    original_outputs = scope["predict_many"](prompts, bundle)
    cases, adapter_differences, retraining_differences = [], [], []
    for case, original in zip(supplied["cases"], original_outputs, strict=True):
        actual = engine.analyze(DataRiskRequest(user_id="parity", text=case["text"]))
        expected = []
        for finding, old in zip(actual.findings, case["predictions"], strict=True):
            label = finding.code
            assert label == old["label"]
            probability = original["probabilities"][label]
            detected = bool(original["risk_flags"][label])
            assert finding.detected == detected == old["detected"]
            adapter_differences.append(abs(finding.probability - probability))
            retraining_differences.append(abs(probability - old["probability"]))
            expected.append(dict(label=label, detected=detected, probability=probability))
        assert bool(original["normal"]) == case["normal"]
        cases.append(dict(text=case["text"], normal=bool(original["normal"]), predictions=expected))
    maximum = max(adapter_differences)
    if maximum > 1e-12:
        raise ValueError(f"같은 모델의 원본·서버 추론 결과가 다릅니다: {maximum}")
    report = dict(model_version=engine.model_version, source_notebook_sha256=SOURCE_SHA256,
                  matched_cases=len(cases), maximum_adapter_difference=maximum,
                  maximum_supplied_json_difference=max(retraining_differences),
                  note="동일 모델의 원본·서버 추론 비교. 제공 JSON은 재학습 전 출력이며 확률 완전 일치는 보장하지 않음.")
    if output:
        output.write_text(json.dumps(dict(report, cases=cases), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--model-dir", type=Path, default=Path(__file__).resolve().parents[2] / "model" / "prompt" / "regression")
    parser.add_argument("--output", type=Path, help="원본 추론 함수의 결과를 회귀 검증용 JSON으로 저장")
    args = parser.parse_args()
    check(args.source, args.model_dir, args.output)
