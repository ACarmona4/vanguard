import { ArrowUpRight } from "lucide-react";
import Notification from "../shared/components/Notification";
import { useRecommendations } from "./api";
import { FindingCard, RecommendationStats } from "./RecommendationComponents";

export default function RecommendationWidgets() {
  const { data, loading, error } = useRecommendations({}, { summary: true });
  return <section className="recommendation-overview" aria-label="Recommendations overview">
    <div className="dashboard-panel-heading"><div><span className="dashboard-section-label">RECOMMENDATIONS</span><h2>Alerts and cost reviews</h2></div><a href="#recommendations">All recommendations <ArrowUpRight size={14} /></a></div>
    <Notification message={error} type="error" />
    {data?.notices.map((notice) => <Notification key={notice} message={notice} type="warning" />)}
    <RecommendationStats summary={data?.summary} loading={loading || !!error} unavailable={data?.telemetry_status === "unavailable"} />
    <div className="recommendation-preview">{data?.items.map((finding) => <FindingCard key={finding.id} finding={finding} compact />)}</div>
    {data && !data.items.length && <p className="dashboard-empty">{data.telemetry_status === "unavailable" ? "Metrics are unavailable. Check Utilization for details." : "No open recommendations."}</p>}
  </section>;
}
