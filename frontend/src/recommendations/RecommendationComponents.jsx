import { Activity, AlertTriangle, ArrowUpRight, Lightbulb, PiggyBank } from "lucide-react";
import Metric from "../shared/components/Metric";
import { date } from "../shared/utils/format";
import { metricValue } from "../utilization/api";
import { typeName } from "../shared/resources/resourceNames";

import { CATEGORY_LABELS, PRIORITY_LABELS, SEVERITY_LABELS, STATE_LABELS } from "./labels";

export const KIND_LABELS = { alert: "Alerts", recommendation: "Recommendations", suggestion: "Suggestions" };

export function RecommendationStats({ summary, loading, unavailable = false }) {
  const cards = [
    ["Open alerts", summary?.alerts, AlertTriangle, `${summary?.critical ?? 0} critical ${summary?.critical === 1 ? "finding" : "findings"}`],
    ["Recommendations", summary?.recommendations, PiggyBank, `${summary?.cost_opportunities ?? 0} cost ${summary?.cost_opportunities === 1 ? "review" : "reviews"}`],
    ["Suggestions", summary?.suggestions, Lightbulb, "Monitoring and maintenance"],
    ["Resources with metrics", summary ? `${summary.monitored} / ${summary.eligible}` : "—", Activity, `${summary?.history_ready ?? 0} with a week of CPU data`],
  ];
  return <div className="metrics recommendation-metrics">{cards.map(([label, value, Icon, caption], index) => (
    // Metric's number formatter is intentionally bypassed for the coverage fraction.
    index === 3 ? <article className="metric" key={label}>
      <div className="metric-label">{label}<Icon size={17} /></div>
      <div className="metric-value">{loading || unavailable ? "—" : value}</div>
      <span className="muted">{caption}</span>
    </article> : <Metric key={label} label={label} value={value ?? 0} icon={Icon} caption={caption} loading={loading} />
  ))}</div>;
}

export function FindingCard({ finding, compact = false, children }) {
  const evidence = finding.evidence;
  return <article className={`finding-card finding-${finding.severity}`}>
    <div className="finding-heading">
      <div className="finding-badges">
        <span className={`finding-severity ${finding.severity}`}>{SEVERITY_LABELS[finding.severity]}</span>
        <span>{KIND_LABELS[finding.kind]}</span><span>{CATEGORY_LABELS[finding.category]}</span>
        {finding.priority && <span className="finding-priority">{PRIORITY_LABELS[finding.priority]} priority</span>}
        {finding.workflow_status && <span>{STATE_LABELS[finding.workflow_status]}</span>}
      </div>
      {compact && <a href="#recommendations" aria-label={`Review ${finding.title}`}><ArrowUpRight size={16} /></a>}
    </div>
    <h3>{finding.title}</h3>
    <p className="finding-resource"><strong>{finding.resource_name}</strong> · {finding.provider.toUpperCase()} · {typeName(finding.resource_type)} · {finding.region || "Global"}</p>
    <p><strong>Reason:</strong> {finding.description}</p>
    {!compact && <>
      {finding.actions[0] && <p><strong>Suggested action:</strong> {finding.actions[0]}</p>}
      {finding.last_detected_at && <p className="muted">Detected {date(finding.first_detected_at)} · Last seen {date(finding.last_detected_at)} · {finding.is_current ? "Still detected" : "No longer detected"}</p>}
      <div className="finding-evidence">
        <div><span>{finding.rule === "cpu_percent_oversized" ? "CPU usage (P95)" : "Measured value"}</span><strong>{metricValue(evidence.value, evidence.unit)}</strong></div>
        <div><span>Threshold</span><strong>{metricValue(evidence.threshold, evidence.unit)}</strong></div>
        <div><span>Period / readings</span><strong>{evidence.window} / {evidence.samples}</strong></div>
        <div><span>Last reading</span><strong>{evidence.observed_at ? date(evidence.observed_at) : "From inventory"}</strong></div>
      </div>
      <details>
        <summary>Details</summary>
        <p>{evidence.explanation}</p>
        <h4>Suggested actions</h4>
        <ol>{finding.actions.map((action) => <li key={action}>{action}</li>)}</ol>
        <p className="muted">Resource: {finding.cloud_resource_id || finding.resource_id} · Account / project: {finding.scope_id || "—"}</p>
        <a href={`#utilization?resource=${finding.resource_id}`}>View metrics <ArrowUpRight size={13} /></a>
      </details>
      {children}
    </>}
  </article>;
}
