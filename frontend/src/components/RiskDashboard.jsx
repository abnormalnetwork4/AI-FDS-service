import { LEVELS, levelText, MAX_PAGES, PAGE_SIZE, PROMPT_MAX_NOTE, SCOPE_NOTE, eventLevel, sameSlotSummary, fetchDates, fetchUsers, fetchSameSlot, fetchEventPage, formatScore, isNotable, isSummaryItem, networkBreakdownParts, occurrenceSummary, promptScoreRows, shouldNotify, viewStatus } from "../lib/events.js";
import { watchEvents } from "../lib/live.js";
import PromptTester from "./PromptTester.jsx";
import RiskOverview from "./RiskOverview.jsx";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  Search,
  Clock,
  User,
  Network,
  MessageSquareWarning,
  ChevronDown,
  Radio,
  Download,
  Pause,
  Play,
  ArrowUpDown,
  TriangleAlert,
  X,
  RefreshCw,
  StepForward,
  Inbox,
  WifiOff,
  Flame,
  FlaskConical,
  Sparkles,
  Loader2,
} from "lucide-react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip as RTooltip,
} from "recharts";

// 화면은 기본적으로 실제 FDS API를 조회합니다. 데모는 환경변수로 명시적으로 켭니다.
const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const POLL_MS = 5000;
const INITIAL_TICK = 1;

// 위협 카탈로그 — 코드/이름/상태별 설명. base 는 더미 기여도 계산용(추후 SHAP 값으로 교체).
const NETWORK_ITEMS = [
  { code: "N1", label: "비정상 대량 업로드", base: 24, details: { normal: "정상 범위 내", caution: "평균 대비 2.4배 업로드", danger: "8.4MB/s (평균 대비 6.0배)" } },
  { code: "N2", label: "저속·분산 누적 전송", base: 14, details: { normal: "누적 전송 정상 범위", caution: "장시간 소량 전송 누적 (평균 대비 1.6배)", danger: "1시간 누적 전송 평균 대비 4.2배" } },
  { code: "N3", label: "자동화·요청 폭주", base: 16, details: { normal: "정상 범위 내", caution: "5분간 요청 12회 (평균 대비 2.3배)", danger: "5분간 요청 41회 · 일정 간격 반복" } },
  { code: "N4", label: "미승인 AI 목적지", base: 24, details: { normal: "미탐지", caution: "비표준 도메인 접근 이력 1건", danger: "화이트리스트 외 API 엔드포인트 접근 감지" } },
  { code: "N5", label: "Gateway·Proxy 우회", base: 12, details: { normal: "정상 Gateway 경유", caution: "Proxy 설정 변경 이력", danger: "Gateway 우회 경로 사용 감지" } },
  { code: "N6", label: "차단 후 우회·재시도", base: 10, details: { normal: "차단 이력 없음", caution: "동일 IP 반복 재접속 3회", danger: "차단 직후 15분 내 우회 재접속" } },
];

const PROMPT_ITEMS = [
  { code: "AI_DISTILL", label: "인공지능 증류", base: 20, details: { normal: "미탐지", caution: "유사 질의 반복 패턴 일부 감지", danger: "유사 질의 대량 반복 (증류 의심)" } },
  { code: "MISUSE", label: "업무 목적 외 오남용", base: 26, details: { normal: "미탐지", caution: "업무 외 주제 질의 일부 포함", danger: "업무 무관 대용량 문서 요약 요청 감지" } },
  { code: "TOKEN_WASTE", label: "토큰 자원 낭비", base: 12, details: { normal: "정상 범위 내", caution: "토큰 사용량 평균 대비 2배", danger: "무의미 반복 입력으로 토큰 과다 소모" } },
  { code: "PROMPT_INJECTION", label: "모델 교란", base: 42, details: { normal: "미탐지", caution: "특수 지시어 패턴 일부 포함", danger: "정책 우회 시도(지시 무시 요청) 감지" } },
];

