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

// 실제 항목의 탐지 여부는 서버의 detected만 사용합니다. 과거 데이터에 없으면 추측하지 않습니다.
// network_score(종합 점수)·network_model(모델 상태)은 개별 탐지 항목이 아니라 네트워크 엔진의 요약 행입니다.
const SUMMARY_CODES = ['network_score', 'network_model'];
export const isSummaryItem = (item) => SUMMARY_CODES.includes(item.code);
export function isNotable(item) {
  if (isSummaryItem(item)) return false;
  if (['detected', 'danger', 'warning', 'caution'].includes(item.status)) return true;
  return item.status === 'complete' && item.detected === true;
}
// 화면 표시용 상태. 탐지로 본 Network 항목은 '분석 완료'가 아니라 '탐지'로 보여 줍니다.
export const viewStatus = (item) => item.status === 'complete' && isNotable(item) ? 'detected' : item.status;

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
    const validProbability = typeof x.probability === 'number' && Number.isFinite(x.probability)
      && x.probability >= 0 && x.probability <= 1;
    const classified = x.status === 'complete' && typeof x.detected === 'boolean'
      && validProbability
      && (x.detection_method === 'argmax' ? x.threshold == null
        : typeof x.threshold === 'number' && x.threshold > 0 && x.threshold < 1
          && x.detected === (x.probability > x.threshold));
    return {
    ...x, label: x.label ?? x.code, detail: x.detail ?? '',
    status: classified ? (x.detected ? 'detected' : 'not_detected')
      : ['pending', 'complete', 'error'].includes(x.status) ? x.status : 'pending',
    detected: classified ? x.detected : null,
    probability: classified && validProbability ? x.probability : null,
    threshold: classified ? x.threshold ?? null : null,
    score: x.status === 'complete' ? nullableScore(x.score) : null,
    };
  };
  return {
    ...raw, isReal: true, id: String(raw.id), sessionId: raw.session_id,
    user: raw.scope === 'company' ? '회사 전체' : raw.user ?? '-',
    dept: raw.scope === 'company' ? `5분 구간 · ${raw.capture_count ?? 0}건` : raw.device_id ?? '-',
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
    const response = await fetcher(`${base}/api/v1/dashboard/company-windows?limit=${PAGE_SIZE}${offset}`, { signal });
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

// ---------------------------------------------------------------- 회사 5분 구간 설명
// 화면 안내 문구입니다. 점수 정책을 바꾸지 않으며 backend/app/dashboard.py의 같은 문구와 맞춥니다.
export const SCOPE_NOTE = [
  '이 결과는 한 명의 개인 위험도가 아니라, 한 회사의 5분 구간 전체 위험도입니다.',
  '현재 시연은 한 명의 가상 사용자로 구성되어 있습니다.',
  '실제 모델 학습 데이터가 여러 사용자의 5분 집계라면, 실제 운영에서는 동일한 수집 범위와 사용자 규모로 검증해야 합니다.',
];
export const PROMPT_MAX_NOTE = '통합 점수에는 구간 안 프롬프트 중 최고 점수 한 건만 반영합니다. '
  + '합산하면 요청 수에 따라 점수가 부풀고, 평균하면 위험 프롬프트 한 건이 정상 요청에 묻히기 때문입니다. '
  + '요청량·전송량은 네트워크 점수가 따로 반영합니다.';

const finite = (v) => typeof v === 'number' && Number.isFinite(v);

// 구간의 프롬프트 점수 목록. 미판정·오류는 0점이 아니라 score=null로 둡니다. 최고 점수 근거 행에 isMax 표시.
export function promptScoreRows(event) {
  const rows = Array.isArray(event?.prompt_scores) ? event.prompt_scores : [];
  return rows.map((p) => {
    const status = ['complete', 'pending', 'error'].includes(p?.status) ? p.status : 'pending';
    return {
      captureId: String(p?.capture_id ?? '-'), userId: String(p?.user_id ?? '-'), status,
      score: status === 'complete' && finite(p.score) ? p.score : null,
      isMax: status === 'complete' && p.capture_id === event.prompt_source_capture_id,
    };
  });
}

// 네트워크 점수 구성. 서버(모델 보고서)가 준 값만 쓰고, 없는 항목은 null로 남겨 '미제공'으로 표시합니다.
export function networkBreakdownParts(breakdown) {
  if (!breakdown || !finite(breakdown.total_score)) return null;
  const value = (v) => finite(v) ? v : null;
  return {
    total: breakdown.total_score,
    threat: typeof breakdown.threat === 'string' ? breakdown.threat : null,
    threatName: typeof breakdown.threat_name === 'string' ? breakdown.threat_name : '',
    parts: [
      { key: 'base_score', label: '기본점수', value: value(breakdown.base_score) },
      { key: 'probability_score', label: '모델 확률 점수', value: value(breakdown.probability_score) },
      { key: 'retry_score', label: '재시도 가산점', value: value(breakdown.retry_score) },
      { key: 'repeat_score', label: '반복 가산점', value: value(breakdown.repeat_score) },
    ],
  };
}

export function networkBreakdownText(breakdown) {
  const b = networkBreakdownParts(breakdown);
  if (!b) return '네트워크 점수 구성 정보 없음';
  const terms = b.parts.map((p, i) => `${i ? '+ ' : '= '}${p.label} ${p.value == null ? '미제공' : formatScore(p.value)}`);
  return [`네트워크 점수 ${formatScore(b.total)}점`, ...terms].join(' ');
}
