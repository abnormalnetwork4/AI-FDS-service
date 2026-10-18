// 현황 화면: 담당자가 가장 먼저 보는 앞단 화면입니다. 위험도가 가장 높은 사용자·단말 3명은 "가장 높은 구간"을 등급 색 카드로 크게,
// 나머지 이상 사용자는 위험도 순 한 줄 목록으로 보여 줍니다. 누르면 그 사용자의 구간 상세 분석으로 들어갑니다. 이상이 없는 사용자는 이름만 모아 둡니다.
// 점수·등급은 서버 값만 쓰며(프론트가 계산하지 않음), 새 API 없이 개요 그래프와 같은 날짜별 조회를 재사용합니다.
import { useState } from "react";
import { ArrowRight, ChevronRight, CircleHelp, ShieldAlert, ShieldCheck } from "lucide-react";
import { LEVELS, eventLevel, formatScore, isNotable, levelText, viewStatus } from "../lib/events.js";
import { formatHm } from "../lib/overview.js";
import { useDayWindows } from "../lib/useDayWindows.js";

const REVIEW_LABEL = "담당자 확인 요망"; // 판단 불가(미판정)는 안전 판정이 아니므로 사람이 직접 확인합니다
const CARD_COUNT = 3; // 큰 카드로 보여 줄 위험도 상위 사용자 수
const ROW_LIMIT = 10; // 그다음 한 줄 목록으로 먼저 보여 줄 사용자 수(사용자가 많아도 화면이 복잡해지지 않게)
// 먼저 확인할 순서: 위험 > 경고 > 미판정·오류(사람이 확인) > 주의 > 정상. 같은 등급이면 점수가 높은 순, 그다음 최근 순입니다.
const ATTENTION = { danger: 5, warning: 4, error: 3, pending: 3, caution: 2, normal: 0 };
const attention = (event) => ATTENTION[eventLevel(event)] ?? 0;
const byAttention = (a, b) => attention(b) - attention(a) || (b.score ?? -1) - (a.score ?? -1) || (b.startedAtMs ?? 0) - (a.startedAtMs ?? 0);
const COUNT_ORDER = ["danger", "warning", "error", "pending", "caution", "normal"];

// 개별 엔진이 탐지한 항목 중 가장 강한 것 하나("N1 비정상 대량 업로드 외 4건")
function findingText(event) {
  const strength = (i) => i.probability ?? (i.score != null ? i.score / 100 : 0);
  const items = [...(event.network_reasons ?? []), ...(event.prompt_reasons ?? [])]
    .filter(isNotable)
    .sort((a, b) => (LEVELS[viewStatus(b)]?.rank ?? 0) - (LEVELS[viewStatus(a)]?.rank ?? 0) || strength(b) - strength(a));
  if (!items.length) return "";
  const first = items[0];
  const label = String(first.label ?? "").replace(/\s*·\s*\d+분 집계$/, "");
  return `${first.code ?? ""} ${label}${items.length > 1 ? ` 외 ${items.length - 1}건` : ""}`.trim();
}

const timeRange = (e) => `${formatHm(e.startedAtMs)}~${formatHm(e.endedAtMs)}`;

// 사용자·단말별로 묶어 "가장 높은 구간"(worst)을 고릅니다. 이상이 큰 사용자부터 정렬합니다.
function groupByUser(events) {
  const map = new Map();
  events.forEach((e) => {
    const key = `${e.user}|${e.device_id ?? ""}`;
    const g = map.get(key) ?? { key, user: e.user, device: e.device_id, windows: [], counts: {} };
    g.windows.push(e);
    const level = eventLevel(e);
    g.counts[level] = (g.counts[level] ?? 0) + 1;
    map.set(key, g);
  });
  return [...map.values()]
    .map((g) => {
      const worst = [...g.windows].sort(byAttention)[0];
      return { ...g, worst, level: eventLevel(worst) };
    })
    .sort((a, b) => byAttention(a.worst, b.worst));
}

function UserCard({ group, onOpen }) {
  const { worst, level, counts, windows } = group;
  const Icon = level === "pending" || level === "error" ? CircleHelp : ShieldAlert;
  const finding = findingText(worst);
  return (
    <button className={`st-card st-card--${level}`} style={{ "--card-bg": LEVELS[level].color }} onClick={() => onOpen(worst)}
      aria-label={`${group.user} ${LEVELS[level].label} ${worst.score == null ? "점수 없음" : `${formatScore(worst.score)}점`}, 세부 화면 보기`}>
      <span className="st-card__top">
        <span className="st-card__grade"><Icon size={30} strokeWidth={1.7} aria-hidden="true" />{LEVELS[level].label}</span>
        {worst.score == null
          ? <span className="st-card__noscore">점수 없음</span>
          : <span className="st-card__score">{formatScore(worst.score)}<small>/100</small></span>}
      </span>
      <span className="st-card__body">
        <span className="st-card__who"><b>{group.user}</b>{group.device ? <span> · {group.device}</span> : null}</span>
        {level === "pending" && <span className="st-card__review">{REVIEW_LABEL}</span>}
        {finding && <span className="st-card__find">{finding}</span>}
        <span className="st-card__meta">가장 높은 구간 {timeRange(worst)} · 전체 {windows.length}개</span>
        <span className="st-card__counts">
          {COUNT_ORDER.filter((k) => counts[k]).map((k) => (
            <span key={k}><i style={{ background: LEVELS[k].color }} />{LEVELS[k].label} {counts[k]}</span>
          ))}
        </span>
        <span className="st-card__go">세부 화면 보기 <ArrowRight size={15} aria-hidden="true" /></span>
      </span>
    </button>
  );
}

