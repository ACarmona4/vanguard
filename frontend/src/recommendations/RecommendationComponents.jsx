import { Activity, AlertTriangle, ArrowUpRight, ChevronDown, Lightbulb, PiggyBank } from "lucide-react";
import { date } from "../shared/utils/format";
import { metricValue } from "../utilization/api";
import { typeName } from "../shared/resources/resourceNames";
import { CATEGORY_LABELS, PRIORITY_LABELS, SEVERITY_LABELS, STATE_LABELS } from "./labels";
import "./recommendations.css";

export const KIND_LABELS = { alert: "Alerts", recommendation: "Recommendations", suggestion: "Suggestions" };

export function RecommendationStats({ summary, loading, unavailable = false }) {
  const cards = [
    ["Open alerts", summary?.alerts, AlertTriangle, summary?.critical ? `${summary.critical} critical · review first` : "No critical findings", "alerts"],
    ["Cost opportunities", summary?.cost_opportunities, PiggyBank, "Review resource efficiency", "recommendations"],
    ["Other reviews", summary ? Math.max(0, summary.recommendations + summary.suggestions - summary.cost_opportunities) : null, Lightbulb, "Capacity, reliability and monitoring", "suggestions"],
  ];
  return <div className="recommendation-summary">
    <div className="recommendation-metrics">{cards.map(([label, value, Icon, caption, key]) => <article className={`recommendation-stat ${key === "alerts" && summary?.critical ? "needs-attention" : ""}`} key={key}>
      <span className="recommendation-stat-icon"><Icon size={18} aria-hidden="true" /></span>
      <div><span className="recommendation-stat-label">{label}</span><div className="recommendation-stat-number">{loading ? "—" : value ?? 0}</div><p>{loading ? "Loading…" : caption}</p></div>
    </article>)}</div>
    <div className="recommendation-coverage"><Activity size={13} aria-hidden="true" />
      {loading ? "Checking coverage…" : unavailable ? "Metrics unavailable · results may be incomplete" : `${summary?.monitored ?? 0} of ${summary?.eligible ?? 0} eligible resources have metrics`}
    </div>
  </div>;
}

const EVIDENCE_LABELS = { cpu_percent: "CPU usage", oldest_message_seconds: "Oldest message", errors_per_second: "Error rate", throttles_per_second: "Throttle rate", read_throttles_per_second: "Read throttle rate", write_throttles_per_second: "Write throttle rate", history_coverage: "Usage history", metric_coverage: "Recent metrics", duration_p95_ms: "Duration (P95)" };

function overviewValue(evidence) {
  if (evidence.unit === "s" && evidence.value >= 60) {
    const [scale, unit] = evidence.value >= 86400 ? [86400, "days"] : evidence.value >= 3600 ? [3600, "hours"] : [60, "min"];
    return metricValue(Number((evidence.value / scale).toFixed(1)), unit);
  }
  return metricValue(evidence.value, evidence.unit);
}

function KeyEvidence({ finding }) {
  const { evidence } = finding;
  if (evidence.value != null) return <span className="finding-key-data"><strong>{overviewValue(evidence)}</strong><span>{finding.rule === "cpu_percent_oversized" ? "CPU usage (P95)" : EVIDENCE_LABELS[evidence.metric] || (evidence.window === "inventory" ? "From inventory" : "Observed value")}</span></span>;
  return null;
}

export function FindingCard({ finding, compact = false, children, onUpdated }) {
  const evidence = finding.evidence;
  const priority = finding.priority || finding.severity;
  const title = <h3>{finding.title}</h3>;
  const resource = <p className="finding-resource"><strong>{finding.resource_name}</strong><span>{finding.provider.toUpperCase()} · {typeName(finding.resource_type)}</span>{finding.workflow_status && finding.workflow_status !== "detected" && <span className="finding-status">{STATE_LABELS[finding.workflow_status]}</span>}</p>;
  const badge = <span className={`finding-priority ${priority}`}>{PRIORITY_LABELS[priority] || SEVERITY_LABELS[priority]}<span className="sr-only"> priority</span></span>;
  if (compact) return <article className="finding-card finding-compact">
    <div className="finding-heading">{badge}<a href="#recommendations" aria-label={`Review ${finding.title}`}><ArrowUpRight size={16} /></a></div>
    {title}{resource}<KeyEvidence finding={finding} />
  </article>;
  return <article className={`finding-card finding-${finding.severity}`}>
    <details className="finding-disclosure">
      <summary className="finding-summary">
        <div className="finding-main"><div className="finding-title-line">{title}{badge}</div>{resource}</div>
        <KeyEvidence finding={finding} />
        <span className="finding-open-label"><span className="when-closed">Review</span><span className="when-open">Close</span><ChevronDown size={16} aria-hidden="true" /></span>
      </summary>
      <div className="finding-detail">
        <div className="finding-detail-meta"><span>{KIND_LABELS[finding.kind]} · {CATEGORY_LABELS[finding.category]}</span><span>{STATE_LABELS[finding.workflow_status]} · {finding.is_current ? "Still detected" : "No longer detected"}</span></div>
        <div className="finding-explanation-grid">
          <section><h4>Why this matters</h4><p>{finding.description}</p></section>
          <section className="finding-next-step"><h4>What to do</h4><ol>{finding.actions.map((action) => <li key={action}>{action}</li>)}</ol></section>
        </div>
        <details className="finding-secondary-details"><summary>Evidence and resource details</summary>
          <div className="finding-evidence">
            <div><span>{finding.rule === "cpu_percent_oversized" ? "CPU usage (P95)" : "Measured value"}</span><strong>{metricValue(evidence.value, evidence.unit)}</strong></div>
            <div><span>Threshold</span><strong>{metricValue(evidence.threshold, evidence.unit)}</strong></div>
            <div><span>Period / readings</span><strong>{evidence.window} / {evidence.samples}</strong></div>
            <div><span>Last reading</span><strong>{evidence.observed_at ? date(evidence.observed_at) : evidence.window === "inventory" ? "From inventory" : "No recent reading"}</strong></div>
          </div>
          <p>{evidence.explanation}</p>
          <dl className="finding-resource-details">
            <div><dt>Resource ID</dt><dd>{finding.cloud_resource_id || finding.resource_id}</dd></div>
            <div><dt>Account / project</dt><dd>{finding.scope_id || "—"}</dd></div>
            <div><dt>Region</dt><dd>{finding.region || "Global"}</dd></div>
            <div><dt>Severity</dt><dd>{SEVERITY_LABELS[finding.severity]}</dd></div>
            {finding.priority_reason && <div><dt>Why this priority</dt><dd>{finding.priority_reason}</dd></div>}
            {finding.last_detected_at && <><div><dt>First detected</dt><dd>{date(finding.first_detected_at)}</dd></div><div><dt>Last detected</dt><dd>{date(finding.last_detected_at)}</dd></div></>}
          </dl>
          <a className="finding-metrics-link" href={`#utilization?resource=${finding.resource_id}`}>View resource metrics <ArrowUpRight size={13} /></a>
        </details>
        {children}
      </div>
    </details>
  </article>;
}
