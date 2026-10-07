import { LEVELS, MAX_PAGES, PAGE_SIZE, PROMPT_MAX_NOTE, SCOPE_NOTE, eventLevel, sameSlotSummary, fetchDates, fetchUsers, fetchSameSlot, fetchEventPage, formatScore, isNotable, isSummaryItem, networkBreakdownParts, networkBreakdownText, promptScoreRows, shouldNotify, viewStatus } from "../lib/events.js";
import { watchEvents } from "../lib/live.js";
import PromptTester from "./PromptTester.jsx";
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
      : `통합 등급 ${LEVELS[grade].label}${e.score != null ? `(${formatScore(e.score)}점)` : ""}.${e.override ? ` 강제 위험 규칙 적용: ${e.overrideReasons.join(", ") || "사유 미제공"}.` : ""}`;
    return `${e.user}: ${head} 저장된 화면 데이터 기준이며 최신 서버 요약 조회에 실패했습니다.`;
  }
  const level = LEVELS[eventLevel(e)].label;
  const top = topContributors(e, 3);
  if (!top.length) {
    return `${e.user} 세션은 모든 탐지 항목이 정상 범위여서 ${e.score}점(${level})으로 산정되었습니다. 예측 신뢰도는 ${e.confidence}%입니다.`;
  }
  const parts = top.map((t) => `${t.label}(${t.detail})`).join(", ");
  const tail = e.confidence < 60 ? "로 낮은 편이라 담당자의 직접 확인이 필요합니다" : "입니다";
  return `${e.user} 세션은 ${parts} 등이 주된 원인이 되어 ${e.score}점(${level})으로 산정되었습니다. 예측 신뢰도는 ${e.confidence}%${tail}.`;
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
  if (status !== "normal") return <ShieldQuestion size={size} color={LEVELS[status].color} />;
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
      <StatusIcon status={status} />
      <div className="evidence-item__text">
        <div className="evidence-item__row">
          <span className="evidence-item__label">
            <span className="evidence-item__code">{item.code}</span>
            {item.label}
          </span>
          <span className="evidence-item__chip" style={{ color, borderColor: color }}>{label}</span>
        </div>
        {item.detail && <span className="evidence-item__detail">{item.detail.replace(LABEL_TAIL, "")}</span>}
        {item.probability != null && (
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

function EvidenceGroup({ title, icon, items: allItems, accent, expanded, onToggle, note }) {
  const summary = allItems.filter(isSummaryItem);
  const items = allItems.filter((i) => !isSummaryItem(i));
  const sorted = sortItems(items);
  const shown = sorted.filter((i) => isNotable(i) || i.status === "error");
  const folded = sorted.filter((i) => !shown.includes(i));
  const pending = items.filter((i) => i.status === "pending").length;
  const rows = expanded ? sorted : shown;

  return (
    <div className="evidence-group" style={{ borderLeftColor: accent }}>
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
      {summary.length > 0 && (
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
      {note && <div className="evidence-note">{note}</div>}
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
              <b style={{ color: LEVELS[viewStatus(t)].color }}>{i + 1}</b>
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
        <span className="score-badge__num" style={{ color, transition: "color 0.6s ease" }}>{formatScore(score)}</span>
        <span className="score-badge__unit">{unit}</span>
      </div>
      <span className="score-badge__label" style={{ color, borderColor: color }}>{label}</span>
    </div>
  );
}

// 예측 신뢰도 — 위험도 색(빨강/주황/초록)과 겹치지 않도록 파랑 계열 + 낮음은 빗금 패턴으로 표현
function ConfidenceMeter({ confidence }) {
  if (confidence == null) return <div className="confidence-card"><div className="confidence-card__title">예측 신뢰도</div><div className="confidence-card__num">—</div><div className="confidence-card__tier">미제공</div></div>;
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
  const level = eventLevel(session);
  const { color } = LEVELS[level];
  const hits = session.isReal ? topContributors(session, 1) : [];
  const hitCount = session.isReal ? [...session.network_reasons, ...session.prompt_reasons].filter(isNotable).length : 0;
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
        <span className="session-row__meta session-row__id" title={session.sessionId ?? session.id}>{session.sessionId ?? session.id}</span>
        <span className="session-row__meta">{session.connectedAt}</span>
        {hits.length > 0 && (
          <span className="session-row__find" style={{ color: LEVELS[viewStatus(hits[0])].color }}>
            탐지 {hits[0].code} {hits[0].label.replace(/\s*·\s*\d+분 집계$/, "")}{hitCount > 1 ? ` 외 ${hitCount - 1}건` : ""}
          </span>
        )}
      </div>
      <span className="session-row__score" style={{ color, transition: "color 0.6s ease" }}>{session.score != null ? formatScore(session.score) : LEVELS[level].label}</span>
      <ChevronRight size={16} color="#B7BAC3" />
    </button>
  );
}

const TOOLTIP_W = 250;
const TOOLTIP_H_EST = 130;
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
        <span style={{ color: LEVELS[level].color }}>{session.score == null ? "점수 미제공" : `${formatScore(session.score)}점`}</span>
        <span className="hover-tooltip__badge" style={{ color: LEVELS[level].color, borderColor: LEVELS[level].color }}>
          {LEVELS[level].label}
        </span>
        <span className="hover-tooltip__conf">신뢰도 {session.confidence == null ? "미제공" : `${session.confidence}%`}</span>
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
function CompanyBreakdown({ event }) {
  const rows = promptScoreRows(event);
  const network = networkBreakdownParts(event.network_score_breakdown);
  const statusLabel = { complete: "완료", pending: "미판정", error: "오류" };
  return (
    <div className="company-breakdown">
      <section className="company-breakdown__col">
        <h4>프롬프트 분석 {event.capture_count ?? 0}건 · 최고 점수만 반영</h4>
        <p className="company-breakdown__counts">
          완료 {event.prompt_complete_count ?? 0} · 미판정 {event.prompt_missing_count ?? 0} · 오류 {event.prompt_error_count ?? 0}
          {" "}· 최고 {formatScore(event.prompt_max_score)}/60
        </p>
        <p className="company-breakdown__note">{PROMPT_MAX_NOTE} 미판정·오류는 0점으로 바꾸지 않으며, 하나라도 있으면 통합 점수는 미판정입니다.</p>
        {rows.length > 0 ? (
          <div className="company-breakdown__table">
            <table>
              <thead><tr><th>capture ID</th><th>사용자</th><th>점수</th><th>상태</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.captureId} className={r.isMax ? "is-max" : ""}>
                    <td title={r.captureId}>{r.captureId}{r.isMax && <b> · 반영</b>}</td>
                    <td>{r.userId}</td>
                    <td>{r.score == null ? "—" : `${formatScore(r.score)}/60`}</td>
                    <td>{statusLabel[r.status]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="company-breakdown__note">개별 프롬프트 점수 목록이 없는 이전 구간입니다.</p>}
      </section>
      <section className="company-breakdown__col">
        <h4>네트워크 점수 구성{network?.threat ? ` · ${network.threat} ${network.threatName}` : ""}</h4>
        {network ? (
          <>
            <p className="company-breakdown__formula">{networkBreakdownText(event.network_score_breakdown)}</p>
            <ul>
              {network.parts.map((p) => (
                <li key={p.key}><span>{p.label}</span><b>{p.value == null ? "미제공" : formatScore(p.value)}</b></li>
              ))}
            </ul>
            <p className="company-breakdown__note">
              네트워크 모델 보고서의 값을 그대로 표시합니다(합계는 반올림·상한 100 적용). 반영 점수 = {formatScore(network.total)} × 0.4 = {formatScore(event.network_contribution)}/40.
              반복 가산점 '미제공'은 현재 모델 설정에서 반복 가산을 쓰지 않는다는 뜻입니다.
            </p>
          </>
        ) : <p className="company-breakdown__note">네트워크 점수 구성 정보가 없습니다(분석 대기 또는 이전 버전 결과).</p>}
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
  return (
    <div className="window-card" role="note">
      <div className="window-card__head">
        <span className={`window-card__phase ${event.phase === "open" ? "is-open" : ""}`}>
          {event.phase === "open" ? "집계 중 · 잠정" : "종료된 구간"}
        </span>
        <span className="window-card__when">{day} {fmtHm(event.startedAtMs)} ~ {fmtHm(event.endedAtMs)}</span>
        <span className="window-card__who">{legacy ? "회사 전체(이전 버전)" : `${event.user} · ${event.device_id}`}</span>
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
          <span className="score-tile__value" style={{ color: LEVELS[level].color }}>{formatScore(event.score)}<small>/100</small></span>
          <span className="score-tile__sub" style={{ color: LEVELS[level].color, fontWeight: 700 }}>{LEVELS[level].label}</span>
        </div>
      </div>

      {capture && (
        <div className="window-card__source">
          <span>최고 점수 근거</span>
          <code title={capture}>{capture}</code>
          {event.prompt_source_user_id && event.prompt_source_user_id !== event.user && <span className="window-card__muted">사용자 {event.prompt_source_user_id}</span>}
        </div>
      )}
      {event.fusion_status !== "complete" && <p className="window-card__reason">{event.reason}</p>}

      <details className="window-card__notes">
        <summary>검토 우선순위용 지표이며 위반 확정이 아닙니다 · 판정 기준 보기</summary>
        <ul>
          {SCOPE_NOTE.map((line) => <li key={line}>{line}</li>)}
          <li>늦게 도착한 기록이 있으면 구간 결과가 갱신됩니다.</li>
        </ul>
      </details>
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
  return (
    <div className="company-breakdown company-breakdown--single">
      <section className="company-breakdown__col">
        <h4>같은 시간대 사용자 구간 · {userCount}명</h4>
        <p className="company-breakdown__counts">
          {["danger", "warning", "caution", "normal"].map((g) => `${LEVELS[g].label} ${counts[g]}`).join(" · ")}
          {` · 미판정 ${pending}`}{error ? ` · 오류 ${error}` : ""}
        </p>
        <div className="company-breakdown__table">
          <table>
            <thead><tr><th>사용자 · 단말</th><th>등급</th><th>통합 점수</th><th>프롬프트 최고</th></tr></thead>
            <tbody>
              {windows.map((w) => (
                <tr key={w.id} className={w.id === selected.id ? "is-max" : "is-link"} onClick={() => onSelect(w)}
                  title="클릭하면 이 사용자 구간을 엽니다">
                  <td>{w.user} · {w.device_id}{w.id === selected.id && <b> · 현재</b>}</td>
                  <td style={{ color: LEVELS[eventLevel(w)].color }}>{LEVELS[eventLevel(w)].label}</td>
                  <td>{formatScore(w.score)}</td>
                  <td>{w.prompt_max_score == null ? "—" : `${formatScore(w.prompt_max_score)}/60`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="company-breakdown__note">{rows ? "서버 기준 같은 시간대 전체 사용자입니다." : "화면에 불러온 구간 기준입니다."} 등급 수만 세며 회사 점수를 합산·평균하지 않습니다. 미판정은 정상으로 세지 않습니다.</p>
      </section>
    </div>
  );
}

export default function RiskDashboard() {
  const [live, setLive] = useState(true);
  const [dateFilter, setDateFilter] = useState(""); // 한국 시간 YYYY-MM-DD, 빈 값은 전체 날짜
  const [userFilter, setUserFilter] = useState(""); // 빈 값은 전체 사용자
  const filters = useMemo(() => ({ date: dateFilter, userId: userFilter }), [dateFilter, userFilter]);
  const { events, total, mode, status, error, lastOkAt, isMock, advance, refresh, hasMore, capped, loadingMore, loadMore } = useEvents({ live, filters });
  const { dates, users } = useScopeOptions(dateFilter, events.length);

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

  const statusText =
    status === "error"
      ? `서버 연결 실패 · 마지막 정상 수신 ${fmtTime(lastOkAt)}`
      : status === "loading"
      ? "데이터를 불러오는 중…"
      : live
      ? `${isMock ? "데모 재생" : mode === "stream" ? "이벤트 수신 중" : "실시간 연결 재시도 · 5초 대체 조회"} · 마지막 갱신 ${fmtTime(lastOkAt)}`
      : "갱신 일시정지됨";
  const dotClass = status === "error" ? "live-dot--err" : live && status === "ok" ? "" : "live-dot--off";

  return (
    <div className="dash">
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
          box-sizing: border-box;
          height: 100vh; height: 100dvh; /* 화면 높이에 맞추고, 남는 공간을 본문이 채워 좌우가 각각 스크롤됩니다 */
          min-height: 560px;
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

        /* 본문은 남는 높이를 채우고, 왼쪽 목록·오른쪽 상세가 각각 스크롤됩니다(페이지 전체가 같이 스크롤되지 않게). */
        .dash > :not(.dash-body) { flex-shrink: 0; }
        .dash-body { display: flex; flex: 1; min-height: 0; }
        .tester-fold { border-bottom: 1px solid var(--border); background: #fafbff; }
        .tester-fold > summary { cursor: pointer; padding: 9px 24px; font-size: 12.5px; font-weight: 500; color: var(--accent); }
        .tester-fold[open] > summary { border-bottom: 1px dashed var(--border); }
        .tester-fold .prompt-tester { border-bottom: none; max-height: 45vh; overflow-y: auto; }

        .side { width: 320px; min-height: 0; flex-shrink: 0; border-right: 1px solid var(--border); display: flex; flex-direction: column; background: #FBFBFD; }
        .filter-bar { flex-shrink: 0; padding: 14px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }
        .search-box { display: flex; align-items: center; gap: 7px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 9px 11px; }
        .search-box input { background: transparent; border: none; outline: none; color: var(--text); font-size: 13px; width: 100%; }
        .search-box input:focus-visible { outline: none; }
        .search-box:focus-within { border-color: var(--accent); }
        .filter-pills { display: flex; gap: 6px; }
        .filter-pill { flex: 1; padding: 7px 0; border-radius: 7px; border: 1px solid var(--border); background: var(--panel); color: var(--text-dim); font-size: 12.5px; cursor: pointer; }
        .filter-pill--active { background: var(--accent); color: #fff; border-color: var(--accent); }
        .tool-row { display: flex; gap: 8px; align-items: stretch; }
        .tool-row .sort-select { flex: 1; min-width: 0; }
        .tool-row .export-btn { flex-shrink: 0; white-space: nowrap; padding: 7px 11px; }
        .sort-select { display: flex; align-items: center; gap: 6px; background: var(--panel); border: 1px solid var(--border); border-radius: 7px; padding: 7px 9px; font-size: 12.5px; color: var(--text-dim); }
        .sort-select select { background: transparent; border: none; outline: none; color: var(--text); font-size: 12.5px; flex: 1; }
        .export-btn { display: flex; align-items: center; justify-content: center; gap: 6px; background: var(--panel); border: 1px solid var(--border); border-radius: 7px; padding: 9px 0; font-size: 12.5px; color: var(--accent); cursor: pointer; font-weight: 500; }
        .export-btn:hover { background: var(--accent-soft); }

        .session-list { overflow-y: auto; overscroll-behavior: contain; min-height: 0; flex: 1; padding: 6px; display: flex; flex-direction: column; gap: 3px; }
        .session-row { display: flex; align-items: center; gap: 10px; width: 100%; text-align: left; background: transparent; border: 1px solid transparent; border-radius: 9px; padding: 11px 12px; cursor: pointer; color: var(--text); }
        .session-row:hover { background: var(--panel); border-color: var(--border); }
        .session-row--active { background: var(--panel); border-color: var(--accent); box-shadow: 0 1px 3px rgba(20,22,30,.06); }
        .session-row--flash { animation: flash-row .45s ease 3; }
        @keyframes flash-row { 0%, 100% { background: transparent; border-color: transparent; } 50% { background: #FBE8E6; border-color: #EFC1BC; } }
        .session-row__dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
        .session-row__main { display: flex; flex-direction: column; flex: 1; min-width: 0; }
        .session-row__user { font-size: 13.5px; font-weight: 500; }
        .session-row__meta { font-size: 11.5px; color: var(--text-dim); font-family: 'IBM Plex Mono', ui-monospace, monospace; }
        .session-row__id { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .session-row__find { margin-top: 3px; font-size: 11.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .session-row__score { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 14.5px; font-weight: 600; }

        .list-empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 40px 20px; color: var(--text-dim); text-align: center; }
        .list-empty button { margin-top: 4px; background: var(--panel); border: 1px solid var(--border); border-radius: 7px; padding: 7px 14px; font-size: 12.5px; color: var(--accent); cursor: pointer; }

        .hover-tooltip { position: fixed; z-index: 60; background: #fff; border: 1px solid var(--border); border-radius: 9px; padding: 11px 13px; font-size: 12px; color: var(--text); pointer-events: none; box-shadow: 0 10px 28px rgba(20,22,30,.14); }
        .hover-tooltip__head { display: flex; align-items: center; gap: 8px; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-weight: 700; font-size: 14px; margin-bottom: 6px; }
        .hover-tooltip__badge { font-size: 11px; border: 1px solid; border-radius: 20px; padding: 1px 8px; font-family: 'Noto Sans KR', sans-serif; font-weight: 500; }
        .hover-tooltip__conf { margin-left: auto; font-size: 11px; font-weight: 500; color: var(--text-dim); font-family: 'Noto Sans KR', sans-serif; }
        .hover-tooltip__row { display: flex; align-items: flex-start; gap: 5px; color: var(--text-dim); margin-top: 4px; line-height: 1.45; }

        .detail { flex: 1; padding: 24px 28px; overflow-y: auto; overscroll-behavior: contain; min-height: 0; min-width: 0; }
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
        .override-note { display: flex; align-items: flex-start; gap: 8px; margin: 10px 0; padding: 9px 12px; border: 1px solid #C6362A; border-radius: 8px; background: #FDF2F1; color: #8F2A21; font-size: 12.5px; line-height: 1.5; }
        .override-note svg { flex-shrink: 0; margin-top: 2px; }
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
        .evidence-item--hit { border-left: 3px solid; padding-left: 9px; }
        .evidence-item--compact { align-items: center; gap: 7px; font-size: 12.5px; color: var(--text-dim); }
        .evidence-item--compact .evidence-item__code { color: var(--text-dim); }
        .evidence-item__short { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .evidence-item__metric { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 11.5px; flex-shrink: 0; }
        .evidence-item__metric-line { font-size: 12.5px; font-weight: 600; margin-top: 3px; }
        .evidence-note { margin-top: 14px; padding-top: 10px; border-top: 1px dashed var(--line, #E3E5EC); font-size: 11.5px; color: var(--text-dim); line-height: 1.5; }
        .evidence-item__chip { font-size: 11.5px; border: 1px solid; border-radius: 20px; padding: 1px 9px; flex-shrink: 0; }
        .list-more { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 10px 6px 6px; font-size: 12px; color: var(--text-dim); text-align: center; }
        .list-more button { background: var(--tint); border: 1px solid var(--line, #E3E5EC); border-radius: 8px; color: var(--accent); font-size: 12.5px; padding: 7px 14px; cursor: pointer; }
        .list-more button:disabled { opacity: .6; cursor: default; }
        .collapse-btn { display: flex; align-items: center; gap: 5px; margin-top: 22px; background: transparent; border: none; color: var(--accent); font-size: 12.5px; cursor: pointer; padding: 4px 0; }

        .company-breakdown { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; margin-top: 14px; }
        .company-breakdown__col { border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; background: var(--panel); min-width: 0; }
        .company-breakdown__col h4 { margin: 0 0 8px; font-size: 13.5px; font-weight: 600; }
        .company-breakdown__counts, .company-breakdown__formula { margin: 0 0 6px; font-size: 13px; font-weight: 600; }
        .company-breakdown__note { margin: 8px 0 0; font-size: 12px; color: var(--text-dim); line-height: 1.55; }
        .company-breakdown__table { max-height: 220px; overflow: auto; margin-top: 8px; }
        .company-breakdown table { width: 100%; border-collapse: collapse; font-size: 12px; table-layout: fixed; }
        .company-breakdown th, .company-breakdown td { text-align: left; padding: 5px 6px; border-bottom: 1px solid var(--tint); }
        .company-breakdown th { color: var(--text-dim); font-weight: 600; }
        .company-breakdown th:first-child { width: 48%; }
        .company-breakdown td { white-space: nowrap; }
        .company-breakdown td:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, monospace; }
        .company-breakdown tr.is-max td { background: var(--accent-soft); }
        .company-breakdown tr.is-link { cursor: pointer; }
        .company-breakdown tr.is-link:hover td { background: var(--tint); }
        .company-breakdown--single { grid-template-columns: 1fr; }
        .scope-filters { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
        .scope-filters select { flex: 1; min-width: 0; padding: 7px 8px; border: 1px solid var(--border); border-radius: 8px;
          background: var(--panel); color: var(--text); font-size: 12px; }
        .company-breakdown ul { list-style: none; margin: 0; padding: 0; font-size: 13px; }
        .company-breakdown li { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px dashed var(--border); }
        .window-card { margin-top: 18px; background: var(--accent-soft); border-radius: 12px; padding: 14px 16px; }
        .window-card__head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 13px; }
        .window-card__phase { font-size: 11.5px; font-weight: 700; color: var(--accent); background: var(--panel); border: 1px solid var(--border); border-radius: 999px; padding: 2px 9px; }
        .window-card__phase.is-open { color: #8A6D00; }
        .window-card__when { font-weight: 600; }
        .window-card__who { color: var(--text-dim); }
        .window-card__formula { display: flex; align-items: stretch; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
        .window-card__op { align-self: center; font-size: 18px; font-weight: 600; color: var(--text-dim); }
        .score-tile { flex: 1 1 120px; display: flex; flex-direction: column; gap: 2px; background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 9px 12px; min-width: 0; }
        .score-tile--total { border-width: 2px; }
        .score-tile__label { font-size: 11.5px; color: var(--text-dim); }
        .score-tile__value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.2; }
        .score-tile__value small { font-size: 12px; font-weight: 500; color: var(--text-dim); margin-left: 2px; }
        .score-tile__sub { font-size: 11.5px; color: var(--text-dim); }
        .window-card__source { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 12px; min-width: 0; }
        .window-card__source > span:first-child { color: var(--text-dim); flex-shrink: 0; }
        .window-card__source code { font-family: ui-monospace, monospace; font-size: 11.5px; background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 2px 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
        .window-card__muted { color: var(--text-dim); flex-shrink: 0; }
        .window-card__reason { margin: 10px 0 0; font-size: 12.5px; color: #8A6D00; }
        .window-card__notes { margin-top: 10px; font-size: 12px; color: var(--text-dim); }
        .window-card__notes summary { cursor: pointer; }
        .window-card__notes ul { margin: 6px 0 0; padding-left: 18px; line-height: 1.6; }
        .llm-box { margin-top: 22px; background: var(--accent-soft); border-radius: 12px; padding: 16px 18px; }
        .llm-box__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 9px; gap: 10px; flex-wrap: wrap; }
        .llm-box__title { display: flex; align-items: center; gap: 7px; font-size: 13.5px; font-weight: 600; color: var(--accent); }
        .llm-btn { display: flex; align-items: center; gap: 6px; background: var(--accent); border: none; color: #fff; border-radius: 7px; padding: 8px 14px; font-size: 12.5px; cursor: pointer; font-weight: 500; }
        .llm-btn:hover { background: #2A3785; }
        .llm-btn:disabled { opacity: .55; cursor: default; }
        .llm-text--clamp { display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
        .evidence-group, .session-row__main, .top-issues, .llm-text, .trend-card { word-break: keep-all; overflow-wrap: anywhere; }
        .trend-card--note { min-width: 260px; }
        .trend-card--note p { margin: 4px 0 0; font-size: 12.5px; line-height: 1.6; color: var(--text-dim); }
        .evidence-summary { margin-top: 12px; font-size: 12.5px; color: var(--text-dim); }
        .evidence-summary summary { cursor: pointer; color: var(--accent); }
        .evidence-summary__row { margin-top: 8px; line-height: 1.5; }
        .evidence-summary__row p { margin: 3px 0 0; font-size: 12px; }
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
          .dash { height: auto; }
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
            사용자·단말별 5분 구간 위험도
            <div className="dash-header__sub">
              <span className={`live-dot ${dotClass}`} />
              {statusText} · 최근 {events.length} / 전체 {total}건
              {isMock && <span className="demo-chip">DEMO 시나리오</span>}
            </div>
          </div>
        </div>
        <div className="dash-header__right">
          <div className="header-stats">
            <div className="header-stats__row">
              <span style={{ color: LEVELS.danger.color }}>위험 {counts.danger}</span>
              <span style={{ color: LEVELS.warning.color }}>경고 {counts.warning}</span>
              <span style={{ color: LEVELS.caution.color }}>주의 {counts.caution}</span>
              <span style={{ color: LEVELS.normal.color }}>정상 {counts.normal}</span>
              <span style={{ color: LEVELS.pending.color }}>미판정 {counts.pending}</span>
              <span style={{ color: LEVELS.error.color }}>오류 {counts.error}</span>
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

      {!isMock && (
        <details className="tester-fold">
          <summary>프롬프트 직접 테스트</summary>
          <PromptTester apiBase={API_BASE} />
        </details>
      )}

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
                    <option key={d.date} value={d.date}>{d.date} · 사용자 {d.user_count}명 · 구간 {d.window_count}</option>
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
                <User size={14} /> <b>{selected.user}</b> · {selected.dept}
              </span>
              <span className="detail-meta__item">
                <Network size={14} /> {selected.scope === "company" || selected.scope === "user_device" ? "구간 ID" : "세션 ID"} <b>{selected.sessionId ?? selected.id}</b>
              </span>
              <span className="detail-meta__item">
                <Clock size={14} /> 관측 {selected.connectedAt}
                {selected.observation_kind === "event" ? " · 개별 이벤트" : <> · 구간 길이{" "}
                  <b><LiveDuration startedAtMs={selected.startedAtMs} endedAtMs={selected.endedAtMs} /></b>
                </>}
              </span>
            </div>

            {(selected.scope === "company" || selected.scope === "user_device") && (
              <WindowSummaryCard event={selected} />
            )}

            {(selected.scope === "company" || selected.scope === "user_device") && <CompanyBreakdown event={selected} />}
            {selected.scope === "user_device" && (
              <SameSlotPanel selected={selected} events={events} isMock={isMock}
                onSelect={(row) => { if (userFilter && row.user !== userFilter) setUserFilter(""); setSelectedId(row.id); }} />
            )}

            <div className="detail-main">
              <RiskScoreBadge score={selected.score} level={eventLevel(selected)} />
              <ConfidenceMeter confidence={selected.confidence} />
              {(selected.score != null || !selected.isReal) && <div className="trend-card">
                <div className="trend-card__title">화면 조회 중 점수 변화</div>
                <ScoreTrendChart
                  history={history[selected.id] || [selected.score]}
                  color={LEVELS[eventLevel(selected)].color}
                />
              </div>}
              {selected.score == null && selected.isReal && (
                <div className="trend-card trend-card--note">
                  <div className="trend-card__title">통합 등급 미판정</div>
                  <p>통합 점수·등급은 아직 계산되지 않았습니다. <b>미판정·미탐지는 안전 판정이 아닙니다.</b> 아래는 엔진별 개별 결과입니다.</p>
                </div>
              )}
            </div>

            <TopIssues event={selected} />
            {selected.override && (
              <div className="override-note" role="note">
                <ShieldAlert size={14} color={LEVELS.danger.color} />
                <span>
                  <b>강제 위험 규칙 적용</b> · 합계 점수와 관계없이 위험으로 판정됨
                  {selected.overrideReasons.length > 0 && <> ({selected.overrideReasons.join(" / ")})</>}
                </span>
              </div>
            )}
            {selected.processing_state === "processing" && <div className="evidence-empty" role="status">분석 진행 중 · 완료된 엔진 결과부터 표시합니다.</div>}

            <div className="evidence-cols">
              <EvidenceGroup
                title={selected.scope === "company" ? "회사 전체 네트워크 · 5분 집계 (N1~N6, 이전 버전)" : "이 사용자·단말 네트워크 · 5분 집계 (N1~N6)"}
                icon={<Network size={15} color="#5F6372" />}
                items={selected.network_reasons}
                accent="#33429A"
                note="항목 점수는 모델 클래스 확률(%)이며 통합 기여도가 아닙니다."
                expanded={openSafe.net}
                onToggle={() => setOpenSafe((s) => ({ ...s, net: !s.net }))}
              />
              <EvidenceGroup
                title="구간 내 최고 점수 프롬프트 분석"
                icon={<MessageSquareWarning size={15} color="#5F6372" />}
                items={selected.prompt_reasons}
                accent="#7A4FB5"
                note="확률은 라벨 분류 결과이며 위험도 점수가 아닙니다. 항목별 판단 근거는 제공되지 않습니다. 미탐지는 안전 판정이 아닙니다."
                expanded={openSafe.prompt}
                onToggle={() => setOpenSafe((s) => ({ ...s, prompt: !s.prompt }))}
              />
            </div>

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
                  <div className={`llm-text ${exOpen[selected.id] ? "" : "llm-text--clamp"}`}>{ex.text}</div>
                  {ex.text.length > 220 && (
                    <button className="collapse-btn" onClick={() => setExOpen((o) => ({ ...o, [selected.id]: !o[selected.id] }))}>
                      <ChevronDown size={13} style={{ transform: exOpen[selected.id] ? "rotate(180deg)" : "none" }} />
                      {exOpen[selected.id] ? "요약 접기" : "전체 요약 보기"}
                    </button>
                  )}
                </>
              )}
              {ex?.status === "done" && (
                <div className="llm-meta">
                  <span className="llm-tag">{ex.source === "stored" ? "저장 결과 요약" : ex.source === "demo" ? "데모 요약" : "화면 데이터 요약"}</span>
                  {ex.source === "rule" && <span>서버 요약 조회에 실패해 마지막 화면 데이터로 요약했습니다.</span>}
                  {exStale && <span className="llm-tag llm-tag--stale">분석 결과가 바뀌어 최신 내용과 다를 수 있어요</span>}
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
