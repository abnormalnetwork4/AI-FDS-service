# FDS 사후 분석 대시보드

React 화면에서 백엔드에 저장된 관측 자료와 분석 결과를 조회합니다. 기본값은 실제 API 연결이며 SSE 변경 알림을 받으면 불러온 범위를 갱신합니다. 처음에는 최근 200건을 읽고, 목록 하단의 "더 보기"로 200건씩 최대 2,000건까지 이어서 불러옵니다. 스트림 연결 실패 시에는 5초 조회로 대체하고 자동 재접속합니다. 검색·필터·CSV 내보내기는 불러온 건만 대상으로 합니다. 상단에 전체 건수도 별도로 표시합니다.

## 실행 (Windows, Docker 불필요)

Python 3.12와 Node.js 22.12 이상을 준비합니다. 두 터미널 모두 저장소 루트에서 시작합니다.

첫 번째 터미널 — 백엔드:

```powershell
cd backend
# 최초 한 번만 환경 생성 및 설치
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

두 번째 터미널 — 화면:

```powershell
cd frontend
npm ci
npm run dev
```

http://127.0.0.1:5173 을 엽니다. Vite가 `/api` 요청을 `http://127.0.0.1:8000`으로 전달합니다. 백엔드가 꺼져 있으면 연결 실패를 표시하며 샘플 데이터로 자동 전환하지 않습니다. 포트가 사용 중이면 기존 서버를 확인하세요.

처음에는 빈 화면이 정상입니다. 세 번째 터미널에서 예시를 보내면 해당 회사 5분 구간이 나타납니다. 같은 구간의 여러 캡처는 한 행에 합쳐집니다.

```powershell
cd backend
.\.venv\Scripts\python.exe examples/passive_demo.py
```

예시는 가상 관측 자료를 DB에 추가합니다. 실제 패킷 캡처나 AI 요청을 발생시키지 않습니다. 수집기·모델 연결은 [백엔드 안내](../backend/README.md)를 참고하세요.

## 화면 표시 기준

- 회사 전체 고정 5분 구간 선택, 구간 ID 검색, 등급 필터, 정렬, CSV 내보내기를 지원합니다.
- 같은 구간 프롬프트 최고 점수(최대 60)와 전체 네트워크 점수 × 0.4(최대 40)를 합칩니다. 최고 프롬프트의 캡처/사용자와 산식을 표시하며 개인 등급으로 표시하지 않습니다.
- 구간이 열려 있으면 잠정 점수이며 종료 후에도 지연 수신에 따라 바뀔 수 있습니다. 구간 내 프롬프트 누락·오류는 0점으로 대체하지 않습니다. 입력 예시는 백엔드 `examples/event_demo.py`를 실행하세요.
- Network 항목은 서버의 `detected`만 사용합니다. `detection_method: argmax`는 최다 확률 클래스 기준이며 50% 임계값이나 설명 문구로 추측하지 않습니다. N5+N6는 기존 정책대로 N6에 표시합니다. 과거 기록에 detected가 없으면 탐지 여부를 추정하지 않습니다.
- Regression 프롬프트 모델(기본 임계값 0.45 초과)의 네 항목별 `탐지/미탐지`, 모델 예측 확률과 판정 기준을 표시합니다. 확률은 위험도 점수나 통합 기여도가 아닙니다.
- 네트워크 모델(`model/network`)은 백엔드에 연결돼 있습니다. 통합 등급·점수·신뢰도는 백엔드가 `grade`, `score`, `confidence`, `override`, `override_reasons`로 내려줄 때만 표시하며, 프론트가 점수로 등급을 계산하지 않습니다. 이 값이 없으면 전체 등급은 `미판정`이고 안전 판정을 뜻하지 않습니다. 프롬프트 항목의 `미탐지`도 안전 판정이 아닙니다. 없는 점수·신뢰도를 만들어 채우지 않으며 기여도 표시는 제거했습니다. 프롬프트와 5분 Network 중 하나라도 진행 중·오류·미판정이면 통합 점수·등급은 null입니다.
- 통합 등급은 `정상`(0~29) / `주의`(30~49) / `경고`(50~69) / `위험`(70~100) 4단계입니다. 프롬프트 점수 50 이상(60점 만점) 또는 네트워크 반영 점수 35 이상(40점 만점)이면 합계와 무관하게 `위험`이며, 이때 화면에 "강제 위험 규칙 적용"과 사유가 표시됩니다.
- 응답 형식 합의안은 `docs/grade-api-contract.md`를 참고하세요.
- `분석 요약 보기`는 저장된 근거를 정리합니다. 외부 LLM 호출이나 자동 접속 차단은 수행하지 않습니다.
- 연결이 끊기면 마지막 수신 자료와 연결 실패 안내를 표시합니다.

## API와 구성

| 용도 | 요청 | 응답 |
|---|---|---|
| 변경 알림 | `GET /api/v1/dashboard/stream` | SSE `changed` 이벤트; 최신 목록 재조회 |
| 회사 5분 구간 목록 | `GET /api/v1/dashboard/company-windows?limit=200[&offset=200…]` | `{events, total, limit, offset}` |
| 저장된 근거 요약 | `GET /api/v1/dashboard/explanation?window_id=...` | `{text, source: "stored"}` |

회사 구간 목록은 `limit`(1~200), `offset`을 지원하며 UI는 `limit=200`과 `offset`으로 페이지를 이어 읽습니다. 개인 필터로 회사 점수를 바꾸지 않습니다. 원문 프롬프트는 목록 응답에 포함하지 않습니다.

- `src/lib/events.js`: API 응답 변환과 미판정 처리
- `src/components/RiskDashboard.jsx`: 갱신·검색·선택 및 화면
- `vite.config.js`: 개발/미리보기 API 프록시
- `../backend/app/dashboard.py`: 저장 결과를 화면용 응답으로 변환

`.env.example`을 `.env.local`로 복사하면 설정을 바꿀 수 있습니다. 변경 후 Vite를 재시작하세요.

- `VITE_USE_MOCK=false`: 기본값, 실제 API 사용. `true`일 때만 DEMO 화면을 표시합니다.
- `VITE_API_BASE_URL=`: 기본값은 동일 출처 `/api`. 다른 서버 URL을 지정하면 백엔드 `CORS_ORIGINS`에 화면 주소를 허용해야 합니다. 프론트 환경변수에는 비밀 키를 넣지 않습니다.

## 검증·빌드

```powershell
npm test
npm run lint
npm run build
npm run preview
```

빌드는 `dist/`에 생성됩니다. 미리보기는 http://127.0.0.1:4173 이며 백엔드 8000번 포트도 실행해야 합니다. 실제 배포 시 정적 파일 서버에 `dist/`를 배치하고 `/api`를 FDS로 연결합니다. Vite 개발 서버는 운영용 서버가 아닙니다. 사내 공개 전 관리자 인증·권한과 수집기 인증은 별도 구현해야 합니다.