function UserRow({ group, onOpen }) {
  const { worst, level } = group;
  const finding = findingText(worst);
  return (
    <li>
      <button className="st-row" onClick={() => onOpen(worst)}
        aria-label={`${group.user} ${LEVELS[level].label} ${worst.score == null ? "점수 없음" : `${formatScore(worst.score)}점`}, 세부 화면 보기`}>
        <i className="st-row__dot" style={{ background: LEVELS[level].color }} />
        <span className="st-row__grade" style={{ color: levelText(level) }}>{LEVELS[level].label}</span>
        <span className="st-row__who"><b>{group.user}</b>{group.device ? ` · ${group.device}` : ""}</span>
        <span className="st-row__find">
          {level === "pending" && <b className="st-row__review">{REVIEW_LABEL}</b>}
          {level === "pending" && finding && " · "}
          {finding}
        </span>
        <span className="st-row__time">{timeRange(worst)}</span>
        <span className="st-row__score" style={{ color: levelText(level) }}>{worst.score == null ? "—" : formatScore(worst.score)}</span>
        <ChevronRight size={16} className="st-row__go" aria-hidden="true" />
      </button>
    </li>
  );
}

export default function RiskStatus({ apiBase, dates, date, onDateChange, version, onOpenWindow }) {
  const { events, status, error } = useDayWindows(apiBase, date, version);
  const [showAllRows, setShowAllRows] = useState(false);
  const groups = groupByUser(events);
  const flagged = groups.filter((g) => attention(g.worst) > 0);
  const calm = groups.filter((g) => attention(g.worst) === 0);
  const cards = flagged.slice(0, CARD_COUNT);
  const restRows = flagged.slice(CARD_COUNT);
  const shownRows = showAllRows ? restRows : restRows.slice(0, ROW_LIMIT);
  const userCounts = {};
  groups.forEach((g) => { userCounts[g.level] = (userCounts[g.level] ?? 0) + 1; });

  return (
    <div className="st">
      <div className="st-bar">
        <label className="ov-field">
          <span>날짜 (KST)</span>
          <select value={date} onChange={(e) => onDateChange(e.target.value)} aria-label="현황 날짜">
            {dates.map((d) => <option key={d.date} value={d.date}>{d.date} · {d.user_count}명 · {d.window_count}구간</option>)}
          </select>
        </label>
        {status === "ok" && groups.length > 0 && (
          <span className="st-bar__sum" aria-label="사용자 기준 등급별 인원">
            사용자 {groups.length}명 ·
            {COUNT_ORDER.filter((k) => userCounts[k]).map((k) => (
              <span key={k} className="st-bar__chip"><i style={{ background: LEVELS[k].color }} />{LEVELS[k].label} <b>{userCounts[k]}</b></span>
            ))}
          </span>
        )}
      </div>

      {status === "error" && <div className="st-note">현황을 불러오지 못했어요 ({error}). 잠시 뒤 자동으로 다시 시도합니다.</div>}
      {status === "loading" && date && <div className="st-note">현황을 불러오는 중이에요…</div>}

      {(status === "ok" || !date) && (
        groups.length === 0 ? (
          <section className="st-banner st-banner--empty">
            <CircleHelp size={44} strokeWidth={1.6} aria-hidden="true" />
            <div>
              <div className="st-banner__title">표시할 구간이 없어요</div>
              <div className="st-banner__sub">이 날짜에 수집된 구간이 없습니다. 날짜를 바꾸거나 데이터가 들어올 때까지 기다려 주세요.</div>
            </div>
          </section>
        ) : (
          <>
            {flagged.length === 0 ? (
              <section className="st-banner st-banner--ok">
                <ShieldCheck size={52} strokeWidth={1.5} aria-hidden="true" />
                <div>
                  <div className="st-banner__title">이상 없음</div>
                  <div className="st-banner__sub">이 날짜의 사용자 {groups.length}명이 모두 정상입니다.</div>
                </div>
              </section>
            ) : (
              <section aria-label="확인이 필요한 사용자">
                <h2 className="st-h">확인이 필요한 사용자 <b>{flagged.length}명</b></h2>
                <div className="st-board">
                  {cards.map((g) => <UserCard key={g.key} group={g} onOpen={onOpenWindow} />)}
                </div>
                {restRows.length > 0 && (
                  <div className="st-rows">
                    <h3 className="st-h st-h--sub">이어서 확인할 사용자 <b>{restRows.length}명</b></h3>
                    <ul>{shownRows.map((g) => <UserRow key={g.key} group={g} onOpen={onOpenWindow} />)}</ul>
                    {restRows.length > ROW_LIMIT && (
                      <button className="st-more" onClick={() => setShowAllRows((v) => !v)}>
                        {showAllRows ? "접기" : `나머지 ${restRows.length - ROW_LIMIT}명 더 보기`}
                      </button>
                    )}
                  </div>
                )}
              </section>
            )}

            {calm.length > 0 && (
              <details className="st-calm" key={flagged.length === 0 ? "open" : "closed"} open={flagged.length === 0}>
                <summary>정상 사용자 {calm.length}명</summary>
                <div className="st-calm__chips">
                  {calm.map((g) => (
                    <button key={g.key} className="st-chip" onClick={() => onOpenWindow(g.worst)} title="이 사용자의 구간 상세 분석 열기">
                      <i style={{ background: LEVELS.normal.color }} />{g.user}{g.device ? ` · ${g.device}` : ""}
                    </button>
                  ))}
                </div>
              </details>
            )}
          </>
        )
      )}
    </div>
  );
}
