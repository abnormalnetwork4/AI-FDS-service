import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  Search,
  Clock,
  User,
  Network,
  MessageSquareWarning,
  ChevronRight,
  ChevronDown,
  Radio,
  Download,
  Sparkles,
  Pause,
  Play,
  Loader2,
  ArrowUpDown,
  TriangleAlert,
  X,
  Gauge,
  RefreshCw,
  StepForward,
  Inbox,
  WifiOff,
  Flame,
} from "lucide-react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip as RTooltip,
} from "recharts";

/* ===========================================================================
 *  이 파일의 구성
 *   1) DATA LAYER  — 백엔드 연동 지점. 화면 코드는 useEvents() 결과만 사용한다.
 *   2) 순수 함수   — 등급/정렬/핵심원인/CSV
 *   3) UI 컴포넌트 — 화면
 *
 *  백엔드(FastAPI) 연동 방법
 *   - USE_MOCK 을 false 로 바꾸고 API_BASE 를 서버 주소로 교체
 *     (Vite 프로젝트라면 import.meta.env.VITE_API_BASE_URL 로 교체)
 *   - GET  {API_BASE}/api/events        → 이벤트 배열 (아래 normalizeEvent 참고)
 *   - POST {API_BASE}/api/explain       → { text } (AI 설명, Claude API 키는 서버에만 보관)
 *   - 백엔드 필드명이 다르면 normalizeEvent() 한 곳만 수정하면 된다.
 * ======================================================================== */

/* ───────────────────────── 1) DATA LAYER ───────────────────────── */

const USE_MOCK = true;
const API_BASE = "http://localhost:8000";
const POLL_MS = 5000;
const INITIAL_TICK = 1;

const LEVELS = {
  danger: { label: "위험", color: "#C6362A", rank: 2 },
  caution: { label: "주의", color: "#AD6300", rank: 1 },
  safe: { label: "양호", color: "#137D57", rank: 0 },
};

function levelFromScore(score) {
  if (score >= 70) return "danger";
  if (score >= 40) return "caution";
  return "safe";
}

// 위협 카탈로그 — 코드/이름/상태별 설명. base 는 더미 기여도 계산용(추후 SHAP 값으로 교체).
const NETWORK_ITEMS = [
  { code: "N1", label: "비정상 대량 업로드", base: 24, details: { safe: "정상 범위 내", caution: "평균 대비 2.4배 업로드", danger: "8.4MB/s (평균 대비 6.0배)" } },
  { code: "N2", label: "저속·분산 누적 전송", base: 14, details: { safe: "누적 전송 정상 범위", caution: "장시간 소량 전송 누적 (평균 대비 1.6배)", danger: "1시간 누적 전송 평균 대비 4.2배" } },
  { code: "N3", label: "자동화·요청 폭주", base: 16, details: { safe: "정상 범위 내", caution: "5분간 요청 12회 (평균 대비 2.3배)", danger: "5분간 요청 41회 · 일정 간격 반복" } },
  { code: "N4", label: "미승인 AI 목적지", base: 24, details: { safe: "미탐지", caution: "비표준 도메인 접근 이력 1건", danger: "화이트리스트 외 API 엔드포인트 접근 감지" } },
  { code: "N5", label: "Gateway·Proxy 우회", base: 12, details: { safe: "정상 Gateway 경유", caution: "Proxy 설정 변경 이력", danger: "Gateway 우회 경로 사용 감지" } },
  { code: "N6", label: "차단 후 우회·재시도", base: 10, details: { safe: "차단 이력 없음", caution: "동일 IP 반복 재접속 3회", danger: "차단 직후 15분 내 우회 재접속" } },
];

const PROMPT_ITEMS = [
  { code: "AI_DISTILL", label: "인공지능 증류", base: 20, details: { safe: "미탐지", caution: "유사 질의 반복 패턴 일부 감지", danger: "유사 질의 대량 반복 (증류 의심)" } },
  { code: "MISUSE", label: "업무 목적 외 오남용", base: 26, details: { safe: "미탐지", caution: "업무 외 주제 질의 일부 포함", danger: "업무 무관 대용량 문서 요약 요청 감지" } },
  { code: "TOKEN_WASTE", label: "토큰 자원 낭비", base: 12, details: { safe: "정상 범위 내", caution: "토큰 사용량 평균 대비 2배", danger: "무의미 반복 입력으로 토큰 과다 소모" } },
  { code: "PROMPT_INJECTION", label: "모델 교란", base: 42, details: { safe: "미탐지", caution: "특수 지시어 패턴 일부 포함", danger: "정책 우회 시도(지시 무시 요청) 감지" } },
];

const INTENSITY = { safe: 0.15, caution: 0.6, danger: 1 };

function buildItems(catalog, states = {}) {
  const raw = catalog.map((c) => {
    const status = states[c.code] || "safe";
    return { c, status, v: c.base * INTENSITY[status] };
  });
  const total = raw.reduce((a, r) => a + r.v, 0) || 1;
  return raw.map((r) => ({
    code: r.c.code,
    label: r.c.label,
    status: r.status,
    detail: r.c.details[r.status],
    weight: Math.round((r.v / total) * 100),
  }));
}

/* 데모 시나리오 — 근거(states)와 점수가 항상 일치하도록 프레임 단위로 손으로 정의.
 * 프레임은 핑퐁(0→1→2→1→0…)으로 재생되어 등급 전환(토스트·플래시)을 자연스럽게 보여준다. */
const MOUNT_TS = Date.now();
const SCENARIO = [
  {
    id: "VPN-88213", user: "김O식 (전산실)", dept: "정보시스템팀", connectedAt: "02:14:07", durationMin: 252,
    frames: [
      { score: 88, confidence: 93, states: { N1: "danger", N2: "caution", N3: "caution", N4: "danger", N6: "danger", AI_DISTILL: "caution", MISUSE: "danger", TOKEN_WASTE: "caution", PROMPT_INJECTION: "danger" } },
      { score: 82, confidence: 91, states: { N1: "danger", N3: "caution", N4: "danger", N6: "caution", AI_DISTILL: "caution", MISUSE: "danger", PROMPT_INJECTION: "danger" } },
    ],
  },
  {
    id: "VPN-77042", user: "이O연 (마케팅)", dept: "마케팅기획팀", connectedAt: "14:02:51", durationMin: 38,
    frames: [
      { score: 46, confidence: 72, states: { N2: "caution", N6: "caution", MISUSE: "caution" } },
      { score: 63, confidence: 68, states: { N2: "caution", N4: "caution", N6: "caution", MISUSE: "caution", PROMPT_INJECTION: "caution" } },
      { score: 76, confidence: 74, states: { N2: "caution", N4: "danger", N6: "danger", MISUSE: "caution", PROMPT_INJECTION: "caution" } },
    ],
  },
  {
    id: "VPN-65310", user: "박O훈 (재무)", dept: "재무회계팀", connectedAt: "10:47:19", durationMin: 62,
    frames: [
      { score: 44, confidence: 57, states: { N4: "caution", MISUSE: "caution" } },
      { score: 41, confidence: 54, states: { N4: "caution", MISUSE: "caution" } },
    ],
  },
  {
    id: "VPN-54129", user: "최O아 (인사)", dept: "인사팀", connectedAt: "09:15:33", durationMin: 22,
    frames: [
      { score: 24, confidence: 84, states: { N3: "caution" } },
      { score: 15, confidence: 88, states: {} },
    ],
  },
  {
    id: "VPN-49887", user: "정O우 (개발)", dept: "플랫폼개발팀", connectedAt: "11:03:44", durationMin: 51,
    frames: [
      { score: 10, confidence: 95, states: {} },
      { score: 8, confidence: 95, states: {} },
    ],
  },
];

