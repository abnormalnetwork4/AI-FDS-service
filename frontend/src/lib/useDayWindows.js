// 선택한 날짜(KST)의 5분 구간을 서버에서 읽어 옵니다. 개요 그래프와 현황 화면이 같이 씁니다.
import { useEffect, useState } from "react";
import { fetchEventPage, MAX_PAGES, PAGE_SIZE } from "./events.js";

export function useDayWindows(apiBase, date, version) {
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
