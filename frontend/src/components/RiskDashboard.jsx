// ⚠️ 목업 UI 컴포넌트 — 백엔드 연동 필요
//
// 1) 더미 데이터 (INITIAL_SESSIONS, 아래)
//    실제 연동 시 아래 필드 형태의 API 응답으로 교체 필요:
//    {
//      id, user, dept, connectedAt, duration, score,
//      network_reasons: [{ label, detail, status, weight }],
//      prompt_reasons:  [{ label, detail, status, weight }]
//    }
//    TODO: useEffect로 API 호출해서 sessions state 채우기
//    (현재는 useState(INITIAL_SESSIONS)로 하드코딩됨 — 아래 RiskDashboard 컴포넌트 참고)
//
// 2) AI 판단 근거 요약 (generateLlmExplanation 함수)
//    현재 fetch가 https://api.anthropic.com/v1/messages 를 브라우저에서 직접 호출 중.
//    이건 채팅 미리보기 환경에서만 동작하는 임시 코드라 실서비스에선 그대로 못 씀
//    (CORS 에러 + API 키가 프론트에 노출됨).
//    TODO: 이 fetch를 우리 백엔드 프록시 엔드포인트(예: POST /api/sessions/:id/explain)로 교체 필요.
//
// 필요한 패키지: recharts, lucide-react (package.json에 추가 필요)

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
  Radio,
  Download,
  Sparkles,
  Pause,
  Play,
  Loader2,
  ArrowUpDown,
  TriangleAlert,
  X,
} from "lucide-react";
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip as RTooltip,
} from "recharts";

// ---------------------------------------------------------------------------
// Dummy data — matches the field contract sketched for the modeling team:
// score, level, network_reasons[], prompt_reasons[]
// 각 근거 항목의 weight(0~100)는 최종 점수에 대한 상대적 기여도(더미) — 추후 SHAP 값으로 교체 예정
// ---------------------------------------------------------------------------

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

