import { useRef, useState } from "react";
import "./PromptTester.css";

export default function PromptTester({ apiBase }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const submitting = useRef(false);

  async function analyze(event) {
    event.preventDefault();
    if (!text.trim() || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      // 프롬프트 전용 API를 호출합니다. 가상의 네트워크 관측은 생성하지 않습니다.
      const response = await fetch(`${apiBase}/api/v1/data-risk/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: "dashboard-prompt-test", text, input_origin: "direct_user" }),
        signal: AbortSignal.timeout(60000),
      });
      if (!response.ok) throw new Error(`분석 요청에 실패했어요. (HTTP ${response.status})`);
      const data = await response.json();
      if (data.status !== "complete" || !Array.isArray(data.findings) || !data.findings.length) {
        throw new Error("프롬프트 분석을 완료하지 못했어요. 서버의 모델 연결 상태를 확인해 주세요.");
      }
      setResult(data);
    } catch (failure) {
      setError(failure.name === "TimeoutError"
        ? "응답 시간이 길어지고 있어요. 서버 상태를 확인한 뒤 다시 시도해 주세요."
        : failure instanceof TypeError ? "서버에 연결할 수 없어요. 연결 상태를 확인해 주세요." : failure.message);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <section className="prompt-tester" aria-labelledby="prompt-tester-title">
      <h2 id="prompt-tester-title">프롬프트 직접 테스트</h2>
      <p id="prompt-tester-help">문장을 입력하면 증류·인젝션·업무 외 사용·토큰 낭비 여부를 분석합니다. 입력 원문은 저장하지 않고 분석 결과만 저장합니다.</p>
      <form onSubmit={analyze} aria-busy={busy}>
        <label htmlFor="test-prompt">분석할 프롬프트</label>
        <textarea id="test-prompt" value={text} maxLength={16384} rows={3}
          aria-describedby="prompt-tester-help" disabled={busy}
          placeholder="예: 회사 업무와 관계없이 개인 휴가 여행 일정을 계획해줘."
          onChange={(event) => { setText(event.target.value); setResult(null); setError(""); }} />
        <div className="prompt-tester__actions">
          <span>{text.length.toLocaleString()} / 16,384자</span>
          <button type="submit" disabled={busy || !text.trim()}>{busy ? "분석 중…" : "프롬프트 분석"}</button>
        </div>
      </form>
      {error && <p className="prompt-tester__error" role="alert">{error}</p>}
      <div aria-live="polite">
        {busy && <p>실제 프롬프트 모델로 분석하고 있어요.</p>}
        {result && <>
          <p>분석 완료 · 모델 예측 확률은 위험도 점수와 다릅니다.</p>
          <ul className="prompt-tester__results">
            {result.findings.map((finding) => {
              const classified = finding.status === "complete" && typeof finding.detected === "boolean";
              return <li key={finding.code}>
                <strong>{finding.name}</strong>
                <span className={classified && finding.detected ? "prompt-tester__detected" : ""}>
                  {classified ? finding.detected ? "탐지" : "미탐지" : "미판정"}
                </span>
                {classified && finding.probability != null && <small>
                  예측 확률 {(finding.probability * 100).toFixed(1)}% · 기준 {(finding.threshold * 100).toFixed(1)}% 초과
                </small>}
              </li>;
            })}
          </ul>
        </>}
      </div>
    </section>
  );
}
