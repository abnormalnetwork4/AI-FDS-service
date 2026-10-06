"""백엔드 연결용 추론 모듈 — 저장된 모델로 새 5분 창의 네트워크 점수와 판단 근거를 냅니다.

노트북(xgboost_risk_score_explain.ipynb)의 '저장' 셀이 만든 artifacts/ 를 읽습니다.
    artifacts/xgb_network_model.json   XGBoost 모델 (Booster JSON, 버전 호환이 좋은 형식)
    artifacts/model_meta.json          피처 순서, 클래스 순서, 결측 대체 중앙값

사용 예 (Python):
    from inference import NetworkRiskEngine
    engine = NetworkRiskEngine.load('model/network/artifacts')
    reports = engine.explain(df)          # df: 피처 열 + window_id, window_start (+ retry_count_after_block)
    print(engine.render(reports[0]))

사용 예 (명령줄):
    python model/network/inference.py 입력.csv 출력.json
"""
from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd
import xgboost as xgb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import risk_scoring as rs  # noqa: E402


class NetworkRiskEngine:
    def __init__(self, booster, features, classes, medians):
        self.booster, self.features, self.classes, self.medians = booster, features, classes, medians

    @classmethod
    def load(cls, artifact_dir):
        artifact_dir = Path(artifact_dir)
        meta = json.loads((artifact_dir / 'model_meta.json').read_text(encoding='utf-8'))
        booster = xgb.Booster()
        booster.load_model(str(artifact_dir / 'xgb_network_model.json'))
        return cls(booster, meta['features'], meta['classes'], meta['imputer_medians'])

    def _matrix(self, df):
        missing = set(self.features) - set(df.columns)
        if missing:
            raise ValueError(f'입력에 피처 열이 없습니다: {sorted(missing)}')
        X_raw = df[self.features].astype(float).replace([np.inf, -np.inf], np.nan).reset_index(drop=True)
        X = X_raw.fillna(self.medians)
        return X_raw, xgb.DMatrix(X.to_numpy(), feature_names=self.features)

    def score(self, df):
        """창별 점수 표(DataFrame)를 반환합니다."""
        _, dmat = self._matrix(df)
        proba = self.booster.predict(dmat)
        return rs.score_windows(df, proba, self.classes)

    def explain(self, df):
        """창별 판단 근거 JSON(dict) 목록을 반환합니다."""
        df = df.reset_index(drop=True)
        X_raw, dmat = self._matrix(df)
        proba = self.booster.predict(dmat)
        contribs = self.booster.predict(dmat, pred_contribs=True)
        scored = rs.score_windows(df, proba, self.classes)
        return [rs.build_report(i, df, scored, proba, contribs, X_raw, self.classes, self.features)
                for i in range(len(df))]

    @staticmethod
    def render(report):
        return rs.render_report(report)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit('사용법: python inference.py 입력.csv 출력.json')
    engine = NetworkRiskEngine.load(Path(__file__).resolve().parent / 'artifacts')
    reports = engine.explain(pd.read_csv(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(reports, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(f'{len(reports)}개 창의 판단 근거를 저장했습니다: {sys.argv[2]}')
