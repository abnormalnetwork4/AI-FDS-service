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