function frameIndex(t, n) {
  if (n === 1) return 0;
  const cycle = 2 * n - 2;
  const p = ((t % cycle) + cycle) % cycle;
  return p < n ? p : cycle - p;
}

function materialize(def, tick) {
  const f = def.frames[frameIndex(tick, def.frames.length)];
  return {
    id: def.id,
    user: def.user,
    dept: def.dept,
    connectedAt: def.connectedAt,
    startedAtMs: MOUNT_TS - def.durationMin * 60000,
    score: f.score,
    confidence: f.confidence,
    network_reasons: buildItems(NETWORK_ITEMS, f.states),
    prompt_reasons: buildItems(PROMPT_ITEMS, f.states),
  };
}

const buildMockEvents = (tick) => SCENARIO.map((d) => materialize(d, tick));

function seedMockHistory() {
  const out = {};
  SCENARIO.forEach((d) => {
    out[d.id] = [];
    for (let t = INITIAL_TICK - 8; t < INITIAL_TICK; t++) {
      out[d.id].push(d.frames[frameIndex(t, d.frames.length)].score);
    }
  });
  return out;
}

// 백엔드 응답 → 화면용 이벤트. 필드명이 다르면 여기서만 매핑한다.
function normItem(x) {
  return {
    code: x.code,
    label: x.label ?? x.code,
    detail: x.detail ?? "",
    status: ["danger", "caution", "safe"].includes(x.status) ? x.status : "safe",
    weight: Number(x.weight ?? 0),
  };
}

function normalizeEvent(raw) {
  const startedAtMs = raw.started_at ? new Date(raw.started_at).getTime() : null;
  return {
    id: String(raw.id ?? raw.event_id),
    user: raw.user ?? raw.user_name ?? "-",
    dept: raw.dept ?? raw.department ?? "-",
    connectedAt:
      raw.connectedAt ??
      (startedAtMs ? new Date(startedAtMs).toLocaleTimeString("ko-KR", { hour12: false }) : "--:--:--"),
    startedAtMs,
    score: Number(raw.score ?? 0),
    confidence: Number(raw.confidence ?? 0),
    network_reasons: (raw.network_reasons ?? []).map(normItem),
    prompt_reasons: (raw.prompt_reasons ?? []).map(normItem),
  };
}

async function fetchEvents(signal) {
  const res = await fetch(`${API_BASE}/api/events`, { signal });
  if (!res.ok) throw new Error(`서버 응답 오류 (HTTP ${res.status})`);
  const data = await res.json();
  return (Array.isArray(data) ? data : data.events ?? []).map(normalizeEvent);
}

function buildEvidenceText(e) {
  return [
    `점수: ${e.score}/100 (${LEVELS[levelFromScore(e.score)].label})`,
    `예측 신뢰도: ${e.confidence}%`,
    "Network Risk Engine 탐지 결과 (N1~N6):",
    ...e.network_reasons.map((r) => `- [${r.code}] ${r.label}: ${r.detail} [${LEVELS[r.status].label}, 기여도 ${r.weight}%]`),
    "Data Risk Engine 탐지 결과:",
    ...e.prompt_reasons.map((r) => `- [${r.code}] ${r.label}: ${r.detail} [${LEVELS[r.status].label}, 기여도 ${r.weight}%]`),
  ].join("\n");
}

const EXPLAIN_PROMPT = (evidence) =>
  `다음은 사내 보안 대시보드에 표시되는 VPN 세션의 위험 판단 근거 데이터다. 보안 담당자가 한눈에 이해할 수 있도록, 이 세션이 왜 이 등급으로 판단됐는지 2~3문장의 자연스러운 한국어 요약으로 설명해줘. 수치를 인용하되 목록을 나열하지 말고 문장으로 풀어써줘.\n\n${evidence}`;

// AI 설명 — 실서버에서는 반드시 백엔드 프록시를 거친다(API 키 노출/CORS 방지).
async function explainEvent(event) {
  const evidence = buildEvidenceText(event);
  if (!USE_MOCK) {
    const res = await fetch(`${API_BASE}/api/explain`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_id: event.id, evidence }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return { text: data.text, source: "ai" };
  }
  // 채팅 미리보기 전용 직접 호출 (배포 환경에서는 동작하지 않음)
  const response = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "claude-sonnet-4-6",
      max_tokens: 300,
      messages: [{ role: "user", content: EXPLAIN_PROMPT(evidence) }],
    }),
  });
  const data = await response.json();
  const text = (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n").trim();
  if (!text) throw new Error("empty");
  return { text, source: "ai" };
}

function useEvents({ live }) {
  const [events, setEvents] = useState([]);
  const [status, setStatus] = useState("loading"); // loading | ok | error
  const [error, setError] = useState(null);
  const [lastOkAt, setLastOkAt] = useState(null);
  const [tick, setTick] = useState(INITIAL_TICK);
  const [reloadKey, setReloadKey] = useState(0);
  const firstLoad = useRef(true);

  // mock: tick → 이벤트 생성 (최초 1회는 로딩 상태를 잠깐 보여줌)
  useEffect(() => {
    if (!USE_MOCK) return undefined;
    const delay = firstLoad.current ? 450 : 0;
    firstLoad.current = false;
    const t = setTimeout(() => {
      setEvents(buildMockEvents(tick));
      setStatus("ok");
      setError(null);
      setLastOkAt(new Date());
    }, delay);
    return () => clearTimeout(t);
  }, [tick]);

  useEffect(() => {
    if (!USE_MOCK || !live) return undefined;
    const id = setInterval(() => setTick((t) => t + 1), POLL_MS);
    return () => clearInterval(id);
  }, [live]);

  // real: 폴링 + 실패 시 이전 데이터 유지
  useEffect(() => {
    if (USE_MOCK) return undefined;
    let ctrl = null;
    let alive = true;
    const load = async () => {
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      try {
        const data = await fetchEvents(ctrl.signal);
        if (!alive) return;
        setEvents(data);
        setStatus("ok");
        setError(null);
        setLastOkAt(new Date());
      } catch (e) {
        if (!alive || e.name === "AbortError") return;
        setStatus("error");
        setError(e.message || "요청 실패");
      }
    };
    load();
    const id = live ? setInterval(load, POLL_MS) : null;
    return () => {
      alive = false;
      if (id) clearInterval(id);
      if (ctrl) ctrl.abort();
    };
  }, [live, reloadKey]);

  return {
    events,
    status,
    error,
    lastOkAt,
    isMock: USE_MOCK,
    advance: () => setTick((t) => t + 1),
    refresh: () => setReloadKey((k) => k + 1),
  };
}

/* ───────────────────────── 2) 순수 함수 ───────────────────────── */

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function fmtTime(d) {
  return d ? d.toLocaleTimeString("ko-KR", { hour12: false }) : "--:--:--";
}

