# AI-FDS-service · 리스크 대시보드 (UI 목업)

VPN 세션 · 프롬프트 위험도를 통합해서 보여주는 보안 모니터링 대시보드입니다.
현재는 **더미 데이터 기반 UI 목업** 단계이며, 백엔드 연동이 필요합니다.

## 실행 방법

```bash
npm install
npm run dev
```

## 주요 구조

```
src/
  components/
    RiskDashboard.jsx   ← 대시보드 메인 컴포넌트 (전체 로직 + 스타일 포함)
  App.jsx                ← RiskDashboard를 렌더링
```

## 백엔드 연동 필요 지점

`RiskDashboard.jsx` 파일 상단과 코드 내 `TODO(backend)` 주석으로 표시해두었습니다.

1. **세션 데이터** — `INITIAL_SESSIONS` 더미 배열을 실제 API 응답으로 교체 필요.
   ```
   { id, user, dept, connectedAt, duration, score,
     network_reasons: [{ label, detail, status, weight }],
     prompt_reasons:  [{ label, detail, status, weight }] }
   ```

2. **AI 판단 근거 요약** — `generateLlmExplanation` 함수 안 `fetch` 호출이
   현재 `api.anthropic.com`을 브라우저에서 직접 부르는 임시 코드입니다.
   CORS 및 API 키 노출 문제가 있어, 실제 배포 전에 백엔드 프록시 엔드포인트로 교체해야 합니다.

## 사용 라이브러리

- [recharts](https://recharts.org/) — 차트
- [lucide-react](https://lucide.dev/) — 아이콘
