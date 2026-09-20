# AI 사용 위험 분석 백엔드 틀

Data Risk Engine / Network Risk Engine, 네트워크 세션 → AI 사용 이벤트 → 행동 Window → 분석 결과 → 대시보드 흐름을 FastAPI + SQLite로 구성했습니다. Python 3.12 이상을 사용합니다. 기존 모델 실험 폴더와 독립적으로 실행됩니다.

현재 구현: 입력 검증, SQLite 영속 저장, 세션과 이벤트 연결 검증, 사용자·단말별 5분/1시간 집계, 엔진 교체 인터페이스, 분석 이력 및 대시보드 조회, Swagger 명세.

모델은 기본적으로 **미연결**입니다. 모든 탐지 항목은 `status: pending`, `score: null`을 반환합니다. 실제 추론, 모델 학습, 통합 위험 점수 정책, 경보·차단, 인증, PCAP 수집, CSV 가져오기, 프런트엔드는 후속 구현 영역입니다. 미분석 결과를 안전 또는 점수 0으로 해석하지 마세요.

## 실행

프로젝트 루트에서 PowerShell:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- API 문서: http://127.0.0.1:8000/docs
- 상태: http://127.0.0.1:8000/health
- 기본 DB: `backend/data/backend.sqlite3` (첫 서버 시작 때 생성)
- `DATABASE_PATH`: DB 파일 위치를 바꾸는 환경 변수
- `CORS_ORIGINS`: 쉼표로 구분한 프런트엔드 origin. 기본값은 `http://localhost:3000,http://localhost:5173`

인증·권한 검증은 아직 없습니다. 로컬 개발용으로 실행하며, 실제 사용자 서비스에는 인증과 사용자별 조회 권한을 먼저 연결해야 합니다. `user_id` 필터는 접근 제어가 아닙니다.

## 폴더

```text
backend/
  app/
    main.py          # 앱 생성, 설정, 의존성 주입
    api.py           # REST 라우터
    schemas.py       # 요청/응답 계약 및 검증
    repository.py    # SQLite 저장소
    services.py      # 행동 집계와 대시보드 통계
    engines.py       # 엔진 Protocol과 미연결 구현
  examples/demo.py   # 수집 → 집계 → 분석 → 조회 예시
  tests/test_api.py  # API 흐름과 경계 조건 검사
```

## API

| Method | 경로 | 역할 |
|---|---|---|
| GET | `/health` | DB 연결 및 엔진 종류 확인 |
| POST / GET | `/api/v1/network-sessions` | 네트워크 세션 등록 / 목록 |
| POST / GET | `/api/v1/ai-usage-events` | AI 사용 이벤트 등록 / 목록 |
| POST / GET | `/api/v1/behavior-windows` | 5분·1시간 집계 생성 / 목록 |
| POST | `/api/v1/data-risk/analyze` | 프롬프트 분석 인터페이스 |
| POST | `/api/v1/network-risk/analyze/{window_id}` | 집계 Window 분석 인터페이스 |
| GET | `/api/v1/risks` | 두 엔진의 분석 결과 목록 |
| GET | `/api/v1/risks/{risk_id}` | 분석 상세와 항목별 근거 |
| GET | `/api/v1/dashboard/summary` | 수집량, 분석 대기 건수, 계산된 점수 평균 |

목록은 `user_id`, `limit`(1~200, 기본 50), `offset`을 지원하며 최근 저장 순서로 반환합니다. 대시보드는 `user_id`를 지원합니다. 평균은 완료된 분석 호출들의 평균이며, 고유 사용자 위험도나 두 엔진을 융합한 통합 점수가 아닙니다.

세션/이벤트 ID 중복은 409, 잘못된 입력·세션 연결은 422, 없는 분석/Window는 404입니다. 이벤트는 먼저 등록한 세션의 사용자·단말 및 세션 시간 범위와 일치해야 합니다. 시간에는 `Z` 또는 `+09:00` 등 시간대가 필요합니다. 원문 프롬프트는 저장하지 않습니다.

## 모델 연결 위치

`engines.py`의 `DataRiskEngine.analyze(request)`와 `NetworkRiskEngine.analyze(window)` 계약을 구현한 객체를 `create_app(data_engine=..., network_engine=...)`에 전달합니다. 점수 계약은 0~100이며 실제 모델 출력의 보정/변환 정책은 어댑터에서 정해야 합니다. 완료 결과는 점수와 완료된 항목별 근거를 반환해야 합니다.

- Data: 민감정보, 업무 외 오남용, 토큰 자원 낭비/모델 추출, 모델 교란의 4개 연결 지점.
- Network: N1 대량 업로드, N2 저속·분산 전송, N3 자동화·폭주, N4 미승인 AI, N5 Gateway 우회, N6 차단 후 재시도.
- 별도 개발한 모델 교란 탐지 엔진은 Data Risk 어댑터의 해당 항목에 연결할 수 있습니다. 모델 파일과 학습 데이터는 이 저장소에 포함하지 않습니다. 분류 점수를 전체 위험 점수와 동일시하지 말고, `input_origin`과 미분석 상태 처리를 유지해야 합니다.

Window는 `[start, end)` 구간의 사용자·단말 스냅샷입니다. 세션은 **시작 시각**을 기준으로 전체 전송량을 한 구간에 귀속하고, 이벤트는 발생 시각으로 집계합니다. 구간을 가로지르는 세션의 바이트 분할, 개인 기준선, 요청 속도 변화, 목적지 전환 순서 등은 후속 feature 구현 대상입니다. 5분과 1시간 Window는 따로 생성합니다. 늦게 들어온 로그를 반영하려면 Window를 다시 생성해야 하며, 같은 구간을 재생성하면 별도 스냅샷으로 남습니다.

## 예시 및 검증

서버 실행 후 별도 터미널에서 `backend` 폴더 기준:

```powershell
.\.venv\Scripts\python.exe examples/demo.py
.\.venv\Scripts\python.exe -m pytest -q
```

데모를 반복하면 예시 로그가 추가됩니다. 테스트는 임시 DB를 사용합니다.

SQLite 저장소는 기본 틀을 위한 JSON 레코드 저장 방식이며, 집계 시 해당 사용자의 레코드를 메모리에서 읽습니다. 실제 트래픽 규모로 확장할 때 정규화 테이블, 시간 인덱스, DB 집계, 마이그레이션과 수집 배치/작업 큐를 추가하는 구조입니다.
