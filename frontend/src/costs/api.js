import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../api/client";

export function useCosts(filters) {
  const [refresh, setRefresh] = useState(0);
  const [state, setState] = useState({ data: null, error: "", loading: true });
  const reload = useCallback(() => setRefresh((value) => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    const query = new URLSearchParams(filters).toString();
    async function load() {
      let nextDelay = 300_000;
      try {
        const data = await apiRequest(`costs?${query}`, { signal: controller.signal });
        if (data.sources?.some((source) => ["ready", "syncing"].includes(source.status))) {
          nextDelay = 5000;
        }
        if (!controller.signal.aborted) setState({ data, error: "", loading: false });
      } catch (error) {
        if (!controller.signal.aborted) {
          setState((current) => ({ ...current, error: error.message, loading: false }));
        }
      } finally {
        if (!controller.signal.aborted) {
          timer = window.setTimeout(load, nextDelay);
        }
      }
    }
    setState((current) => ({ ...current, loading: true }));
    load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [filters.days, filters.provider, refresh]);
  return { ...state, reload };
}

export const syncCosts = () =>
  apiRequest("costs/sync", { method: "POST" });

export const configureGcpCosts = (connectionId, payload) =>
  apiRequest(`costs/sources/${connectionId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
