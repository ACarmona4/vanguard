import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import Filter from "../shared/components/Filter";
import Notification from "../shared/components/Notification";
import { date } from "../shared/utils/format";
import { useRecommendations } from "./api";
import { FindingCard, KIND_LABELS, RecommendationStats } from "./RecommendationComponents";
import RecommendationWorkflow from "./RecommendationWorkflow";
import { useAuth } from "../auth/AuthProvider";
import { apiRequest } from "../api/client";

import { CATEGORY_LABELS, PRIORITY_LABELS, SEVERITY_LABELS, STATE_LABELS } from "./labels";

const options = (values) => values.map((value) => ({ value }));

export default function RecommendationsPage() {
  const { user } = useAuth();
  const operator = ["owner", "admin", "member"].includes(user.role);
  const [filters, setFilters] = useState({ provider: "all", kind: "", severity: "", category: "", priority: "", workflow_status: "open", order_by: "priority" });
  const [analyzing, setAnalyzing] = useState(false);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, loading, error, refresh } = useRecommendations({ ...filters, offset, limit: 20 });
  useEffect(() => {
    if (data && offset > 0 && offset >= data.total) setOffset(0);
  }, [data, offset]);
  function change(key, value) { setFilters((current) => ({ ...current, [key]: value || (["provider", "workflow_status"].includes(key) ? "all" : "") })); setOffset(0); }
  function updated(message) { setNotice(message); refresh(); }
  async function analyze() {
    setAnalyzing(true); setActionError(""); setNotice("");
    try { await apiRequest("recommendations/sync", { method: "POST" }); updated("Analysis updated."); }
    catch (failure) { setActionError(failure.message); }
    finally { setAnalyzing(false); }
  }
  return <main>
    <section className="page-heading">
      <div><h1>Recommendations</h1><p>Review usage alerts and opportunities to reduce costs.</p></div>
      <div className="recommendation-workflow-actions"><button className="button" disabled={loading || analyzing} onClick={refresh}><RefreshCw size={14} className={loading ? "spin" : ""} /> Refresh list</button>
        {operator && <button className="button primary" disabled={analyzing || loading} onClick={analyze}>{analyzing ? "Analyzing…" : "Analyze now"}</button>}</div>
    </section>
    <Notification message={error} type="error" />
    <Notification message={actionError} type="error" /><Notification message={notice} />
    {data?.notices.map((notice) => <Notification key={notice} message={notice} type="warning" />)}
    <RecommendationStats summary={data?.summary} loading={loading || !!error} unavailable={data?.telemetry_status === "unavailable"} />
    <div className="filters recommendation-filters">
      <Filter label="Cloud" value={filters.provider === "all" ? "" : filters.provider} options={options(["aws", "gcp"])} format={(value) => value.toUpperCase()} onChange={(value) => change("provider", value)} />
      <Filter label="Type" value={filters.kind} options={options(Object.keys(KIND_LABELS))} format={(value) => KIND_LABELS[value]} onChange={(value) => change("kind", value)} />
      <Filter label="Severity" value={filters.severity} options={options(["critical", "warning", "info"])} format={(value) => SEVERITY_LABELS[value]} onChange={(value) => change("severity", value)} />
      <Filter label="Focus" value={filters.category} options={options(["cost", "capacity", "performance", "reliability", "observability"])} format={(value) => CATEGORY_LABELS[value]} onChange={(value) => change("category", value)} />
      <Filter label="Priority" value={filters.priority} options={options(["critical", "high", "medium", "low"])} format={(value) => PRIORITY_LABELS[value]} onChange={(value) => change("priority", value)} />
      <Filter label="Status" value={filters.workflow_status === "all" ? "" : filters.workflow_status} options={options(["open", "detected", "approved", "executed", "rejected"])} format={(value) => STATE_LABELS[value]} onChange={(value) => change("workflow_status", value)} />
      <label className="filter"><span>Sort by</span><select aria-label="Sort by" value={filters.order_by} onChange={(event) => change("order_by", event.target.value)}>
        <option value="priority">Priority</option><option value="economic_impact">Cost impact</option><option value="business_relevance">Business relevance</option><option value="newest">Newest</option>
      </select></label>
    </div>
    <p className="muted recommendation-context">{data ? data.analysis_status === "pending" ? "No results yet." : `${data.total} results · Last analysis ${date(data.generated_at)}` : "Loading saved recommendations…"}</p>
    {loading && <p role="status">Loading recommendations…</p>}
    {data && data.items.length === 0 && <div className="empty-state">
      <h3>{data.analysis_status === "pending" ? "No results yet" : "No matching recommendations"}</h3>
      <p>{data.analysis_status === "pending" ? operator ? "Run an analysis to get started." : "Results will appear after the next analysis." : "Try different filters."}</p>
    </div>}
    <div className="finding-list">{data?.items.map((finding) => <FindingCard key={finding.id} finding={finding}><RecommendationWorkflow finding={finding} onUpdated={updated} /></FindingCard>)}</div>
    {data && data.total > 20 && <div className="table-footer">
      <span>{offset + 1}–{Math.min(offset + 20, data.total)} of {data.total}</span>
      <div className="pagination"><button disabled={offset === 0 || loading} onClick={() => setOffset(offset - 20)}>Previous</button><button disabled={offset + 20 >= data.total || loading} onClick={() => setOffset(offset + 20)}>Next</button></div>
    </div>}
    <details className="recommendation-methodology dashboard-panel">
      <summary>About recommendations</summary>
      <p>Alerts flag sustained high usage. Capacity reviews look for low usage over a full week.</p>
      <p>Check peak demand, backups and service requirements before making a change.</p>
      <p>Apply approved changes outside Vanguard, then record the result here.</p>
    </details>
  </main>;
}
