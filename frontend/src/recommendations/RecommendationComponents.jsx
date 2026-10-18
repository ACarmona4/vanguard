import { Activity, AlertTriangle, ArrowUpRight, Lightbulb, PiggyBank } from "lucide-react";
import Metric from "../shared/components/Metric";
import { date } from "../shared/utils/format";
import { metricValue } from "../utilization/api";
import { typeName } from "../shared/resources/resourceNames";

export const KIND_LABELS = { alert: "Alerts", recommendation: "Recommendations", suggestion: "Suggestions" };

export function RecommendationStats({ summary, loading, unavailable = false }) {
  const cards = [
    ["Active alerts", summary?.alerts, AlertTriangle, `${summary?.critical ?? 0} critical ${summary?.critical === 1 ? "finding" : "findings"}`],
    ["Recommendations", summary?.recommendations, PiggyBank, `${summary?.cost_opportunities ?? 0} cost optimization opportunities`],
    ["Suggestions", summary?.suggestions, Lightbulb, "Visibility and operational improvements"],
    ["Resources with telemetry", summary ? `${summary.monitored} / ${summary.eligible}` : "—", Activity, `${summary?.history_ready ?? 0} with weekly CPU history`],
  ];
  return <div className="metrics recommendation-metrics">{cards.map(([label, value, Icon, caption], index) => (
    // Metric's number formatter is intentionally bypassed for the coverage fraction.
    index === 3 ? <article className="metric" key={label}>
      <div className="metric-label">{label}<Icon size={17} /></div>
      <div className="metric-value">{loading || unavailable ? "—" : value}</div>
      <span className="muted">{caption}</span>
    </article> : <Metric key={label} label={label} value={value ?? 0} icon={Icon} caption={caption} loading={loading || unavailable} />
  ))}</div>;
}

export function FindingCard({ finding, compact = false }) {
  const evidence = finding.evidence;
  return <article className={`finding-card finding-${finding.severity}`}>
    <div className="finding-heading">
      <div className="finding-badges">
        <span className={`finding-severity ${finding.severity}`}>{finding.severity}</span>
        <span>{KIND_LABELS[finding.kind]}</span><span>{finding.category}</span>
      </div>
      {compact && <a href="#recommendations" aria-label={`Review ${finding.title}`}><ArrowUpRight size={16} /></a>}
    </div>
    <h3>{finding.title}</h3>
    <p className="finding-resource"><strong>{finding.resource_name}</strong> · {finding.provider.toUpperCase()} · {typeName(finding.resource_type)} · {finding.region || "Global"}</p>
    <p>{finding.description}</p>
    {!compact && <>
      <div className="finding-evidence">
        <div><span>Observed value</span><strong>{metricValue(evidence.value, evidence.unit)}</strong></div>
        <div><span>Reference threshold</span><strong>{metricValue(evidence.threshold, evidence.unit)}</strong></div>
        <div><span>Window / samples</span><strong>{evidence.window} / {evidence.samples}</strong></div>
        <div><span>Last observation</span><strong>{evidence.observed_at ? date(evidence.observed_at) : "Inventory / capability check"}</strong></div>
      </div>
      <details>
        <summary>Evidence and suggested actions</summary>
        <p>{evidence.explanation}</p>
        <ol>{finding.actions.map((action) => <li key={action}>{action}</li>)}</ol>
        <p className="muted">Account / project: {finding.scope_id || "—"} · Rule: {finding.rule}</p>
        <a href={`#utilization?resource=${finding.resource_id}`}>Inspect resource metrics <ArrowUpRight size={13} /></a>
      </details>
    </>}
  </article>;
}
