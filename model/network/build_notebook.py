"""xgboost_risk_score_explain.ipynb 생성 스크립트. 재실행하면 노트북의 편집 내용과 출력이 초기화됩니다."""
from pathlib import Path
import nbformat as nbf

md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
cells = [
md("""# XGBoost 네트워크 위험 점수 + 판단 근거 — 회사 전체 대상 (개인 구분 없음)

회의 결정에 따라 **개인 기준선 없이 회사 전체 창을 하나의 모델로** 판정합니다. `user_id`·개인 기준선 피처는 입력에도, 점수에도, 근거 문장에도 쓰지 않습니다.

```
네트워크 점수  = 기본 점수 + (20 × 모델 확률) + 재시도 가산점      (최대 100점)
통합 반영 점수 = 네트워크 점수 × 0.4                              (최대 40점, 프롬프트 60점과 합산)
```

| 기본 점수 | N1 대량 업로드 | N2 저속 누적 전송 | N3 자동화 폭주 | N4 미승인 목적지 | N5 Gateway 우회 | N6 차단 후 재시도 |
| --- | --- | --- | --- | --- | --- | --- |
| 점수 | 50 | 45 | 30 | 40 | 50 | 60 |

- **판정 위협**: 가장 높은 확률의 클래스. `N5+N6`는 더 심각한 N6로 계산. 정상 판정은 0점.
- **모델 확신도**: 20 × 판정 클래스의 `predict_proba` 값(보정 전).
- **재시도 가산점**: Gateway 정책 로그의 차단 후 재시도 횟수 × 5점(최대 10점). 모델 입력이 아니라 규칙 근거로만 사용.
- **반복 가산점**: 같은 행위자 식별이 필요하므로 기본값은 끔(`REPEAT_KEY=None`).
- **등급**: 0–29 정상 / 30–49 주의 / 50–69 경고 / 70–100 위험. 통합 시 프롬프트 ≥ 50(60점 만점) 또는 네트워크 반영 ≥ 35(40점 만점)이면 무조건 위험.
- 설정값은 `risk_scoring.py` 맨 위에 있습니다. 모델 설정은 `model/xgboost_baseline.ipynb`와 같습니다(트리 100, seed 42, 튜닝 없음).
- 판단 근거는 XGBoost 내장 `pred_contribs=True`(정확한 TreeSHAP)로 계산합니다. 별도 `shap` 패키지가 필요 없습니다.

필요 패키지: `pandas numpy scikit-learn xgboost ipykernel`"""),
code("""from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd
import xgboost as xgb
from IPython.display import display
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier

ROOT = next(p for p in [Path.cwd(), *Path.cwd().parents]
            if (p / '데이터셋' / '증강 데이터셋_v3' / 'reinforced_data_model' / 'reinforced_model_train.csv').exists())
sys.path.insert(0, str(ROOT / 'model'))
sys.path.insert(0, str(ROOT / 'model_test'))
from baseline_common import SEED, CLASSES, FEATURES as BEHAVIOR_FEATURES, load_splits, classification_metrics
import risk_scoring as rs

# 행동 피처 24개 (model/ 기본 모델과 동일). 개인 기준선 피처 7개는 쓰지 않습니다.
# 마지막 4개(최근 1시간 누적)는 같은 출발지의 직전 1시간 집계입니다. 이것까지 빼려면 아래 값을 True로 바꿉니다.
DROP_1H_HISTORY = False
HISTORY_1H = ['user_upload_bytes_observed_1h', 'user_request_count_observed_1h',
              'history_coverage_seconds_1h', 'history_complete_1h']
FEATURES = [f for f in BEHAVIOR_FEATURES if not (DROP_1H_HISTORY and f in HISTORY_1H)]

pd.set_option('display.max_colwidth', 120)
print(f'입력 피처 {len(FEATURES)}개')
print('기본 점수:', rs.BASE_SCORE)
print(f'확률 가중치 {rs.PROB_WEIGHT}, 재시도 회당 {rs.RETRY_POINT}점(최대 {rs.RETRY_MAX}), 반복 기준 열 {rs.REPEAT_KEY}, '
      f'통합 비중 {rs.INTEGRATION_WEIGHT}')"""),
md("""## 1. 데이터와 모델 학습

`model/baseline_common.py`의 파일·분할·누출 검사(`load_splits`)를 그대로 씁니다. 사용자 ID는 입력에 넣지 않습니다."""),
code("""data, split_summary, class_distribution = load_splits(ROOT)
(X_train, y_train, train_df), (X_val, y_val, val_df), (X_test, y_test, test_df) = (
    data['train'], data['validation'], data['test'])
X_train, X_val, X_test = X_train[FEATURES], X_val[FEATURES], X_test[FEATURES]
val_df, test_df = val_df.reset_index(drop=True), test_df.reset_index(drop=True)
X_val, X_test = X_val.reset_index(drop=True), X_test.reset_index(drop=True)

model = Pipeline([
    ('imputer', SimpleImputer(strategy='median')),
    ('classifier', XGBClassifier(objective='multi:softprob', num_class=len(CLASSES),
                                 n_estimators=100, random_state=SEED, n_jobs=4)),
]).set_output(transform='pandas')          # 피처 이름을 모델에 그대로 넘겨 SHAP 근거와 맞춥니다.
model.fit(X_train, y_train)
assert list(model.named_steps['classifier'].classes_) == list(range(len(CLASSES)))

proba_val, proba_test = model.predict_proba(X_val), model.predict_proba(X_test)
val_summary, _, _ = classification_metrics(y_val, proba_val.argmax(axis=1))
test_summary, test_report, test_matrix = classification_metrics(y_test, proba_test.argmax(axis=1))
display(pd.DataFrame({'validation': val_summary, 'test': test_summary}).T.round(3))
display(test_report.loc[CLASSES, ['precision', 'recall', 'f1-score', 'support']].round(3))
display(test_matrix)"""),
md("""## 2. 네트워크 위험 점수 계산

`rs.score_windows(원본 표, 모델 확률, 클래스 목록)`이 창마다 점수를 계산합니다.

| 열 | 의미 |
| --- | --- |
| `threat` | 판정 위협 (N5+N6 → N6) |
| `base_score` | 기본 점수 |
| `probability_score` | 모델 확신도 = 20 × 모델 확률 |
| `retry_count`, `retry_score` | 차단 후 재시도 횟수(정책 로그), 재시도 가산점 |
| `network_score`, `network_grade` | 네트워크 점수(100점), 등급 |
| `integrated_contribution` | 통합 반영 점수 = 네트워크 점수 × 0.4 |"""),
code("""scored_val = rs.score_windows(val_df, proba_val, CLASSES)
scored_test = rs.score_windows(test_df, proba_test, CLASSES)

cols = ['threat', 'base_score', 'model_probability', 'probability_score', 'retry_count', 'retry_score',
        'network_score', 'network_grade', 'integrated_contribution']
display(pd.concat([test_df[['window_id', 'window_start']], scored_test[cols]], axis=1).head(12).round(2))"""),
md("""### 2.1 실제 클래스별 등급 분포 (Test)

위험 등급 정답은 없으므로 정확도 평가가 아니라 **분포 확인**입니다."""),
code("""true_class = pd.Series(np.array(CLASSES)[y_test], name='실제 클래스')
display(pd.crosstab(true_class, scored_test.network_grade.rename('network_grade'))
        .reindex(index=CLASSES, columns=list(rs.GRADE_NAMES), fill_value=0))

summary = pd.concat([true_class, scored_test[['network_score', 'integrated_contribution']]], axis=1) \\
    .groupby('실제 클래스').median().reindex(CLASSES).round(1)
summary.columns = ['네트워크 점수 중앙값', '통합 반영 점수 중앙값']
display(summary)

is_threat = true_class.to_numpy() != 'normal'
alert = scored_test.network_grade.to_numpy() != rs.GRADE_NAMES[0]
print(f'위협 창 중 주의 이상: {alert[is_threat].mean():.3f}  /  정상 창 중 주의 이상(오경보): {alert[~is_threat].mean():.3f}')
print(f'네트워크 점수 최댓값: {scored_test.network_score.max():.1f}  →  통합 반영 {scored_test.integrated_contribution.max():.1f}/40')"""),
md("""## 3. 판단 근거 (설명 가능성)

### 3.1 전체 기준 설명 — 위협별로 판정에 많이 쓰인 피처

회사 전체 Test 창에서, 각 클래스 판정에 대한 TreeSHAP 절댓값 평균이 큰 피처 상위 5개입니다.
"이 모델은 N1을 주로 무엇을 보고 판단하는가"에 대한 답이며, 보고서·발표용 근거로 쓸 수 있습니다."""),
code("""booster = model.named_steps['classifier'].get_booster()
X_test_imputed = model.named_steps['imputer'].transform(X_test)
dtest = xgb.DMatrix(X_test_imputed, feature_names=FEATURES)
contrib_test = booster.predict(dtest, pred_contribs=True)          # (창, 클래스, 피처 + bias)
assert contrib_test.shape == (len(X_test), len(CLASSES), len(FEATURES) + 1)
np.testing.assert_allclose(contrib_test.sum(axis=2), booster.predict(dtest, output_margin=True), rtol=1e-3, atol=1e-3)
print('TreeSHAP 합계 = 모델 원점수 확인 완료')

global_rows = []
for c, cls in enumerate(CLASSES):
    mean_abs = pd.Series(np.abs(contrib_test[:, c, :-1]).mean(axis=0), index=FEATURES).sort_values(ascending=False)
    global_rows.append({'클래스': cls, **{f'{k + 1}위': f'{rs.feature_name(f)} ({v:.2f})'
                                          for k, (f, v) in enumerate(mean_abs.head(5).items())}})
display(pd.DataFrame(global_rows).set_index('클래스'))"""),
md("""### 3.2 창별 설명 — 대시보드 표시 문장

창마다 ① 점수 분해(기본 + 모델 확신도 + 재시도), ② 모델 확률과 다른 후보, ③ 판정을 **지지**한 피처 상위 3개와 가장 크게 **억제**한 피처 1개(TreeSHAP), ④ 규칙 근거를 붙입니다.
SHAP 값은 모델 원점수(logit) 기준 기여도입니다. 확률 변화량이나 인과관계로 설명하지 않습니다."""),
code("""reports = [rs.build_report(i, test_df, scored_test, proba_test, contrib_test, X_test, CLASSES, FEATURES)
           for i in range(len(test_df))]
print('생성한 근거 리포트 수:', len(reports))

examples = []
for cls in ['N1', 'N2', 'N3', 'N4', 'N5', 'N5+N6', 'normal']:
    idx = np.flatnonzero((true_class.to_numpy() == cls) & (scored_test.predicted_class.to_numpy() == cls))
    if len(idx):
        examples.append(int(idx[len(idx) // 2]))     # 중간쯤의 대표 사례

for i in examples:
    print(f'실제 클래스: {true_class.iloc[i]}  |  window_id: {reports[i]["window_id"]}')
    print(rs.render_report(reports[i]))
    print()"""),
md("### 3.3 API·대시보드로 넘길 JSON 예시"),
code("""print(json.dumps(reports[examples[0]], ensure_ascii=False, indent=2, default=str))"""),
md("""## 4. 회사 전체 위험도 (시간대별)

개인을 구분하지 않으므로 대시보드의 최상위 지표는 **시간대별 회사 전체 위험도**입니다.
`company_score`는 그 시간대 창 중 가장 높은 네트워크 점수(가장 심각한 사례 기준)이고, 등급별 창 수와 주요 위협을 함께 보여 줍니다."""),
code("""company_5m = rs.company_summary(test_df, scored_test, freq='5min')
display(company_5m.head(12))
print('5분 시간대별 회사 등급 분포:')
display(company_5m.company_grade.value_counts().reindex(list(rs.GRADE_NAMES), fill_value=0).to_frame('시간대 수'))

company_1h = rs.company_summary(test_df, scored_test, freq='1h')
display(company_1h)"""),
md("""## 5. 통합 등급 (프롬프트 60 + 네트워크 40)

`rs.integrate(프롬프트 점수, 네트워크 통합 반영 점수)`가 최종 등급을 냅니다.
**프롬프트 ≥ 50 또는 네트워크 반영 ≥ 35**이면 합계와 관계없이 `위험`입니다.

아래는 시연 시나리오 예시입니다. 네트워크 값은 Test 창의 실제 점수를 쓰고, **프롬프트 점수는 가상값**입니다(프롬프트 엔진 연결 전)."""),
code("""def pick(cls):
    idx = np.flatnonzero((true_class.to_numpy() == cls) & (scored_test.predicted_class.to_numpy() == cls))
    return int(idx[len(idx) // 2])

scenarios = [
    ('정상 사용',                    pick('normal'), 10),
    ('자동화 호출 + 평범한 프롬프트',  pick('N3'),     12),
    ('대량 업로드 + 내부 문서 일부',   pick('N1'),     25),
    ('차단 후 재시도 + 민감정보 다수', pick('N5+N6'),  40),
    ('네트워크 정상 + 프롬프트 고위험', pick('normal'), 52),
]
rows = []
for name, i, prompt in scenarios:
    net = scored_test.iloc[i]
    res = rs.integrate(prompt, net.integrated_contribution)
    rows.append({'시나리오': name, '네트워크 판정': net.threat, '네트워크 점수': round(net.network_score, 1),
                 '네트워크 반영(/40)': round(net.integrated_contribution, 1), '프롬프트(/60, 가상)': prompt,
                 '합계': res['total_score'], '최종 등급': res['grade'], '강제 위험 사유': '; '.join(res['override_reasons']) or '-'})
display(pd.DataFrame(rows))

# 네트워크 강제 위험(≥35/40)은 네트워크 점수 87.5 이상이 필요합니다. 현재 설정에서 도달 가능한 경우만 확인합니다.
best = rs.BASE_SCORE['N6'] + rs.PROB_WEIGHT + rs.RETRY_MAX
print(f'네트워크 점수 이론상 최댓값 = N6 {rs.BASE_SCORE["N6"]} + {rs.PROB_WEIGHT} + 재시도 최대 {rs.RETRY_MAX} = {best}'
      f' → 반영 {best * rs.INTEGRATION_WEIGHT:.0f}/40')
print('Test에서 네트워크 반영 ≥ 35인 창 수:', int((scored_test.integrated_contribution >= rs.NETWORK_OVERRIDE).sum()))"""),
md("""## 6. 저장 (백엔드 연결용)

`model_test/artifacts/`에 저장합니다. `inference.py`가 이 파일을 읽어 새 창을 점수화합니다.
저장 후 `inference.py`로 다시 계산한 결과가 노트북 결과와 같은지 확인합니다."""),
code("""out = ROOT / 'model_test' / 'artifacts'
out.mkdir(parents=True, exist_ok=True)
booster.save_model(str(out / 'xgb_network_model.json'))
imputer = model.named_steps['imputer']
meta = {'features': FEATURES, 'classes': CLASSES,
        'imputer_medians': {f: float(v) for f, v in zip(FEATURES, imputer.statistics_)},
        'base_score': rs.BASE_SCORE, 'prob_weight': rs.PROB_WEIGHT, 'integration_weight': rs.INTEGRATION_WEIGHT,
        'xgboost_version': xgb.__version__, 'seed': SEED, 'n_estimators': 100,
        'test_macro_f1': round(float(test_summary['combination_macro_f1']), 4),
        'test_accuracy': round(float(test_summary['accuracy']), 4)}
(out / 'model_meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
pd.concat([test_df[['window_id', 'window_start']], scored_test], axis=1).to_csv(
    out / 'test_network_scores.csv', index=False, encoding='utf-8-sig')
company_5m.to_csv(out / 'test_company_risk_5min.csv', index=False, encoding='utf-8-sig')
(out / 'test_evidence_examples.json').write_text(
    json.dumps([reports[i] for i in examples], ensure_ascii=False, indent=1, default=str), encoding='utf-8')

from inference import NetworkRiskEngine
engine = NetworkRiskEngine.load(out)
check = engine.score(test_df)
np.testing.assert_allclose(check.network_score.to_numpy(), scored_test.network_score.to_numpy(), atol=1e-4)
assert engine.explain(test_df.iloc[[examples[0]]])[0]['model_evidence'] == reports[examples[0]]['model_evidence']
print('저장 완료:', sorted(p.name for p in out.iterdir()))
print('inference.py 재계산 결과 = 노트북 결과 확인 완료')"""),
md("""## 7. 한계

- 기본 점수·확률 가중치·재시도 가산점·등급 경계는 회의 결정값이며 데이터로 검증한 값이 아닙니다. 위험 등급 정답이 없어 등급 정확도는 평가할 수 없습니다.
- 모델 확률은 보정하지 않은 값입니다. 실제 위험 확률로 해석하지 않습니다.
- 재시도 가산점의 원천(`retry_count_after_block`)은 이 데이터에서 N6 정답과 완전히 겹칩니다. 효과를 검증할 수 없고, 계산이 동작하는지 보는 시연입니다.
- 최근 1시간 누적 피처 4개는 같은 출발지의 직전 이력을 모아야 계산됩니다. 개인 기준선은 아니지만 출발지 구분이 전제입니다(`DROP_1H_HISTORY`로 제외 가능).
- 7클래스 구조라 N1+N3 같은 복합 위협은 표현할 수 없습니다. 단일 seed, 튜닝 없음."""),
]
nb = nbf.v4.new_notebook(cells=cells, metadata={
    'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
    'language_info': {'name': 'python'}})
nbf.write(nb, Path(__file__).resolve().parent / 'xgboost_risk_score_explain.ipynb')
print('노트북 생성 완료')
