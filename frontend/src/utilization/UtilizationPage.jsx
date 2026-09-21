import { useState } from "react";
import { useUtilization, metricValue } from "./api";
import { typeName } from "../inventory/resourceNames";
import { date } from "../shared/utils/format";
import Filter from "../shared/components/Filter";

const resourceTypes = [
  "ec2_instance",
  "ebs_volume",
  "compute_instance",
  "cloudsql_instance",
  "dynamodb_table",
  "sqs_queue",
  "sns_topic",
  "pubsub_topic",
  "pubsub_subscription",
];

function MetricStatus({ metric }) {
  return (
    <span className="muted">
      {metric.status === "ok"
        ? date(metric.observed_at)
        : metric.status === "stale"
          ? `Dato antiguo · ${date(metric.observed_at)}`
          : metric.automatically_managed
            ? "Agente automático: esperando métricas"
            : metric.requires_agent
            ? "Requiere agente en la VM"
            : "Sin datos publicados"}
    </span>
  );
}

function HistoryChart({ metric }) {
  const points = metric.points;
  if (!points.length)
    return <p className="muted">Sin muestras en este período.</p>;
  const minTime = points[0][0];
  const duration = Math.max(points.at(-1)[0] - minTime, 1);
  const maximum = Math.max(...points.map(([, value]) => value));
  const scale = Math.max(maximum, 1);
  // Gaps remain visible: a missing collection must not look like continuous data.
  const segments = [[]];
  points.forEach(([timestamp, value], index) => {
    if (index && timestamp - points[index - 1][0] > 600_000) segments.push([]);
    segments
      .at(-1)
      .push(
        `${10 + ((timestamp - minTime) / duration) * 480},${100 - (value / scale) * 90}`,
      );
  });
  return (
    <>
      <svg
        className="utilization-chart"
        viewBox="0 0 500 115"
        role="img"
        aria-label={`${metric.label}: ${points.length} muestras, máximo ${metricValue(maximum, metric.unit)}`}
      >
        <line x1="10" y1="100" x2="490" y2="100" stroke="#e5e7eb" />
        {segments.map((segment, index) =>
          segment.length === 1 ? (
            <circle
              key={index}
              cx={segment[0].split(",")[0]}
              cy={segment[0].split(",")[1]}
              r="3"
              fill="currentColor"
            />
          ) : (
            <polyline
              key={index}
              points={segment.join(" ")}
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            />
          ),
        )}
      </svg>
      <div className="chart-caption">
        <span>{date(minTime)}</span>
        <span>Máx. {metricValue(maximum, metric.unit)}</span>
        <span>{date(points.at(-1)[0])}</span>
      </div>
    </>
  );
}

