import { useEffect, useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  Box,
  CircleDollarSign,
  Cloud,
  Globe2,
  Layers3,
  Sparkles,
} from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import CostTrend from "../costs/components/CostTrend";
import { useCosts } from "../costs/api";
import { money } from "../costs/format";
import { getInventory } from "../inventory/api";
import { typeName } from "../inventory/resourceNames";
import Metric from "../shared/components/Metric";
import Notification from "../shared/components/Notification";
import { date } from "../shared/utils/format";
import UtilizationWidgets from "../utilization/UtilizationWidgets";

const COST_FILTERS = Object.freeze({ days: "30", provider: "all", infrastructure: "" });

function Breakdown({ items, currency, names = {} }) {
  const visible = items.filter((item) => item.currency === currency).slice(0, 5);
  const maximum = Math.max(...visible.map((item) => Math.abs(item.amount)), 1);
  if (!visible.length) return <p className="dashboard-empty">No spend reported for this period.</p>;

  return (
    <div className="dashboard-breakdown">
      {visible.map((item) => (
        <div key={item.name}>
          <div>
            <span>{names[item.name] || item.name}</span>
            <strong>{money(item.amount, item.currency)}</strong>
          </div>
          <div className="dashboard-bar">
            <span style={{ width: `${(Math.abs(item.amount) / maximum) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const auth = useAuth();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const { data: costs, error: costError, loading: costsLoading } = useCosts(COST_FILTERS);

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    async function load() {
      try {
        const [summary, collection] = await Promise.all([
          getInventory("summary", { provider: "all" }, controller.signal),
          getInventory("collection-status", {}, controller.signal),
        ]);
        if (!controller.signal.aborted) {
          setData({ summary, collection });
          setError("");
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure.message);
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(load, 60_000);
      }
    }
    load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, []);

  const summary = data?.summary;
  const collection = data?.collection;
  const currency = costs?.totals?.[0]?.currency || costs?.trend?.[0]?.currency || "USD";
  const total = costs?.totals?.find((item) => item.currency === currency);
  const change = total?.change_percent;
  const topTypes = useMemo(
    () => [...(summary?.by_type || [])].sort((left, right) => right.count - left.count).slice(0, 5),
    [summary?.by_type],
  );
  const syncStatus = !collection
    ? { label: "Checking sync", tone: "pending" }
    : !collection.enabled
      ? { label: "Sync paused", tone: "pending" }
      : collection.status === "error"
        ? { label: "Sync needs attention", tone: "error" }
        : collection.status === "partial"
          ? { label: "Partial sync", tone: "warning" }
          : collection.running
            ? { label: "Syncing clouds", tone: "pending" }
            : { label: "Clouds in sync", tone: "success" };

  return (
    <main id="dashboard">
      <Notification message={error || costError} type="error" />

      <section className="dashboard-hero">
        <div className="dashboard-hero-copy">
          <span className="dashboard-kicker"><Sparkles size={14} /> Cloud command center</span>
          <h1>Good to see you, {auth.user.full_name.split(" ")[0]}.</h1>
          <p>Infrastructure, utilization, and spend in one clear view.</p>
          <div className="dashboard-hero-actions">
            <a className="dashboard-primary-action" href="#inventory">Explore inventory <ArrowUpRight size={15} /></a>
            <a href="#costs">Analyze costs</a>
          </div>
        </div>
        <div className="dashboard-spend">
          <div className="dashboard-spend-heading">
            <span>Cloud spend</span>
            <span>Last 30 days</span>
          </div>
          <strong>{costsLoading ? "—" : money(total?.amount, currency)}</strong>
          <div className="dashboard-spend-meta">
            <span className={change > 0 ? "increase" : "decrease"}>
              {change == null ? "No previous-period comparison" : (
                <>{change > 0 ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}{change > 0 ? "+" : ""}{change.toFixed(1)}%</>
              )}
            </span>
            <span>{costs?.estimated ? "Estimated" : "Provider reported"}</span>
          </div>
          <div className="dashboard-cloud-chips">
            {(costs?.by_provider || []).filter((item) => item.currency === currency).map((item) => (
              <span key={item.name}><i className={item.name} />{item.name.toUpperCase()} {money(item.amount, item.currency)}</span>
            ))}
          </div>
        </div>
      </section>

      <section className="metrics dashboard-metrics" aria-label="Cloud summary">
        <Metric label="Inventoried resources" value={summary?.total ?? 0} icon={Box} caption="Across all connected clouds" loading={!data} />
        <Metric label="Cloud accounts" value={summary?.by_account.length ?? 0} icon={Cloud} caption="Accounts and projects" loading={!data} />
        <Metric label="Regions" value={summary?.by_region.filter((row) => row.value).length ?? 0} icon={Globe2} caption="With registered resources" loading={!data} />
        <Metric label="Costed services" value={costs?.service_count ?? 0} icon={CircleDollarSign} caption="Reported in the last 30 days" loading={costsLoading} />
      </section>

      <section className="dashboard-grid dashboard-cost-grid" aria-label="Cost overview">
        <article className="dashboard-panel dashboard-trend-panel">
          <div className="dashboard-panel-heading">
            <div><span className="dashboard-section-label">COST TREND</span><h2>Spend over time</h2></div>
            <a href="#costs">Full analysis <ArrowUpRight size={14} /></a>
          </div>
          <CostTrend points={costs?.trend || []} currency={currency} compact />
        </article>
        <article className="dashboard-panel">
          <div className="dashboard-panel-heading">
            <div><span className="dashboard-section-label">DISTRIBUTION</span><h2>Spend by cloud</h2></div>
            <Layers3 size={17} />
          </div>
          <Breakdown items={costs?.by_provider || []} currency={currency} names={{ aws: "AWS", gcp: "Google Cloud" }} />
          <div className="dashboard-divider" />
          <span className="dashboard-section-label">TOP COST DRIVERS</span>
          <Breakdown items={costs?.by_category || []} currency={currency} />
        </article>
      </section>

      <section className="dashboard-grid dashboard-inventory-grid" aria-label="Inventory overview">
        <article className="dashboard-panel">
          <div className="dashboard-panel-heading">
            <div><span className="dashboard-section-label">INVENTORY</span><h2>Resource composition</h2></div>
            <a href="#inventory">View all <ArrowUpRight size={14} /></a>
          </div>
          <div className="dashboard-type-list">
            {topTypes.map((item) => (
              <div key={item.value}>
                <span>{typeName(item.value)}</span><strong>{item.count}</strong>
              </div>
            ))}
            {!topTypes.length && <p className="dashboard-empty">No resources inventoried yet.</p>}
          </div>
        </article>
        <article className="dashboard-panel dashboard-sync-panel">
          <div className={`dashboard-sync-icon ${syncStatus.tone}`}><Cloud size={22} /></div>
          <div>
            <span className={`dashboard-status ${syncStatus.tone}`}><i />{syncStatus.label}</span>
            <h2>Your inventory stays current</h2>
            <p>Cloud APIs are checked automatically using each saved connection.</p>
            <small>Last successful sync: {collection?.last_success_at ? date(collection.last_success_at) : "Pending"}</small>
          </div>
        </article>
      </section>

      <UtilizationWidgets />
    </main>
  );
}
