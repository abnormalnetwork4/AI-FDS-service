# 프롬프트 모델

## 기본: Regression

`regression`은 2026-10-07에 전달받은 `Regression.ipynb`의 설정과 데이터 검사를 유지해 학습한 서버용 모델입니다. 서버 시작 시 읽으며 요청마다 재학습하지 않습니다.

- 모델 버전: `regression-a6cc47c9d7fff948` (원본 표기 `Regression-tier1-20261007-r1`).
- 공통 TF-IDF: `char_wb`, `ngram_range=(3,5)`, `max_features=150000`, 실제 특징 150,000개.
- LogisticRegression 네 개: `C=3`, `max_iter=1000`, L2·lbfgs·tol은 원본 기본값.
- 네 항목의 임계값은 각각 **0.45 초과**입니다. 0.45와 같으면 미탐지입니다.
- 승인 고유 문장 546,973건. 항목별 알려진 라벨 164,604 / 175,084 / 164,857 / 90,158건으로 학습했습니다. 빈 라벨을 정상(0)으로 바꾸지 않습니다.
- 네 모델 모두 수렴했고 학습 경고가 없습니다. 정확도·오탐률 평가나 튜닝은 수행하지 않았습니다.
- 원본 모델은 확률·탐지 여부만 산출합니다. 서버는 별도 `prompt-product-base10-v1` 정책으로 미탐지 시 0점, 하나 이상 탐지 시 `min(60, 10 + (1 − 탐지 계수의 곱) × 60)`을 소수 첫째 자리로 반올림합니다. 증류·교란 계수는 각각 0.6, 오남용·토큰 낭비는 각각 0.8입니다. 기본 10점은 한 번만 더하며, 네 항목 모두 탐지되면 56.2점입니다. 이는 모델 확률·정확도가 아닌 정책 점수입니다. 항목별 기여도와 개별 판단 근거는 산출하지 않습니다.

배포 파일은 TF-IDF `vectorizer.joblib`, 분류기 네 개의 `.joblib`, `manifest.json`입니다. manifest는 원본 노트북·데이터·모델의 해시, 환경 버전, 라벨별 수량과 수렴 정보를 기록합니다. 원본 학습 CSV와 노트북은 저장소에 복제하지 않습니다. joblib은 검토한 학습 절차로 생성한 서버 관리 파일만 로딩합니다.

원본이 제공한 형식 확인용 5건·20개 확률은 `backend/tests/fixtures/regression_reference.json`에 보관합니다. 재학습 모델의 탐지 여부는 5건 모두 일치하지만 확률은 최대 `0.012923471492145455`(약 1.29%p) 차이가 있습니다. 원본 학습 체크포인트와 전체 실행 환경이 없어 원본 JSON 확률의 완전 재현은 확인하지 못했습니다. 테스트는 원본 JSON과 탐지 여부를 비교하고, 확률이 완전히 같다고 간주하지 않습니다.

같은 재학습 모델을 원본 노트북의 `predict_many`와 서버 어댑터에 넣었을 때는 20개 확률의 최대 차이가 **0**입니다. 이 원본 함수의 출력을 `backend/tests/fixtures/regression_retrained_reference.json`에 보관해 1e-12 허용 차이로 검사합니다. 두 비교 모두 연결 검증이며 탐지 정확도 평가가 아닙니다.

```powershell
# backend 폴더, 최초 제공 원본에서 새 폴더로 재학습
.\.venv\Scripts\python.exe scripts/train_regression_prompt_model.py --source 'C:\경로\All_in_one(regression)' --output '../model/prompt/regression-retrained'
# 동일 체크포인트를 원본 노트북의 예측 함수와 서버 어댑터로 각각 실행해 비교
.\.venv\Scripts\python.exe scripts/check_regression_prompt_parity.py --source 'C:\경로\All_in_one(regression)'
# 실제 모델의 API·저장·대시보드 연결 및 원본 예측 비교
.\.venv\Scripts\python.exe -m pytest tests/test_prompt_engine.py -q
```

기본 모드는 `PROMPT_ENGINE=regression`입니다. `PROMPT_MODEL_DIR`를 지정하면 이 형식의 폴더여야 합니다. 모델 누락이나 형식·해시·scikit-learn 버전 불일치는 시작 오류로 처리하며 다른 모델로 자동 전환하지 않습니다. 변경 전 서버는 재시작해야 새 모델을 사용합니다. 기존 DB 기록은 당시 모델 버전을 유지하고 새 요청부터 새 모델로 분석합니다.

## 반복 횟수 집계 — 엔진 측 연결 인터페이스

`backend/app/prompt_engine.py`의 `analyze_with_occurrences()`에 `Regression.ipynb`의 문장별 집계를 추가했습니다. 기존 `analyze()`는 기존 `RiskResult`만 반환합니다. API·공통 스키마·수집·저장 연결은 변경하지 않았으며, 백엔드에서 새 진입점을 명시적으로 연결해야 합니다. 기본 Regression과 이전 All_in_one 모두 같은 집계 계약을 사용합니다.

```python
result = engine.analyze_with_occurrences(
    DataRiskRequest(user_id="user-1", text=prompt),
    request_id=capture_id,
)
payload = result.model_dump(mode="json")
```

