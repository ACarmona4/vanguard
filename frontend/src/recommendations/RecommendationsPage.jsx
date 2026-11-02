import { useEffect, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, Lightbulb, PiggyBank, RefreshCw, SlidersHorizontal, TrendingUp } from "lucide-react";
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
const PREVIEW = 3;
const PAGE = 20;
// Operational problems come before optimization opportunities.
const SECTIONS = [
  { id: "alerts", title: "Alerts", description: "Problems that need attention", icon: AlertTriangle, open: true },
  { id: "savings", title: "Cost opportunities", description: "Review resource efficiency", icon: PiggyBank, open: true },
  { id: "changes", title: "Capacity and performance", description: "Plan your next improvements", icon: TrendingUp, open: false },
  { id: "monitoring", title: "Monitoring", description: "Gaps in visibility and maintenance", icon: Lightbulb, open: false },
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
  const { data, loading, error, refresh } = useRecommendations({ ...filters, section: section.id, limit, offset: expanded ? offset : 0 });
  useEffect(() => { setExpanded(false); setOffset(0); }, [filters]);
  if (data && data.total === 0) return null;
  const total = data?.total;
  const Icon = section.icon;
  return <section className={`recommendation-section${open ? " open" : ""}`} aria-labelledby={`section-${section.id}`}>
    <h2 className="recommendation-section-header"><button type="button" className="recommendation-section-heading" aria-expanded={open} onClick={onToggle}>
      <span className="recommendation-section-icon"><Icon size={18} aria-hidden="true" /></span>
      <span className="recommendation-section-copy"><span id={`section-${section.id}`} className="recommendation-section-title">{section.title}<span className="recommendation-tab-count">{total ?? "…"}</span></span><span className="recommendation-section-description">{section.description}</span></span>
      {open ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
    </button></h2>
    {open && <>
      {loading && !data && <p className="recommendation-section-status" role="status">Loading…</p>}
      {error && <div className="recommendation-section-status"><Notification message={error} type="error" /><button className="button" onClick={refresh}>Try again</button></div>}
      <div className="finding-list">{data?.items.map((finding) => <FindingCard key={finding.id} finding={finding} onUpdated={onUpdated}><RecommendationWorkflow finding={finding} onUpdated={onUpdated} /></FindingCard>)}</div>
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
  const [showFilters, setShowFilters] = useState(false);
  const extraFilterCount = [filters.kind, filters.severity, filters.category, filters.priority].filter(Boolean).length;
  const hasFilters = extraFilterCount > 0 || filters.provider !== "all" || filters.workflow_status !== "open" || filters.order_by !== "priority";
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
  return <main className="recommendations-page">
    <section className="page-heading">
      <div><span className="eyebrow">YOUR NEXT STEPS</span><h1>Alerts & recommendations</h1><p>See what matters. Review one action at a time.</p></div>
      <div className="recommendation-workflow-actions"><button className="button recommendation-refresh" aria-label="Refresh recommendations" title="Refresh list" disabled={loading || analyzing} onClick={reload}><RefreshCw size={14} className={loading ? "spin" : ""} /></button>
        {operator && <button className="button primary" disabled={analyzing || loading} onClick={analyze}>{analyzing ? "Analyzing…" : "Analyze now"}</button>}</div>
    </section>
    <Notification message={error} type="error" />
    <Notification message={actionError} type="error" /><Notification message={notice} />
    {data?.notices.map((notice) => <Notification key={notice} message={notice} type="warning" />)}
    <RecommendationStats summary={data?.summary} loading={loading || !!error} unavailable={data?.telemetry_status === "unavailable"} />
    <div className="recommendation-toolbar"><div className="recommendation-tabs" role="group" aria-label="Detection status">
      {[["current", "Active"], ["cleared", "No longer detected"]].map(([value, label]) => <button key={value} type="button" aria-pressed={filters.detection === value} className={filters.detection === value ? "active" : ""} onClick={() => change("detection", value)}>
        {label}{value === "cleared" && data?.summary?.cleared ? <span className="recommendation-tab-count">{data.summary.cleared}</span> : null}
      </button>)}
    </div><span className="recommendation-updated">{data?.analysis_status === "pending" ? "Awaiting first analysis" : data ? `Last analysis ${date(data.generated_at)}` : "Loading…"}</span></div>
    {filters.detection === "cleared" && <p className="muted recommendation-context">The condition is no longer detected. This does not confirm a completed change.</p>}
    <div className="recommendation-filter-bar">
      <div className="filters recommendation-filters">
        <Filter label="Cloud" value={filters.provider === "all" ? "" : filters.provider} options={options(["aws", "gcp"])} format={(value) => value.toUpperCase()} onChange={(value) => change("provider", value)} />
        <Filter label="Status" value={filters.workflow_status === "all" ? "" : filters.workflow_status} options={options(["open", "detected", "approved", "executed", "rejected"])} format={(value) => STATE_LABELS[value]} onChange={(value) => change("workflow_status", value)} />
        <button className={`button recommendation-filter-toggle${showFilters ? " selected" : ""}`} aria-expanded={showFilters} aria-controls="recommendation-extra-filters" onClick={() => setShowFilters(!showFilters)}><SlidersHorizontal size={14} />Filters{extraFilterCount > 0 && <span className="recommendation-tab-count">{extraFilterCount}</span>}</button>
        {hasFilters && <button className="recommendation-reset" onClick={() => setFilters((current) => ({ ...current, provider: "all", kind: "", severity: "", category: "", priority: "", workflow_status: "open", order_by: "priority" }))}>Reset filters</button>}
      </div>
      <label className="recommendation-sort">Sort by<select aria-label="Sort by" value={filters.order_by} onChange={(event) => change("order_by", event.target.value)}>
        <option value="priority">Priority</option><option value="economic_impact">Cost impact</option><option value="business_relevance">Business relevance</option><option value="newest">Newest</option>
      </select></label>
    </div>
    <div className="filters recommendation-extra-filters" id="recommendation-extra-filters" hidden={!showFilters}>
      <Filter label="Type" value={filters.kind} options={options(Object.keys(KIND_LABELS))} format={(value) => KIND_LABELS[value]} onChange={(value) => change("kind", value)} />
      <Filter label="Severity" value={filters.severity} options={options(["critical", "warning", "info"])} format={(value) => SEVERITY_LABELS[value]} onChange={(value) => change("severity", value)} />
      <Filter label="Focus" value={filters.category} options={options(["cost", "capacity", "performance", "reliability", "observability"])} format={(value) => CATEGORY_LABELS[value]} onChange={(value) => change("category", value)} />
      <Filter label="Priority" value={filters.priority} options={options(["critical", "high", "medium", "low"])} format={(value) => PRIORITY_LABELS[value]} onChange={(value) => change("priority", value)} />
    </div>
    {data && data.total > 0 && <p className="recommendation-result-count">{data.total} results · Open an item to review</p>}
    {loading && !data && <p role="status">Loading recommendations…</p>}
    {data && data.total === 0 && <div className="empty-state">
      <h3>{data.analysis_status === "pending" ? "No results yet" : filters.detection === "cleared" ? "No cleared findings" : "No matching recommendations"}</h3>
      <p>{data.analysis_status === "pending" ? operator ? "Run an analysis to get started." : "Results will appear after the next analysis." : "Try different filters."}</p>
    </div>}
    {data && data.total > 0 && SECTIONS.map((section) => <RecommendationSection key={`${section.id}-${revision}`} section={section} filters={filters}
      open={open[section.id]} onToggle={() => toggle(section.id)} onUpdated={updated} />)}
    <details className="recommendation-methodology dashboard-panel">
      <summary>About recommendations</summary>
      <p>Alerts flag sustained problems. Recommendations identify cost and capacity changes; suggestions improve visibility and maintenance.</p>
      <p>Review the evidence and workload requirements. Apply approved changes in your cloud, then record the outcome here.</p>
      <p>{data?.summary?.history_ready ?? 0} resources have a full week of CPU history for capacity reviews.</p>
    </details>
  </main>;
}
