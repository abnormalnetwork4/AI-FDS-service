// 통합 점수·신뢰도·등급은 백엔드가 계산해서 내려주는 값만 표시합니다(없으면 null). 없는 숫자를 0으로 바꾸지 않습니다.
// 통합 등급은 정상/주의/경고/위험 4단계입니다. 기준은 model/network/risk_scoring.py의 GRADE_BOUNDS(30, 50, 70)와 같습니다.
export const LEVELS = {
  danger: { label: '위험', color: '#C6362A', rank: 3 },
  warning: { label: '경고', color: '#C2570C', rank: 2 },
  caution: { label: '주의', color: '#8A6D00', rank: 1 },
  normal: { label: '정상', color: '#137D57', rank: 0 },
  pending: { label: '미판정', color: '#737987', rank: -1 },
  error: { label: '분석 오류', color: '#6B4FA3', rank: 4 },
  complete: { label: '분석 완료', color: '#33429A', rank: 0 },
  detected: { label: '탐지', color: '#AD6300', rank: 1 },
  not_detected: { label: '미탐지', color: '#33429A', rank: 0 },
};

export function nullableScore(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
}

// 각 등급의 하한 점수입니다. UI 데모와 알림 여유 계산에만 쓰며, 실제 등급 정책은 백엔드 책임입니다.
export const GRADE_LOWER = { normal: 0, caution: 30, warning: 50, danger: 70 };

export function levelFromScore(score) {
  if (nullableScore(score) === null) return 'pending';
  return score >= GRADE_LOWER.danger ? 'danger' : score >= GRADE_LOWER.warning ? 'warning'
    : score >= GRADE_LOWER.caution ? 'caution' : 'normal';
}

// 서버가 보낸 등급 값을 4등급 키로 바꿉니다. 알 수 없는 값(예: 'unassessed')은 null이며 정상으로 취급하지 않습니다.
const GRADE_ALIASES = {
  normal: 'normal', caution: 'caution', warning: 'warning', danger: 'danger',
  정상: 'normal', 주의: 'caution', 경고: 'warning', 위험: 'danger',
};
export function normalizeGrade(value) {
  return typeof value === 'string' ? GRADE_ALIASES[value.trim().toLowerCase()] ?? null : null;
}

// 실제 데이터는 서버 등급만 사용하고, 없으면 미판정입니다. 점수로 등급을 직접 계산하는 건 데모뿐입니다.
export function eventLevel(event) {
  if (event.status === 'error') return 'error';
  return event.isReal ? event.grade ?? 'pending' : levelFromScore(event.score);
}

// 등급 전환 알림 여부. 실제 데이터는 서버 등급이 바뀌었는지만 보고(강제 위험 규칙으로 점수와 등급이 어긋날 수 있음),
// 데모는 경계선 깜빡임을 막으려고 ±2점 여유를 둡니다.
export function shouldNotify(oldLevel, newLevel, score, isReal = false) {
  if (oldLevel === newLevel || [oldLevel, newLevel].some((v) => !(v in GRADE_LOWER))) return false;
  if (isReal) return true;
  if (score == null) return false;
  return LEVELS[newLevel].rank > LEVELS[oldLevel].rank
    ? score >= GRADE_LOWER[newLevel] + 2
    : score <= GRADE_LOWER[oldLevel] - 2;
}

// 점수는 소수일 수 있어 정수면 그대로, 아니면 소수 첫째 자리까지 표시합니다. 없으면 '—'.
export function formatScore(value) {
  const n = nullableScore(value);
  return n === null ? '—' : Number.isInteger(n) ? String(n) : n.toFixed(1);
}

// 서버가 보낸 강제 위험(override) 사유. 문자열만 받고 나머지는 버립니다.
const reasonList = (value) => Array.isArray(value) ? value.filter((v) => typeof v === 'string' && v.trim()) : [];

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
    grade: normalizeGrade(raw.grade ?? raw.final_grade),
    score: nullableScore(raw.score), confidence: nullableScore(raw.confidence),
    override: raw.override === true, overrideReasons: reasonList(raw.override_reasons),
    network_reasons: (raw.network_reasons ?? []).map(item),
    prompt_reasons: (raw.prompt_reasons ?? []).map(item),
    revision: JSON.stringify(raw),
  };
}

// 백엔드 limit 상한이 200이라 200건 단위 페이지를 offset으로 이어서 읽습니다. 최대 10페이지(2,000건)까지 조회합니다.
export const PAGE_SIZE = 200;
export const MAX_PAGES = 10;

export async function fetchEventPage(base, signal, fetcher = fetch, pages = 1) {
  const count = Math.max(1, Math.min(MAX_PAGES, Math.trunc(pages) || 1));
  const one = async (index) => {
    const offset = index ? `&offset=${index * PAGE_SIZE}` : '';
    const response = await fetcher(`${base}/api/v1/dashboard/events?limit=${PAGE_SIZE}${offset}`, { signal });
    if (!response.ok) throw new Error(`서버 응답 오류 (HTTP ${response.status})`);
    const page = await response.json();
    if (!Array.isArray(page.events) || !Number.isInteger(page.total)) throw new Error('올바르지 않은 대시보드 응답');
    return page;
  };
  const results = await Promise.all(Array.from({ length: count }, (_, i) => one(i)));
  // 페이지를 읽는 사이 새 캡처가 들어오면 경계의 항목이 겹칠 수 있어 id로 중복을 제거합니다. 다음 갱신에서 바로잡힙니다.
  const seen = new Set();
  const events = results.flatMap((page) => page.events).filter((e) => {
    const id = String(e.id);
    return seen.has(id) ? false : (seen.add(id), true);
  }).map(normalizeEvent);
  return { events, total: Math.max(...results.map((page) => page.total)) };
}
