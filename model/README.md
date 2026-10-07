# 모델

FDS 백엔드가 서버 시작 시 불러오는 탐지 모델을 엔진별로 둡니다. 모델 파일만 있고 서버 연결 코드는 `backend/app`에 있습니다.

| 폴더 | 엔진 | 백엔드 연결 코드 | 끄기 |
|---|---|---|---|
| [`prompt/`](prompt/) | Data(프롬프트) 위험 — All_in_one TF-IDF + XGBoost 네 분류기 | `backend/app/prompt_engine.py` | `PROMPT_ENGINE=stub` |
| [`network/`](network/) | Network 위험 — XGBoost 5분 구간 모델 | `backend/app/network_model.py` | `NETWORK_ENGINE=stub` |

새 모델을 추가할 때도 이 폴더 아래에 엔진별 폴더를 만들고, `backend/app/engines.py`의 엔진 계약에 맞는 어댑터를 연결합니다.
