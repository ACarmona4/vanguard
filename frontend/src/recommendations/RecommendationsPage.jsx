import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import Filter from "../shared/components/Filter";
import Notification from "../shared/components/Notification";
import { date } from "../shared/utils/format";
import { useRecommendations } from "./api";
import { FindingCard, KIND_LABELS, RecommendationStats } from "./RecommendationComponents";

const options = (values) => values.map((value) => ({ value }));

export default function RecommendationsPage() {
  const [filters, setFilters] = useState({ provider: "all", kind: "", severity: "", category: "" });
  const [offset, setOffset] = useState(0);
  const { data, loading, error, refresh } = useRecommendations({ ...filters, offset, limit: 20 });
  useEffect(() => {
    if (data && offset > 0 && offset >= data.total) setOffset(0);
  }, [data, offset]);
  function change(key, value) { setFilters((current) => ({ ...current, [key]: value || (key === "provider" ? "all" : "") })); setOffset(0); }
  return <main>
    <section className="page-heading">
      <div><h1>Recommendations</h1><p>Find capacity risks and infrastructure you may be overpaying for.</p></div>
      <button className="button" disabled={loading} onClick={refresh}><RefreshCw size={14} className={loading ? "spin" : ""} /> Refresh analysis</button>
    </section>
    <Notification message={error} type="error" />
    {data?.notices.map((notice) => <Notification key={notice} message={notice} type="warning" />)}
    <RecommendationStats summary={data?.summary} loading={loading || !!error} unavailable={data?.telemetry_status === "unavailable"} />
    <div className="filters recommendation-filters">
      <Filter label="Cloud" value={filters.provider === "all" ? "" : filters.provider} options={options(["aws", "gcp"])} format={(value) => value.toUpperCase()} onChange={(value) => change("provider", value)} />
      <Filter label="Type" value={filters.kind} options={options(Object.keys(KIND_LABELS))} format={(value) => KIND_LABELS[value]} onChange={(value) => change("kind", value)} />
      <Filter label="Severity" value={filters.severity} options={options(["critical", "warning", "info"])} onChange={(value) => change("severity", value)} />
      <Filter label="Focus" value={filters.category} options={options(["cost", "capacity", "performance", "reliability", "observability"])} onChange={(value) => change("category", value)} />
    </div>
    <p className="muted recommendation-context">{data ? `${data.total} findings · Evaluated ${date(data.generated_at)} · Statistics cover the selected cloud; filters narrow the findings below.` : "Evaluating inventory and usage history…"}</p>
    {loading && <p role="status">Analyzing infrastructure…</p>}
    {data && data.items.length === 0 && <div className="empty-state">
      <h3>{data.telemetry_status === "unavailable" ? "Telemetry could not be evaluated" : data.summary.eligible === 0 ? "No compatible active resources" : "No findings for these filters"}</h3>
      <p>{data.telemetry_status === "unavailable" ? "Restore metric collection to evaluate utilization and optimization opportunities." : "Try another focus or wait for a representative usage history. Missing data is never treated as idle capacity."}</p>
    </div>}
    <div className="finding-list">{data?.items.map((finding) => <FindingCard key={finding.id} finding={finding} />)}</div>
    {data && data.total > 20 && <div className="table-footer">
      <span>{offset + 1}–{Math.min(offset + 20, data.total)} of {data.total}</span>
      <div className="pagination"><button disabled={offset === 0 || loading} onClick={() => setOffset(offset - 20)}>Previous</button><button disabled={offset + 20 >= data.total || loading} onClick={() => setOffset(offset + 20)}>Next</button></div>
    </div>}
    <details className="recommendation-methodology dashboard-panel">
      <summary>How the engine decides</summary>
      <p>Alerts require at least four observations spanning {data?.policy.sustained_minutes ?? 15} minutes, with gaps no larger than 10 minutes. CPU, memory and disk thresholds start at {data?.policy.warning_percent ?? 80}% / {data?.policy.critical_percent ?? 90}%. These are operational defaults to adapt to your workload.</p>
      <p>Overprovisioning checks use seven days with at least 80% hourly coverage. CPU reviews require P95 of hourly peaks below {data?.policy.low_cpu_p95 ?? 20}% and every peak below {data?.policy.low_cpu_peak ?? 50}%. Storage reviews require every hourly peak below {data?.policy.low_storage_peak ?? 20}%. Recent load must remain low. Validate infrequent jobs and availability requirements before reducing capacity.</p>
      <p>Network traffic is compared with its own baseline. Bandwidth saturation needs a known link limit. Guest memory and occupied disk space are unavailable from native VM metrics. Recommendations are reviews; monetary savings require pricing and billing validation.</p>
      <p>Findings are recalculated every minute and disappear when their conditions no longer apply. Thresholds are centralized in the engine policy.</p>
      <ul>{data?.sources.map((source) => <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a></li>)}</ul>
    </details>
  </main>;
}
