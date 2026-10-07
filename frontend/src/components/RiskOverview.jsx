// 기본 개요 화면: 선택한 날짜(KST)의 5분 시간대별 사용자 통합 점수를 사용자마다 다른 선으로 보여 줍니다.
// 배경 띠는 등급 구간(정상 0~29 / 주의 30~49 / 경고 50~69 / 위험 70~100)입니다.
// 점이나 표의 칸을 누르면 그 사용자 구간의 상세 분석 화면으로 이동합니다. 점수는 서버 값만 씁니다.
import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { LEVELS, levelText, eventLevel, fetchEventPage, formatScore, MAX_PAGES, PAGE_SIZE } from "../lib/events.js";
import {
  COMPACT_SLOTS, MAX_SERIES, SERIES_COLORS, assignColors, buildMatrix, buildRows, defaultSelection, formatHm, rankUsers, timeTicks,
} from "../lib/overview.js";

const BANDS = [
  { key: "normal", y1: 0, y2: 30 },
  { key: "caution", y1: 30, y2: 50 },
  { key: "warning", y1: 50, y2: 70 },
  { key: "danger", y1: 70, y2: 100 },
];

function useDayWindows(apiBase, date, version) {
  const [state, setState] = useState({ events: [], status: "loading", error: null });
  useEffect(() => {
    if (!date) return undefined;
    const ctrl = new AbortController();
    // 첫 페이지로 전체 건수를 확인한 뒤 필요한 만큼만 더 읽습니다(최대 2,000건).
    fetchEventPage(apiBase, ctrl.signal, fetch, 1, { date })
      .then((first) => first.total <= PAGE_SIZE ? first
        : fetchEventPage(apiBase, ctrl.signal, fetch, Math.min(MAX_PAGES, Math.ceil(first.total / PAGE_SIZE)), { date }))
      .then((page) => setState({ events: page.events, status: "ok", error: null }))
      .catch((error) => { if (!ctrl.signal.aborted) setState((s) => ({ ...s, status: "error", error: error.message })); });
    return () => ctrl.abort();
  }, [apiBase, date, version]);
  return state;
}

function OverviewTooltip({ active, payload, label, matrix, colors }) {
  if (!active || label == null) return null;
  const rows = Object.keys(colors)
    .map((user) => ({ user, w: matrix.cell(user, label) }))
    .filter((r) => r.w)
    .sort((a, b) => (b.w.score ?? -1) - (a.w.score ?? -1));
  if (!rows.length && !payload?.length) return null;
  return (
    <div className="ov-tip">
      <div className="ov-tip__time">{formatHm(label)} ~ {formatHm(label + 5 * 60 * 1000)}</div>
      {rows.map(({ user, w }) => {
        const level = eventLevel(w);
        return (
          <div key={user} className="ov-tip__row">
            <span className="ov-swatch" style={{ background: SERIES_COLORS[colors[user]] }} />
            <span className="ov-tip__user">{user}</span>
            <span className="ov-tip__score">{formatScore(w.score)}</span>
            <span style={{ color: levelText(level), fontWeight: 600 }}>{LEVELS[level].label}{w.override ? " · 강제" : ""}</span>
          </div>
        );
      })}
      <div className="ov-tip__hint">점을 누르면 상세 분석으로 이동</div>
    </div>
  );
}

