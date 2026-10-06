"""받은 v6의 저장 예측과 서버 예측을 비교합니다. 정답을 읽거나 모델을 바꾸지 않습니다."""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.prompt_engine import AllInOneDataRiskEngine
from app.schemas import DataRiskRequest


def check(source, model_dir):
    root = source / "data_set" / "test_dataset"
    def read(name):
        with (root / name).open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))
    reference = {row["id"]: row for row in read("integrated_ko_100_v6_predictions.csv")}
    inputs = read("integrated_ko_100_v6_input.csv")
    if len(reference) != 100 or len(inputs) != 100 or {r["id"] for r in inputs} != set(reference):
        raise ValueError("100건 입력·저장 예측의 ID가 일치해야 합니다.")
    engine = AllInOneDataRiskEngine(model_dir)
    equal_rows, max_difference = 0, 0.0
    for row in inputs:
        result = engine.analyze(DataRiskRequest(user_id="parity-test", text=row["prompt"]))
        previous = reference[row["id"]]
        equal_rows += all(f.detected == bool(int(previous[f.code])) for f in result.findings)
        max_difference = max(max_difference, max(abs(f.probability - float(previous["probability_" + f.code])) for f in result.findings))
    report = {"model_version": engine.model_version, "reference_rows": len(inputs),
              "all_four_flags_identical_rows": equal_rows, "max_probability_difference": max_difference}
    print(json.dumps(report), flush=True)
    if equal_rows != 100 or max_difference > 1e-6:
        raise ValueError("원본의 저장된 예측과 다릅니다. 임계값을 조정하지 말고 환경·학습 구성을 확인하세요.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--model-dir", type=Path, default=Path(__file__).resolve().parents[1] / "models" / "all-in-one")
    args = parser.parse_args()
    check(args.source, args.model_dir)