const INITIAL_SESSIONS = [
  {
    id: "VPN-88213",
    user: "김O식 (전산실)",
    dept: "정보시스템팀",
    connectedAt: "02:14:07",
    duration: "4시간 12분",
    score: 82,
    network_reasons: [
      { label: "세션 지속시간", detail: "4시간 12분 (평균 대비 3.1배)", status: "danger", weight: 34 },
      { label: "접속 시간대", detail: "새벽 02:14 접속 (업무 외 시간)", status: "danger", weight: 28 },
      { label: "초당 전송 바이트", detail: "8.4MB/s (평균 대비 6.0배)", status: "danger", weight: 31 },
      { label: "통신 빈도", detail: "정상 범위 내", status: "safe", weight: 7 },
    ],
    prompt_reasons: [
      { label: "개인정보 탐지", detail: "계좌번호 3건, 주민등록번호 1건", status: "danger", weight: 52 },
      { label: "위험 요청 패턴", detail: "대용량 문서 전체 요약 요청 감지", status: "caution", weight: 33 },
      { label: "금칙어", detail: "미탐지", status: "safe", weight: 15 },
    ],
  },
  {
    id: "VPN-77042",
    user: "이O연 (마케팅)",
    dept: "마케팅기획팀",
    connectedAt: "14:02:51",
    duration: "38분",
    score: 63,
    network_reasons: [
      { label: "세션 지속시간", detail: "38분 (평균 범위)", status: "safe", weight: 10 },
      { label: "접속 시간대", detail: "14:02 접속 (업무 시간 내)", status: "safe", weight: 8 },
      { label: "초당 전송 바이트", detail: "2.1MB/s (평균 대비 1.8배)", status: "caution", weight: 39 },
      { label: "통신 빈도", detail: "동일 IP 반복 재접속 3회", status: "caution", weight: 43 },
    ],
    prompt_reasons: [
      { label: "개인정보 탐지", detail: "이메일 주소 2건", status: "caution", weight: 61 },
      { label: "위험 요청 패턴", detail: "미탐지", status: "safe", weight: 20 },
      { label: "금칙어", detail: "미탐지", status: "safe", weight: 19 },
    ],
  },
  {
    id: "VPN-65310",
    user: "박O훈 (재무)",
    dept: "재무회계팀",
    connectedAt: "10:47:19",
    duration: "1시간 02분",
    score: 41,
    network_reasons: [
      { label: "세션 지속시간", detail: "1시간 02분 (평균 범위)", status: "safe", weight: 22 },
      { label: "접속 시간대", detail: "10:47 접속 (업무 시간 내)", status: "safe", weight: 18 },
      { label: "초당 전송 바이트", detail: "1.4MB/s (평균 범위)", status: "safe", weight: 24 },
      { label: "통신 빈도", detail: "정상 범위 내", status: "safe", weight: 16 },
    ],
    prompt_reasons: [
      { label: "개인정보 탐지", detail: "계좌번호 1건", status: "caution", weight: 68 },
      { label: "위험 요청 패턴", detail: "미탐지", status: "safe", weight: 17 },
      { label: "금칙어", detail: "미탐지", status: "safe", weight: 15 },
    ],
  },
  {
    id: "VPN-54129",
    user: "최O아 (인사)",
    dept: "인사팀",
    connectedAt: "09:15:33",
    duration: "22분",
    score: 15,
    network_reasons: [
      { label: "세션 지속시간", detail: "22분 (평균 범위)", status: "safe", weight: 26 },
      { label: "접속 시간대", detail: "09:15 접속 (업무 시간 내)", status: "safe", weight: 24 },
      { label: "초당 전송 바이트", detail: "0.6MB/s (평균 범위)", status: "safe", weight: 25 },
      { label: "통신 빈도", detail: "정상 범위 내", status: "safe", weight: 25 },
    ],
    prompt_reasons: [
      { label: "개인정보 탐지", detail: "미탐지", status: "safe", weight: 34 },
      { label: "위험 요청 패턴", detail: "미탐지", status: "safe", weight: 33 },
      { label: "금칙어", detail: "미탐지", status: "safe", weight: 33 },
    ],
  },
  {
    id: "VPN-49887",
    user: "정O우 (개발)",
    dept: "플랫폼개발팀",
    connectedAt: "11:03:44",
    duration: "51분",
    score: 8,
    network_reasons: [
      { label: "세션 지속시간", detail: "51분 (평균 범위)", status: "safe", weight: 25 },
      { label: "접속 시간대", detail: "11:03 접속 (업무 시간 내)", status: "safe", weight: 25 },
      { label: "초당 전송 바이트", detail: "0.9MB/s (평균 범위)", status: "safe", weight: 25 },
      { label: "통신 빈도", detail: "정상 범위 내", status: "safe", weight: 25 },
    ],
    prompt_reasons: [
      { label: "개인정보 탐지", detail: "미탐지", status: "safe", weight: 34 },
      { label: "위험 요청 패턴", detail: "미탐지", status: "safe", weight: 33 },
      { label: "금칙어", detail: "미탐지", status: "safe", weight: 33 },
    ],
  },
];

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function nowLabel() {
  const d = new Date();
  return d.toLocaleTimeString("ko-KR", { hour12: false });
}

// ---------------------------------------------------------------------------

function StatusIcon({ status, size = 14 }) {
  if (status === "danger") return <ShieldAlert size={size} color={LEVELS.danger.color} />;
  if (status === "caution") return <ShieldQuestion size={size} color={LEVELS.caution.color} />;
  return <ShieldCheck size={size} color={LEVELS.safe.color} />;
}

function EvidenceGroup({ title, icon, items, accent }) {
  return (
    <div className="evidence-group" style={{ borderLeftColor: accent }}>
      <div className="evidence-group__head">
        {icon}
        <span>{title}</span>
      </div>
      <ul className="evidence-list">
        {items.map((item, i) => (
          <li key={i} className="evidence-item">
            <StatusIcon status={item.status} />
            <div className="evidence-item__text">
              <div className="evidence-item__row">
                <span className="evidence-item__label">{item.label}</span>
                <span
                  className="evidence-item__chip"
                  style={{ color: LEVELS[item.status].color, borderColor: LEVELS[item.status].color }}
                >
                  {LEVELS[item.status].label}
                </span>
              </div>
              <span className="evidence-item__detail">{item.detail}</span>
              <div className="weight-bar">
                <div
                  className="weight-bar__fill"
                  style={{ width: `${item.weight}%`, background: LEVELS[item.status].color }}
                />
                <span className="weight-bar__label">기여도 {item.weight}%</span>
              </div>
            </div>
          </li>
        ))}
      </ul>
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
        <span className="score-badge__num" style={{ color, transition: "color 0.6s ease" }}>
          {score}
        </span>
        <span className="score-badge__unit">/ 100</span>
      </div>
      <span className="score-badge__label" style={{ color, borderColor: color }}>
        {label}
      </span>
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
      <span className="session-row__score" style={{ color, transition: "color 0.6s ease" }}>
        {session.score}
      </span>
      <ChevronRight size={16} color="#B7BAC3" />
    </button>
  );
}

