# AI-FDS-service · 리스크 대시보드 (Frontend)

VPN 세션의 네트워크 · 프롬프트 위험도를 통합해서 보여주는 보안 모니터링 대시보드입니다.
현재는 **더미 데이터로 동작하는 UI 목업** 단계이며, UI는 계속 수정될 수 있습니다.

## 실행 방법

```bash
cd frontend
npm install
npm run dev
```

## 구조

```
frontend/
  src/
    components/
      RiskDashboard.jsx   ← 대시보드 전체 (데이터 레이어 + 화면)
    App.jsx               ← RiskDashboard 렌더링
```

`RiskDashboard.jsx`는 크게 3부분으로 나뉩니다.

1. **DATA LAYER** (파일 상단) — 백엔드 연동 지점. 화면 코드는 `useEvents()` 결과만 사용합니다.
2. **순수 함수** — 등급 계산, 정렬, 핵심 원인 추출, CSV
3. **UI 컴포넌트** — 화면

## 백엔드 연동 방법

파일 상단의 설정 두 줄만 바꾸면 됩니다.

```js
const USE_MOCK = true;                    // false 로 변경
const API_BASE = "http://localhost:8000"; // 서버 주소로 교체
```

| 용도 | 요청 | 응답 |
|---|---|---|
| 이벤트 목록 | `GET {API_BASE}/api/events` | 이벤트 배열 (또는 `{ events: [...] }`) |
| AI 설명 | `POST {API_BASE}/api/explain` (`{ event_id, evidence }`) | `{ text }` |

### 이벤트 필드

`normalizeEvent()`가 아래 필드를 받아 화면용으로 변환합니다. 백엔드 필드명이 다르면 **이 함수 한 곳만 수정**하면 됩니다.

```
id (또는 event_id), user (또는 user_name), dept (또는 department),
started_at, score, confidence,
network_reasons: [{ code, label, detail, status, weight }],
prompt_reasons:  [{ code, label, detail, status, weight }]
```

- `status`: `danger` | `caution` | `safe`
- `score`: 0~100 (70 이상 위험, 40 이상 주의)

### 주의

- 현재 `USE_MOCK = true`일 때만 `api.anthropic.com`을 브라우저에서 직접 호출하는 미리보기용 코드가 동작합니다. 실서비스에서는 반드시 `/api/explain` 백엔드 프록시를 거쳐야 합니다 (API 키 노출 / CORS 방지).
- Claude API 키는 프론트 코드에 넣지 않고 서버에만 보관합니다.

## 사용 라이브러리

- [recharts](https://recharts.org/) — 차트
- [lucide-react](https://lucide.dev/) — 아이콘
