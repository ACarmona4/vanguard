import { useUtilization, metricValue } from "./api";

export default function UtilizationWidgets() {
  const { data, loading, error } = useUtilization("/summary");
  const cards = [
    [
      "Resources with metrics",
      data ? `${data.monitored} / ${data.eligible}` : "—",
      "With at least one recent sample",
    ],
    [
      "Average CPU",
      metricValue(data?.cpu_average, "%"),
      `Average per resource · ${data?.cpu_samples ?? 0} with data`,
    ],
    [
      "Average memory",
      metricValue(data?.memory_average, "%"),
      `Average per resource · ${data?.memory_samples ?? 0} with data`,
    ],
    [
      "High utilization",
      data?.high_utilization ?? "—",
      "CPU, memory, or disk ≥ 80%",
    ],
  ];
  return (
    <section className="utilization-summary" aria-label="Utilization summary">
      <div className="utilization-title">
        <h2>Utilization</h2>
        <a href="#utilization">View utilization →</a>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {data?.collection?.errors?.length > 0 && (
        <p className="utilization-notice" role="status">
          Metric collection is partial. See the details under Utilization.
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