function formatDuration(ms) {
  const totalMin = Math.max(0, Math.floor(ms / 60000));
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return h > 0 ? `${h}시간 ${String(m).padStart(2, "0")}분` : `${m}분`;
}

// 심각도 → 기여도 순 정렬 (위험한 항목이 위로)
function sortItems(items) {
  return [...items].sort(
    (a, b) => LEVELS[b.status].rank - LEVELS[a.status].rank || b.weight - a.weight
  );
}

function topContributors(event, n = 3) {
  return sortItems([...event.network_reasons, ...event.prompt_reasons])
    .filter((i) => i.status !== "safe")
    .slice(0, n);
}

function buildFallbackSummary(e) {
  const level = LEVELS[levelFromScore(e.score)].label;
  const top = topContributors(e, 3);
  if (!top.length) {
    return `${e.user} 세션은 모든 탐지 항목이 정상 범위여서 ${e.score}점(${level})으로 산정되었습니다. 예측 신뢰도는 ${e.confidence}%입니다.`;
  }
  const parts = top.map((t) => `${t.label}(${t.detail})`).join(", ");
  const tail = e.confidence < 60 ? "로 낮은 편이라 담당자의 직접 확인이 필요합니다" : "입니다";
  return `${e.user} 세션은 ${parts} 등이 주된 원인이 되어 ${e.score}점(${level})으로 산정되었습니다. 예측 신뢰도는 ${e.confidence}%${tail}.`;
}

// 등급 전환 알림 — 경계선 근처에서 깜빡일 때 토스트가 반복되지 않도록 ±2점 여유를 둔다.
function shouldNotify(oldLevel, newLevel, score) {
  if (oldLevel === newLevel) return false;
  const up = LEVELS[newLevel].rank > LEVELS[oldLevel].rank;
  if (up) {
    const boundary = newLevel === "danger" ? 70 : 40;
    return score >= boundary + 2;
  }
  const boundary = oldLevel === "danger" ? 70 : 40;
  return score <= boundary - 2;
}

