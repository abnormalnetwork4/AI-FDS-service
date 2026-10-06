// API에는 통합 점수/신뢰도/기여도가 아직 없습니다. 없는 숫자를 0으로 바꾸지 않습니다.
export const LEVELS = {
  danger: { label: '위험', color: '#C6362A', rank: 2 },
  caution: { label: '주의', color: '#AD6300', rank: 1 },
  safe: { label: '양호', color: '#137D57', rank: 0 },
  pending: { label: '미판정', color: '#737987', rank: -1 },
  error: { label: '분석 오류', color: '#AD6300', rank: 3 },
  complete: { label: '분석 완료', color: '#33429A', rank: 0 },
  detected: { label: '탐지', color: '#AD6300', rank: 1 },
  not_detected: { label: '미탐지', color: '#33429A', rank: 0 },
};

export function nullableScore(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
}

// 이 임계값은 명시적으로 켠 UI 데모에서만 사용합니다. 실제 등급 정책은 백엔드 책임입니다.
export function levelFromScore(score) {
  if (nullableScore(score) === null) return 'pending';
  return score >= 70 ? 'danger' : score >= 40 ? 'caution' : 'safe';
}

export function eventLevel(event) {
  return event.status === 'error' ? 'error' : event.isReal ? 'pending' : levelFromScore(event.score);
}

const dateMs = (value) => value && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
export function normalizeEvent(raw) {
  const startedAtMs = dateMs(raw.started_at);
  const item = (x) => {
    const classified = x.status === 'complete' && typeof x.detected === 'boolean'
      && typeof x.probability === 'number' && Number.isFinite(x.probability)
      && x.probability >= 0 && x.probability <= 1
      && typeof x.threshold === 'number' && x.threshold > 0 && x.threshold < 1
      && x.detected === (x.probability > x.threshold);
    return {
    ...x, label: x.label ?? x.code, detail: x.detail ?? '',
    status: classified ? (x.detected ? 'detected' : 'not_detected')
      : ['pending', 'complete', 'error'].includes(x.status) ? x.status : 'pending',
    detected: classified ? x.detected : null,
    probability: classified ? x.probability : null,
    threshold: classified ? x.threshold : null,
    score: x.status === 'complete' ? nullableScore(x.score) : null,
    weight: null,
    };
  };
  return {
    ...raw, isReal: true, id: String(raw.id), sessionId: raw.session_id,
    user: raw.user ?? '-', dept: raw.device_id ?? '-',
    startedAtMs, endedAtMs: dateMs(raw.ended_at),
    connectedAt: startedAtMs === null ? '--' : new Date(startedAtMs).toLocaleString('ko-KR', { hour12: false }),
    score: null, confidence: null,
    network_reasons: (raw.network_reasons ?? []).map(item),
    prompt_reasons: (raw.prompt_reasons ?? []).map(item),
    revision: JSON.stringify(raw),
  };
}

export async function fetchEventPage(base, signal, fetcher = fetch) {
  // 최근 200건만 조회합니다. 전체 건수는 별도로 표시해 조회 범위를 알립니다.
  const response = await fetcher(`${base}/api/v1/dashboard/events?limit=200`, { signal });
  if (!response.ok) throw new Error(`서버 응답 오류 (HTTP ${response.status})`);
  const page = await response.json();
  if (!Array.isArray(page.events) || !Number.isInteger(page.total)) throw new Error('올바르지 않은 대시보드 응답');
  return { events: page.events.map(normalizeEvent), total: page.total };
}
