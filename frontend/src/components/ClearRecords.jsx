// 기록 비우기 패널: 선택한 날짜(KST)만, 또는 전체 기록을 지웁니다.
// 서버가 지우기 전에 DB 백업 파일을 만들고(기본), 확인 문구 CLEAR를 직접 입력해야 진행합니다.
import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";

export default function ClearRecords({ apiBase, date, onCleared }) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState("date");
  const [backup, setBackup] = useState(true);
  const [typed, setTyped] = useState("");
  const [preview, setPreview] = useState(null);
  const [state, setState] = useState({ busy: false, message: "", error: "" });
  const effectiveScope = date ? scope : "all";

  useEffect(() => {
    if (!open) return undefined;
    const ctrl = new AbortController();
    const query = effectiveScope === "date" ? `?date=${encodeURIComponent(date)}` : "";
    fetch(`${apiBase}/api/v1/admin/clear/preview${query}`, { signal: ctrl.signal })
      .then(async (r) => {
        if (r.status === 403) throw new Error("서버에서 기록 비우기가 꺼져 있습니다(FDS_ALLOW_CLEAR=0).");
        if (!r.ok) throw new Error(`미리 보기 실패 (HTTP ${r.status})`);
        return r.json();
      })
      .then((p) => setPreview(p))
      .catch((e) => { if (e.name !== "AbortError") { setPreview(null); setState((s) => ({ ...s, error: e.message })); } });
    return () => ctrl.abort();
  }, [open, effectiveScope, date, apiBase]);

  const close = () => { setOpen(false); setTyped(""); setPreview(null); setState({ busy: false, message: "", error: "" }); };

  const submit = async () => {
    setState({ busy: true, message: "", error: "" });
    try {
      const body = { scope: effectiveScope, confirm: "CLEAR", backup, ...(effectiveScope === "date" ? { date } : {}) };
      const r = await fetch(`${apiBase}/api/v1/admin/clear`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`삭제 실패 (HTTP ${r.status})`);
      const result = await r.json();
      const what = result.scope === "all" ? `기록 ${result.deleted_rows.records}행` : `구간 ${result.deleted_windows}개 · 관측 ${result.deleted_captures}건`;
      setState({ busy: false, error: "", message: `삭제 완료: ${what}${result.backup ? ` · 백업 ${result.backup}` : ""}` });
      setTyped("");
      setPreview(null);
      onCleared?.(result);
    } catch (e) {
      setState({ busy: false, message: "", error: e.message });
    }
  };

  if (!open) {
    return (
      <button className="clear-btn" onClick={() => setOpen(true)} title="기록 비우기">
        <Trash2 size={13} /> 기록 비우기
      </button>
    );
  }
  const empty = preview && !preview.windows && !preview.captures && !preview.legacy_company_windows;
  return (
    <div className="clear-panel" role="dialog" aria-label="기록 비우기">
      <div className="clear-panel__row">
        <b>기록 비우기</b>
        <label><input type="radio" checked={effectiveScope === "date"} disabled={!date} onChange={() => setScope("date")} /> {date || "날짜 없음"}만</label>
        <label><input type="radio" checked={effectiveScope === "all"} onChange={() => setScope("all")} /> 전체 기록</label>
        <label><input type="checkbox" checked={backup} onChange={(e) => setBackup(e.target.checked)} /> 삭제 전 백업</label>
        <button className="ghost-btn" onClick={close}>닫기</button>
      </div>
      {preview && (
        <div className="clear-panel__row">
          <span>삭제 대상: 5분 구간 <b>{preview.windows}</b>개 · 관측 <b>{preview.captures}</b>건 · 사용자 <b>{preview.users}</b>명
            {preview.legacy_company_windows ? ` · 이전 회사 구간 ${preview.legacy_company_windows}개` : ""}</span>
        </div>
      )}
      {!empty && preview && (
        <div className="clear-panel__row">
          <input className="clear-panel__confirm" value={typed} onChange={(e) => setTyped(e.target.value)}
            placeholder="CLEAR 입력" aria-label="확인 문구 CLEAR 입력" />
          <button className="clear-panel__go" disabled={typed !== "CLEAR" || state.busy} onClick={submit}>
            {state.busy ? "삭제 중…" : "삭제"}
          </button>
          <span className="clear-panel__hint">되돌리려면 백업 파일을 DB 이름으로 바꾸면 됩니다.</span>
        </div>
      )}
      {empty && <div className="clear-panel__row">지울 기록이 없습니다.</div>}
      {state.message && <div className="clear-panel__ok" role="status">{state.message}</div>}
      {state.error && <div className="clear-panel__err" role="alert">{state.error}</div>}
    </div>
  );
}