// CSV — 따옴표/쉼표 이스케이프 + 엑셀 수식 인젝션 방어
function csvCell(v) {
  let s = String(v ?? "");
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  if (/[",\n\r]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
  return s;
}

/* ───────────────────────── 3) UI 컴포넌트 ───────────────────────── */

function StatusIcon({ status, size = 15 }) {
  if (status === "danger") return <ShieldAlert size={size} color={LEVELS.danger.color} />;
  if (status === "caution") return <ShieldQuestion size={size} color={LEVELS.caution.color} />;
  return <ShieldCheck size={size} color={LEVELS.safe.color} />;
}

function EvidenceItem({ item }) {
  return (
    <li className="evidence-item">
      <StatusIcon status={item.status} />
      <div className="evidence-item__text">
        <div className="evidence-item__row">
          <span className="evidence-item__label">
            <span className="evidence-item__code">{item.code}</span>
            {item.label}
          </span>
          <span
            className="evidence-item__chip"
            style={{ color: LEVELS[item.status].color, borderColor: LEVELS[item.status].color }}
          >
            {LEVELS[item.status].label}
          </span>
        </div>
        <span className="evidence-item__detail">{item.detail}</span>
        <div className="weight-bar">
          <div className="weight-bar__fill" style={{ width: `${item.weight}%`, background: LEVELS[item.status].color }} />
          <span className="weight-bar__label">기여도 {item.weight}%</span>
        </div>
      </div>
    </li>
  );
}

function EvidenceGroup({ title, icon, items, accent, expanded, onToggle }) {
  const sorted = sortItems(items);
  const risky = sorted.filter((i) => i.status !== "safe");
  const safe = sorted.filter((i) => i.status === "safe");
  const visible = expanded ? sorted : risky;

  return (
    <div className="evidence-group" style={{ borderLeftColor: accent }}>
      <div className="evidence-group__head">
        {icon}
        <span>{title}</span>
        <span className="evidence-group__count">
          이상 {risky.length} · 양호 {safe.length}
        </span>
      </div>
      {visible.length === 0 && <div className="evidence-empty">모든 항목이 양호합니다.</div>}
      <ul className="evidence-list">
        {visible.map((item) => (
          <EvidenceItem key={item.code} item={item} />
        ))}
      </ul>
      {safe.length > 0 && (
        <button className="collapse-btn" onClick={onToggle}>
          <ChevronDown size={13} style={{ transform: expanded ? "rotate(180deg)" : "none", transition: "transform .2s" }} />
          {expanded ? "양호 항목 접기" : `양호 ${safe.length}개 항목 보기`}
        </button>
      )}
    </div>
  );
}

function TopIssues({ event }) {
  const top = topContributors(event, 3);
  return (
    <div className="top-issues">
      <span className="top-issues__title">
        <Flame size={14} /> 핵심 원인 TOP {top.length || 3}
      </span>
      {top.length === 0 ? (
        <span className="top-issues__none">특이 징후 없음 · 모든 항목이 정상 범위입니다</span>
      ) : (
        <div className="top-issues__chips">
          {top.map((t, i) => (
            <span key={t.code} className="issue-chip" style={{ borderColor: LEVELS[t.status].color }}>
              <b style={{ color: LEVELS[t.status].color }}>{i + 1}</b>
              <span className="issue-chip__code">{t.code}</span>
              {t.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function RiskScoreBadge({ score }) {
  const level = levelFromScore(score);
  const { color, label } = LEVELS[level];
  const circumference = 2 * Math.PI * 54;
  const offset = circumference - (score / 100) * circumference;

  return (
    <div className="score-badge">
      <svg width="140" height="140" viewBox="0 0 140 140">
        <circle cx="70" cy="70" r="54" fill="none" stroke="#E7E9EE" strokeWidth="10" />
        <circle
          cx="70"
          cy="70"
          r="54"
          fill="none"
          stroke={color}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform="rotate(-90 70 70)"
          style={{ transition: "stroke-dashoffset 0.6s ease, stroke 0.6s ease" }}
        />
      </svg>
      <div className="score-badge__center">
        <span className="score-badge__num" style={{ color, transition: "color 0.6s ease" }}>{score}</span>
        <span className="score-badge__unit">/ 100</span>
      </div>
      <span className="score-badge__label" style={{ color, borderColor: color }}>{label}</span>
    </div>
  );
}

// 예측 신뢰도 — 위험도 색(빨강/주황/초록)과 겹치지 않도록 파랑 계열 + 낮음은 빗금 패턴으로 표현
function ConfidenceMeter({ confidence }) {
  const tier = confidence >= 80 ? "높음" : confidence >= 60 ? "보통" : "낮음";
  const textColor = confidence >= 80 ? "#33429A" : "#5A6072";
  const fillStyle =
    confidence >= 80
      ? { background: "#33429A" }
      : confidence >= 60
      ? { background: "#7C8BD0" }
      : { background: "repeating-linear-gradient(135deg, #8B93A7 0 4px, #C9CDD9 4px 8px)" };
  return (
    <div className="confidence-card">
      <div className="confidence-card__title">
        <Gauge size={13} /> 예측 신뢰도
      </div>
      <div className="confidence-card__num" style={{ color: textColor }}>
        {confidence}
        <span>%</span>
      </div>
      <div className="confidence-bar">
        <div className="confidence-bar__fill" style={{ width: `${confidence}%`, ...fillStyle }} />
      </div>
      <div className="confidence-card__tier" style={{ color: textColor }}>
        신뢰도 {tier}
        {confidence < 60 && <div>직접 확인 권장</div>}
      </div>
    </div>
  );
}

function SessionRow({ session, active, flashing, onClick, onHover, onLeave }) {
  const level = levelFromScore(session.score);
  const { color } = LEVELS[level];
  return (
    <button
      className={`session-row ${active ? "session-row--active" : ""} ${flashing ? "session-row--flash" : ""}`}
      onClick={onClick}
      onMouseEnter={(e) => onHover(session, e)}
      onMouseMove={(e) => onHover(session, e)}
      onMouseLeave={onLeave}
    >
      <span className="session-row__dot" style={{ background: color }} />
      <div className="session-row__main">
        <span className="session-row__user">{session.user}</span>
        <span className="session-row__meta">
          {session.id} · {session.connectedAt}
        </span>
      </div>
      <span className="session-row__score" style={{ color, transition: "color 0.6s ease" }}>{session.score}</span>
      <ChevronRight size={16} color="#B7BAC3" />
    </button>
  );
}

const TOOLTIP_W = 250;
const TOOLTIP_H_EST = 130;
const EDGE_MARGIN = 12;

function HoverTooltip({ session, pos }) {
  if (!session) return null;
  const level = levelFromScore(session.score);
  const net = sortItems(session.network_reasons)[0];
  const pr = sortItems(session.prompt_reasons)[0];

  const vw = typeof window !== "undefined" ? window.innerWidth : 1024;
  const vh = typeof window !== "undefined" ? window.innerHeight : 768;
  const flipLeft = pos.x + 16 + TOOLTIP_W + EDGE_MARGIN > vw;
  const left = flipLeft ? pos.x - TOOLTIP_W - 16 : pos.x + 16;
  const top = clamp(pos.y, EDGE_MARGIN, vh - TOOLTIP_H_EST - EDGE_MARGIN);

  return (
    <div className="hover-tooltip" style={{ left, top, width: TOOLTIP_W }}>
      <div className="hover-tooltip__head">
        <span style={{ color: LEVELS[level].color }}>{session.score}점</span>
        <span className="hover-tooltip__badge" style={{ color: LEVELS[level].color, borderColor: LEVELS[level].color }}>
          {LEVELS[level].label}
        </span>
        <span className="hover-tooltip__conf">신뢰도 {session.confidence}%</span>
      </div>
      {net && (
        <div className="hover-tooltip__row">
          <Network size={12} /> [{net.code}] {net.label}: {net.detail}
        </div>
      )}
      {pr && (
        <div className="hover-tooltip__row">
          <MessageSquareWarning size={12} /> [{pr.code}] {pr.label}: {pr.detail}
        </div>
      )}
    </div>
  );
}

function LevelBar({ counts, total }) {
  const segs = [
    { key: "danger", value: counts.danger },
    { key: "caution", value: counts.caution },
    { key: "safe", value: counts.safe },
  ];
  return (
    <div className="level-bar">
      {segs.map((s) =>
        s.value > 0 ? (
          <div
            key={s.key}
            className="level-bar__seg"
            style={{ width: `${(s.value / total) * 100}%`, background: LEVELS[s.key].color }}
            title={`${LEVELS[s.key].label} ${s.value}건`}
          />
        ) : null
      )}
    </div>
  );
}

function ScoreTrendChart({ history, color }) {
  const data = history.map((v, i) => ({ i, score: v }));
  return (
    <div style={{ width: "100%", height: 112 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -16 }}>
          <XAxis dataKey="i" hide />
          <YAxis domain={[0, 100]} width={30} tick={{ fontSize: 11, fill: "#7A7E8C" }} axisLine={false} tickLine={false} />
          <RTooltip
            contentStyle={{ background: "#FFFFFF", border: "1px solid #E3E5EA", borderRadius: 6, fontSize: 12 }}
            labelFormatter={() => ""}
            formatter={(v) => [`${v}점`, "점수"]}
          />
          <Line
            type="monotone"
            dataKey="score"
            stroke={color}
            strokeWidth={2.2}
            isAnimationActive={false}
            dot={(p) =>
              p.index === data.length - 1 ? (
                <circle key="last" cx={p.cx} cy={p.cy} r={4} fill={color} stroke="#fff" strokeWidth={1.5} />
              ) : (
                <g key={p.index} />
              )
            }
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function LiveDuration({ startedAtMs, fallback }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!startedAtMs) return undefined;
    const id = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(id);
  }, [startedAtMs]);
  if (!startedAtMs) return <>{fallback ?? "-"}</>;
  return <>{formatDuration(now - startedAtMs)}</>;
}

function ToastStack({ toasts, onDismiss }) {
  return (
    <div className="toast-stack">
      {toasts.map((t) => (
        <div key={t.id} className="toast" style={{ borderLeftColor: t.color }}>
          <TriangleAlert size={15} color={t.color} />
          <span className="toast__text">{t.text}</span>
          <button className="toast__close" onClick={() => onDismiss(t.id)} aria-label="알림 닫기">
            <X size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}

function SkeletonList() {
  return (
    <div className="session-list">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="skeleton-row">
          <span className="skeleton skeleton--dot" />
          <div style={{ flex: 1 }}>
            <span className="skeleton skeleton--line" style={{ width: "60%" }} />
            <span className="skeleton skeleton--line" style={{ width: "40%", height: 9, marginTop: 6 }} />
          </div>
          <span className="skeleton skeleton--num" />
        </div>
      ))}
    </div>
  );
}

function SkeletonDetail() {
  return (
    <div className="detail">
      <span className="skeleton skeleton--line" style={{ width: 320, marginBottom: 24 }} />
      <div style={{ display: "flex", gap: 24 }}>
        <span className="skeleton" style={{ width: 140, height: 140, borderRadius: "50%" }} />
        <span className="skeleton" style={{ width: 128, height: 140, borderRadius: 12 }} />
        <span className="skeleton" style={{ flex: 1, height: 140, borderRadius: 12 }} />
      </div>
      <span className="skeleton" style={{ width: "100%", height: 200, marginTop: 28, borderRadius: 12 }} />
    </div>
  );
}

/* ───────────────────────── 메인 ───────────────────────── */

export default function RiskDashboard() {
  const [live, setLive] = useState(true);
  const { events, status, error, lastOkAt, isMock, advance, refresh } = useEvents({ live });

  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [sortBy, setSortBy] = useState("default");
  const [selectedId, setSelectedId] = useState(null);
  const [openSafe, setOpenSafe] = useState({ net: false, prompt: false });
  const [history, setHistory] = useState(() => (USE_MOCK ? seedMockHistory() : {}));
  const [hover, setHover] = useState({ session: null, pos: { x: 0, y: 0 } });
  const [llm, setLlm] = useState({});
  const [toasts, setToasts] = useState([]);
  const [flashIds, setFlashIds] = useState({});

  const notifiedRef = useRef(null);
  const toastIdRef = useRef(0);
  const timersRef = useRef(new Set());

  const later = (fn, ms) => {
    const id = setTimeout(() => {
      timersRef.current.delete(id);
      fn();
    }, ms);
    timersRef.current.add(id);
  };
  useEffect(() => () => timersRef.current.forEach(clearTimeout), []);

  // 점수 히스토리 누적 (추이 차트)
  useEffect(() => {
    if (!events.length) return;
    setHistory((prev) => {
      const next = { ...prev };
      events.forEach((e) => {
        next[e.id] = [...(next[e.id] || []), e.score].slice(-12);
      });
      return next;
    });
  }, [events]);

  // 등급 전환 감지 → 토스트/플래시 (히스테리시스 적용)
  useEffect(() => {
    if (!events.length) return;
    if (notifiedRef.current === null) {
      notifiedRef.current = Object.fromEntries(events.map((e) => [e.id, levelFromScore(e.score)]));
      return;
    }
    const newToasts = [];
    const newFlash = {};
    events.forEach((e) => {
      const lv = levelFromScore(e.score);
      const old = notifiedRef.current[e.id];
      if (old === undefined) {
        notifiedRef.current[e.id] = lv;
        return;
      }
      if (shouldNotify(old, lv, e.score)) {
        toastIdRef.current += 1;
        newToasts.push({
          id: toastIdRef.current,
          text: `${e.user} 세션이 ${LEVELS[old].label} → ${LEVELS[lv].label} 등급으로 전환됨`,
          color: LEVELS[lv].color,
        });
        if (LEVELS[lv].rank > LEVELS[old].rank) newFlash[e.id] = true;
        notifiedRef.current[e.id] = lv;
      }
    });
    if (newToasts.length) {
      setToasts((prev) => [...prev, ...newToasts].slice(-4));
      newToasts.forEach((t) => later(() => setToasts((prev) => prev.filter((x) => x.id !== t.id)), 4500));
    }
    if (Object.keys(newFlash).length) {
      setFlashIds((prev) => ({ ...prev, ...newFlash }));
      Object.keys(newFlash).forEach((id) => later(() => setFlashIds((prev) => ({ ...prev, [id]: false })), 1800));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let list = events.filter((s) => {
      const matchesQuery =
        q === "" || s.user.toLowerCase().includes(q) || s.id.toLowerCase().includes(q) || s.dept.toLowerCase().includes(q);
      const matchesFilter = filter === "all" || levelFromScore(s.score) === filter;
      return matchesQuery && matchesFilter;
    });
    if (sortBy === "score_desc") {
      list = [...list].sort((a, b) => b.score - a.score);
    } else if (sortBy === "recent") {
      list = [...list].sort((a, b) =>
        a.startedAtMs && b.startedAtMs ? b.startedAtMs - a.startedAtMs : a.connectedAt < b.connectedAt ? 1 : -1
      );
    }
    return list;
  }, [events, query, filter, sortBy]);

  const selected = events.find((e) => e.id === selectedId) || filtered[0] || null;

  const counts = useMemo(() => {
    const c = { danger: 0, caution: 0, safe: 0 };
    events.forEach((s) => c[levelFromScore(s.score)]++);
    return c;
  }, [events]);

  function handleHover(session, e) {
    setHover({ session, pos: { x: e.clientX, y: e.clientY } });
  }
  function handleLeave() {
    setHover({ session: null, pos: { x: 0, y: 0 } });
  }
  function resetFilters() {
    setQuery("");
    setFilter("all");
  }

  function exportCsv() {
    const header = ["세션ID", "사용자", "부서", "점수", "등급", "예측신뢰도", "접속시간"];
    const rows = filtered.map((s) => [
      s.id,
      s.user,
      s.dept,
      s.score,
      LEVELS[levelFromScore(s.score)].label,
      `${s.confidence}%`,
      s.connectedAt,
    ]);
    const csv = [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `risk-sessions_${Date.now()}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // AI 설명 — 세션별 상태 관리(로딩이 다른 세션 화면에 번지지 않음), 실패 시 규칙 기반 요약으로 대체
  async function generateExplanation() {
    if (!selected) return;
    const target = selected;
    const snapshot = { atScore: target.score, atLevel: levelFromScore(target.score) };
    setLlm((p) => ({ ...p, [target.id]: { ...(p[target.id] || {}), status: "loading" } }));
    try {
      const r = await explainEvent(target);
      setLlm((p) => ({ ...p, [target.id]: { status: "done", text: r.text, source: r.source, ...snapshot } }));
    } catch (err) {
      setLlm((p) => ({
        ...p,
        [target.id]: { status: "done", text: buildFallbackSummary(target), source: "rule", ...snapshot },
      }));
    }
  }

  const ex = selected ? llm[selected.id] : null;
  const exLoading = ex?.status === "loading";
  const exStale =
    ex?.status === "done" &&
    selected &&
    (Math.abs(selected.score - ex.atScore) >= 10 || levelFromScore(selected.score) !== ex.atLevel);

  const statusText =
    status === "error"
      ? `서버 연결 실패 · 마지막 정상 수신 ${fmtTime(lastOkAt)}`
      : status === "loading"
      ? "데이터를 불러오는 중…"
      : live
      ? `실시간 갱신 중 · 마지막 갱신 ${fmtTime(lastOkAt)}`
      : "갱신 일시정지됨";
  const dotClass = status === "error" ? "live-dot--err" : live && status === "ok" ? "" : "live-dot--off";

  return (
    <div className="dash">
      <link rel="preconnect" href="https://fonts.googleapis.com" />
      <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="true" />
      <link
        href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&family=IBM+Plex+Mono:wght@500;600&display=swap"
        rel="stylesheet"
      />
      <style>{`
        .dash {
          --bg: #F4F5F8;
          --panel: #FFFFFF;
          --tint: #F0F1F6;
          --border: #E1E3EA;
          --text: #191B22;
          --text-dim: #5F6372;
          --accent: #33429A;
          --accent-soft: #ECEEFA;
          position: relative;
          font-family: 'Noto Sans KR', 'Apple SD Gothic Neo', -apple-system, sans-serif;
          font-size: 13px;
          background: var(--bg);
          color: var(--text);
          border-radius: 14px;
          border: 1px solid var(--border);
          overflow: hidden;
          min-height: 680px;
          display: flex;
          flex-direction: column;
          box-shadow: 0 1px 2px rgba(20,22,30,0.04);
        }
        .dash * { box-sizing: border-box; }
        .dash button, .dash input, .dash select { font-family: inherit; }
        .dash button:focus-visible, .dash input:focus-visible, .dash select:focus-visible {
          outline: 2px solid var(--accent);
          outline-offset: 2px;
        }
        .dash__topline {
          height: 3px;
          width: 100%;
          background: linear-gradient(90deg, var(--accent) 0%, #6B7FE0 45%, #9FB0EE 100%);
          flex-shrink: 0;
        }

        .toast-stack { position: absolute; top: 16px; right: 16px; z-index: 40; display: flex; flex-direction: column; gap: 8px; width: 320px; }
        .toast {
          display: flex; align-items: center; gap: 9px;
          background: #fff; border: 1px solid var(--border); border-left: 3px solid;
          border-radius: 8px; padding: 11px 13px; font-size: 12.5px; color: var(--text);
          box-shadow: 0 10px 26px rgba(20,22,30,0.12); animation: toast-in .25s ease;
        }
        @keyframes toast-in { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }
        .toast__text { flex: 1; }
        .toast__close { background: transparent; border: none; color: var(--text-dim); cursor: pointer; display: flex; }

        .dash-header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 18px 24px 16px; border-bottom: 1px solid var(--border); gap: 18px; flex-wrap: wrap;
        }
        .dash-header__title { display: flex; align-items: center; gap: 11px; font-size: 17px; font-weight: 700; letter-spacing: -0.1px; }
        .dash-header__icon { width: 34px; height: 34px; border-radius: 9px; background: var(--accent-soft); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
        .dash-header__sub { display: flex; align-items: center; gap: 7px; font-size: 12.5px; font-weight: 400; color: var(--text-dim); margin-top: 3px; flex-wrap: wrap; }
        .demo-chip { font-size: 11px; font-weight: 700; color: var(--accent); background: var(--accent-soft); border-radius: 4px; padding: 1px 6px; letter-spacing: .03em; }
        .live-dot { width: 7px; height: 7px; border-radius: 50%; background: #137D57; display: inline-block; animation: pulse 1.6s infinite; }
        .live-dot--off { background: #B7BAC3; animation: none; }
        .live-dot--err { background: #C6362A; animation: none; }
        @keyframes pulse {
          0% { box-shadow: 0 0 0 0 rgba(19,125,87,.35); }
          70% { box-shadow: 0 0 0 6px rgba(19,125,87,0); }
          100% { box-shadow: 0 0 0 0 rgba(19,125,87,0); }
        }
        .dash-header__right { display: flex; align-items: center; gap: 14px; }
        .header-stats { display: flex; flex-direction: column; gap: 6px; min-width: 190px; }
        .header-stats__row { display: flex; justify-content: space-between; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 12.5px; }
        .level-bar { display: flex; width: 100%; height: 6px; border-radius: 4px; overflow: hidden; background: var(--tint); }
        .level-bar__seg { height: 100%; }
        .ghost-btn {
          display: flex; align-items: center; gap: 6px; background: var(--panel);
          border: 1px solid var(--border); border-radius: 7px; padding: 7px 12px;
          font-size: 12.5px; color: var(--text-dim); cursor: pointer;
        }
        .ghost-btn:hover { color: var(--text); border-color: #C7CAD4; }

        .stale-banner {
          display: flex; align-items: center; gap: 9px; padding: 9px 24px;
          background: #FBEDEB; border-bottom: 1px solid #EFC1BC; color: #8E2A20; font-size: 12.5px;
        }
        .stale-banner button { margin-left: auto; background: #fff; border: 1px solid #EFC1BC; color: #8E2A20; border-radius: 6px; padding: 4px 10px; font-size: 12px; cursor: pointer; }

        .dash-body { display: flex; flex: 1; min-height: 0; }

        .side { width: 320px; border-right: 1px solid var(--border); display: flex; flex-direction: column; background: #FBFBFD; }
        .filter-bar { padding: 14px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }
        .search-box { display: flex; align-items: center; gap: 7px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 9px 11px; }
        .search-box input { background: transparent; border: none; outline: none; color: var(--text); font-size: 13px; width: 100%; }
        .search-box input:focus-visible { outline: none; }
        .search-box:focus-within { border-color: var(--accent); }
        .filter-pills { display: flex; gap: 6px; }
        .filter-pill { flex: 1; padding: 7px 0; border-radius: 7px; border: 1px solid var(--border); background: var(--panel); color: var(--text-dim); font-size: 12.5px; cursor: pointer; }
        .filter-pill--active { background: var(--accent); color: #fff; border-color: var(--accent); }
        .sort-select { display: flex; align-items: center; gap: 6px; background: var(--panel); border: 1px solid var(--border); border-radius: 7px; padding: 7px 9px; font-size: 12.5px; color: var(--text-dim); }
        .sort-select select { background: transparent; border: none; outline: none; color: var(--text); font-size: 12.5px; flex: 1; }
        .export-btn { display: flex; align-items: center; justify-content: center; gap: 6px; background: var(--panel); border: 1px solid var(--border); border-radius: 7px; padding: 9px 0; font-size: 12.5px; color: var(--accent); cursor: pointer; font-weight: 500; }
        .export-btn:hover { background: var(--accent-soft); }

        .session-list { overflow-y: auto; flex: 1; padding: 6px; display: flex; flex-direction: column; gap: 3px; }
        .session-row { display: flex; align-items: center; gap: 10px; width: 100%; text-align: left; background: transparent; border: 1px solid transparent; border-radius: 9px; padding: 11px 12px; cursor: pointer; color: var(--text); }
        .session-row:hover { background: var(--panel); border-color: var(--border); }
        .session-row--active { background: var(--panel); border-color: var(--accent); box-shadow: 0 1px 3px rgba(20,22,30,.06); }
        .session-row--flash { animation: flash-row .45s ease 3; }
        @keyframes flash-row { 0%, 100% { background: transparent; border-color: transparent; } 50% { background: #FBE8E6; border-color: #EFC1BC; } }
        .session-row__dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
        .session-row__main { display: flex; flex-direction: column; flex: 1; min-width: 0; }
        .session-row__user { font-size: 13.5px; font-weight: 500; }
        .session-row__meta { font-size: 11.5px; color: var(--text-dim); font-family: 'IBM Plex Mono', ui-monospace, monospace; }
        .session-row__score { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 14.5px; font-weight: 600; }

        .list-empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 40px 20px; color: var(--text-dim); text-align: center; }
        .list-empty button { margin-top: 4px; background: var(--panel); border: 1px solid var(--border); border-radius: 7px; padding: 7px 14px; font-size: 12.5px; color: var(--accent); cursor: pointer; }

        .hover-tooltip { position: fixed; z-index: 60; background: #fff; border: 1px solid var(--border); border-radius: 9px; padding: 11px 13px; font-size: 12px; color: var(--text); pointer-events: none; box-shadow: 0 10px 28px rgba(20,22,30,.14); }
        .hover-tooltip__head { display: flex; align-items: center; gap: 8px; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-weight: 700; font-size: 14px; margin-bottom: 6px; }
        .hover-tooltip__badge { font-size: 11px; border: 1px solid; border-radius: 20px; padding: 1px 8px; font-family: 'Noto Sans KR', sans-serif; font-weight: 500; }
        .hover-tooltip__conf { margin-left: auto; font-size: 11px; font-weight: 500; color: var(--text-dim); font-family: 'Noto Sans KR', sans-serif; }
        .hover-tooltip__row { display: flex; align-items: flex-start; gap: 5px; color: var(--text-dim); margin-top: 4px; line-height: 1.45; }

        .detail { flex: 1; padding: 24px 28px; overflow-y: auto; min-width: 0; }
        .detail-meta { display: flex; gap: 22px; margin-bottom: 22px; flex-wrap: wrap; }
        .detail-meta__item { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-dim); }
        .detail-meta__item b { color: var(--text); font-weight: 600; }

        .detail-main { display: flex; gap: 22px; align-items: flex-start; flex-wrap: wrap; }
        .score-badge { position: relative; width: 140px; height: 140px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; margin-bottom: 26px; }
        .score-badge__center { position: absolute; display: flex; flex-direction: column; align-items: center; }
        .score-badge__num { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 36px; font-weight: 700; line-height: 1; }
        .score-badge__unit { font-size: 12px; color: var(--text-dim); margin-top: 2px; }
        .score-badge__label { position: absolute; bottom: -26px; font-size: 12.5px; border: 1px solid; border-radius: 20px; padding: 2px 13px; background: #fff; }

        .trend-card { flex: 1; min-width: 240px; background: var(--tint); border-radius: 12px; padding: 14px 16px; }
        .trend-card__title { font-size: 12.5px; color: var(--text-dim); font-weight: 500; margin-bottom: 2px; }

        .confidence-card { width: 140px; flex-shrink: 0; background: var(--tint); border-radius: 12px; padding: 14px 14px 12px; display: flex; flex-direction: column; align-items: center; text-align: center; }
        .confidence-card__title { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--text-dim); font-weight: 500; margin-bottom: 6px; }
        .confidence-card__num { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 28px; font-weight: 700; line-height: 1; }
        .confidence-card__num span { font-size: 14px; font-weight: 500; }
        .confidence-bar { width: 100%; height: 6px; background: #E3E5EC; border-radius: 4px; overflow: hidden; margin: 10px 0 8px; }
        .confidence-bar__fill { height: 100%; border-radius: 4px; transition: width .4s ease; }
        .confidence-card__tier { font-size: 12px; font-weight: 500; line-height: 1.45; }

        .top-issues { margin-top: 6px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 12px 14px; background: var(--accent-soft); border-radius: 10px; }
        .top-issues__title { display: flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 700; color: var(--accent); }
        .top-issues__none { font-size: 12.5px; color: var(--text-dim); }
        .top-issues__chips { display: flex; gap: 8px; flex-wrap: wrap; }
        .issue-chip { display: flex; align-items: center; gap: 6px; background: #fff; border: 1px solid; border-radius: 20px; padding: 4px 11px 4px 8px; font-size: 12.5px; font-weight: 500; }
        .issue-chip b { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 12px; }
        .issue-chip__code { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 11px; color: var(--text-dim); }

        .evidence-cols { display: flex; gap: 0; margin-top: 20px; border-top: 1px solid var(--border); }
        .evidence-group { flex: 1; padding: 18px 20px; border-left: 3px solid; min-width: 0; }
        .evidence-group:first-child { border-right: 1px solid var(--border); }
        .evidence-group__head { display: flex; align-items: center; gap: 7px; font-size: 13.5px; font-weight: 600; color: var(--text); margin-bottom: 14px; flex-wrap: wrap; }
        .evidence-group__count { margin-left: auto; font-size: 12px; font-weight: 400; color: var(--text-dim); }
        .evidence-empty { font-size: 13px; color: var(--text-dim); padding: 4px 0 8px; }
        .evidence-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 15px; }
        .evidence-item { display: flex; align-items: flex-start; gap: 9px; }
        .evidence-item__text { display: flex; flex-direction: column; flex: 1; min-width: 0; }
        .evidence-item__row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
        .evidence-item__label { font-size: 13.5px; font-weight: 500; display: flex; align-items: center; gap: 7px; }
        .evidence-item__code { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 11px; font-weight: 700; border: 1px solid currentColor; border-radius: 4px; padding: 1px 5px; color: var(--accent); }
        .evidence-item__detail { font-size: 12.5px; color: var(--text-dim); margin-top: 2px; }
        .evidence-item__chip { font-size: 11.5px; border: 1px solid; border-radius: 20px; padding: 1px 9px; flex-shrink: 0; }
        .weight-bar { position: relative; margin-top: 7px; height: 4px; background: #E9EAF0; border-radius: 4px; }
        .weight-bar__fill { height: 100%; border-radius: 4px; opacity: .9; transition: width .4s ease; }
        .weight-bar__label { position: absolute; right: 0; top: 7px; font-size: 11px; color: var(--text-dim); }
        .collapse-btn { display: flex; align-items: center; gap: 5px; margin-top: 22px; background: transparent; border: none; color: var(--accent); font-size: 12.5px; cursor: pointer; padding: 4px 0; }

        .llm-box { margin-top: 22px; background: var(--accent-soft); border-radius: 12px; padding: 16px 18px; }
        .llm-box__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 9px; gap: 10px; flex-wrap: wrap; }
        .llm-box__title { display: flex; align-items: center; gap: 7px; font-size: 13.5px; font-weight: 600; color: var(--accent); }
        .llm-btn { display: flex; align-items: center; gap: 6px; background: var(--accent); border: none; color: #fff; border-radius: 7px; padding: 8px 14px; font-size: 12.5px; cursor: pointer; font-weight: 500; }
        .llm-btn:hover { background: #2A3785; }
        .llm-btn:disabled { opacity: .55; cursor: default; }
        .llm-text { font-size: 13.5px; line-height: 1.7; color: var(--text); }
        .llm-hint { font-size: 12.5px; color: var(--text-dim); }
        .llm-meta { display: flex; gap: 8px; align-items: center; margin-top: 9px; font-size: 12px; color: var(--text-dim); flex-wrap: wrap; }
        .llm-tag { border-radius: 4px; padding: 1px 7px; font-size: 11.5px; font-weight: 600; background: #fff; color: var(--accent); }
        .llm-tag--stale { color: #8E5200; background: #FFF1DC; }

        .footnote { margin-top: 22px; padding-top: 14px; border-top: 1px solid var(--border); font-size: 12px; color: var(--text-dim); display: flex; align-items: center; gap: 6px; }

        .state-panel { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: var(--text-dim); padding: 40px; text-align: center; }
        .state-panel b { color: var(--text); font-size: 15px; }
        .state-panel button { margin-top: 6px; background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: 9px 16px; font-size: 13px; cursor: pointer; display: flex; align-items: center; gap: 6px; }

        .skeleton { display: block; background: linear-gradient(90deg, #ECEEF3 25%, #F6F7FA 50%, #ECEEF3 75%); background-size: 200% 100%; animation: shimmer 1.3s infinite; border-radius: 6px; }
        @keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
        .skeleton--line { height: 13px; }
        .skeleton--dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
        .skeleton--num { width: 26px; height: 14px; }
        .skeleton-row { display: flex; align-items: center; gap: 10px; padding: 14px 12px; }

        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

        @media (max-width: 1100px) {
          .side { width: 276px; }
          .detail { padding: 20px; }
          .trend-card { flex-basis: 100%; }
        }
        @media (max-width: 860px) {
          .dash-body { flex-direction: column; }
          .side { width: 100%; border-right: none; border-bottom: 1px solid var(--border); max-height: 340px; }
          .evidence-cols { flex-direction: column; }
          .evidence-group:first-child { border-right: none; border-bottom: 1px solid var(--border); }
          .dash-header { padding: 16px; }
          .stale-banner { padding: 9px 16px; }
          .toast-stack { width: calc(100% - 32px); }
        }
        @media (max-width: 520px) {
          .dash-header__right { width: 100%; justify-content: space-between; }
          .header-stats { min-width: 0; flex: 1; }
        }
      `}</style>

      <div className="dash__topline" />
      <ToastStack toasts={toasts} onDismiss={(id) => setToasts((prev) => prev.filter((t) => t.id !== id))} />

      <div className="dash-header">
        <div className="dash-header__title">
          <div className="dash-header__icon">
            <Radio size={17} color="#33429A" />
          </div>
          <div>
            VPN·프롬프트 통합 리스크 모니터링
            <div className="dash-header__sub">
              <span className={`live-dot ${dotClass}`} />
              {statusText}
              {isMock && <span className="demo-chip">DEMO 시나리오</span>}
            </div>
          </div>
        </div>
        <div className="dash-header__right">
          <div className="header-stats">
            <div className="header-stats__row">
              <span style={{ color: LEVELS.danger.color }}>위험 {counts.danger}</span>
              <span style={{ color: LEVELS.caution.color }}>주의 {counts.caution}</span>
              <span style={{ color: LEVELS.safe.color }}>양호 {counts.safe}</span>
            </div>
            <LevelBar counts={counts} total={events.length || 1} />
          </div>
          {isMock && !live && (
            <button className="ghost-btn" onClick={advance} title="시나리오를 한 단계 진행">
              <StepForward size={13} /> 다음 단계
            </button>
          )}
          <button className="ghost-btn" onClick={() => setLive((v) => !v)}>
            {live ? <Pause size={13} /> : <Play size={13} />}
            {live ? "일시정지" : "재개"}
          </button>
        </div>
      </div>

      {status === "error" && events.length > 0 && (
        <div className="stale-banner">
          <WifiOff size={15} />
          서버 응답이 없어 마지막 정상 수신({fmtTime(lastOkAt)}) 기준 데이터를 표시 중입니다.
          <button onClick={refresh}>다시 시도</button>
        </div>
      )}

      <div className="dash-body">
        <div className="side">
          <div className="filter-bar">
            <div className="search-box">
              <Search size={14} color="#8B8F99" />
              <input
                placeholder="사용자·세션ID·부서 검색"
                aria-label="세션 검색"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="filter-pills" role="group" aria-label="등급 필터">
              {[
                { key: "all", label: "전체" },
                { key: "danger", label: "위험" },
                { key: "caution", label: "주의" },
                { key: "safe", label: "양호" },
              ].map((f) => (
                <button
                  key={f.key}
                  className={`filter-pill ${filter === f.key ? "filter-pill--active" : ""}`}
                  aria-pressed={filter === f.key}
                  onClick={() => setFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <div className="sort-select">
              <ArrowUpDown size={13} color="#8B8F99" />
              <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} aria-label="정렬">
                <option value="default">기본순</option>
                <option value="score_desc">점수 높은순</option>
                <option value="recent">최근 접속순</option>
              </select>
            </div>
            <button className="export-btn" onClick={exportCsv} disabled={!filtered.length}>
              <Download size={13} /> CSV 내보내기 ({filtered.length}건)
            </button>
          </div>

          {status === "loading" ? (
            <SkeletonList />
          ) : (
            <div className="session-list">
              {filtered.length === 0 && (
                <div className="list-empty">
                  <Inbox size={26} />
                  <span>{events.length ? "조건에 맞는 세션이 없어요" : "표시할 세션이 없어요"}</span>
                  {events.length > 0 && <button onClick={resetFilters}>필터 초기화</button>}
                </div>
              )}
              {filtered.map((s) => (
                <SessionRow
                  key={s.id}
                  session={s}
                  active={selected?.id === s.id}
                  flashing={!!flashIds[s.id]}
                  onClick={() => setSelectedId(s.id)}
                  onHover={handleHover}
                  onLeave={handleLeave}
                />
              ))}
            </div>
          )}
        </div>

        {status === "loading" && <SkeletonDetail />}

        {status === "error" && events.length === 0 && (
          <div className="state-panel">
            <WifiOff size={30} />
            <b>서버에 연결할 수 없어요</b>
            <span>{error || "잠시 후 다시 시도해 주세요."}</span>
            <button onClick={refresh}>
              <RefreshCw size={14} /> 다시 시도
            </button>
          </div>
        )}

        {status !== "loading" && !selected && events.length > 0 && (
          <div className="state-panel">
            <Inbox size={30} />
            <b>세션을 선택해 주세요</b>
          </div>
        )}

        {selected && (
          <div className="detail">
            <div className="detail-meta">
              <span className="detail-meta__item">
                <User size={14} /> <b>{selected.user}</b> · {selected.dept}
              </span>
              <span className="detail-meta__item">
                <Network size={14} /> 세션 ID <b>{selected.id}</b>
              </span>
              <span className="detail-meta__item">
                <Clock size={14} /> 접속 {selected.connectedAt} · 지속시간{" "}
                <b>
                  <LiveDuration startedAtMs={selected.startedAtMs} />
                </b>
              </span>
            </div>

            <div className="detail-main">
              <RiskScoreBadge score={selected.score} />
              <ConfidenceMeter confidence={selected.confidence} />
              <div className="trend-card">
                <div className="trend-card__title">최근 점수 추이</div>
                <ScoreTrendChart
                  history={history[selected.id] || [selected.score]}
                  color={LEVELS[levelFromScore(selected.score)].color}
                />
              </div>
            </div>

            <TopIssues event={selected} />

            <div className="evidence-cols">
              <EvidenceGroup
                title="Network Risk Engine 탐지 (N1~N6)"
                icon={<Network size={15} color="#5F6372" />}
                items={selected.network_reasons}
                accent="#33429A"
                expanded={openSafe.net}
                onToggle={() => setOpenSafe((s) => ({ ...s, net: !s.net }))}
              />
              <EvidenceGroup
                title="Data Risk Engine 탐지"
                icon={<MessageSquareWarning size={15} color="#5F6372" />}
                items={selected.prompt_reasons}
                accent="#7A4FB5"
                expanded={openSafe.prompt}
                onToggle={() => setOpenSafe((s) => ({ ...s, prompt: !s.prompt }))}
              />
            </div>

            <div className="llm-box">
              <div className="llm-box__head">
                <span className="llm-box__title">
                  <Sparkles size={14} /> AI 판단 근거 요약
                </span>
                <button className="llm-btn" onClick={generateExplanation} disabled={exLoading}>
                  {exLoading ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
                  {exLoading ? "생성 중..." : ex?.status === "done" ? (exStale ? "최신 상태로 다시 생성" : "다시 생성") : "AI 설명 보기"}
                </button>
              </div>
              {ex?.status === "done" && <div className="llm-text">{ex.text}</div>}
              {ex?.status === "done" && (
                <div className="llm-meta">
                  <span className="llm-tag">{ex.source === "ai" ? "AI 생성" : "규칙 기반 요약"}</span>
                  {ex.source === "rule" && <span>AI 연결에 실패해 탐지 결과로 자동 요약했어요.</span>}
                  {exStale && <span className="llm-tag llm-tag--stale">점수가 바뀌어 최신 내용과 다를 수 있어요</span>}
                </div>
              )}
              {exLoading && !ex?.text && <div className="llm-hint">근거 데이터를 바탕으로 요약을 만들고 있어요…</div>}
              {!ex && <div className="llm-hint">버튼을 누르면 위 근거 데이터를 바탕으로 자연어 요약을 생성합니다.</div>}
            </div>

            <div className="footnote">
              <ShieldQuestion size={14} />
              본 점수는 보안 담당자 참고용 지표이며(Out-of-Path 사후 분석), 시스템이 자동으로 접속을 차단하지 않습니다.
            </div>
          </div>
        )}
      </div>

      <HoverTooltip session={hover.session} pos={hover.pos} />
    </div>
  );
}
