import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";

export function useUtilization(path, filters = {}) {
  const query = new URLSearchParams(
    Object.entries(filters).filter(
      ([, value]) => value !== "" && value != null,
    ),
  ).toString();
  const [state, setState] = useState({ data: null, error: "", loading: true });
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    setState({ data: null, error: "", loading: true });
    async function load() {
      try {
        const body = await apiRequest(`utilization${path}?${query}`, {
          signal: controller.signal,
        });
        if (!controller.signal.aborted)
          setState({ data: body, error: "", loading: false });
      } catch (error) {
        if (!controller.signal.aborted)
          setState({
            data: null,
            error: error.message || "Unable to connect to the API.",
            loading: false,
          });
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(load, 60_000);
      }
    }
    load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [path, query]);
  return state;
}

export function metricValue(value, unit = "") {
  if (value == null) return "—";
  const formatted = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 2,
  }).format(value);
  return `${formatted} ${unit}`.trim();
}