새 반환 형식은 엔진 전용 `PromptRiskResult`입니다. 기존 공통 `RiskResult`에 바로 넣는 대신, 백엔드에서 아래 선택 필드와 nullable 횟수를 응답·저장 형식에 반영해야 합니다.

| 필드 | 의미 |
|---|---|
| `occurrence_status` | `ok` 또는 `error`. 횟수 분석 오류와 기존 전체 분류 상태는 별개입니다. |
| `occurrence_analysis_version` | `sentence-occurrence-v1` |
| `findings[].occurrence_count` | 최초 탐지를 포함한 해당 라벨의 총 횟수 |
| `label_occurrence_counts` | 같은 횟수를 네 라벨의 사전으로 전달. 위 필드와 이중 합산하지 않습니다. |
| `label_sentence_counts` | 전체 판정의 집계 허용 여부와 관계없이 문장에서 탐지된 라벨별 수 |
| `sentence_count`, `sentence_detections` | 문장 수와 원문 위치·확률·탐지 여부·집계 여부. 원문 텍스트는 포함하지 않습니다. |
| `occurrences` | 실제 집계 단위의 라벨·위치·출처·재처리 식별자 `event_id` |
| `occurrence_error` | 분석 실패 또는 제한 초과의 고정 오류 코드·메시지 |

전체 프롬프트에서 탐지된 라벨만 문장별 횟수에 반영합니다. 같은 문장이 다른 위치에서 반복되면 각각 셉니다. 전체에서만 탐지된 라벨은 1회로 유지하고 `source=whole_prompt_fallback`, `sentence_index=null`로 구분합니다. 기존 전체 판정·확률·점수는 횟수 때문에 바꾸지 않습니다.

인용문·코드·URL·이메일·소수점·영문 약어의 내부 구두점을 보호하며, 원문 위치는 Python Unicode 코드 포인트 기준의 `[start, end)`입니다. JavaScript의 UTF-16 문자열 인덱스로 바로 사용하면 안 됩니다. 256문장 초과 또는 보조 분석 실패는 `occurrence_status=error`로 전달하며 횟수와 상세를 모두 `null`로 둡니다. 부분 집계나 0회로 대체하지 않습니다.

`request_id`는 기존 식별자 형식을 따르는 전역 고유 요청 ID를 전달합니다. 같은 ID·원문·위치·라벨·출처로 재처리하면 같은 `event_id`가 나옵니다. 생략하면 매 분석 결과 ID를 사용하므로 별도 호출을 같은 요청으로 합치지 않습니다. 엔진은 요청 간 누적 상태나 영구 중복 제거를 수행하지 않으며, 수신 측에서 `event_id`로 재처리를 구별합니다.

## 이전 버전: All_in_one XGBoost

`PROMPT_ENGINE=all-in-one`을 명시하면 아래 모델을 사용할 수 있습니다. 새 모델은 별도 폴더에 두어 기존 모델과 기록을 보존했습니다.

`all-in-one`은 전달받은 All_in_one.ipynb의 기본 구성으로 2026-10-06에 생성한 서버용 아티팩트입니다. 서버 시작 시 읽으며 요청마다 재학습하지 않습니다.

- 공통 TF-IDF: 학습 문장 546,973건, 특징 91,419개.
- 독립 XGBoost 네 개: AI_steal, prompt_injection, abuse_act, token_waste_repeat.
- 원본 전처리·기본 학습 설정 유지, 항목별 임계값은 0.5 초과.
- 추가 품질 검토·목적 대비 보강 실험과 설정 탐색은 비활성 상태 유지.
- 모델 버전: `all-in-one-5c510f4828e433e4`. 원본 노트북 해시, 파일별 해시, 라이브러리 버전과 학습 수량은 `all-in-one/manifest.json`에 기록.

전달된 v6 입력 100건을 서버 어댑터로 예측해 기존 저장 예측과 비교했습니다. 네 항목의 판정은 100건 모두 일치했고, 400개 확률 값의 최대 차이는 0입니다. 이는 연결 과정에서 출력이 유지됐는지 확인한 결과이며 신규 정확도 평가나 성능 개선 주장이 아닙니다. 원본 시범 모델의 오탐·맥락 판정 한계도 유지됩니다.

```powershell
# backend 폴더에서 실행; source는 원본 노트북과 data_set이 있는 폴더
.\.venv\Scripts\python.exe scripts/check_prompt_parity.py --source 'C:\경로\All_in_one'
```

배포 파일은 TF-IDF `vectorizer.joblib`, 분류기 네 개의 `.ubj`, manifest입니다. 학습 CSV와 원본 프롬프트는 이 폴더에 포함하지 않습니다. joblib은 신뢰하는 학습 절차에서 생성한 파일만 사용해야 하며 API를 통한 모델 업로드·경로 지정은 지원하지 않습니다.

이전 모델 재학습 도구는 `backend/scripts/train_prompt_model.py`입니다. 현재 기본 모델과 설정 변경 방법은 [백엔드 README](../../backend/README.md)의 프롬프트·모델 연결 항목을 참고하세요.