function buildItems(catalog, states = {}) {
  return catalog.map((c) => {
    const status = states[c.code] || "normal";
    return { code: c.code, label: c.label, status, detail: c.details[status] ?? c.details.caution };
  });
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
      { score: 63, confidence: 68, states: { N2: "caution", N4: "warning", N6: "warning", MISUSE: "caution", PROMPT_INJECTION: "caution" } },
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
    endedAtMs: MOUNT_TS,
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

// 요약도 내부 FDS API에서 저장된 결과로 만듭니다. 외부 LLM으로 데이터를 보내지 않습니다.
async function explainEvent(event) {
  if (USE_MOCK) return { text: buildFallbackSummary(event), source: "demo" };
  const key = event.scope === "company" || event.scope === "user_device" ? "window_id" : "capture_id";
  const res = await fetch(`${API_BASE}/api/v1/dashboard/explanation?${key}=${encodeURIComponent(event.id)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// 서버 요약은 줄바꿈으로 문장이 나뉘어 옵니다. 줄 단위로 보여 주고, ISO 시각(UTC)은 보기 쉬운 현지 시각으로 바꿉니다.
const ISO_TIME = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})/g;
const LLM_PREVIEW_LINES = 4;
function summaryLines(text) {
  return String(text ?? "").split("\n").map((line) => line.trim()).filter(Boolean).map((line) => line.replace(ISO_TIME, (m) => {
    const d = new Date(m);
    return Number.isNaN(d.getTime()) ? m : d.toLocaleString("ko-KR", { year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  }));
}

function useEvents({ live, filters }) {
  const [events, setEvents] = useState([]);
  const [total, setTotal] = useState(0);
  const [mode, setMode] = useState("polling");
  const [status, setStatus] = useState("loading"); // loading | ok | error
  const [error, setError] = useState(null);
  const [lastOkAt, setLastOkAt] = useState(null);
  const [tick, setTick] = useState(INITIAL_TICK);
  const [reloadKey, setReloadKey] = useState(0);
  const [pages, setPages] = useState(1); // 요청한 페이지 수 (200건 단위)
  const [loadedPages, setLoadedPages] = useState(1); // 화면에 반영된 페이지 수
  const firstLoad = useRef(true);

  // mock: tick → 이벤트 생성 (최초 1회는 로딩 상태를 잠깐 보여줌)
  useEffect(() => {
    if (!USE_MOCK) return undefined;
    const delay = firstLoad.current ? 450 : 0;
    firstLoad.current = false;
    const t = setTimeout(() => {
      setEvents(buildMockEvents(tick));
      setTotal(SCENARIO.length);
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

  // real: 서버 변경 알림을 받으면 즉시 조회. 스트림 장애 시에만 5초 대체 조회.
  useEffect(() => {
    if (USE_MOCK) return undefined;
    const onData = (data) => {
        setEvents(data.events);
        setTotal(data.total);
        setLoadedPages(pages);
        setStatus("ok");
        setError(null);
        setLastOkAt(new Date());
    };
    const onError = (e) => {
        setStatus("error");
        setError(e.message || "요청 실패");
    };
    if (live) return watchEvents({ base: API_BASE,
      fetchPage: (signal) => fetchEventPage(API_BASE, signal, fetch, pages, filters), onData, onError, onMode: setMode });
    const ctrl = new AbortController();
    fetchEventPage(API_BASE, ctrl.signal, fetch, pages, filters).then((data) => {
      if (!ctrl.signal.aborted) onData(data);
    }).catch((error) => { if (!ctrl.signal.aborted) onError(error); });
    return () => ctrl.abort();
  }, [live, reloadKey, pages, filters]);

  return {
    events,
    total,
    mode,
    status,
    error,
    lastOkAt,
    isMock: USE_MOCK,
    advance: () => setTick((t) => t + 1),
    refresh: () => setReloadKey((k) => k + 1),
    hasMore: !USE_MOCK && events.length < total && pages < MAX_PAGES,
    capped: !USE_MOCK && events.length < total && pages >= MAX_PAGES,
    loadingMore: pages !== loadedPages && status !== "error",
    loadMore: () => setPages((p) => Math.min(p + 1, MAX_PAGES)),
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
  const strength = (i) => i.probability ?? (i.score != null ? i.score / 100 : 0);
  return [...items].sort(
    (a, b) => LEVELS[viewStatus(b)].rank - LEVELS[viewStatus(a)].rank || strength(b) - strength(a)
  );
}

// 개별 엔진이 탐지한 항목(통합 등급과 별개). 실데이터·데모 모두 같은 기준을 씁니다.
function topContributors(event, n = 3) {
  return sortItems([...event.network_reasons, ...event.prompt_reasons]).filter(isNotable).slice(0, n);
}

function buildFallbackSummary(e) {
  if (e.isReal) {
    const grade = eventLevel(e);
    const head = ["pending", "error"].includes(grade) ? e.reason
      : `통합 등급 ${LEVELS[grade].label}${e.score != null ? `(${formatScore(e.score)}점)` : ""}.${e.override ? ` 강제 위험 규칙 적용: ${e.overrideReasons.join(" / ")}.` : ""}`;
    return `${e.user}: ${head}\n저장된 화면 데이터 기준이며 최신 서버 요약 조회에 실패했습니다.`;
  }
  const level = LEVELS[eventLevel(e)].label;
  const top = topContributors(e, 3);
  if (!top.length) return `${e.user} 세션은 모든 탐지 항목이 정상 범위여서 ${e.score}점(${level})으로 산정되었습니다.`;
  const parts = top.map((t) => `${t.label}(${t.detail})`).join(", ");
  return `${e.user} 세션은 ${parts} 등이 주된 원인이 되어 ${e.score}점(${level})으로 산정되었습니다.`;
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
  if (status === "danger" || status === "warning") return <ShieldAlert size={size} color={LEVELS[status].color} />;
  if (status !== "normal") return <ShieldQuestion size={size} color={levelText(status)} />;
  return <ShieldCheck size={size} color={LEVELS.normal.color} />;
}

const LABEL_TAIL = /\s*라벨 설명이며 개별 판단 근거는 미제공\.?\s*$/; // 모든 Data 항목에 반복되는 꼬리 문구는 그룹 안내로 한 번만 보여 줍니다.

function EvidenceItem({ item, compact = false }) {
  const status = viewStatus(item);
  const { color, label } = LEVELS[status];
  const metric = item.probability != null
    ? `확률 ${(item.probability * 100).toFixed(1)}%`
    : item.score != null ? `${formatScore(item.score)}점` : "";
  if (compact) {
    return (
      <li className="evidence-item evidence-item--compact">
        <StatusIcon status={status} size={13} />
        <span className="evidence-item__code">{item.code}</span>
        <span className="evidence-item__short">{item.label}</span>
        <span className="evidence-item__metric">{metric}</span>
      </li>
    );
  }
  return (
    <li className="evidence-item evidence-item--hit" style={{ borderLeftColor: color }}>
      <div className="evidence-item__text">
        <div className="evidence-item__row">
          <span className="evidence-item__label">
            <span className="evidence-item__code">{item.code}</span>
            {item.label}
          </span>
          <span className="evidence-item__chip" style={{ color, borderColor: color }}>{label}</span>
        </div>
        {item.detail && <span className="evidence-item__detail">{item.detail.replace(LABEL_TAIL, "")}</span>}
        {item.probability != null && !/확률\s*[\d.]+%/.test(item.detail ?? "") && (
          <span className="evidence-item__metric-line" style={{ color }}>
            모델 확률 {(item.probability * 100).toFixed(1)}%
            {item.detection_method === 'argmax' ? ' · 최다 확률 클래스 기준' : item.threshold != null ? ` · 판정 기준 ${(item.threshold * 100).toFixed(1)}% 초과` : ''}
          </span>
        )}
        {item.probability == null && item.score != null && !/확률\s*[\d.]+%/.test(item.detail ?? "") && (
          <span className="evidence-item__metric-line" style={{ color }}>항목 점수 {formatScore(item.score)}/100</span>
        )}
      </div>
    </li>
  );
}

function EvidenceGroup({ title, icon, items: allItems, accent, expanded, onToggle, note, embedded = false }) {
  const summary = allItems.filter(isSummaryItem);
  const items = allItems.filter((i) => !isSummaryItem(i));
  const sorted = sortItems(items);
  const shown = sorted.filter((i) => isNotable(i) || i.status === "error");
  const folded = sorted.filter((i) => !shown.includes(i));
  const pending = items.filter((i) => i.status === "pending").length;
  const rows = expanded ? sorted : shown;

  return (
    <div className={`evidence-group ${embedded ? "evidence-group--embedded" : ""}`} style={{ "--line-accent": accent }}>
      <div className="evidence-group__head">
        {icon}
        <span>{title}</span>
        <span className="evidence-group__count">
          탐지 {items.filter(isNotable).length}개 · 전체 {items.length}개{pending > 0 && ` · 미분석 ${pending}개`}
        </span>
      </div>
      {items.length === 0 && <div className="evidence-empty">분석 결과가 아직 없습니다.</div>}
      {items.length > 0 && shown.length === 0 && <div className="evidence-empty">탐지된 항목이 없습니다.</div>}
      <ul className="evidence-list">
        {rows.map((item) => (
          <EvidenceItem key={item.id ?? item.code} item={item} compact={!(isNotable(item) || item.status === "error")} />
        ))}
      </ul>
      {folded.length > 0 && (
        <button className="collapse-btn" onClick={onToggle}>
          <ChevronDown size={13} style={{ transform: expanded ? "rotate(180deg)" : "none", transition: "transform .2s" }} />
          {expanded ? "탐지 없는 항목 접기" : `탐지 없는 ${folded.length}개 항목 보기`}
        </button>
      )}
      {summary.some((i) => i.status === "error" || i.status === "pending") && (
        <details className="evidence-summary">
          <summary>네트워크 종합·모델 상태 ({summary.length})</summary>
          {summary.map((i) => (
            <div key={i.id ?? i.code} className="evidence-summary__row">
              <span className="evidence-item__code">{i.code}</span> {i.label}
              {i.score != null && <b> · {formatScore(i.score)}/100</b>}
              {i.detail && <p>{i.detail}</p>}
            </div>
          ))}
        </details>
      )}
      {note && <details className="fold"><summary>안내 보기</summary><div className="evidence-note">{note}</div></details>}
    </div>
  );
}

function TopIssues({ event }) {
  const top = topContributors(event, 3);
  return (
    <div className="top-issues">
      <span className="top-issues__title">
        <Flame size={14} /> {event.isReal ? "개별 엔진 탐지" : `핵심 원인 TOP ${top.length || 3}`}
      </span>
      {top.length === 0 ? (
        <span className="top-issues__none">
          {event.isReal ? "탐지된 항목 없음" : "특이 징후 없음 · 데모 판정"}
        </span>
      ) : (
        <div className="top-issues__chips">
          {top.map((t, i) => (
            <span key={t.code} className="issue-chip" style={{ borderColor: LEVELS[viewStatus(t)].color }}>
              <b style={{ color: levelText(viewStatus(t)) }}>{i + 1}</b>
              <span className="issue-chip__code">{t.code}</span>
              {t.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function RiskScoreBadge({ score, level }) {
  const { color, label } = LEVELS[level];
  const circumference = 2 * Math.PI * 54;
  const offset = circumference - ((score ?? 0) / 100) * circumference;
  const unit = score != null ? "/ 100" : "";

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
        <span className="score-badge__num" style={{ color: levelText(level), transition: "color 0.6s ease" }}>{formatScore(score)}</span>
        <span className="score-badge__unit">{unit}</span>
      </div>
      <span className="score-badge__label" style={{ color: levelText(level), borderColor: color }}>{label}</span>
    </div>
  );
}

// 판단 불가(미판정) 건은 안전 판정이 아니라서, 담당자가 직접 확인하도록 같은 문구로 표시합니다.
const REVIEW_LABEL = "담당자 확인 요망";
function ReviewBadge() {
  return <span className="review-badge">{REVIEW_LABEL}</span>;
}

function SessionRow({ session, active, flashing, onClick, onHover, onLeave }) {
  const level = eventLevel(session);
  const { color } = LEVELS[level];
  const hits = session.isReal ? topContributors(session, 1) : [];
  const hitCount = session.isReal ? [...session.network_reasons, ...session.prompt_reasons].filter(isNotable).length : 0;
  const when = typeof session.connectedAt === "string" ? session.connectedAt.replace(/^\d{4}\.\s*/, "") : session.connectedAt; // 연도는 줄여 한 줄에 담습니다
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
        <div className="session-row__top">
          <span className="session-row__user">{session.user}</span>
          <span className="session-row__meta">{when}</span>
        </div>
        {(hits.length > 0 || level === "pending") && (
          <span className="session-row__find" style={{ color: hits.length > 0 ? levelText(viewStatus(hits[0])) : undefined }}>
            {level === "pending" && <b className="session-row__review">{REVIEW_LABEL}</b>}
            {level === "pending" && hits.length > 0 && " · "}
            {hits.length > 0 && <>{hits[0].code} {hits[0].label.replace(/\s*·\s*\d+분 집계$/, "")}{hitCount > 1 ? ` 외 ${hitCount - 1}건` : ""}</>}
          </span>
        )}
      </div>
      <span className="session-row__score" style={{ color: levelText(level), transition: "color 0.6s ease" }}>{session.score != null ? formatScore(session.score) : LEVELS[level].label}</span>
    </button>
  );
}

const TOOLTIP_W = 250;
const TOOLTIP_H_EST = 170;
const EDGE_MARGIN = 12;

function HoverTooltip({ session, pos }) {
  if (!session) return null;
  const level = eventLevel(session);
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
        <span style={{ color: levelText(level) }}>{session.score == null ? "점수 미제공" : `${formatScore(session.score)}점`}</span>
        <span className="hover-tooltip__badge" style={{ color: levelText(level), borderColor: LEVELS[level].color }}>
          {LEVELS[level].label}
        </span>
        {level === "pending" && <ReviewBadge />}
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
      <div className="hover-tooltip__id">구간 ID {session.sessionId ?? session.id}</div>
    </div>
  );
}

function LevelBar({ counts, total }) {
  const segs = [
    { key: "danger", value: counts.danger },
    { key: "warning", value: counts.warning },
    { key: "caution", value: counts.caution },
    { key: "normal", value: counts.normal },
    { key: "pending", value: counts.pending },
    { key: "error", value: counts.error },
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
  if (!history.some((v) => v != null)) return <div className="evidence-empty">통합 점수 정책이 연결되면 표시됩니다.</div>;
  const data = history.filter((v) => v != null).map((v, i) => ({ i, score: v }));
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

// 완료된 캡처의 관측 구간은 화면을 켜 두어도 증가하지 않습니다.
function LiveDuration({ startedAtMs, endedAtMs }) {
  if (startedAtMs == null || endedAtMs == null) return <>—</>;
  return <>{formatDuration(endedAtMs - startedAtMs)}</>;
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

// 5분 구간의 프롬프트 점수 목록과 네트워크 점수 구성. 서버 값만 표시하고 없는 값은 미제공/미판정으로 둡니다.
// "AI 탈취 1, 프롬프트 인젝션 2" 같은 반복 횟수 문구를 작은 칩으로 보여 줍니다(2회 이상은 노랗게).
function OccurrenceChips({ text }) {
  if (text == null) return <span className="c-muted">—</span>;
  if (text === "없음") return <span className="c-muted">없음</span>;
  if (text === "집계 실패") return <span className="rep-chip rep-chip--warn">집계 실패</span>;
  return (
    <div className="rep-chips">
      {text.split(", ").map((part) => {
        const m = part.match(/^(.*) (\d+)$/);
        return (
          <span key={part} className={`rep-chip ${m && Number(m[2]) > 1 ? "is-repeat" : ""}`}>
            {m ? m[1] : part}{m && <b>{m[2]}</b>}
          </span>
        );
      })}
    </div>
  );
}

// 잘려 보이는 ID 위에 마우스를 올리면 전체 값이 바로 뜹니다(브라우저 기본 title은 느리고 표 안에서는 안 뜨기도 함). 클릭하면 복사합니다.
function FullId({ id, as: Tag = "span", className = "", children }) {
  const [tip, setTip] = useState(null);
  const [copied, setCopied] = useState(false);
  const show = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setTip({ x: Math.max(8, Math.min(rect.left, window.innerWidth - 528)), y: rect.top });
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch { /* 복사 권한이 없으면 조용히 넘어갑니다 */ }
  };
  return (
    <Tag className={`fullid ${className}`} onMouseEnter={show} onMouseLeave={() => { setTip(null); setCopied(false); }} onClick={copy}>
      {children ?? id}
      {tip && <span className="id-tip" role="tooltip" style={{ left: tip.x, top: tip.y }}>{id}<em>{copied ? "복사했어요" : "클릭하면 복사"}</em></span>}
    </Tag>
  );
}

// 긴 capture ID는 서로 다른 뒷부분만 보여 줍니다(전체는 마우스를 올리면 나옵니다).
const shortCaptureId = (id) => (id.length > 24 ? `…${id.slice(-12)}` : id);

// 점수가 있는 프롬프트(또는 반영 행·미판정·오류)만 먼저 보이고, 0점 정상 행은 접어 둡니다.
const showsFirst = (r) => r.isMax || r.status !== "complete" || (r.score ?? 0) > 0;

function CompanyBreakdown({ event, promptEvidence, networkEvidence }) {
  const [showRest, setShowRest] = useState(false);
  const rows = promptScoreRows(event);
  const network = networkBreakdownParts(event.network_score_breakdown);
  const statusLabel = { complete: "완료", pending: "미판정", error: "오류" };
  const hasOccurrence = rows.some((r) => r.occurrence != null);
  const showUser = rows.some((r) => r.userId !== event.user); // 모두 같은 사용자면 열을 숨깁니다
  const showState = rows.some((r) => r.status !== "complete"); // 모두 완료면 열을 숨기고 위 건수로 갈음합니다
  const shownRows = showRest ? rows : rows.filter(showsFirst);
  const hiddenCount = rows.length - rows.filter(showsFirst).length;
  return (
    <div className="company-breakdown">
      <section className="company-breakdown__col company-breakdown__col--accent" style={{ "--line-accent": "#7A4FB5" }}>
        <h4>프롬프트 분석 {event.capture_count ?? 0}건 · 최고 점수만 반영</h4>
        <p className="company-breakdown__counts">
          완료 {event.prompt_complete_count ?? 0} · 미판정 {event.prompt_missing_count ?? 0} · 오류 {event.prompt_error_count ?? 0}
        </p>
        <details className="fold"><summary>반영 방식 보기</summary><p className="company-breakdown__note">{PROMPT_MAX_NOTE} 미판정·오류는 0점으로 바꾸지 않으며, 하나라도 있으면 통합 점수는 미판정입니다.</p></details>
        {rows.length > 0 ? (
          <div className="company-breakdown__table">
            <table>
              <thead><tr><th className="c-idcol">capture ID</th>{showUser && <th className="c-user">사용자</th>}<th className="c-num">점수</th>{showState && <th className="c-state">상태</th>}{hasOccurrence && <th className="c-rep" title="문장별 반복 횟수(점수 반영, 라벨당 최대 3회)">반복</th>}</tr></thead>
              <tbody>
                {shownRows.map((r) => (
                  <tr key={r.captureId} className={r.isMax ? "is-max" : ""}>
                    <td className="c-id"><div className="c-id__wrap"><FullId id={r.captureId} className="c-id__text">{shortCaptureId(r.captureId)}</FullId>{r.isMax && <b className="c-tag">반영</b>}</div></td>
                    {showUser && <td className="c-user">{r.userId}</td>}
                    <td className="c-num">{r.score == null ? "—" : `${formatScore(r.score)}/60`}</td>
                    {showState && <td className="c-state">{statusLabel[r.status]}</td>}
                    {hasOccurrence && <td className="c-rep"><OccurrenceChips text={r.occurrence} /></td>}
                  </tr>
                ))}
              </tbody>
            </table>
            {shownRows.length === 0 && <p className="company-breakdown__note">점수가 있는 프롬프트가 없습니다.</p>}
          </div>
        ) : <p className="company-breakdown__note">개별 프롬프트 점수 목록이 없는 이전 구간입니다.</p>}
        {hiddenCount > 0 && (
          <button className="collapse-btn" onClick={() => setShowRest((v) => !v)} aria-expanded={showRest}>
            <ChevronDown size={13} style={{ transform: showRest ? "rotate(180deg)" : "none" }} />
            {showRest ? "점수 없는 프롬프트 접기" : `나머지 ${hiddenCount}건 보기 · 점수 0`}
          </button>
        )}
        {promptEvidence}
      </section>
      <section className="company-breakdown__col company-breakdown__col--accent" style={{ "--line-accent": "#33429A" }}>
        <h4>네트워크 점수 구성</h4>
        {network ? (
          <>
            <ul className="breakdown-list">
              {network.parts.map((p) => (
                <li key={p.key}><span>{p.label}</span><b>{p.value == null ? "미제공" : formatScore(p.value)}</b></li>
              ))}
              <li className="is-total"><span>네트워크 점수</span><b>{formatScore(network.total)}</b></li>
            </ul>
            <details className="fold"><summary>산정 방식 보기</summary><p className="company-breakdown__note">
              네트워크 모델 보고서의 값을 그대로 표시합니다(합계는 반올림·상한 100 적용). 반영 점수 = {formatScore(network.total)} × 0.4 = {formatScore(event.network_contribution)}/40.
              반복 가산점 '미제공'은 현재 모델 설정에서 반복 가산을 쓰지 않는다는 뜻입니다.
            </p></details>
          </>
        ) : <p className="company-breakdown__note">네트워크 점수 구성 정보가 없습니다(분석 대기 또는 이전 버전 결과).</p>}
        {networkEvidence}
      </section>
    </div>
  );
}

// 구간 요약 카드: 시간·대상 → 점수 산식(타일) → 최고 점수 근거 → 접을 수 있는 판정 기준 안내.
// 값은 서버 응답만 쓰며, 미판정 값은 '—'로 둡니다.
function WindowSummaryCard({ event }) {
  const level = eventLevel(event);
  const fmtHm = (ms) => (ms == null ? "--:--" : new Date(ms).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false }));
  const day = event.startedAtMs == null ? "" : new Date(event.startedAtMs).toLocaleDateString("ko-KR");
  const legacy = event.scope === "company";
  const capture = event.prompt_source_capture_id;
  const showSource = !!capture && promptScoreRows(event).length === 0; // 표의 '반영' 행과 같은 정보라 표가 없을 때만 보여 줍니다
  return (
    <div className="window-card" role="note">
      <div className="window-card__head">
        <span className={`window-card__phase ${event.phase === "open" ? "is-open" : ""}`}>
          {event.phase === "open" ? "집계 중 · 잠정" : "종료된 구간"}
        </span>
        <span className="window-card__when">{day} {fmtHm(event.startedAtMs)} ~ {fmtHm(event.endedAtMs)}</span>
        {legacy && <span className="window-card__who">회사 전체(이전 버전)</span>}
      </div>

      <div className="window-card__formula" aria-label="통합 점수 산식">
        <div className="score-tile">
          <span className="score-tile__label">프롬프트 최고</span>
          <span className="score-tile__value">{formatScore(event.prompt_max_score)}<small>/60</small></span>
          <span className="score-tile__sub">구간 내 최고 1건</span>
        </div>
        <span className="window-card__op">+</span>
        <div className="score-tile">
          <span className="score-tile__label">네트워크 반영</span>
          <span className="score-tile__value">{formatScore(event.network_contribution)}<small>/40</small></span>
          <span className="score-tile__sub">{formatScore(event.network_score)}점 × 0.4</span>
        </div>
        <span className="window-card__op">=</span>
        <div className="score-tile score-tile--total" style={{ borderColor: LEVELS[level].color }}>
          <span className="score-tile__label">통합 점수</span>
          <span className="score-tile__value" style={{ color: levelText(level) }}>{formatScore(event.score)}<small>/100</small></span>
          <span className="score-tile__sub" style={{ color: levelText(level), fontWeight: 700 }}>{LEVELS[level].label}{level === "pending" && <ReviewBadge />}</span>
        </div>
      </div>

      {event.override && (
        <div className="override-note" role="note">
          <ShieldAlert size={14} color={LEVELS.danger.color} />
          <span>
            <b>강제 위험 규칙 적용</b> · 합계 점수와 관계없이 위험으로 판정됨
            {event.overrideReasons?.length > 0 && <> ({event.overrideReasons.join(" / ")})</>}
          </span>
        </div>
      )}

      {(showSource || occurrenceSummary(event)) && (
        <div className="window-card__extra">
          {showSource && (
            <div className="window-card__source">
              <span>최고 점수 근거</span>
              <FullId as="code" id={capture} />
              {event.prompt_source_user_id && event.prompt_source_user_id !== event.user && <span className="window-card__muted">사용자 {event.prompt_source_user_id}</span>}
            </div>
          )}
          <OccurrenceRow event={event} />
        </div>
      )}
      {event.fusion_status !== "complete" && <p className="window-card__reason">{event.reason}</p>}

      <details className="window-card__notes fold">
        <summary>검토 우선순위용 지표이며 위반 확정이 아닙니다 · 판정 기준 보기</summary>
        <ul>
          {SCOPE_NOTE.map((line) => <li key={line}>{line}</li>)}
          <li>늦게 도착한 기록이 있으면 구간 결과가 갱신됩니다.</li>
        </ul>
      </details>
    </div>
  );
}

// 문장별 반복 횟수(참고). 서버가 집계한 구간에서만 보이며 점수·등급과 무관합니다.
function OccurrenceRow({ event }) {
  const s = occurrenceSummary(event);
  if (!s) return null;
  return (
    <div className="window-card__occurrence" title="같은 위험 라벨이 몇 문장에서 탐지됐는지 셉니다. 반복될수록 탐지 계수를 한 번 더 곱해 프롬프트 점수가 올라갑니다(라벨당 최대 3회).">
      <span>반복 탐지</span>
      {s.detected.length
        ? s.detected.map((i) => <b key={i.code} className={i.count > 1 ? "is-repeat" : ""}>{i.label} {i.count}회</b>)
        : <span className="window-card__muted">탐지된 항목 없음</span>}
      <span className="window-card__muted">프롬프트 {s.counted}/{s.total}건 집계 · 점수 반영(최대 3회)</span>
    </div>
  );
}

// 날짜·사용자 선택 목록. 수집 결과가 바뀌면(목록 건수 변화) 다시 읽습니다.
function useScopeOptions(date, version) {
  const [dates, setDates] = useState([]);
  const [users, setUsers] = useState([]);
  useEffect(() => {
    if (USE_MOCK) return undefined;
    const ctrl = new AbortController();
    fetchDates(API_BASE, ctrl.signal).then(setDates).catch(() => {});
    fetchUsers(API_BASE, date, ctrl.signal).then(setUsers).catch(() => {});
    return () => ctrl.abort();
  }, [date, version]);
  return { dates, users };
}

// 같은 5분 시간대의 다른 사용자 구간. 사용자 필터와 무관하게 서버에서 그 시간대 전체를 읽습니다.
// 클릭하면 그 구간 상세로 이동합니다. 회사 점수를 따로 계산하지 않습니다.
function SameSlotPanel({ selected, events, isMock, onSelect }) {
  const [rows, setRows] = useState(null);
  useEffect(() => {
    if (isMock || !selected.windowStart) return undefined;
    const ctrl = new AbortController();
    fetchSameSlot(API_BASE, selected.windowStart, ctrl.signal).then(setRows).catch(() => setRows(null));
    return () => ctrl.abort();
  }, [isMock, selected.windowStart, selected.revision]);
  const summary = sameSlotSummary(rows ?? events, selected);
  if (!summary) return null;
  const { windows, counts, pending, error, userCount } = summary;
  const tiles = [
    ...["danger", "warning", "caution", "normal"].map((g) => ({ key: g, label: LEVELS[g].label, value: counts[g], dot: LEVELS[g].color, color: levelText(g) })),
    { key: "pending", label: "미판정", value: pending, dot: LEVELS.pending.color, color: LEVELS.pending.color },
    ...(error ? [{ key: "error", label: "오류", value: error, dot: LEVELS.error.color, color: LEVELS.error.color }] : []),
  ];
  return (
    <div className="company-breakdown company-breakdown--single">
      <section className="company-breakdown__col">
        <h4>같은 시간대 사용자 구간 · {userCount}명</h4>
        <div className="slot-layout">
          <div className="company-breakdown__table slot-layout__table">
            <table>
              <thead><tr><th>사용자 · 단말</th><th className="c-grade">등급</th><th className="c-num">통합 점수</th><th className="c-num">프롬프트 최고</th></tr></thead>
              <tbody>
                {windows.map((w) => (
                  <tr key={w.id} className={w.id === selected.id ? "is-max" : "is-link"} onClick={() => onSelect(w)}
                    title="클릭하면 이 사용자 구간을 엽니다">
                    <td>{w.user} <span className="c-sub">{w.device_id}</span>{w.id === selected.id && <b className="c-tag">현재</b>}</td>
                    <td className="c-grade" style={{ color: levelText(eventLevel(w)) }}><i style={{ background: LEVELS[eventLevel(w)].color }} />{LEVELS[eventLevel(w)].label}</td>
                    <td className="c-num">{formatScore(w.score)}</td>
                    <td className="c-num">{w.prompt_max_score == null ? "—" : `${formatScore(w.prompt_max_score)}/60`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="slot-counts" role="group" aria-label="등급별 사용자 수">
            {tiles.map((t) => (
              <div key={t.key} className={`slot-count ${t.value ? "" : "slot-count--zero"}`}>
                <span className="slot-count__num" style={{ color: t.color }}>{t.value}</span>
                <span className="slot-count__label"><i style={{ background: t.dot }} />{t.label}</span>
              </div>
            ))}
          </div>
        </div>
        <details className="fold"><summary>집계 기준 보기</summary><p className="company-breakdown__note">{rows ? "서버 기준 같은 시간대 전체 사용자입니다." : "화면에 불러온 구간 기준입니다."} 등급 수만 세며 회사 점수를 합산·평균하지 않습니다. 미판정은 정상으로 세지 않습니다.</p></details>
      </section>
    </div>
  );
}

export default function RiskDashboard() {
  const [live, setLive] = useState(true);
  const [testerOpen, setTesterOpen] = useState(false); // 프롬프트 직접 테스트 패널
  const [view, setView] = useState(USE_MOCK ? "detail" : "overview"); // overview: 기본 그래프, detail: 구간 상세 분석
  const [dateFilter, setDateFilter] = useState(""); // 한국 시간 YYYY-MM-DD, 빈 값은 전체 날짜
  const [userFilter, setUserFilter] = useState(""); // 빈 값은 전체 사용자
  const filters = useMemo(() => ({ date: dateFilter, userId: userFilter }), [dateFilter, userFilter]);
  const { events, total, mode, status, error, lastOkAt, isMock, advance, refresh, hasMore, capped, loadingMore, loadMore } = useEvents({ live, filters });
  const [scopeVersion, setScopeVersion] = useState(0); // 기록을 비운 뒤 날짜·사용자 목록을 다시 읽기 위한 번호
  const { dates, users } = useScopeOptions(dateFilter, `${events.length}:${scopeVersion}`);

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
        if (e.score != null && (USE_MOCK || next[e.id]?.at(-1) !== e.score)) {
          next[e.id] = [...(next[e.id] || []), e.score].slice(-12);
        }
      });
      return next;
    });
  }, [events]);

  // 등급 전환 감지 → 토스트/플래시 (히스테리시스 적용)
  useEffect(() => {
    if (!events.length) return;
    if (notifiedRef.current === null) {
      notifiedRef.current = Object.fromEntries(events.map((e) => [e.id, eventLevel(e)]));
      return;
    }
    const newToasts = [];
    const newFlash = {};
    events.forEach((e) => {
      const lv = eventLevel(e);
      const old = notifiedRef.current[e.id];
      if (old === undefined) {
        notifiedRef.current[e.id] = lv;
        return;
      }
      if (shouldNotify(old, lv, e.score, e.isReal)) {
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
        q === "" || [s.user, s.id, s.sessionId ?? "", s.dept].some((value) => value.toLowerCase().includes(q));
      const matchesFilter = filter === "all" || eventLevel(s) === filter;
      return matchesQuery && matchesFilter;
    });
    if (sortBy === "score_desc") {
      list = [...list].sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
    } else if (sortBy === "recent") {
      list = [...list].sort((a, b) =>
        a.startedAtMs && b.startedAtMs ? b.startedAtMs - a.startedAtMs : a.connectedAt < b.connectedAt ? 1 : -1
      );
    }
    return list;
  }, [events, query, filter, sortBy]);

  const selected = filtered.find((e) => e.id === selectedId) || filtered[0] || null;

  const counts = useMemo(() => {
    const c = { danger: 0, warning: 0, caution: 0, normal: 0, pending: 0, error: 0 };
    events.forEach((s) => c[eventLevel(s)]++);
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
    const header = ["구간ID", "참조ID", "집계대상", "집계범위", "점수", "등급", "강제위험", "예측신뢰도", "구간시작", "집계상태", "프롬프트최고점수", "최고점수캡처ID", "네트워크점수"];
    const rows = filtered.map((s) => [
      s.id,
      s.sessionId ?? s.id,
      s.user,
      s.dept,
      s.score == null ? "" : formatScore(s.score),
      LEVELS[eventLevel(s)].label,
      s.override ? "Y" : "",
      s.confidence == null ? "" : `${s.confidence}%`,
      s.connectedAt,
      s.phase === "open" ? "잠정" : "종료",
      s.prompt_max_score ?? "",
      s.prompt_source_capture_id ?? "",
      s.network_score ?? "",
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

  // 저장 결과 요약 — 캡처별 상태 관리(로딩이 다른 세션 화면에 번지지 않음), 실패 시 규칙 기반 요약으로 대체
  async function generateExplanation() {
    if (!selected) return;
    const target = selected;
    const snapshot = { atScore: target.score, atLevel: eventLevel(target), revision: target.revision };
    setLlm((p) => ({ ...p, [target.id]: { ...(p[target.id] || {}), status: "loading" } }));
    try {
      const r = await explainEvent(target);
      setLlm((p) => ({ ...p, [target.id]: { status: "done", text: r.text, source: r.source, ...snapshot } }));
    } catch {
      setLlm((p) => ({
        ...p,
        [target.id]: { status: "done", text: buildFallbackSummary(target), source: "rule", ...snapshot },
      }));
    }
  }

  const [exOpen, setExOpen] = useState({});
  const ex = selected ? llm[selected.id] : null;
  const exLoading = ex?.status === "loading";
  const exStale =
    ex?.status === "done" &&
    selected &&
    (selected.revision !== ex.revision || selected.score !== ex.atScore || eventLevel(selected) !== ex.atLevel);
  const exLines = ex?.status === "done" ? summaryLines(ex.text) : [];

  const statusText =
    status === "error"
      ? `서버 연결 실패 · 마지막 정상 수신 ${fmtTime(lastOkAt)}`
      : status === "loading"
      ? "데이터를 불러오는 중…"
      : live
      ? `${isMock ? "데모 재생" : mode === "stream" ? "이벤트 수신 중" : "실시간 연결 재시도 · 5초 대체 조회"} · 마지막 갱신 ${fmtTime(lastOkAt)}`
      : "갱신 일시정지됨";
  const dotClass = status === "error" ? "live-dot--err" : live && status === "ok" ? "" : "live-dot--off";

  const hasWindowCard = !!selected && (selected.scope === "company" || selected.scope === "user_device"); // 구간 요약 카드가 점수·시간을 이미 보여 줍니다
  const trendPoints = selected ? (history[selected.id] || [selected.score]).filter((v) => v != null).length : 0;
  const evidenceGroups = selected ? {
    net: (
      <EvidenceGroup
        embedded={hasWindowCard}
        title={hasWindowCard ? "탐지 항목" : selected.scope === "company" ? "회사 전체 네트워크 · 5분 집계 (N1~N6, 이전 버전)" : "이 사용자·단말 네트워크 · 5분 집계 (N1~N6)"}
        icon={hasWindowCard ? null : <Network size={15} color="#5F6372" />}
        items={selected.network_reasons}
        accent="#33429A"
        note="항목 점수는 모델 클래스 확률(%)이며 통합 기여도가 아닙니다."
        expanded={openSafe.net}
        onToggle={() => setOpenSafe((s) => ({ ...s, net: !s.net }))}
      />
    ),
    prompt: (
      <EvidenceGroup
        embedded={hasWindowCard}
        title={hasWindowCard ? "탐지 항목" : "구간 내 최고 점수 프롬프트 분석"}
        icon={hasWindowCard ? null : <MessageSquareWarning size={15} color="#5F6372" />}
        items={selected.prompt_reasons}
        accent="#7A4FB5"
        note="확률은 라벨 분류 결과이며 위험도 점수가 아닙니다. 항목별 판단 근거는 제공되지 않습니다. 미탐지는 안전 판정이 아닙니다."
        expanded={openSafe.prompt}
        onToggle={() => setOpenSafe((s) => ({ ...s, prompt: !s.prompt }))}
      />
    ),
  } : null;
  const showTrend = !!selected && (selected.score != null || !selected.isReal) && trendPoints > 1;

  return (
    <div className="dash">
      <style>{`
        /* ───────── 디자인 토큰 ───────── */
        .dash {
          --bg: #F6F7F9;
          --panel: #FFFFFF;
          --tint: #F3F4F7;
          --border: #E7E9EE;
          --border-strong: #D5D8E0;
          --text: #1A1D26;
          --text-dim: #4B5060;
          --text-mute: #6E7385;
          --accent: #33429A;
          --accent-soft: #EEF0FA;
          --radius: 12px;
          --mono: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace;
          position: relative;
          font-family: "Pretendard Variable", Pretendard, "Noto Sans KR", "Apple SD Gothic Neo", -apple-system, "Malgun Gothic", sans-serif;
          font-size: 14px;
          line-height: 1.5;
          letter-spacing: -0.005em;
          -webkit-font-smoothing: antialiased;
          font-variant-numeric: tabular-nums;
          background: var(--bg);
          color: var(--text);
          width: 100%;
          box-sizing: border-box;
          overflow: hidden;
          height: 100vh; height: 100dvh; /* 화면 높이에 맞추고, 남는 공간을 본문이 채워 좌우가 각각 스크롤됩니다 */
          min-height: 560px;
          display: flex;
          flex-direction: column;
        }
        .dash * { box-sizing: border-box; }
        .dash button, .dash input, .dash select { font-family: inherit; }
        .dash button:focus-visible, .dash input:focus-visible, .dash select:focus-visible, .dash summary:focus-visible {
          outline: 2px solid var(--accent);
          outline-offset: 2px;
        }
        .dash h1, .dash h4 { margin: 0; }
        .dash code { font-family: var(--mono); }

        /* ───────── 알림 ───────── */
        .toast-stack { position: absolute; top: 14px; right: 16px; z-index: 40; display: flex; flex-direction: column; gap: 8px; width: 320px; }
        .toast {
          display: flex; align-items: center; gap: 9px;
          background: #fff; border: 1px solid var(--border); border-left: 3px solid;
          border-radius: 10px; padding: 11px 13px; font-size: 13px; color: var(--text);
          box-shadow: 0 10px 26px rgba(20,22,30,0.12); animation: toast-in .25s ease;
        }
        @keyframes toast-in { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }
        .toast__text { flex: 1; }
        .toast__close { background: transparent; border: none; color: var(--text-dim); cursor: pointer; display: flex; }

        /* ───────── 헤더: 한 줄(로고 · 탭 · 요약 · 버튼) ───────── */
        .dash-header {
          display: flex; align-items: center; gap: 12px 24px; flex-wrap: wrap;
          padding: 12px 24px; background: var(--panel); border-bottom: 1px solid var(--border);
        }
        .dash-header__title { display: flex; align-items: center; gap: 12px; min-width: 0; }
        .dash-header__icon { width: 32px; height: 32px; border-radius: 9px; background: var(--accent); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
        .dash-header__text { min-width: 0; }
        .dash-header__text h1 { font-size: 15px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.3; white-space: nowrap; }
        .dash-header__sub { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--text-mute); margin-top: 1px; flex-wrap: wrap; }
        .dash-header__count { padding-left: 6px; margin-left: 2px; border-left: 1px solid var(--border-strong); }
        .demo-chip { font-size: 11.5px; font-weight: 700; color: var(--accent); background: var(--accent-soft); border-radius: 4px; padding: 1px 6px; letter-spacing: .03em; }
        .live-dot { width: 7px; height: 7px; border-radius: 50%; background: #137D57; display: inline-block; animation: pulse 1.6s infinite; flex-shrink: 0; }
        .live-dot--off { background: #B7BAC3; animation: none; }
        .live-dot--err { background: #C6362A; animation: none; }
        @keyframes pulse {
          0% { box-shadow: 0 0 0 0 rgba(19,125,87,.35); }
          70% { box-shadow: 0 0 0 6px rgba(19,125,87,0); }
          100% { box-shadow: 0 0 0 0 rgba(19,125,87,0); }
        }
        .dash-header__right { display: flex; align-items: center; gap: 12px 16px; margin-left: auto; flex-wrap: wrap; justify-content: flex-end; }
        .dash-header__actions { display: flex; align-items: center; gap: 8px; }
        .header-stats { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
        .header-stats__row { display: flex; gap: 10px; font-size: 12.5px; color: var(--text-dim); white-space: nowrap; }
        .stat { display: inline-flex; align-items: center; gap: 5px; }
        .stat i { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
        .stat b { color: var(--text); font-weight: 650; }
        .stat--zero { opacity: .45; }
        .stat--gone { display: none; }
        .level-bar { display: flex; width: 100%; height: 4px; border-radius: 4px; overflow: hidden; background: var(--tint); }
        .level-bar__seg { height: 100%; }
        .ghost-btn {
          display: inline-flex; align-items: center; gap: 6px; background: var(--panel);
          border: 1px solid var(--border); border-radius: 8px; padding: 6px 11px;
          font-size: 13px; color: var(--text-dim); cursor: pointer; white-space: nowrap;
        }
        .ghost-btn:hover { color: var(--text); border-color: var(--border-strong); background: var(--tint); }
        .ghost-btn.is-on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
        .chat-link { display: inline-flex; align-items: center; font-size: 13px; font-weight: 600; color: var(--accent); text-decoration: none; border: 1px solid var(--border); border-radius: 8px; padding: 6px 11px; white-space: nowrap; background: var(--panel); }
        .chat-link:hover { background: var(--accent-soft); border-color: #CBD0EE; }

        /* ───────── 탭(분할 버튼) ───────── */
        .view-tabs { display: inline-flex; gap: 2px; padding: 3px; background: var(--tint); border-radius: 10px; }
        .view-tabs button { border: none; background: transparent; padding: 6px 14px; font-size: 13.5px; font-weight: 600; color: var(--text-dim); cursor: pointer; border-radius: 8px; white-space: nowrap; }
        .view-tabs button:hover { color: var(--text); }
        .view-tabs button.is-on { background: var(--panel); color: var(--accent); box-shadow: 0 1px 2px rgba(20,22,30,.08); }

        .stale-banner {
          display: flex; align-items: center; gap: 9px; padding: 9px 24px;
          background: #FBEDEB; border-bottom: 1px solid #EFC1BC; color: #8E2A20; font-size: 13px;
        }
        .stale-banner button { margin-left: auto; background: #fff; border: 1px solid #EFC1BC; color: #8E2A20; border-radius: 6px; padding: 4px 10px; font-size: 12.5px; cursor: pointer; }

        /* 본문은 남는 높이를 채우고, 왼쪽 목록·오른쪽 상세가 각각 스크롤됩니다(페이지 전체가 같이 스크롤되지 않게). */
        .dash > :not(.dash-body) { flex-shrink: 0; }
        .dash-body { display: flex; flex: 1; min-height: 0; }
        .tester-panel { border-bottom: 1px solid var(--border); background: var(--panel); max-height: 50vh; overflow-y: auto; }

        /* ───────── 왼쪽 목록 ───────── */
        .side { width: 304px; min-height: 0; flex-shrink: 0; border-right: 1px solid var(--border); display: flex; flex-direction: column; background: var(--panel); }
        .filter-bar { flex-shrink: 0; padding: 12px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }
        .scope-filters { display: flex; gap: 6px; }
        .scope-filters select { flex: 1; min-width: 0; height: 32px; padding: 0 6px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); color: var(--text); font-size: 13px; text-overflow: ellipsis; }
        .search-box { display: flex; align-items: center; gap: 7px; background: var(--tint); border: 1px solid transparent; border-radius: 8px; height: 32px; padding: 0 10px; }
        .search-box input { background: transparent; border: none; outline: none; color: var(--text); font-size: 13.5px; width: 100%; }
        .search-box input:focus-visible { outline: none; }
        .search-box:focus-within { border-color: var(--accent); background: var(--panel); }
        .filter-pills { display: flex; gap: 4px; }
        .filter-pill { flex: 1; padding: 4px 0; border-radius: 999px; border: 1px solid var(--border); background: var(--panel); color: var(--text-dim); font-size: 12.5px; cursor: pointer; white-space: nowrap; }
        .filter-pill:hover { background: var(--tint); }
        .filter-pill--active { background: var(--accent); color: #fff; border-color: var(--accent); }
        .tool-row { display: flex; gap: 6px; align-items: stretch; }
        .tool-row .sort-select { flex: 1; min-width: 0; }
        .tool-row .export-btn { flex-shrink: 0; white-space: nowrap; }
        .sort-select { display: flex; align-items: center; gap: 6px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; height: 30px; padding: 0 8px; font-size: 13px; color: var(--text-dim); }
        .sort-select select { background: transparent; border: none; outline: none; color: var(--text); font-size: 13px; flex: 1; min-width: 0; }
        .export-btn { display: flex; align-items: center; justify-content: center; gap: 5px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; height: 30px; padding: 0 10px; font-size: 13px; color: var(--accent); cursor: pointer; font-weight: 500; }
        .export-btn:hover { background: var(--accent-soft); }
        .export-btn:disabled { color: var(--text-mute); cursor: default; background: var(--panel); }

        .session-list { overflow-y: auto; overscroll-behavior: contain; min-height: 0; flex: 1; padding: 6px; display: flex; flex-direction: column; gap: 2px; }
        .session-row { display: flex; align-items: center; gap: 10px; width: 100%; text-align: left; background: transparent; border: 1px solid transparent; border-radius: 10px; padding: 9px 12px; cursor: pointer; color: var(--text); }
        .session-row:hover { background: var(--tint); }
        .session-row--active, .session-row--active:hover { background: var(--accent-soft); border-color: #CBD0EE; }
        .session-row--flash { animation: flash-row .45s ease 3; }
        @keyframes flash-row { 0%, 100% { background: transparent; border-color: transparent; } 50% { background: #FBE8E6; border-color: #EFC1BC; } }
        .session-row__dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
        .session-row__main { display: flex; flex-direction: column; gap: 1px; flex: 1; min-width: 0; }
        .session-row__top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; min-width: 0; }
        .session-row__user { font-size: 14px; font-weight: 600; white-space: nowrap; }
        .session-row__meta { font-size: 12px; color: var(--text-mute); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
        .session-row__find { font-size: 12.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .session-row__review { color: var(--text); font-weight: 700; }
        .session-row__score { font-size: 15px; font-weight: 700; min-width: 30px; text-align: right; }

        .list-empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 40px 20px; color: var(--text-dim); text-align: center; }
        .list-empty button { margin-top: 4px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 6px 14px; font-size: 13px; color: var(--accent); cursor: pointer; }
        .list-more { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 10px 6px 6px; font-size: 12.5px; color: var(--text-dim); text-align: center; }
        .list-more button { background: var(--tint); border: 1px solid var(--border); border-radius: 8px; color: var(--accent); font-size: 13px; padding: 6px 14px; cursor: pointer; }
        .list-more button:disabled { opacity: .6; cursor: default; }

        .hover-tooltip { position: fixed; z-index: 60; background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 11px 13px; font-size: 12.5px; color: var(--text); pointer-events: none; box-shadow: 0 10px 28px rgba(20,22,30,.14); }
        .hover-tooltip__head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; font-weight: 700; font-size: 14px; margin-bottom: 6px; }
        .hover-tooltip__head > span { white-space: nowrap; }
        .hover-tooltip__badge { font-size: 11.5px; border: 1px solid; border-radius: 20px; padding: 1px 8px; font-weight: 500; }
        .hover-tooltip__id { margin-top: 8px; padding-top: 6px; border-top: 1px solid var(--tint); font-family: var(--mono); font-size: 11px; color: var(--text-mute); overflow-wrap: anywhere; line-height: 1.45; }
        .hover-tooltip__row { display: flex; align-items: flex-start; gap: 5px; color: var(--text-dim); margin-top: 4px; line-height: 1.45; }

        /* ───────── 오른쪽 상세 ───────── */
        .detail { flex: 1; padding: 20px 28px 32px; overflow-y: auto; overscroll-behavior: contain; min-height: 0; min-width: 0; display: flex; flex-direction: column; gap: 14px; }
        .detail > * { flex-shrink: 0; }
        .detail-meta { display: flex; align-items: center; gap: 6px 20px; flex-wrap: wrap; font-size: 13.5px; }
        .detail-meta__item { display: flex; align-items: center; gap: 6px; color: var(--text-mute); min-width: 0; }
        .detail-meta__item b { color: var(--text); font-weight: 650; font-size: 14px; }
        .detail-meta__dim { color: var(--text-dim); }
        .detail-meta__id code { font-size: 12.5px; color: var(--text-dim); background: var(--tint); border-radius: 6px; padding: 1px 7px; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

        .detail-main { display: flex; gap: 12px; align-items: stretch; flex-wrap: wrap; }
        .score-badge { position: relative; width: 120px; height: 120px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; margin-bottom: 24px; }
        .score-badge svg { width: 120px; height: 120px; }
        .score-badge__center { position: absolute; display: flex; flex-direction: column; align-items: center; }
        .score-badge__num { font-size: 32px; font-weight: 700; line-height: 1; }
        .score-badge__unit { font-size: 12.5px; color: var(--text-dim); margin-top: 2px; }
        .score-badge__label { position: absolute; bottom: -24px; font-size: 13px; border: 1px solid; border-radius: 20px; padding: 1px 13px; background: #fff; }

        .trend-card { flex: 1; min-width: 240px; background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 16px; }
        .trend-card__title { font-size: 12.5px; color: var(--text-dim); font-weight: 500; margin-bottom: 2px; }
        .override-note { display: flex; align-items: flex-start; gap: 8px; padding: 9px 12px; border: 1px solid #E8B4AE; border-radius: 10px; background: #FDF4F3; color: #8F2A21; font-size: 13px; line-height: 1.5; }
        .override-note svg { flex-shrink: 0; margin-top: 2px; }
        .window-card .override-note { margin-top: 12px; }

        .top-issues { display: flex; align-items: center; gap: 8px 14px; flex-wrap: wrap; padding: 10px 14px; background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); }
        .top-issues__title { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 650; color: var(--text-dim); }
        .top-issues__none { font-size: 13px; color: var(--text-mute); }
        .top-issues__chips { display: flex; gap: 6px; flex-wrap: wrap; }
        .issue-chip { display: flex; align-items: center; gap: 6px; background: var(--tint); border: 1px solid; border-radius: 20px; padding: 2px 11px 2px 8px; font-size: 13px; font-weight: 500; }
        .issue-chip b { font-size: 12.5px; }
        .issue-chip__code { font-family: var(--mono); font-size: 11.5px; color: var(--text-mute); }

        /* ───────── 근거 카드(네트워크 / 프롬프트) ───────── */
        .evidence-cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: start; }
        .evidence-group { background: var(--panel); border: 1px solid var(--border); border-top: 2px solid var(--line-accent, var(--border)); border-radius: var(--radius); padding: 16px 18px; min-width: 0; }
        .evidence-group--embedded { background: transparent; border: none; border-top: 1px solid var(--border); border-radius: 0; padding: 14px 0 0; margin-top: 16px; }
        .evidence-group--embedded .evidence-group__head { font-size: 13.5px; color: var(--text-dim); margin-bottom: 10px; }
        .company-breakdown__col.company-breakdown__col--accent { border-top: 2px solid var(--line-accent, var(--border)); }
        .evidence-group__head { display: flex; align-items: center; gap: 7px; font-size: 14px; font-weight: 650; color: var(--text); margin-bottom: 12px; flex-wrap: wrap; }
        .evidence-group__count { margin-left: auto; font-size: 12.5px; font-weight: 400; color: var(--text-mute); }
        .evidence-empty { font-size: 13.5px; color: var(--text-mute); padding: 4px 0 8px; }
        .evidence-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
        .evidence-item { display: flex; align-items: flex-start; gap: 9px; }
        .evidence-item__text { display: flex; flex-direction: column; flex: 1; min-width: 0; }
        .evidence-item__row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
        .evidence-item__label { font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 7px; }
        .evidence-item__code { font-family: var(--mono); font-size: 11.5px; font-weight: 700; border: 1px solid currentColor; border-radius: 4px; padding: 0 5px; color: var(--accent); }
        .evidence-item__detail { font-size: 13px; color: var(--text-dim); margin-top: 2px; line-height: 1.55; }
        .evidence-item--hit { border-left: 3px solid; padding-left: 10px; }
        .evidence-item--compact { align-items: center; gap: 7px; font-size: 13px; color: var(--text-dim); }
        .evidence-item--compact .evidence-item__code { color: var(--text-mute); }
        .evidence-item__short { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .evidence-item__metric { font-size: 12px; flex-shrink: 0; color: var(--text-mute); }
        .evidence-item__metric-line { font-size: 13px; font-weight: 600; margin-top: 3px; }
        .evidence-note { margin-top: 6px; font-size: 12px; color: var(--text-dim); line-height: 1.6; }
        .evidence-item__chip { font-size: 12px; border: 1px solid; border-radius: 20px; padding: 0 9px; flex-shrink: 0; }
        .collapse-btn { display: flex; align-items: center; gap: 5px; margin-top: 14px; background: transparent; border: none; color: var(--text-mute); font-size: 12.5px; cursor: pointer; padding: 2px 0; }
        .collapse-btn:hover { color: var(--accent); }
        .evidence-summary { margin-top: 12px; font-size: 13px; color: var(--text-dim); }
        .evidence-summary summary { cursor: pointer; color: var(--accent); }
        .evidence-summary__row { margin-top: 8px; line-height: 1.5; }
        .evidence-summary__row p { margin: 3px 0 0; font-size: 12.5px; }

        /* ───────── 구간 요약 카드 ───────── */
        .window-card { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 16px 18px; }
        .window-card__head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 13.5px; }
        .window-card__phase { font-size: 12px; font-weight: 650; color: var(--accent); background: var(--accent-soft); border-radius: 999px; padding: 2px 10px; }
        .window-card__phase.is-open { color: #8A6100; background: #FEF6D8; }
        .window-card__when { font-size: 14px; font-weight: 650; }
        .window-card__who { color: var(--text-dim); }
        .window-card__formula { display: flex; align-items: stretch; gap: 10px; margin-top: 14px; flex-wrap: wrap; }
        .window-card__op { align-self: center; font-size: 20px; font-weight: 500; color: var(--text-mute); }
        .score-tile { flex: 1 1 120px; display: flex; flex-direction: column; gap: 1px; background: var(--tint); border: 1px solid transparent; border-radius: 12px; padding: 12px 16px; min-width: 0; }
        .score-tile--total { background: var(--panel); border-width: 2px; padding: 11px 15px; }
        .score-tile__label { font-size: 15px; font-weight: 700; color: var(--text); }
        .score-tile__value { font-size: 32px; font-weight: 700; line-height: 1.15; letter-spacing: -0.02em; }
        .score-tile__value small { font-size: 13.5px; font-weight: 500; color: var(--text-mute); margin-left: 3px; letter-spacing: 0; }
        .review-badge { display: inline-flex; align-items: center; margin-left: 8px; padding: 0 8px; border: 1px solid var(--border-strong); border-radius: 999px; background: var(--tint); color: var(--text); font-size: 12px; font-weight: 700; line-height: 20px; white-space: nowrap; vertical-align: middle; }
        .hover-tooltip__head .review-badge { margin-left: 0; font-size: 11.5px; line-height: 18px; }
        .score-tile__sub { font-size: 12.5px; color: var(--text-mute); }
        .window-card__extra { display: flex; flex-direction: column; gap: 8px; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border); }
        .window-card__source { display: flex; align-items: center; gap: 8px; font-size: 13px; min-width: 0; }
        .window-card__source > span:first-child { color: var(--text-mute); flex-shrink: 0; }
        .window-card__source code { font-size: 12px; color: var(--text-dim); background: var(--tint); border-radius: 6px; padding: 2px 7px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
        .window-card__muted { color: var(--text-mute); flex-shrink: 0; }
        .window-card__occurrence { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; font-size: 13px; }
        .window-card__occurrence > span:first-child { color: var(--text-mute); }
        .window-card__occurrence b { font-weight: 600; background: var(--tint); border: 1px solid var(--border); border-radius: 999px; padding: 0 9px; }
        .window-card__occurrence b.is-repeat { background: #FEF6D8; border-color: #EAB308; }
        .window-card__reason { margin: 10px 0 0; font-size: 13px; color: #8A6100; }
        .window-card__notes { margin-top: 10px; font-size: 12.5px; color: var(--text-mute); }
        .window-card__notes summary { cursor: pointer; }
        .window-card__notes ul { margin: 6px 0 0; padding-left: 18px; line-height: 1.6; }

        /* ───────── 점수 구성 표 ───────── */
        .company-breakdown { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr); gap: 12px; align-items: start; }
        .company-breakdown--single { grid-template-columns: minmax(0, 1fr); }
        .company-breakdown__col { border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; background: var(--panel); min-width: 0; }
        .company-breakdown__col h4 { font-size: 14px; font-weight: 650; margin: 0 0 4px; }
        .company-breakdown__counts, .company-breakdown__formula { margin: 0 0 8px; font-size: 13px; color: var(--text-dim); }
        .company-breakdown__formula { color: var(--text); font-weight: 600; line-height: 1.55; }
        .company-breakdown__note { margin: 6px 0 0; font-size: 12.5px; color: var(--text-dim); line-height: 1.6; }
        .company-breakdown__table { max-height: 340px; overflow-y: auto; overflow-x: hidden; margin-top: 6px; }
        .company-breakdown table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }
        .company-breakdown th, .company-breakdown td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--tint); vertical-align: middle; }
        .company-breakdown th { color: var(--text-mute); font-weight: 500; font-size: 12.5px; position: sticky; top: 0; background: var(--panel); }
        .company-breakdown td { white-space: nowrap; }
        .company-breakdown th.c-user, .company-breakdown td.c-user { width: 64px; }
        .company-breakdown th.c-num, .company-breakdown td.c-num { width: 72px; text-align: right; }
        .company-breakdown th.c-state, .company-breakdown td.c-state { width: 52px; }
        .company-breakdown th.c-idcol { width: 26%; }
        .company-breakdown td.c-rep { white-space: normal; }
        .rep-chips { display: flex; flex-wrap: wrap; gap: 4px; }
        .rep-chip { display: inline-flex; align-items: center; gap: 4px; background: var(--tint); border: 1px solid var(--border); border-radius: 999px; padding: 0 8px; font-size: 12px; line-height: 20px; color: var(--text-dim); white-space: nowrap; }
        .rep-chip b { font-weight: 700; color: var(--text); }
        .rep-chip.is-repeat { background: #FEF6D8; border-color: #EAB308; }
        .rep-chip--warn { background: #FBEDEB; border-color: #EFC1BC; color: #8E2A20; }
        .c-muted { color: var(--text-mute); }
        .company-breakdown__table + .collapse-btn { margin-top: 8px; }
        .company-breakdown--single th.c-num, .company-breakdown--single td.c-num { width: 112px; white-space: nowrap; }
        .company-breakdown th.c-grade, .company-breakdown td.c-grade { width: 90px; }
        .company-breakdown td.c-grade { font-weight: 600; }
        .company-breakdown td.c-grade i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }
        .company-breakdown td.c-id { overflow: hidden; }
        .c-id__wrap { display: flex; align-items: center; gap: 6px; min-width: 0; }
        .c-id__text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; font-family: var(--mono); font-size: 12px; color: var(--text-dim); }
        .fullid { cursor: copy; }
        .id-tip { position: fixed; z-index: 80; transform: translateY(calc(-100% - 8px)); max-width: min(520px, calc(100vw - 16px)); background: #1F2330; color: #fff; border-radius: 8px; padding: 7px 10px; font-family: var(--mono); font-size: 12px; font-weight: 400; line-height: 1.5; white-space: normal; overflow-wrap: anywhere; text-overflow: clip; pointer-events: none; box-shadow: 0 6px 18px rgba(20,22,30,.25); }
        .id-tip em { display: block; font-style: normal; font-family: "Pretendard Variable", Pretendard, "Noto Sans KR", sans-serif; font-size: 11.5px; color: #B8BDCB; margin-top: 2px; }
        .c-tag { flex-shrink: 0; font-size: 11.5px; font-weight: 650; color: var(--accent); background: var(--accent-soft); border-radius: 4px; padding: 0 6px; margin-left: 6px; }
        .c-id .c-tag { margin-left: 0; }
        .c-sub { color: var(--text-mute); margin-left: 4px; }
        .company-breakdown tr.is-max td { background: var(--accent-soft); }
        .company-breakdown tr.is-link { cursor: pointer; }
        .company-breakdown tr.is-link:hover td { background: var(--tint); }
        .breakdown-list { list-style: none; margin: 8px 0 0; padding: 0; font-size: 13.5px; }
        .breakdown-list li { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--tint); }
        .breakdown-list li span { color: var(--text-dim); }
        .breakdown-list li.is-total { border-bottom: none; border-top: 1px solid var(--border-strong); margin-top: 2px; font-weight: 700; }
        .breakdown-list li.is-total span { color: var(--text); }
        .slot-layout { display: flex; gap: 24px; align-items: flex-start; margin-top: 8px; }
        .slot-layout__table { flex: 1; min-width: 0; margin-top: 0; }
        .slot-counts { flex: 0 0 288px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
        .slot-count { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; background: var(--tint); border-radius: 10px; padding: 10px 12px; }
        .slot-count--zero { opacity: .55; }
        .slot-count__num { font-size: 26px; font-weight: 700; line-height: 1.1; }
        .slot-count__label { white-space: nowrap; display: inline-flex; align-items: center; gap: 5px; font-size: 12.5px; color: var(--text-dim); }
        .slot-count__label i { width: 6px; height: 6px; border-radius: 50%; }
        .fold { margin-top: 8px; }
        .fold > summary { cursor: pointer; font-size: 12.5px; color: var(--text-mute); list-style: none; display: inline-flex; align-items: center; gap: 4px; }
        .fold > summary::-webkit-details-marker { display: none; }
        .fold > summary::before { content: "▸"; font-size: 10px; }
        .fold[open] > summary::before { content: "▾"; }
        .fold > summary:hover { color: var(--accent); }

        /* ───────── 요약(LLM) 카드 / 하단 ───────── */
        .evidence-group, .session-row__main, .top-issues, .trend-card { word-break: keep-all; overflow-wrap: anywhere; }
        .trend-card--note { min-width: 260px; }
        .trend-card--note p { margin: 4px 0 0; font-size: 13px; line-height: 1.6; color: var(--text-dim); }
        .llm-box { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; }
        .llm-box__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; gap: 10px; flex-wrap: wrap; }
        .llm-box__title { display: flex; align-items: center; gap: 7px; font-size: 14px; font-weight: 650; color: var(--text); }
        .llm-btn { display: flex; align-items: center; gap: 6px; background: var(--accent); border: none; color: #fff; border-radius: 8px; padding: 6px 12px; font-size: 13px; cursor: pointer; font-weight: 500; }
        .llm-btn:hover { background: #2A3785; }
        .llm-btn:disabled { opacity: .55; cursor: default; }
        .llm-lines { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; font-size: 14px; line-height: 1.65; color: var(--text); word-break: keep-all; overflow-wrap: anywhere; }
        .llm-lines li { padding-left: 12px; border-left: 2px solid var(--border); }
        .llm-hint { font-size: 13px; color: var(--text-mute); }
        .llm-meta { display: flex; gap: 8px; align-items: center; margin-top: 10px; font-size: 12.5px; color: var(--text-dim); flex-wrap: wrap; }
        .llm-tag { border-radius: 4px; padding: 0 7px; font-size: 12px; font-weight: 600; background: var(--accent-soft); color: var(--accent); }
        .llm-tag--stale { color: #8E5200; background: #FFF1DC; }
        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        .footnote { padding-top: 4px; font-size: 12.5px; color: var(--text-mute); display: flex; align-items: center; gap: 6px; }

        .state-panel { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: var(--text-dim); padding: 40px; text-align: center; }
        .state-panel b { color: var(--text); font-size: 15px; }
        .state-panel button { margin-top: 6px; background: var(--accent); color: #fff; border: none; border-radius: 8px; padding: 8px 16px; font-size: 13.5px; cursor: pointer; display: flex; align-items: center; gap: 6px; }

        .skeleton { display: block; background: linear-gradient(90deg, #ECEEF3 25%, #F6F7FA 50%, #ECEEF3 75%); background-size: 200% 100%; animation: shimmer 1.3s infinite; border-radius: 6px; }
        @keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
        .skeleton--line { height: 13px; }
        .skeleton--dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
        .skeleton--num { width: 26px; height: 14px; }
        .skeleton-row { display: flex; align-items: center; gap: 10px; padding: 14px 12px; }

        /* ───────── 개요 그래프 ───────── */
        .ov { padding: 16px 24px 28px; display: flex; flex-direction: column; gap: 14px; flex: 1; min-height: 0; min-width: 0; overflow-y: auto; overflow-x: hidden; }
        .ov > * { flex-shrink: 0; min-width: 0; }
        .ov-chart { width: 100%; min-width: 0; }
        .ov-toolbar { display: flex; gap: 10px 14px; align-items: center; flex-wrap: wrap; }
        .ov-toolbar > .clear-btn { order: 3; margin-left: auto; }
        .ov-toolbar > .clear-panel { order: 4; flex: 1 1 100%; }
        .ov-field { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--text-mute); }
        .ov-field select { height: 32px; padding: 0 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); color: var(--text); font-size: 13px; }
        .ov-legend { display: flex; flex-wrap: wrap; gap: 6px; flex: 1; min-width: 0; }
        .ov-chip { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 11px; border: 1px solid var(--border); border-radius: 999px; background: var(--panel); font-size: 12.5px; color: var(--text-mute); cursor: pointer; }
        .ov-chip:hover { background: var(--tint); }
        .ov-chip.is-on { color: var(--text); border-color: var(--border-strong); font-weight: 600; }
        .ov-chip__grade { font-size: 11.5px; font-weight: 600; }
        .ov-swatch { display: inline-block; width: 14px; height: 3px; border-radius: 2px; border: 1px solid transparent; flex-shrink: 0; }
        .ov-chip:not(.is-on) .ov-swatch { border-color: var(--border-strong); height: 1px; }
        .ov-hint { font-size: 12.5px; color: #8A6100; }
        .ov-card { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 16px 18px; }
        .ov-card__head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; font-size: 14px; }
        .ov-card__head b { font-weight: 650; }
        .ov-card__head span { font-size: 12.5px; color: var(--text-mute); }
        .ov-empty { padding: 48px 0; text-align: center; color: var(--text-mute); font-size: 13.5px; }
        .ov-note { font-size: 12px; color: var(--text-mute); line-height: 1.6; }
        .ov-card .ov-note { margin-top: 8px; }
        .ov-tip { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; box-shadow: 0 6px 20px rgba(0,0,0,.08); font-size: 12.5px; min-width: 200px; }
        .ov-tip__time { font-weight: 700; margin-bottom: 6px; }
        .ov-tip__row { display: grid; grid-template-columns: 16px 1fr auto auto; gap: 8px; align-items: center; padding: 2px 0; }
        .ov-tip__score { font-weight: 600; text-align: right; }
        .ov-tip__hint { margin-top: 6px; color: var(--text-mute); font-size: 11.5px; }
        .ov-table-wrap { overflow-x: auto; }
        .ov-table { border-collapse: collapse; font-size: 12.5px; width: 100%; }
        .ov-table th, .ov-table td { padding: 5px 6px; text-align: center; border-bottom: 1px solid var(--tint); white-space: nowrap; }
        .ov-table thead th { color: var(--text-mute); font-weight: 500; }
        .ov-table tbody th, .ov-table thead th:first-child { text-align: left; font-weight: 600; width: 110px; }
        .ov-rowhead { display: inline-flex; align-items: center; gap: 6px; }
        .ov-td--compact, .ov-table .ov-cell--empty { padding: 3px 1px; }
        .ov-th--compact { padding: 4px 0 !important; font-size: 10.5px; text-align: left !important; overflow: visible; max-width: 14px; }
        .ov-dot { display: block; width: 12px; height: 14px; border: none; border-radius: 3px; padding: 0; margin: 0 auto; cursor: pointer; }
        .ov-dot:hover { outline: 2px solid var(--text); outline-offset: 1px; }
        .ov-cell--empty { color: var(--border-strong); }
        .ov-dot--normal { opacity: .55; }
        .ov-dot--lg { width: 34px; height: 22px; border-radius: 7px; }
        .ov-cell-tip { position: fixed; z-index: 70; transform: translate(-50%, calc(-100% - 8px)); background: #1F2330; color: #fff; border-radius: 8px; padding: 7px 10px; font-size: 12.5px; line-height: 1.5; white-space: nowrap; pointer-events: none; box-shadow: 0 6px 18px rgba(20,22,30,.25); }
        .ov-cell-tip__review { margin-top: 2px; font-weight: 700; color: #fff; }
        .ov-cell-tip span { color: #B8BDCB; margin-left: 4px; }
        .ov-cell-tip i { font-style: normal; font-weight: 700; filter: brightness(1.6); }
        .ov-dot--static { display: inline-block; width: 10px; height: 10px; margin: 0 4px 0 10px; vertical-align: -1px; cursor: default; }
        .ov-keys { margin-left: auto; font-size: 12px; color: var(--text-mute); }

        /* ───────── 기록 비우기 ───────── */
        .clear-btn { display: inline-flex; align-items: center; gap: 5px; height: 30px; padding: 0 11px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); color: var(--text-dim); font-size: 12.5px; cursor: pointer; }
        .clear-btn:hover { background: #FDF1F0; color: #C6362A; border-color: #F1C9C5; }
        .clear-panel { border: 1px solid #F1C9C5; background: #FFF8F7; border-radius: 12px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; font-size: 13px; min-width: 0; }
        .clear-panel__row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
        .clear-panel__row label { display: inline-flex; align-items: center; gap: 4px; }
        .clear-panel__row .ghost-btn { margin-left: auto; }
        .clear-panel__confirm { width: 120px; height: 30px; padding: 0 8px; border: 1px solid var(--border-strong); border-radius: 8px; font-family: var(--mono); }
        .clear-panel__go { height: 30px; padding: 0 14px; border: none; border-radius: 8px; background: #C6362A; color: #fff; font-weight: 600; cursor: pointer; }
        .clear-panel__go:disabled { background: #E5B5B0; cursor: not-allowed; }
        .clear-panel__hint { color: var(--text-mute); font-size: 12px; }
        .clear-panel__ok { color: #137D57; word-break: break-all; }
        .clear-panel__err { color: #C6362A; }

        /* ───────── 반응형 ───────── */
        @media (max-width: 1360px) {
          .dash-header__count { display: none; }
          .company-breakdown { grid-template-columns: minmax(0, 1fr); }
          .slot-layout { flex-direction: column; }
          .slot-counts { flex: none; width: 100%; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); }
        }
        @media (max-width: 1280px) {
          .evidence-cols { grid-template-columns: minmax(0, 1fr); }
        }
        @media (max-width: 1100px) {
          .side { width: 276px; }
          .detail { padding: 18px 20px 28px; }
          .trend-card { flex-basis: 100%; }
          .company-breakdown { grid-template-columns: minmax(0, 1fr); }
        }
        @media (max-width: 860px) {
          .dash { height: auto; }
          .dash-body { flex-direction: column; }
          .side { width: 100%; border-right: none; border-bottom: 1px solid var(--border); max-height: 340px; }
          .dash-header { padding: 12px 16px; }
          .dash-header__right { margin-left: 0; justify-content: flex-start; }
          .stale-banner { padding: 9px 16px; }
          .toast-stack { width: calc(100% - 32px); }
          .ov { padding: 14px 16px 24px; }
        }
        @media (max-width: 520px) {
          .header-stats__row { flex-wrap: wrap; gap: 4px 10px; }
          .view-tabs { width: 100%; }
          .view-tabs button { flex: 1; padding: 6px 8px; }
        }
      `}</style>

      <ToastStack toasts={toasts} onDismiss={(id) => setToasts((prev) => prev.filter((t) => t.id !== id))} />

      <header className="dash-header">
        <div className="dash-header__title">
          <div className="dash-header__icon">
            <Radio size={16} color="#fff" />
          </div>
          <div className="dash-header__text">
            <h1>사용자·단말별 5분 구간 위험도</h1>
            <div className="dash-header__sub">
              <span className={`live-dot ${dotClass}`} />
              <span>{statusText}</span>
              <span className="dash-header__count">최근 {events.length} / 전체 {total}건</span>
              {isMock && <span className="demo-chip">DEMO 시나리오</span>}
            </div>
          </div>
        </div>

        {!isMock && (
          <div className="view-tabs" role="tablist" aria-label="화면 선택">
            <button role="tab" aria-selected={view === "overview"} className={view === "overview" ? "is-on" : ""} onClick={() => setView("overview")}>
              개요 그래프
            </button>
            <button role="tab" aria-selected={view === "detail"} className={view === "detail" ? "is-on" : ""} onClick={() => setView("detail")}>
              구간 상세 분석{userFilter ? ` · ${userFilter}` : ""}
            </button>
          </div>
        )}

        <div className="dash-header__right">
          <div className="header-stats" aria-label="등급별 건수">
            <div className="header-stats__row">
              {[
                { key: "danger", label: "위험", color: LEVELS.danger.color },
                { key: "warning", label: "경고", color: LEVELS.warning.color },
                { key: "caution", label: "주의", color: LEVELS.caution.color },
                { key: "normal", label: "정상", color: LEVELS.normal.color },
                { key: "pending", label: "미판정", color: LEVELS.pending.color, hideZero: true },
                { key: "error", label: "오류", color: LEVELS.error.color, hideZero: true },
              ].map((g) => (
                <span key={g.key} className={`stat ${counts[g.key] ? "" : g.hideZero ? "stat--gone" : "stat--zero"}`}>
                  <i style={{ background: g.color }} />{g.label}<b>{counts[g.key]}</b>
                </span>
              ))}
            </div>
            <LevelBar counts={counts} total={events.length || 1} />
          </div>
          <div className="dash-header__actions">
            <a className="chat-link" href="/chat.html" target="_blank" rel="noopener" title="사내 AI 채팅(recevie)을 새 탭에서 엽니다. 보낸 프롬프트가 이 대시보드에 기록됩니다.">사내 AI 채팅 ↗</a>
            {!isMock && view === "detail" && (
              <button className={`ghost-btn ${testerOpen ? "is-on" : ""}`} onClick={() => setTesterOpen((v) => !v)} aria-expanded={testerOpen} title="프롬프트 직접 테스트">
                <FlaskConical size={13} /> 프롬프트 테스트
              </button>
            )}
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
      </header>

      {!isMock && view === "overview" && (
        <RiskOverview key={dateFilter || dates[0]?.date || "none"} apiBase={API_BASE} dates={dates} date={dateFilter || dates[0]?.date || ""}
          onDateChange={setDateFilter} version={`${lastOkAt?.getTime() ?? 0}:${scopeVersion}`}
          onCleared={(result) => {
            // 날짜 키가 바뀌면 개요 화면이 새로 그려져 패널 메시지가 사라지므로, 결과는 알림으로도 띄웁니다.
            toastIdRef.current += 1;
            const what = result.scope === "all" ? "전체 기록" : `${result.date}${result.user_id ? ` · ${result.user_id}` : ""} 기록`;
            setToasts((prev) => [...prev, { id: toastIdRef.current, color: "#137D57",
              text: `${what}을 지웠습니다.${result.backup ? ` 백업: ${result.backup}` : " (백업 없음)"}` }].slice(-4));
            setDateFilter(""); setUserFilter(""); setSelectedId(null); setScopeVersion((v) => v + 1); refresh();
          }}
          onOpenWindow={(w) => {
            setDateFilter(new Date(w.startedAtMs + 9 * 3600 * 1000).toISOString().slice(0, 10)); // KST 날짜
            setUserFilter(w.user);
            setSelectedId(w.id);
            setView("detail");
          }} />
      )}

      {!isMock && view === "detail" && (
        <div className="tester-panel" hidden={!testerOpen}>
          <PromptTester apiBase={API_BASE} />
        </div>
      )}

      {(isMock || view === "detail") && <>
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
            {!isMock && (
              <div className="scope-filters">
                <select value={dateFilter} onChange={(e) => setDateFilter(e.target.value)} aria-label="날짜(KST)">
                  <option value="">전체 날짜</option>
                  {dates.map((d) => (
                    <option key={d.date} value={d.date}>{d.date} · {d.user_count}명 · {d.window_count}구간</option>
                  ))}
                </select>
                <select value={userFilter} onChange={(e) => setUserFilter(e.target.value)} aria-label="사용자">
                  <option value="">전체 사용자</option>
                  {users.map((u) => (
                    <option key={u.user_id} value={u.user_id}>
                      {u.user_id} · 최고 {u.top_grade ? LEVELS[u.top_grade].label : "미판정"} · 구간 {u.window_count}
                    </option>
                  ))}
                  {userFilter && !users.some((u) => u.user_id === userFilter) && <option value={userFilter}>{userFilter} (이 날짜 기록 없음)</option>}
                </select>
              </div>
            )}
            <div className="search-box">
              <Search size={14} color="#8B8F99" />
              <input
                placeholder="사용자·단말·구간 ID 검색"
                aria-label="구간 검색"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="filter-pills" role="group" aria-label="등급 필터">
              {[
                { key: "all", label: "전체" },
                { key: "danger", label: "위험" },
                { key: "warning", label: "경고" },
                { key: "caution", label: "주의" },
                { key: "normal", label: "정상" },
                { key: "pending", label: "미판정" },
                { key: "error", label: "오류" },
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
            <div className="tool-row">
              <div className="sort-select">
                <ArrowUpDown size={13} color="#8B8F99" />
                <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} aria-label="정렬">
                  <option value="default">기본순</option>
                  <option value="score_desc">점수 높은순</option>
                  <option value="recent">최근 관측순</option>
                </select>
              </div>
              <button className="export-btn" onClick={exportCsv} disabled={!filtered.length}
                title="현재 목록을 CSV로 내보내기" aria-label={`CSV 내보내기 (${filtered.length}건)`}>
                <Download size={13} /> CSV ({filtered.length})
              </button>
            </div>
          </div>

          {status === "loading" ? (
            <SkeletonList />
          ) : (
            <div className="session-list">
              {filtered.length === 0 && (
                <div className="list-empty">
                  <Inbox size={26} />
                  <span>{events.length ? "조건에 맞는 구간이 없어요" : "표시할 구간이 없어요"}</span>
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
              {(hasMore || capped) && (
                <div className="list-more">
                  {query.trim() && <span>검색·필터는 불러온 {events.length}건 안에서만 적용됩니다.</span>}
                  {hasMore && (
                    <button onClick={loadMore} disabled={loadingMore}>
                      {loadingMore ? "불러오는 중…" : `더 보기 (${events.length} / ${total}건)`}
                    </button>
                  )}
                  {capped && <span>최대 {MAX_PAGES * PAGE_SIZE}건까지 조회합니다. 이전 기록은 CSV·API로 확인해 주세요.</span>}
                </div>
              )}
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

        {status === "ok" && !selected && (
          <div className="state-panel">
            <Inbox size={30} />
            <b>{events.length ? "조건에 맞는 구간이 없습니다." : "수집된 구간이 없습니다."}</b>
            <span>수집기가 관측 자료를 전송하면 자동으로 표시됩니다.</span>
          </div>
        )}

        {selected && (
          <div className="detail">
            <div className="detail-meta">
              <span className="detail-meta__item">
                <User size={14} /> <b>{selected.user}</b> <span className="detail-meta__dim">· {selected.dept}</span>
              </span>
              <span className="detail-meta__item detail-meta__id">
                <Network size={14} /> <span className="detail-meta__dim">{selected.scope === "company" || selected.scope === "user_device" ? "구간 ID" : "세션 ID"}</span> <FullId as="code" id={selected.sessionId ?? selected.id} />
              </span>
              {!hasWindowCard && (
                <span className="detail-meta__item">
                  <Clock size={14} /> <span className="detail-meta__dim">관측 {selected.connectedAt}
                  {selected.observation_kind === "event" ? " · 개별 이벤트" : <> · 구간 길이{" "}
                    <b><LiveDuration startedAtMs={selected.startedAtMs} endedAtMs={selected.endedAtMs} /></b>
                  </>}</span>
                </span>
              )}
            </div>

            {hasWindowCard && <WindowSummaryCard event={selected} />}

            {(!hasWindowCard || showTrend || (selected.score == null && selected.isReal)) && (
              <div className="detail-main">
                {!hasWindowCard && <RiskScoreBadge score={selected.score} level={eventLevel(selected)} />}
                {showTrend && <div className="trend-card">
                  <div className="trend-card__title">화면 조회 중 점수 변화</div>
                  <ScoreTrendChart
                    history={history[selected.id] || [selected.score]}
                    color={LEVELS[eventLevel(selected)].color}
                  />
                </div>}
                {selected.score == null && selected.isReal && (
                  <div className="trend-card trend-card--note">
                    <div className="trend-card__title">통합 등급 미판정 <ReviewBadge /></div>
                    <p>통합 점수·등급은 아직 계산되지 않았습니다. <b>미판정·미탐지는 안전 판정이 아닙니다.</b> 아래는 엔진별 개별 결과입니다.</p>
                  </div>
                )}
              </div>
            )}

            {!hasWindowCard && <TopIssues event={selected} />}
            {selected.override && !hasWindowCard && (
              <div className="override-note" role="note">
                <ShieldAlert size={14} color={LEVELS.danger.color} />
                <span>
                  <b>강제 위험 규칙 적용</b> · 합계 점수와 관계없이 위험으로 판정됨
                  {selected.overrideReasons.length > 0 && <> ({selected.overrideReasons.join(" / ")})</>}
                </span>
              </div>
            )}
            {selected.processing_state === "processing" && <div className="evidence-empty" role="status">분석 진행 중 · 완료된 엔진 결과부터 표시합니다.</div>}

            {hasWindowCard
              ? <CompanyBreakdown key={selected.id} event={selected} promptEvidence={evidenceGroups.prompt} networkEvidence={evidenceGroups.net} />
              : <div className="evidence-cols">{evidenceGroups.net}{evidenceGroups.prompt}</div>}

            {selected.scope === "user_device" && (
              <SameSlotPanel selected={selected} events={events} isMock={isMock}
                onSelect={(row) => { if (userFilter && row.user !== userFilter) setUserFilter(""); setSelectedId(row.id); }} />
            )}

            <div className="llm-box">
              <div className="llm-box__head">
                <span className="llm-box__title">
                  <Sparkles size={14} /> 저장된 분석 근거 요약
                </span>
                <button className="llm-btn" onClick={generateExplanation} disabled={exLoading}>
                  {exLoading ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
                  {exLoading ? "생성 중..." : ex?.status === "done" ? (exStale ? "최신 상태로 다시 생성" : "다시 생성") : "분석 요약 보기"}
                </button>
              </div>
              {ex?.status === "done" && (
                <>
                  <ul className="llm-lines">
                    {(exOpen[selected.id] ? exLines : exLines.slice(0, LLM_PREVIEW_LINES)).map((line, i) => <li key={i}>{line}</li>)}
                  </ul>
                  {exLines.length > LLM_PREVIEW_LINES && (
                    <button className="collapse-btn" onClick={() => setExOpen((o) => ({ ...o, [selected.id]: !o[selected.id] }))}>
                      <ChevronDown size={13} style={{ transform: exOpen[selected.id] ? "rotate(180deg)" : "none" }} />
                      {exOpen[selected.id] ? "요약 접기" : `전체 요약 보기 (${exLines.length}줄)`}
                    </button>
                  )}
                  {(ex.source === "rule" || exStale) && (
                    <div className="llm-meta">
                      {ex.source === "rule" && <span>서버 요약 조회에 실패해 마지막 화면 데이터로 요약했습니다.</span>}
                      {exStale && <span className="llm-tag llm-tag--stale">분석 결과가 바뀌어 최신 내용과 다를 수 있어요</span>}
                    </div>
                  )}
                </>
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
      </>}

      <HoverTooltip session={hover.session} pos={hover.pos} />
    </div>
  );
}
