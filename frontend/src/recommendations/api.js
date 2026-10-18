import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";

export function useRecommendations(filters = {}, { summary = false } = {}) {
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== "" && value != null)).toString();
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState({ data: null, loading: true, error: "" });
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    setState({ data: null, loading: true, error: "" });
    async function load() {
      try {
        const data = await apiRequest(`recommendations${summary ? "/summary" : ""}?${query}`, { signal: controller.signal });
        if (!controller.signal.aborted) setState({ data, loading: false, error: "" });
      } catch (failure) {
        if (!controller.signal.aborted) setState({ data: null, loading: false, error: failure.message });
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(load, 60_000);
      }
    }
    load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [query, summary, revision]);
  return { ...state, refresh: () => setRevision((value) => value + 1) };
}
