import { useEffect, useState } from "react";

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
        const response = await fetch(`/api/utilization${path}?${query}`, {
          signal: controller.signal,
        });
        const body = await response.json();
        if (!response.ok)
          throw new Error(
            typeof body.detail === "string"
              ? body.detail
              : "No fue posible consultar las métricas.",
          );
        if (!controller.signal.aborted)
          setState({ data: body, error: "", loading: false });
      } catch (error) {
        if (!controller.signal.aborted)
          setState({
            data: null,
            error: error.message || "Sin conexión con la API.",
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
  const formatted = new Intl.NumberFormat("es-CO", {
    maximumFractionDigits: 2,
  }).format(value);
  return `${formatted} ${unit}`.trim();
}