function ResourceHistory({ id, onClose }) {
  const [hours, setHours] = useState("1");
  const { data, error, loading } = useUtilization(`/${id}`, { hours });
  return (
    <section className="utilization-history" aria-label="Historial del recurso">
      <div className="utilization-title">
        <h2>{data?.name || "Historial del recurso"}</h2>
        <button className="button" onClick={onClose}>
          Cerrar historial
        </button>
      </div>
      <label className="filter">
        <span>Período</span>
        <select
          value={hours}
          onChange={(event) => setHours(event.target.value)}
        >
          <option value="1">Última hora</option>
          <option value="6">Últimas 6 horas</option>
          <option value="24">Últimas 24 horas</option>
        </select>
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">Consultando historial…</p>}
      <div className="utilization-chart-grid">
        {data?.metrics.map((metric) => (
          <article className="metric" key={metric.key}>
            <h3>{metric.label}</h3>
            <div className="metric-value">
              {metricValue(metric.value, metric.unit)}
            </div>
            <MetricStatus metric={metric} />
            <HistoryChart metric={metric} />
            <details>
              <summary>Ver muestras</summary>
              <div className="utilization-samples">
                <table>
                  <thead>
                    <tr>
                      <th>Fecha</th>
                      <th>Valor</th>
                    </tr>
                  </thead>
                  <tbody>
                    {metric.points.map(([timestamp, value]) => (
                      <tr key={timestamp}>
                        <td>{date(timestamp)}</td>
                        <td>{metricValue(value, metric.unit)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </article>
        ))}
      </div>
    </section>
  );
}

export default function UtilizationPage() {
  const [filters, setFilters] = useState({
    provider: "all",
    resource_type: "",
    scope_id: "",
    region: "",
  });
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState(null);
  const { data, error, loading } = useUtilization("", {
    ...filters,
    offset,
    limit: 20,
  });
  function change(key, value) {
    setFilters((previous) => ({
      ...previous,
      [key]: value || (key === "provider" ? "all" : ""),
    }));
    setOffset(0);
    setSelected(null);
  }
  return (
    <main>
      <section className="page-heading">
        <div>
          <h1>Utilización</h1>
          <p>Métricas reales de tus máquinas y servicios cloud.</p>
        </div>
      </section>
      <p className="utilization-notice">
        Solo recursos compatibles. CPU, memoria y disco se muestran donde
        aplican; las colas y bases administradas tienen métricas propias. Las
        muestras cloud pueden tardar varios minutos.
      </p>
      <div className="filters">
        <Filter
          label="Nube"
          value={filters.provider === "all" ? "" : filters.provider}
          options={[{ value: "aws" }, { value: "gcp" }]}
          format={(value) => value.toUpperCase()}
          onChange={(value) => change("provider", value)}
        />
        <Filter
          label="Tipo"
          value={filters.resource_type}
          options={resourceTypes.map((value) => ({ value }))}
          format={typeName}
          onChange={(value) => change("resource_type", value)}
        />
        <label className="filter">
          <span>Cuenta / proyecto</span>
          <input
            value={filters.scope_id}
            placeholder="Todos"
            onChange={(event) => change("scope_id", event.target.value)}
          />
        </label>
        <label className="filter">
          <span>Región</span>
          <input
            value={filters.region}
            placeholder="Todas"
            onChange={(event) => change("region", event.target.value)}
          />
        </label>
      </div>
      {error && (
        <p className="error" role="alert">
          {error} Se reintentará automáticamente.
        </p>
      )}
      {data?.collection?.status === "disabled" && (
        <p className="utilization-notice">
          La recolección de métricas está desactivada.
        </p>
      )}
      {data?.collection?.errors?.length > 0 && (
        <div className="error" role="alert">
          <strong>Recolección parcial</strong>
          <ul>
            {data.collection.errors.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        </div>
      )}
      {loading && <p role="status">Consultando utilización…</p>}
      {data && (
        <p className="muted">
          {data.total}{" "}
          {data.total === 1 ? "recurso compatible" : "recursos compatibles"} ·
          Última consulta cloud: {date(data.collection.last_finished_at)} ·
          Actualización cada minuto
        </p>
      )}
      {data?.items.length === 0 && (
        <div className="empty-state">
          <h3>No hay recursos compatibles</h3>
          <p>Ajusta los filtros o espera la sincronización del inventario.</p>
        </div>
      )}
      <div className="utilization-resources">
        {data?.items.map((resource) => (
          <article className="utilization-resource" key={resource.id}>
            <div className="utilization-title">
              <div>
                <h2>{resource.name || resource.resource_id}</h2>
                <p className="muted">
                  {resource.provider.toUpperCase()} ·{" "}
                  {typeName(resource.resource_type)} · {resource.scope_id} ·{" "}
                  {resource.region || "Global"}
                </p>
              </div>
              <button
                className="button"
                aria-expanded={selected === resource.id}
                onClick={() =>
                  setSelected(selected === resource.id ? null : resource.id)
                }
              >
                Ver historial
              </button>
            </div>
            <div className="utilization-values">
              {resource.metrics.map((metric) => (
                <div key={metric.key}>
                  <span>{metric.label}</span>
                  <strong
                    className={
                      metric.unit === "%" && metric.value >= 80
                        ? "utilization-high"
                        : ""
                    }
                  >
                    {metricValue(metric.value, metric.unit)}
                  </strong>
                  <MetricStatus metric={metric} />
                </div>
              ))}
            </div>
            {selected === resource.id && (
              <ResourceHistory
                key={resource.id}
                id={resource.id}
                onClose={() => setSelected(null)}
              />
            )}
          </article>
        ))}
      </div>
      {data && data.total > 20 && (
        <div className="table-footer">
          <span>
            {offset + 1}–{Math.min(offset + 20, data.total)} de {data.total}
          </span>
          <div className="pagination">
            <button
              disabled={offset === 0}
              onClick={() => {
                setOffset(offset - 20);
                setSelected(null);
              }}
            >
              Anterior
            </button>
            <button
              disabled={offset + 20 >= data.total}
              onClick={() => {
                setOffset(offset + 20);
                setSelected(null);
              }}
            >
              Siguiente
            </button>
          </div>
        </div>
      )}
    </main>
  );
}