export default function RiskOverview({ apiBase, dates, date, onDateChange, version, onOpenWindow }) {
  const { events, status, error } = useDayWindows(apiBase, date, version);
  const ranked = useMemo(() => rankUsers(events), [events]);
  // 사용자 → 색 칸. null이면 기본(가장 위험한 8명). 켜고 끌 때 남은 사용자의 색은 바뀌지 않습니다.
  // 날짜가 바뀌면 부모가 key로 이 컴포넌트를 새로 만들어 기본 선택으로 돌아갑니다.
  const [picked, setPicked] = useState(null);
  const [hint, setHint] = useState("");
  const colors = useMemo(() => {
    const present = new Set(ranked.map((u) => u.user));
    if (picked) return Object.fromEntries(Object.entries(picked).filter(([u]) => present.has(u)));
    return assignColors(defaultSelection(events));
  }, [picked, events, ranked]);
  const shown = useMemo(() => Object.keys(colors).sort(), [colors]);

  const matrix = useMemo(() => buildMatrix(events), [events]);
  const { rows, segments } = useMemo(() => buildRows(events, shown), [events, shown]);
  const ticks = timeTicks(matrix.times);
  const pending = events.filter((e) => e.score == null).length;
  const compact = matrix.times.length > COMPACT_SLOTS;

  const toggle = (user) => {
    if (user in colors) {
      const next = { ...colors };
      delete next[user];
      setPicked(next); setHint("");
      return;
    }
    const used = new Set(Object.values(colors));
    const slot = SERIES_COLORS.findIndex((_, i) => !used.has(i));
    if (slot === -1) { setHint(`선은 최대 ${MAX_SERIES}명까지 그립니다. 다른 사용자를 먼저 끄세요.`); return; }
    setPicked({ ...colors, [user]: slot }); setHint("");
  };

  const dot = (user, key) => function Dot(props) {
    const { cx, cy, payload } = props;
    if (cx == null || cy == null || payload?.[key] == null) return null;
    const w = matrix.cell(user, payload.t);
    const ring = w?.override;
    return (
      <g key={`${user}-${payload.t}`} style={{ cursor: "pointer" }} onClick={() => w && onOpenWindow(w)}>
        <circle cx={cx} cy={cy} r={12} fill="transparent" />
        {ring && <circle cx={cx} cy={cy} r={8} fill="none" stroke={LEVELS.danger.color} strokeWidth={2} />}
        <circle cx={cx} cy={cy} r={4.5} fill={SERIES_COLORS[colors[user]]} stroke="#fff" strokeWidth={2} />
      </g>
    );
  };

  return (
    <div className="ov">
      <div className="ov-toolbar">
        <label className="ov-field">
          <span>날짜(KST)</span>
          <select value={date ?? ""} onChange={(e) => onDateChange(e.target.value)}>
            {dates.map((d) => <option key={d.date} value={d.date}>{d.date} · 사용자 {d.user_count}명 · 구간 {d.window_count}</option>)}
          </select>
        </label>
        <div className="ov-legend" role="group" aria-label="표시할 사용자">
          {ranked.map(({ user, grade }) => {
            const on = user in colors;
            return (
              <button key={user} className={`ov-chip ${on ? "is-on" : ""}`} onClick={() => toggle(user)} aria-pressed={on}
                title={on ? "누르면 선을 숨깁니다" : "누르면 선을 그립니다"}>
                <span className="ov-swatch" style={{ background: on ? SERIES_COLORS[colors[user]] : "transparent" }} />
                {user}
                <span className="ov-chip__grade" style={{ color: grade ? levelText(grade) : LEVELS.pending.color }}>
                  {grade ? LEVELS[grade].label : "미판정"}
                </span>
              </button>
            );
          })}
        </div>
      </div>
      {hint && <div className="ov-hint" role="status">{hint}</div>}

      <div className="ov-card">
        <div className="ov-card__head">
          <b>시간대별 사용자 통합 점수</b>
          <span>점 = 사용자·단말 5분 구간의 통합 점수 · 배경 = 등급 구간 · 빨간 고리 = 강제 위험(합계와 무관하게 위험)</span>
        </div>
        {status === "error" && <div className="ov-empty">불러오지 못했습니다: {error}</div>}
        {status !== "error" && !events.length && <div className="ov-empty">{date ? "이 날짜에 구간이 없습니다." : "수집된 구간이 없습니다."}</div>}
        {events.length > 0 && (
          <div className="ov-chart" aria-label="시간대별 사용자 통합 점수 선 그래프">
            <ResponsiveContainer width="100%" height={380}>
              <LineChart data={rows} margin={{ top: 12, right: 88, bottom: 8, left: 0 }}>
                {BANDS.map((b) => (
                  <ReferenceArea key={b.key} y1={b.y1} y2={b.y2} fill={LEVELS[b.key].color} fillOpacity={b.key === "caution" ? 0.12 : 0.07} stroke="none"
                    ifOverflow="hidden"
                    label={{ value: `${LEVELS[b.key].label} ${b.y1}~${b.y2 === 100 ? 100 : b.y2 - 1}`, position: "right",
                      fill: levelText(b.key), fontSize: 11, fontWeight: 600 }} />
                ))}
                <CartesianGrid vertical={false} stroke="#e1e0d9" strokeDasharray="0" />
                <XAxis dataKey="t" type="number" scale="time" domain={[matrix.times[0] - 60000, matrix.times[matrix.times.length - 1] + 60000]}
                  ticks={ticks} tickFormatter={formatHm} tick={{ fontSize: 11, fill: "#898781" }} stroke="#c3c2b7" />
                <YAxis domain={[0, 100]} ticks={[0, 30, 50, 70, 100]} tick={{ fontSize: 11, fill: "#898781" }} stroke="#c3c2b7" width={36} />
                <Tooltip content={<OverviewTooltip matrix={matrix} colors={colors} />} cursor={{ stroke: "#898781", strokeDasharray: "3 3" }} />
                {shown.filter((u) => u in colors).flatMap((user) => segments[user].map((key) => (
                  <Line key={key} dataKey={key} name={user} type="linear" stroke={SERIES_COLORS[colors[user]]} strokeWidth={2}
                    connectNulls isAnimationActive={false} dot={dot(user, key)} activeDot={false} />
                )))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        <div className="ov-note">선은 한 사용자의 활동 구간을 시간 순서로 이은 것입니다(활동이 없던 시간은 건너뛰고, 30분 넘게 비면 끊음).
          {pending > 0 && ` 미판정 구간 ${pending}개는 점수가 없어 점을 찍지 않습니다(0점으로 그리지 않음). 아래 표에서 확인할 수 있습니다.`}</div>
      </div>

      {events.length > 0 && (
        <div className="ov-card">
          <div className="ov-card__head">
            <b>사용자 × 시간대 표</b>
            <span>칸을 누르면 상세 분석 · 위험한 사용자 순{compact ? " · 칸 색 = 등급(마우스를 올리면 점수)" : ""}</span>
            {compact && (
              <span className="ov-keys">
                {["normal", "caution", "warning", "danger", "pending"].map((g) => (
                  <span key={g}><i className="ov-dot ov-dot--static" style={{ background: LEVELS[g].color }} />{LEVELS[g].label}</span>
                ))}
              </span>
            )}
          </div>
          <div className="ov-table-wrap">
            <table className="ov-table">
              <thead>
                <tr><th>사용자</th>{matrix.times.map((t) => (
                  <th key={t} className={compact ? "ov-th--compact" : ""}>{compact ? (new Date(t).getMinutes() % 30 === 0 ? formatHm(t) : "") : formatHm(t)}</th>
                ))}</tr>
              </thead>
              <tbody>
                {ranked.map(({ user }) => (
                  <tr key={user}>
                    <th scope="row">
                      <span className="ov-rowhead">
                        <span className="ov-swatch" style={{ background: user in colors ? SERIES_COLORS[colors[user]] : "transparent" }} />
                        {user}
                      </span>
                    </th>
                    {matrix.times.map((t) => {
                      const w = matrix.cell(user, t);
                      if (!w) return <td key={t} className="ov-cell--empty">{compact ? "" : "·"}</td>;
                      const level = eventLevel(w);
                      if (compact) {
                        return (
                          <td key={t} className="ov-td--compact">
                            <button className={`ov-dot ov-dot--${level}`} onClick={() => onOpenWindow(w)}
                              style={{ background: LEVELS[level].color }}
                              title={`${user} · ${w.device_id} · ${formatHm(t)} · ${LEVELS[level].label} ${formatScore(w.score)}`}
                              aria-label={`${user} ${formatHm(t)} ${LEVELS[level].label} ${formatScore(w.score)}점`} />
                          </td>
                        );
                      }
                      return (
                        <td key={t}>
                          <button className="ov-cell" onClick={() => onOpenWindow(w)}
                            style={{ color: levelText(level), borderColor: LEVELS[level].color, background: level === "caution" ? "#FEF9C3" : undefined }}
                            title={`${user} · ${w.device_id} · ${formatHm(t)}`}>
                            <b>{LEVELS[level].label}</b> {formatScore(w.score)}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div className="ov-note">사용자 점수를 합산·평균한 회사 점수는 만들지 않습니다. 점수는 검토 우선순위이며 위반 확정이 아닙니다.</div>
    </div>
  );
}
