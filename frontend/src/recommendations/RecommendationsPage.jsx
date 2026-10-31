import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
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
const PREVIEW = 5;
const PAGE = 20;
// Cost savings come first: the FinOps view should not need scrolling past alerts.
const SECTIONS = [
  { id: "savings", title: "Cost savings", description: "Resources that cost money without being used, or that could be smaller.", open: true },
  { id: "alerts", title: "Alerts", description: "Sustained problems happening now.", open: true },
  { id: "changes", title: "Capacity and performance", description: "Changes to review before they become problems.", open: false },
  { id: "monitoring", title: "Monitoring", description: "What Vanguard cannot see yet, and how to fix it.", open: false },
];

function initialOpen() {
  const defaults = Object.fromEntries(SECTIONS.map((section) => [section.id, section.open]));
  try { return { ...defaults, ...JSON.parse(window.localStorage.getItem("recommendation-sections") || "{}") }; }
  catch { return defaults; }
}

function RecommendationSection({ section, filters, open, onToggle, onUpdated }) {
  const [expanded, setExpanded] = useState(false);
  const [offset, setOffset] = useState(0);
  const limit = !open ? 1 : expanded ? PAGE : PREVIEW;
  const { data, loading } = useRecommendations({ ...filters, section: section.id, limit, offset: expanded ? offset : 0 });
  useEffect(() => { setExpanded(false); setOffset(0); }, [filters]);
  if (data && data.total === 0) return null;
  const total = data?.total;
  return <section className={`recommendation-section${open ? " open" : ""}`} aria-labelledby={`section-${section.id}`}>
    <button type="button" className="recommendation-section-heading" aria-expanded={open} onClick={onToggle}>
      {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
      <span id={`section-${section.id}`} className="recommendation-section-title">{section.title}</span>
      <span className="recommendation-tab-count">{total ?? "…"}</span>
      <span className="muted recommendation-section-description">{section.description}</span>
    </button>
    {open && <>
      {loading && !data && <p role="status">Loading…</p>}
      <div className="finding-list">{data?.items.map((finding) => <FindingCard key={finding.id} finding={finding}><RecommendationWorkflow finding={finding} onUpdated={onUpdated} /></FindingCard>)}</div>
      {data && !expanded && total > PREVIEW && <button type="button" className="button recommendation-section-more" onClick={() => setExpanded(true)}>Show all ({total})</button>}
      {data && expanded && <div className="table-footer">
        <span>{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
        <div className="pagination">
          <button disabled={offset === 0 || loading} onClick={() => setOffset(offset - PAGE)}>Previous</button>
          <button disabled={offset + PAGE >= total || loading} onClick={() => setOffset(offset + PAGE)}>Next</button>
          <button onClick={() => { setExpanded(false); setOffset(0); }}>Show less</button>
        </div>
      </div>}
    </>}
  </section>;
}

export default function RecommendationsPage() {
  const { user } = useAuth();
  const operator = ["owner", "admin", "member"].includes(user.role);
  const [filters, setFilters] = useState({ provider: "all", kind: "", severity: "", category: "", priority: "", workflow_status: "open", detection: "current", order_by: "priority" });
  const [analyzing, setAnalyzing] = useState(false);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [open, setOpen] = useState(initialOpen);
  const [revision, setRevision] = useState(0);
  // Page-level request: summary, notices and the overall total; sections load their own items.
  const { data, loading, error, refresh } = useRecommendations({ ...filters, limit: 1 });
  function change(key, value) { setFilters((current) => ({ ...current, [key]: value || (["provider", "workflow_status"].includes(key) ? "all" : "") })); }
  function reload() { refresh(); setRevision((value) => value + 1); }
  function updated(message) { setNotice(message); reload(); }
  function toggle(id) {
    setOpen((current) => {
      const next = { ...current, [id]: !current[id] };
      try { window.localStorage.setItem("recommendation-sections", JSON.stringify(next)); } catch { /* optional preference */ }
      return next;
    });
  }
  async function analyze() {
    setAnalyzing(true); setActionError(""); setNotice("");
    try { await apiRequest("recommendations/sync", { method: "POST" }); updated("Analysis updated."); }
    catch (failure) { setActionError(failure.message); }
    finally { setAnalyzing(false); }
  }
  return <main>
    <section className="page-heading">
      <div><h1>Recommendations</h1><p>Review usage alerts and opportunities to reduce costs.</p></div>
      <div className="recommendation-workflow-actions"><button className="button" disabled={loading || analyzing} onClick={reload}><RefreshCw size={14} className={loading ? "spin" : ""} /> Refresh list</button>
        {operator && <button className="button primary" disabled={analyzing || loading} onClick={analyze}>{analyzing ? "Analyzing…" : "Analyze now"}</button>}</div>
    </section>
    <Notification message={error} type="error" />
    <Notification message={actionError} type="error" /><Notification message={notice} />
    {data?.notices.map((notice) => <Notification key={notice} message={notice} type="warning" />)}
    <RecommendationStats summary={data?.summary} loading={loading || !!error} unavailable={data?.telemetry_status === "unavailable"} />
    <div className="recommendation-tabs" role="tablist" aria-label="Detection status">
      {[["current", "Active"], ["cleared", "Resolved"]].map(([value, label]) => <button key={value} type="button" role="tab" aria-selected={filters.detection === value} className={filters.detection === value ? "active" : ""} onClick={() => change("detection", value)}>
        {label}{value === "cleared" && data?.summary?.cleared ? <span className="recommendation-tab-count">{data.summary.cleared}</span> : null}
      </button>)}
    </div>
    {filters.detection === "cleared" && <p className="muted recommendation-context">The latest analysis no longer detects these. Review them and record the outcome, or leave them here as history.</p>}
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
    {loading && !data && <p role="status">Loading recommendations…</p>}
    {data && data.total === 0 && <div className="empty-state">
      <h3>{data.analysis_status === "pending" ? "No results yet" : filters.detection === "cleared" ? "No resolved recommendations" : "No matching recommendations"}</h3>
      <p>{data.analysis_status === "pending" ? operator ? "Run an analysis to get started." : "Results will appear after the next analysis." : "Try different filters."}</p>
    </div>}
    {data && data.total > 0 && SECTIONS.map((section) => <RecommendationSection key={`${section.id}-${revision}`} section={section} filters={filters}
      open={open[section.id]} onToggle={() => toggle(section.id)} onUpdated={updated} />)}
    <details className="recommendation-methodology dashboard-panel">
      <summary>About recommendations</summary>
      <p>Alerts flag sustained high usage. Capacity reviews look for low usage over a full week.</p>
      <p>Check peak demand, backups and service requirements before making a change.</p>
      <p>Apply approved changes outside Vanguard, then record the result here.</p>
    </details>
  </main>;
}