const TOOLTIP_W = 230;
const TOOLTIP_H_EST = 120;
const EDGE_MARGIN = 12;

function HoverTooltip({ session, pos }) {
  if (!session) return null;
  const level = levelFromScore(session.score);
  const topNetwork = session.network_reasons.find((r) => r.status !== "safe") || session.network_reasons[0];
  const topPrompt = session.prompt_reasons.find((r) => r.status !== "safe") || session.prompt_reasons[0];

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
      </div>
      <div className="hover-tooltip__row">
        <Network size={11} /> {topNetwork.label}: {topNetwork.detail}
      </div>
      <div className="hover-tooltip__row">
        <MessageSquareWarning size={11} /> {topPrompt.label}: {topPrompt.detail}
      </div>
    </div>
  );
}

function LevelBar({ counts, total }) {
  // 도넛 대신 헤더에 바로 얹는 얇은 세그먼트 바 — 박스 하나를 덜어내고 한눈에 비율을 보여줌
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
    <div style={{ width: "100%", height: 108 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 6, right: 10, bottom: 0, left: -18 }}>
          <XAxis dataKey="i" hide />
          <YAxis domain={[0, 100]} width={26} tick={{ fontSize: 9, fill: "#8B8F99" }} axisLine={false} tickLine={false} />
          <RTooltip
            contentStyle={{ background: "#FFFFFF", border: "1px solid #E3E5EA", borderRadius: 6, fontSize: 11 }}
            labelFormatter={() => ""}
            formatter={(v) => [`${v}점`, "점수"]}
          />
          <Line type="monotone" dataKey="score" stroke={color} strokeWidth={2.2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ToastStack({ toasts, onDismiss }) {
  return (
    <div className="toast-stack">
      {toasts.map((t) => (
        <div key={t.id} className="toast" style={{ borderLeftColor: t.color }}>
          <TriangleAlert size={14} color={t.color} />
          <span className="toast__text">{t.text}</span>
          <button className="toast__close" onClick={() => onDismiss(t.id)}>
            <X size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------

export default function RiskDashboard() {
  // TODO(backend): 더미 데이터 대신 API 응답으로 교체. 예)
  // const [sessions, setSessions] = useState([]);
  // useEffect(() => { fetchSessions().then(setSessions); }, []);
  const [sessions, setSessions] = useState(INITIAL_SESSIONS);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [sortBy, setSortBy] = useState("default");
  const [selectedId, setSelectedId] = useState(INITIAL_SESSIONS[0].id);
  const [live, setLive] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(nowLabel());
  const [history, setHistory] = useState(() =>
    Object.fromEntries(INITIAL_SESSIONS.map((s) => [s.id, [s.score]]))
  );
  const [hover, setHover] = useState({ session: null, pos: { x: 0, y: 0 } });
  const [llmText, setLlmText] = useState({});
  const [llmLoading, setLlmLoading] = useState(false);
  const [llmError, setLlmError] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [flashIds, setFlashIds] = useState({});
  const listRef = useRef(null);
  const prevLevels = useRef(
    Object.fromEntries(INITIAL_SESSIONS.map((s) => [s.id, levelFromScore(s.score)]))
  );
  const toastIdRef = useRef(0);

  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => {
      setSessions((prev) =>
        prev.map((s) => {
          const delta = Math.round((Math.random() - 0.5) * 6);
          const nextScore = clamp(s.score + delta, 1, 99);
          return { ...s, score: nextScore };
        })
      );
      setLastUpdated(nowLabel());
    }, 5000);
    return () => clearInterval(t);
  }, [live]);

  useEffect(() => {
    setHistory((prev) => {
      const next = { ...prev };
      sessions.forEach((s) => {
        const arr = next[s.id] ? [...next[s.id], s.score] : [s.score];
        next[s.id] = arr.slice(-12);
      });
      return next;
    });

    const newToasts = [];
    const newFlash = {};
    sessions.forEach((s) => {
      const newLevel = levelFromScore(s.score);
      const oldLevel = prevLevels.current[s.id];
      if (oldLevel && newLevel !== oldLevel) {
        const escalated = LEVELS[newLevel].rank > LEVELS[oldLevel].rank;
        toastIdRef.current += 1;
        newToasts.push({
          id: toastIdRef.current,
          text: `${s.user} 세션이 ${LEVELS[oldLevel].label} → ${LEVELS[newLevel].label} 등급으로 전환됨`,
          color: LEVELS[newLevel].color,
        });
        if (escalated) newFlash[s.id] = true;
      }
      prevLevels.current[s.id] = newLevel;
    });

    if (newToasts.length) {
      setToasts((prev) => [...prev, ...newToasts].slice(-4));
      newToasts.forEach((t) => {
        setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== t.id)), 4500);
      });
    }
    if (Object.keys(newFlash).length) {
      setFlashIds((prev) => ({ ...prev, ...newFlash }));
      Object.keys(newFlash).forEach((id) => {
        setTimeout(() => setFlashIds((prev) => ({ ...prev, [id]: false })), 1800);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions]);

  const filtered = useMemo(() => {
    let list = sessions.filter((s) => {
      const q = query.trim().toLowerCase();
      const matchesQuery =
        q === "" ||
        s.user.toLowerCase().includes(q) ||
        s.id.toLowerCase().includes(q) ||
        s.dept.toLowerCase().includes(q);
      const matchesFilter = filter === "all" || levelFromScore(s.score) === filter;
      return matchesQuery && matchesFilter;
    });

    if (sortBy === "score_desc") {
      list = [...list].sort((a, b) => b.score - a.score);
    } else if (sortBy === "recent") {
      list = [...list].sort((a, b) => (a.connectedAt < b.connectedAt ? 1 : -1));
    }
    return list;
  }, [query, filter, sortBy, sessions]);

  const selected = sessions.find((s) => s.id === selectedId) || filtered[0];

  const counts = useMemo(() => {
    const c = { danger: 0, caution: 0, safe: 0 };
    sessions.forEach((s) => c[levelFromScore(s.score)]++);
    return c;
  }, [sessions]);

  function handleHover(session, e) {
    setHover({ session, pos: { x: e.clientX, y: e.clientY } });
  }

  function handleLeave() {
    setHover({ session: null, pos: { x: 0, y: 0 } });
  }

  function dismissToast(id) {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }

  function exportCsv() {
    const header = ["세션ID", "사용자", "부서", "점수", "등급", "접속시간", "지속시간"];
    const rows = filtered.map((s) => [
      s.id,
      s.user,
      s.dept,
      s.score,
      LEVELS[levelFromScore(s.score)].label,
      s.connectedAt,
      s.duration,
    ]);
    const csv = [header, ...rows].map((r) => r.join(",")).join("\n");
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

  async function generateLlmExplanation() {
    if (!selected) return;
    if (llmText[selected.id]) return;
    setLlmLoading(true);
    setLlmError(null);
    try {
      const evidenceText = [
        `점수: ${selected.score}/100 (${LEVELS[levelFromScore(selected.score)].label})`,
        "네트워크 이상 징후:",
        ...selected.network_reasons.map((r) => `- ${r.label}: ${r.detail} [${LEVELS[r.status].label}, 기여도 ${r.weight}%]`),
        "프롬프트 위험 탐지:",
        ...selected.prompt_reasons.map((r) => `- ${r.label}: ${r.detail} [${LEVELS[r.status].label}, 기여도 ${r.weight}%]`),
      ].join("\n");

      // TODO(backend): 아래 fetch는 채팅 미리보기 전용 임시 코드.
      // api.anthropic.com을 브라우저에서 직접 호출하면 CORS 에러 + API 키 노출 문제가 있음.
      // 실서비스에서는 우리 백엔드 프록시(예: POST /api/sessions/:id/explain)로 교체하고,
      // evidenceText(또는 session 데이터)를 body에 담아 보내는 방식으로 변경 필요.
      const response = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "claude-sonnet-4-6",
          max_tokens: 300,
          messages: [
            {
              role: "user",
              content: `다음은 사내 보안 대시보드에 표시되는 VPN 세션의 위험 판단 근거 데이터다. 보안 담당자가 한눈에 이해할 수 있도록, 이 세션이 왜 이 등급으로 판단됐는지 2~3문장의 자연스러운 한국어 요약으로 설명해줘. 수치를 인용하되 목록을 나열하지 말고 문장으로 풀어써줘.\n\n${evidenceText}`,
            },
          ],
        }),
      });
      const data = await response.json();
      const text = (data.content || [])
        .filter((b) => b.type === "text")
        .map((b) => b.text)
        .join("\n")
        .trim();
      setLlmText((prev) => ({ ...prev, [selected.id]: text || "설명을 생성하지 못했습니다." }));
    } catch (err) {
      setLlmError("설명 생성 중 오류가 발생했습니다.");
    } finally {
      setLlmLoading(false);
    }
  }

  const totalCount = sessions.length;

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
          --tint: #F2F3F7;
          --border: #E3E5EC;
          --text: #191B22;
          --text-dim: #6D7180;
          --accent: #33429A;
          --accent-soft: #ECEEFA;
          font-family: 'Noto Sans KR', 'Apple SD Gothic Neo', -apple-system, sans-serif;
          background: var(--bg);
          color: var(--text);
          border-radius: 14px;
          border: 1px solid var(--border);
          overflow: hidden;
          min-height: 660px;
          display: flex;
          flex-direction: column;
          box-shadow: 0 1px 2px rgba(20,22,30,0.04);
        }
        .dash * { box-sizing: border-box; }
        .dash__topline {
          height: 3px;
          width: 100%;
          background: linear-gradient(90deg, var(--accent) 0%, #6B7FE0 45%, #9FB0EE 100%);
          flex-shrink: 0;
        }

        .toast-stack {
          position: absolute;
          top: 16px;
          right: 16px;
          z-index: 40;
          display: flex;
          flex-direction: column;
          gap: 8px;
          width: 300px;
        }
        .toast {
          display: flex;
          align-items: center;
          gap: 8px;
          background: #FFFFFF;
          border: 1px solid var(--border);
          border-left: 3px solid;
          border-radius: 8px;
          padding: 10px 12px;
          font-size: 11.5px;
          color: var(--text);
          box-shadow: 0 10px 26px rgba(20,22,30,0.12);
          animation: toast-in 0.25s ease;
        }
        @keyframes toast-in {
          from { opacity: 0; transform: translateY(-6px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .toast__text { flex: 1; }
        .toast__close {
          background: transparent;
          border: none;
          color: var(--text-dim);
          cursor: pointer;
          display: flex;
        }

        .dash-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 18px 24px 16px;
          border-bottom: 1px solid var(--border);
          gap: 18px;
          flex-wrap: wrap;
          position: relative;
        }
        .dash-header__title {
          display: flex;
          align-items: center;
          gap: 11px;
          font-size: 16px;
          font-weight: 700;
          letter-spacing: -0.1px;
        }
        .dash-header__icon {
          width: 32px;
          height: 32px;
          border-radius: 9px;
          background: var(--accent-soft);
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
        }
        .dash-header__title small {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 11.5px;
          font-weight: 400;
          color: var(--text-dim);
          margin-top: 3px;
        }
        .live-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: #137D57;
          display: inline-block;
          animation: pulse 1.6s infinite;
        }
        .live-dot--off { background: #B7BAC3; animation: none; }
        @keyframes pulse {
          0% { box-shadow: 0 0 0 0 rgba(19,125,87,0.35); }
          70% { box-shadow: 0 0 0 6px rgba(19,125,87,0); }
          100% { box-shadow: 0 0 0 0 rgba(19,125,87,0); }
        }

        .dash-header__right {
          display: flex;
          align-items: center;
          gap: 16px;
        }
        .header-stats {
          display: flex;
          flex-direction: column;
          gap: 5px;
          min-width: 170px;
        }
        .header-stats__row {
          display: flex;
          justify-content: space-between;
          font-family: 'IBM Plex Mono', ui-monospace, monospace;
          font-size: 11px;
          color: var(--text-dim);
        }
        .level-bar {
          display: flex;
          width: 100%;
          height: 6px;
          border-radius: 4px;
          overflow: hidden;
          background: var(--tint);
        }
        .level-bar__seg { height: 100%; }

        .live-toggle {
          display: flex;
          align-items: center;
          gap: 6px;
          background: var(--panel);
          border: 1px solid var(--border);
          border-radius: 7px;
          padding: 6px 11px;
          font-size: 11.5px;
          color: var(--text-dim);
          cursor: pointer;
        }
        .live-toggle:hover { color: var(--text); border-color: #C7CAD4; }

        .dash-body {
          display: flex;
          flex: 1;
          min-height: 0;
        }

        .side {
          width: 300px;
          border-right: 1px solid var(--border);
          display: flex;
          flex-direction: column;
          position: relative;
          background: #FBFBFD;
        }
        .filter-bar {
          padding: 14px;
          border-bottom: 1px solid var(--border);
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .search-box {
          display: flex;
          align-items: center;
          gap: 7px;
          background: var(--panel);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 8px 10px;
        }
        .search-box input {
          background: transparent;
          border: none;
          outline: none;
          color: var(--text);
          font-size: 12.5px;
          width: 100%;
          font-family: inherit;
        }
        .filter-pills {
          display: flex;
          gap: 6px;
        }
        .filter-pill {
          flex: 1;
          padding: 6px 0;
          border-radius: 7px;
          border: 1px solid var(--border);
          background: var(--panel);
          color: var(--text-dim);
          font-size: 11.5px;
          cursor: pointer;
          font-family: inherit;
        }
        .filter-pill--active {
          background: var(--accent);
          color: #fff;
          border-color: var(--accent);
        }
        .row-2 { display: flex; gap: 6px; }
        .sort-select {
          flex: 1;
          display: flex;
          align-items: center;
          gap: 6px;
          background: var(--panel);
          border: 1px solid var(--border);
          border-radius: 7px;
          padding: 6px 8px;
          font-size: 11.5px;
          color: var(--text-dim);
        }
        .sort-select select {
          background: transparent;
          border: none;
          outline: none;
          color: var(--text);
          font-size: 11.5px;
          flex: 1;
          font-family: inherit;
        }
        .export-btn {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          background: var(--panel);
          border: 1px solid var(--border);
          border-radius: 7px;
          padding: 8px 0;
          font-size: 11.5px;
          color: var(--accent);
          cursor: pointer;
          font-weight: 500;
          font-family: inherit;
        }
        .export-btn:hover { background: var(--accent-soft); }

        .session-list {
          overflow-y: auto;
          flex: 1;
          padding: 6px;
          display: flex;
          flex-direction: column;
          gap: 3px;
        }
        .session-row {
          display: flex;
          align-items: center;
          gap: 10px;
          width: 100%;
          text-align: left;
          background: transparent;
          border: 1px solid transparent;
          border-radius: 9px;
          padding: 10px 11px;
          cursor: pointer;
          color: var(--text);
          font-family: inherit;
        }
        .session-row:hover { background: var(--panel); border-color: var(--border); }
        .session-row--active {
          background: var(--panel);
          border-color: var(--accent);
          box-shadow: 0 1px 3px rgba(20,22,30,0.06);
        }
        .session-row--flash { animation: flash-row 0.45s ease 3; }
        @keyframes flash-row {
          0%, 100% { background: transparent; border-color: transparent; }
          50% { background: #FBE8E6; border-color: #EFC1BC; }
        }
        .session-row__dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
        .session-row__main { display: flex; flex-direction: column; flex: 1; min-width: 0; }
        .session-row__user { font-size: 12.5px; font-weight: 500; }
        .session-row__meta {
          font-size: 10.5px;
          color: var(--text-dim);
          font-family: 'IBM Plex Mono', ui-monospace, monospace;
        }
        .session-row__score {
          font-family: 'IBM Plex Mono', ui-monospace, monospace;
          font-size: 13px;
          font-weight: 600;
        }

        .hover-tooltip {
          position: fixed;
          z-index: 60;
          background: #FFFFFF;
          border: 1px solid var(--border);
          border-radius: 9px;
          padding: 11px 13px;
          font-size: 11px;
          color: var(--text);
          pointer-events: none;
          box-shadow: 0 10px 28px rgba(20,22,30,0.14);
        }
        .hover-tooltip__head {
          display: flex;
          align-items: center;
          gap: 8px;
          font-family: 'IBM Plex Mono', ui-monospace, monospace;
          font-weight: 700;
          font-size: 13px;
          margin-bottom: 6px;
        }
        .hover-tooltip__badge {
          font-size: 10px;
          border: 1px solid;
          border-radius: 20px;
          padding: 1px 8px;
          font-family: 'Noto Sans KR', sans-serif;
          font-weight: 500;
        }
        .hover-tooltip__row {
          display: flex;
          align-items: flex-start;
          gap: 5px;
          color: var(--text-dim);
          margin-top: 4px;
          line-height: 1.4;
        }

        .detail { flex: 1; padding: 24px 28px; overflow-y: auto; }
        .detail-meta {
          display: flex;
          gap: 22px;
          margin-bottom: 22px;
          flex-wrap: wrap;
        }
        .detail-meta__item {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 12.5px;
          color: var(--text-dim);
        }
        .detail-meta__item b { color: var(--text); font-weight: 600; }

        .detail-main {
          display: flex;
          gap: 26px;
          align-items: flex-start;
        }

        .score-badge {
          position: relative;
          width: 140px;
          height: 140px;
          flex-shrink: 0;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .score-badge__center { position: absolute; display: flex; flex-direction: column; align-items: center; }
        .score-badge__num {
          font-family: 'IBM Plex Mono', ui-monospace, monospace;
          font-size: 34px;
          font-weight: 700;
          line-height: 1;
        }
        .score-badge__unit { font-size: 10.5px; color: var(--text-dim); margin-top: 2px; }
        .score-badge__label {
          position: absolute;
          bottom: -26px;
          font-size: 11.5px;
          border: 1px solid;
          border-radius: 20px;
          padding: 2px 12px;
          background: #fff;
        }

        .trend-card {
          flex: 1;
          min-width: 220px;
          background: var(--tint);
          border-radius: 12px;
          padding: 14px 16px;
        }
        .trend-card__title {
          font-size: 11.5px;
          color: var(--text-dim);
          font-weight: 500;
          margin-bottom: 2px;
        }

        .evidence-cols {
          display: flex;
          gap: 0;
          margin-top: 24px;
          border-top: 1px solid var(--border);
        }
        .evidence-group {
          flex: 1;
          padding: 18px 20px;
          border-left: 3px solid;
        }
        .evidence-group:first-child { border-right: 1px solid var(--border); }
        .evidence-group__head {
          display: flex;
          align-items: center;
          gap: 7px;
          font-size: 12.5px;
          font-weight: 600;
          color: var(--text-dim);
          margin-bottom: 12px;
        }
        .evidence-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }
        .evidence-item { display: flex; align-items: flex-start; gap: 9px; }
        .evidence-item__text { display: flex; flex-direction: column; flex: 1; min-width: 0; }
        .evidence-item__row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
        .evidence-item__label { font-size: 12.5px; font-weight: 500; }
        .evidence-item__detail {
          font-size: 11px;
          color: var(--text-dim);
          font-family: 'IBM Plex Mono', ui-monospace, monospace;
          margin-top: 2px;
        }
        .evidence-item__chip { font-size: 10px; border: 1px solid; border-radius: 20px; padding: 1px 8px; flex-shrink: 0; }
        .weight-bar { position: relative; margin-top: 6px; height: 4px; background: #E9EAF0; border-radius: 4px; overflow: hidden; }
        .weight-bar__fill { height: 100%; border-radius: 4px; opacity: 0.9; transition: width 0.4s ease; }
        .weight-bar__label { position: absolute; right: 0; top: 6px; font-size: 9px; color: var(--text-dim); }

        .llm-box { margin-top: 22px; background: var(--accent-soft); border-radius: 12px; padding: 16px 18px; }
        .llm-box__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 9px; }
        .llm-box__title { display: flex; align-items: center; gap: 7px; font-size: 12.5px; font-weight: 600; color: var(--accent); }
        .llm-btn {
          display: flex;
          align-items: center;
          gap: 6px;
          background: var(--accent);
          border: none;
          color: #fff;
          border-radius: 7px;
          padding: 7px 13px;
          font-size: 11.5px;
          cursor: pointer;
          font-family: inherit;
          font-weight: 500;
        }
        .llm-btn:hover { background: #2A3785; }
        .llm-btn:disabled { opacity: 0.55; cursor: default; }
        .llm-text { font-size: 12.5px; line-height: 1.65; color: var(--text); }
        .llm-error { font-size: 12px; color: var(--text-dim); }

        .footnote {
          margin-top: 22px;
          padding-top: 14px;
          border-top: 1px solid var(--border);
          font-size: 11px;
          color: var(--text-dim);
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>

      <div className="dash__topline" />
      <ToastStack toasts={toasts} onDismiss={dismissToast} />

      <div className="dash-header">
        <div className="dash-header__title">
          <div className="dash-header__icon">
            <Radio size={16} color="#33429A" />
          </div>
          <div>
            VPN·프롬프트 통합 리스크 모니터링
            <small>
              <span className={`live-dot ${live ? "" : "live-dot--off"}`} />
              {live ? `실시간 갱신 중 · 마지막 갱신 ${lastUpdated}` : "갱신 일시정지됨"}
            </small>
          </div>
        </div>
        <div className="dash-header__right">
          <div className="header-stats">
            <div className="header-stats__row">
              <span style={{ color: LEVELS.danger.color }}>위험 {counts.danger}</span>
              <span style={{ color: LEVELS.caution.color }}>주의 {counts.caution}</span>
              <span style={{ color: LEVELS.safe.color }}>양호 {counts.safe}</span>
            </div>
            <LevelBar counts={counts} total={totalCount} />
          </div>
          <button className="live-toggle" onClick={() => setLive((v) => !v)}>
            {live ? <Pause size={12} /> : <Play size={12} />}
            {live ? "일시정지" : "재개"}
          </button>
        </div>
      </div>

      <div className="dash-body">
        <div className="side">
          <div className="filter-bar">
            <div className="search-box">
              <Search size={13} color="#8B8F99" />
              <input
                placeholder="사용자·세션ID·부서 검색"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="filter-pills">
              {[
                { key: "all", label: "전체" },
                { key: "danger", label: "위험" },
                { key: "caution", label: "주의" },
                { key: "safe", label: "양호" },
              ].map((f) => (
                <button
                  key={f.key}
                  className={`filter-pill ${filter === f.key ? "filter-pill--active" : ""}`}
                  onClick={() => setFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <div className="row-2">
              <div className="sort-select">
                <ArrowUpDown size={12} color="#8B8F99" />
                <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
                  <option value="default">기본순</option>
                  <option value="score_desc">점수 높은순</option>
                  <option value="recent">최근 접속순</option>
                </select>
              </div>
            </div>
            <button className="export-btn" onClick={exportCsv}>
              <Download size={12} /> CSV 내보내기 ({filtered.length}건)
            </button>
          </div>
          <div className="session-list" ref={listRef}>
            {filtered.map((s) => (
              <SessionRow
                key={s.id}
                session={s}
                active={s.id === selectedId}
                flashing={!!flashIds[s.id]}
                onClick={() => setSelectedId(s.id)}
                onHover={handleHover}
                onLeave={handleLeave}
              />
            ))}
          </div>
        </div>

        {selected && (
          <div className="detail">
            <div className="detail-meta">
              <span className="detail-meta__item">
                <User size={13} /> <b>{selected.user}</b> · {selected.dept}
              </span>
              <span className="detail-meta__item">
                <Network size={13} /> 세션 ID <b>{selected.id}</b>
              </span>
              <span className="detail-meta__item">
                <Clock size={13} /> 접속 {selected.connectedAt} · 지속시간 {selected.duration}
              </span>
            </div>

            <div className="detail-main">
              <RiskScoreBadge score={selected.score} />
              <div className="trend-card">
                <div className="trend-card__title">최근 점수 추이</div>
                <ScoreTrendChart
                  history={history[selected.id] || [selected.score]}
                  color={LEVELS[levelFromScore(selected.score)].color}
                />
              </div>
            </div>

            <div className="evidence-cols">
              <EvidenceGroup
                title="네트워크 이상 징후"
                icon={<Network size={14} color="#6D7180" />}
                items={selected.network_reasons}
                accent="#33429A"
              />
              <EvidenceGroup
                title="프롬프트 위험 탐지"
                icon={<MessageSquareWarning size={14} color="#6D7180" />}
                items={selected.prompt_reasons}
                accent="#7A4FB5"
              />
            </div>

            <div className="llm-box">
              <div className="llm-box__head">
                <span className="llm-box__title">
                  <Sparkles size={13} /> AI 판단 근거 요약
                </span>
                <button className="llm-btn" onClick={generateLlmExplanation} disabled={llmLoading || !!llmText[selected.id]}>
                  {llmLoading ? <Loader2 size={12} className="spin" /> : <Sparkles size={12} />}
                  {llmText[selected.id] ? "생성 완료" : llmLoading ? "생성 중..." : "AI 설명 보기"}
                </button>
              </div>
              {llmError && <div className="llm-error">{llmError}</div>}
              {llmText[selected.id] && <div className="llm-text">{llmText[selected.id]}</div>}
              {!llmText[selected.id] && !llmLoading && !llmError && (
                <div className="llm-error">버튼을 누르면 위 근거 데이터를 바탕으로 자연어 요약을 생성합니다.</div>
              )}
            </div>

            <div className="footnote">
              <ShieldQuestion size={13} />
              본 점수는 보안 담당자 참고용 지표이며, 시스템이 자동으로 접속을 차단하지 않습니다.
            </div>
          </div>
        )}
      </div>

      <HoverTooltip session={hover.session} pos={hover.pos} />
    </div>
  );
}
