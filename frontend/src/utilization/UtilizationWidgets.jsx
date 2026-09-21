import { useUtilization, metricValue } from "./api";

export default function UtilizationWidgets() {
  const { data, loading, error } = useUtilization("/summary");
  const cards = [
    [
      "Recursos con métricas",
      data ? `${data.monitored} / ${data.eligible}` : "—",
      "Con al menos una muestra reciente",
    ],
    [
      "CPU promedio",
      metricValue(data?.cpu_average, "%"),
      `Media por recurso · ${data?.cpu_samples ?? 0} con datos`,
    ],
    [
      "Memoria promedio",
      metricValue(data?.memory_average, "%"),
      `Media por recurso · ${data?.memory_samples ?? 0} con datos`,
    ],
    [
      "Utilización alta",
      data?.high_utilization ?? "—",
      "CPU, memoria o disco ≥ 80 %",
    ],
  ];
  return (
    <section
      className="utilization-summary"
      aria-label="Resumen de utilización"
    >
      <div className="utilization-title">
        <h2>Utilización</h2>
        <a href="#utilization">Ver utilización →</a>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {data?.collection?.errors?.length > 0 && (
        <p className="utilization-notice" role="status">
          La recolección de métricas es parcial. Consulta los detalles en
          Utilización.
        </p>
      )}
      <div className="metrics utilization-metrics">
        {cards.map(([label, value, caption]) => (
          <article className="metric" key={label}>
            <div className="metric-label">{label}</div>
            <div className="metric-value">{loading ? "—" : value}</div>
            <span className="muted">{caption}</span>
          </article>
        ))}
      </div>
    </section>
  );
}
