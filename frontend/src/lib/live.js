// SSE는 변경 알림만 보냅니다. 실제 자료는 기존 조회 API에서 다시 읽습니다.
// 조회 중 도착한 알림도 다음 조회로 이어서 처리하여 마지막 결과를 놓치지 않습니다.
export function watchEvents({ base, fetchPage, onData, onError, onMode,
  Source = globalThis.EventSource, pollMs = 5000 }) {
  let stopped = false, loading = false, dirty = false, timer = null, retry = null, stream = null;
  const controller = new AbortController();
  const load = async () => {
    dirty = true;
    if (loading || stopped) return;
    loading = true;
    try {
      while (dirty && !stopped) {
        dirty = false;
        try {
          const page = await fetchPage(controller.signal);
          if (!stopped) {
            clearTimeout(retry); retry = null;
            onData(page);
          }
        } catch (error) {
          if (!stopped && error.name !== 'AbortError') {
            onError(error);
            // 스트림이 정상이어도 목록 API만 일시 실패할 수 있으므로 다시 조회합니다.
            if (retry === null) retry = setTimeout(() => { retry = null; void load(); }, pollMs);
          }
        }
      }
    } finally { loading = false; }
  };
  const fallback = () => {
    if (stopped) return;
    onMode('polling');
    if (timer === null) timer = setInterval(load, pollMs);
    void load();
  };
  // 스트림이 열리지 않는 경우에도 조회가 멈추지 않도록 시작부터 대체 조회를 준비합니다.
  fallback();
  if (Source) {
    try { stream = new Source(`${base}/api/v1/dashboard/stream`); }
    catch { stream = null; }
  }
  if (stream) {
    stream.onopen = () => {
      if (stopped) return;
      clearInterval(timer); timer = null;
      onMode('stream');
      void load();
    };
    stream.addEventListener('changed', load);
    stream.onerror = fallback;
  }
  return () => {
    stopped = true;
    controller.abort();
    clearInterval(timer);
    clearTimeout(retry);
    stream?.close();
  };
}
