// 개요 그래프용 순수 함수. 점수·등급은 서버 값만 쓰고, 없는 값(미판정)은 0으로 바꾸지 않고 비워 둡니다.

// 사용자 선 색: 범주형 8색(고정 순서). 9번째 사용자부터는 새 색을 만들지 않고 선택에서 빼게 합니다.
export const SERIES_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'];
export const MAX_SERIES = SERIES_COLORS.length;
export const SLOT_MS = 5 * 60 * 1000;

const RANK = { normal: 0, caution: 1, warning: 2, danger: 3 };
const worse = (a, b) => {
  // 등급이 높을수록, 같으면 점수가 높을수록 '더 위험'. 미판정은 가장 낮게 봅니다(정상으로 세지는 않음).
  const ra = a.grade ? RANK[a.grade] : -1;
  const rb = b.grade ? RANK[b.grade] : -1;
  return ra !== rb ? ra > rb : (a.score ?? -1) > (b.score ?? -1);
};

// 사용자별 대표 위험도: 가장 높은 등급 → 높은 점수. 기본 표시 사용자 선정과 정렬에 씁니다.
export function rankUsers(events) {
  const best = new Map();
  for (const e of events) {
    if (e.scope !== 'user_device') continue;
    const prev = best.get(e.user);
    if (!prev || worse(e, prev)) best.set(e.user, e);
  }
  return [...best.entries()]
    .sort(([ua, a], [ub, b]) => (worse(a, b) ? -1 : worse(b, a) ? 1 : ua.localeCompare(ub)))
    .map(([user, e]) => ({ user, grade: e.grade ?? null, score: e.score ?? null }));
}

// 기본 표시: 가장 위험한 사용자부터 최대 8명. 색은 사용자 ID 순으로 붙여 순위가 바뀌어도 같은 사람은 같은 색을 유지합니다.
export function defaultSelection(events, max = MAX_SERIES) {
  return rankUsers(events).slice(0, max).map((u) => u.user).sort();
}

// 선택 순서대로 색 칸을 배정합니다. 이미 배정된 사용자는 기존 색을 유지합니다(필터가 색을 바꾸지 않음).
export function assignColors(selected, previous = {}) {
  const result = {};
  const used = new Set();
  for (const user of selected) {
    const slot = previous[user];
    if (Number.isInteger(slot) && slot >= 0 && slot < MAX_SERIES && !used.has(slot)) {
      result[user] = slot;
      used.add(slot);
    }
  }
  for (const user of selected) {
    if (user in result) continue;
    const slot = SERIES_COLORS.findIndex((_, i) => !used.has(i));
    if (slot === -1) break; // 8색을 넘으면 색을 만들지 않음
    result[user] = slot;
    used.add(slot);
  }
  return result;
}

// 5분 시간대 × 사용자 행렬. 같은 시간대에 한 사용자의 단말이 여럿이면 가장 위험한 구간을 대표로 씁니다.
export function buildMatrix(events) {
  const slots = new Set();
  const cells = new Map(); // `${user}|${slot}` -> window
  for (const e of events) {
    if (e.scope !== 'user_device' || e.startedAtMs == null) continue;
    slots.add(e.startedAtMs);
    const key = `${e.user}|${e.startedAtMs}`;
    const prev = cells.get(key);
    if (!prev || worse(e, prev)) cells.set(key, e);
  }
  const times = [...slots].sort((a, b) => a - b);
  return { times, cell: (user, t) => cells.get(`${user}|${t}`) ?? null };
}

// 한 사용자의 활동 사이 간격이 이보다 길면(예: 점심시간) 선을 끊습니다.
export const MAX_GAP_MS = 30 * 60 * 1000;

// Recharts 행 데이터. 사용자 선을 '구간 묶음(segment)'으로 나눠 키를 `${user}::${n}`로 둡니다.
// 같은 묶음 안에서는 활동 없는 시간대를 건너뛰어 잇고(connectNulls), 30분 넘게 비거나 미판정 구간이 끼면 새 묶음으로 끊습니다.
// 반환: { rows: [{ t, [key]: score|null }], segments: { [user]: [key, ...] } }
export function buildRows(events, users) {
  const { times, cell } = buildMatrix(events);
  const rows = times.map((t) => ({ t }));
  const segments = {};
  for (const user of users) {
    segments[user] = [];
    let key = null;
    let lastT = null;
    times.forEach((t, i) => {
      const w = cell(user, t);
      if (!w) return;
      if (typeof w.score !== 'number') { key = null; return; } // 미판정: 점 없이 선을 끊음(0점으로 그리지 않음)
      if (key === null || t - lastT > MAX_GAP_MS) {
        key = `${user}::${segments[user].length}`;
        segments[user].push(key);
      }
      rows[i][key] = w.score;
      lastT = t;
    });
  }
  return { rows, segments };
}

export function formatHm(ms) {
  if (ms == null) return '--:--';
  return new Date(ms).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
}

// x축 눈금: 2시간 이내면 5분, 그보다 길면 정각·30분 단위로 줄입니다(하루 근무시간이면 30분 간격).
export function timeTicks(times) {
  if (!times.length) return [];
  const first = times[0];
  const last = times[times.length - 1];
  if (last - first <= 2 * 3600 * 1000) {
    const ticks = [];
    for (let t = first; t <= last; t += SLOT_MS) ticks.push(t);
    return ticks;
  }
  const step = 30 * 60 * 1000;
  const ticks = [];
  for (let t = Math.ceil(first / step) * step; t <= last; t += step) ticks.push(t);
  return ticks;
}

// 시간대가 많으면(하루 전체 등) 표를 글자 대신 색 칸으로 줄여 보여 줍니다.
export const COMPACT_SLOTS = 24;
