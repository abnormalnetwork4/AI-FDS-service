# All_in_one 프롬프트 모델

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

재학습과 설정 변경 방법은 [백엔드 README](../README.md)의 프롬프트·모델 연결 항목을 참고하세요.
