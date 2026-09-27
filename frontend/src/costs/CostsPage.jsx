import { useCallback, useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  CalendarDays,
  Cloud,
  Coins,
  Database,
  LoaderCircle,
  RefreshCw,
  Server,
  Settings2,
} from "lucide-react";
import Notification from "../shared/components/Notification";
import { date } from "../shared/utils/format";
import { configureGcpCosts, syncCosts, useCosts } from "./api";

function money(amount, currency = "USD") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: amount < 10 ? 2 : 0,
  }).format(amount || 0);
}

function day(value) {
  return value
    ? new Intl.DateTimeFormat("en-US", { dateStyle: "medium" }).format(
        new Date(`${value}T00:00:00`),
      )
    : "—";
}

function CostTrend({ points, currency }) {
  const selected = points.filter((point) => point.currency === currency);
  if (!selected.length) return <div className="cost-empty-chart">No cost history yet.</div>;
  const width = 760;
  const height = 220;
  const values = selected.map((point) => point.amount);
  const maximum = Math.max(...values, 0.01);
  const minimum = Math.min(...values, 0);
  const range = Math.max(maximum - minimum, 0.01);
  const coordinates = selected.map((point, index) => {
    const x = 16 + (index / Math.max(selected.length - 1, 1)) * (width - 32);
    const y = 18 + ((maximum - point.amount) / range) * (height - 48);
    return [x, y, point];
  });
  const line = coordinates.map(([x, y]) => `${x},${y}`).join(" ");
  const area = `16,${height - 30} ${line} ${width - 16},${height - 30}`;
  return (
    <div className="cost-chart-wrap">
      <svg className="cost-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Daily cost trend">
        <line x1="16" y1={height - 30} x2={width - 16} y2={height - 30} className="cost-axis" />
        <polygon points={area} className="cost-area" />
        <polyline points={line} className="cost-line" />
        {coordinates.map(([x, y, point]) => (
          <circle key={`${point.date}-${point.currency}`} cx={x} cy={y} r="3" className="cost-point">
            <title>{`${point.date}: ${money(point.amount, point.currency)}`}</title>
          </circle>
        ))}
      </svg>
      <div className="chart-caption"><span>{day(selected[0].date)}</span><span>Max. {money(maximum, currency)}</span><span>{day(selected.at(-1).date)}</span></div>
    </div>
  );
}

function Breakdown({ title, items, currency, onSelect, selected }) {
  const filtered = items.filter((item) => item.currency === currency);
  const maximum = Math.max(...filtered.map((item) => Math.abs(item.amount)), 1);
  return (
    <section className="cost-panel">
      <div className="section-heading"><h2>{title}</h2><span className="count-badge">{filtered.length}</span></div>
      <div className="cost-breakdown">
        {filtered.map((item) => {
          const label = title === "By cloud" ? item.name.toUpperCase() : item.name;
          const content = <><div><span>{label}</span><strong>{money(item.amount, item.currency)}</strong></div><div className="bar-track"><div style={{ width: `${Math.abs(item.amount) / maximum * 100}%` }} /></div></>;
          return onSelect ? <button key={item.name} className={selected === item.name ? "active" : ""} onClick={() => onSelect(selected === item.name ? "" : item.name)}>{content}</button> : <div key={item.name}>{content}</div>;
        })}
        {!filtered.length && <p className="muted">No data for this period.</p>}
      </div>
    </section>
  );
}

function GcpSourceForm({ source, onSaved }) {
  const [table, setTable] = useState(source.billing_export_table || "");
  const [location, setLocation] = useState(source.billing_location || "US");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await configureGcpCosts(source.connection_id, {
        billing_export_table: table,
        billing_location: location,
      });
      onSaved();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <form className="cost-source-form" onSubmit={submit}>
      <label>Detailed billing table<input required value={table} onChange={(event) => setTable(event.target.value.trim())} placeholder="project.dataset.gcp_billing_export_resource_v1_…" /></label>
      <label>Location<input required value={location} onChange={(event) => setLocation(event.target.value.trim())} placeholder="US" /></label>
      <button className="button" disabled={saving}>{saving ? "Saving…" : "Save"}</button>
      <Notification message={error} type="error" />
    </form>
  );
}

export default function CostsPage() {
  const [filters, setFilters] = useState({ days: "30", provider: "all" });
  const [category, setCategory] = useState("");
  const [selectedCurrency, setSelectedCurrency] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [notice, setNotice] = useState({ message: "", type: "success" });
  const dismissNotice = useCallback(() => setNotice({ message: "", type: "success" }), []);
  const { data, error, loading, reload } = useCosts(filters);
  const currency = data?.totals?.some((item) => item.currency === selectedCurrency)
    ? selectedCurrency
    : data?.totals?.[0]?.currency || data?.trend?.[0]?.currency || "USD";
  const total = data?.totals?.find((item) => item.currency === currency);
  const average = data?.daily_average?.find((item) => item.currency === currency)?.amount || 0;
  const resources = useMemo(
    () => (data?.top_resources || []).filter((item) => !category || item.category === category),
    [data?.top_resources, category],
  );

  async function synchronize() {
    setSyncing(true);
    try {
      await syncCosts();
      setNotice({ message: "Cost synchronization started.", type: "success" });
      window.setTimeout(reload, 1500);
    } catch (failure) {
      setNotice({ message: failure.message, type: "error" });
    } finally {
      setSyncing(false);
    }
  }

  return (
    <main id="costs">
      <section className="page-heading">
        <div><h1>Costs</h1><p>Cloud spend, trends, and cost drivers.</p></div>
        <button className="button" onClick={synchronize} disabled={syncing}>
          {syncing ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />} Sync
        </button>
      </section>
      <Notification message={error} type="error" />
      <Notification message={notice.message} type={notice.type} onDismiss={dismissNotice} />
      <div className="cost-filters">
        <label><CalendarDays size={15} /><select value={filters.days} onChange={(event) => setFilters({ ...filters, days: event.target.value })}><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select></label>
        <label><Cloud size={15} /><select value={filters.provider} onChange={(event) => setFilters({ ...filters, provider: event.target.value })}><option value="all">All clouds</option><option value="aws">AWS</option><option value="gcp">Google Cloud</option></select></label>
        {data?.totals?.length > 1 && <label><Coins size={15} /><select value={currency} onChange={(event) => setSelectedCurrency(event.target.value)}>{data.totals.map((item) => <option key={item.currency} value={item.currency}>{item.currency}</option>)}</select></label>}
      </div>

      <section className="metrics cost-metrics">
        <article className="metric"><div className="metric-label">Spend <Coins size={16} /></div><div className="metric-value">{loading ? "—" : money(total?.amount, currency)}</div><span className="muted">{data ? `${data.estimated ? "Estimated · " : ""}${day(data.period.start)} – ${day(data.period.end)}` : "Selected period"}</span></article>
        <article className="metric"><div className="metric-label">Change {total?.change_percent > 0 ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}</div><div className={`metric-value ${total?.change_percent > 0 ? "cost-increase" : "cost-decrease"}`}>{total?.change_percent == null ? "—" : `${total.change_percent > 0 ? "+" : ""}${total.change_percent.toFixed(1)}%`}</div><span className="muted">Against previous period</span></article>
        <article className="metric"><div className="metric-label">Daily average <CalendarDays size={16} /></div><div className="metric-value">{loading ? "—" : money(average, currency)}</div><span className="muted">Across the selected period</span></article>
        <article className="metric"><div className="metric-label">Costed services <Server size={16} /></div><div className="metric-value">{loading ? "—" : data?.service_count || 0}</div><span className="muted">Services in this period</span></article>
      </section>

      <section className="cost-panel cost-trend-panel">
        <div className="section-heading"><div><h2>Spend over time</h2><p className="muted">Daily net cost, including credits.</p></div><strong>{money(total?.amount, currency)}</strong></div>
        <CostTrend points={data?.trend || []} currency={currency} />
      </section>

      <div className="cost-grid">
        <Breakdown title="By cloud" items={data?.by_provider || []} currency={currency} />
        <Breakdown title="By infrastructure" items={data?.by_category || []} currency={currency} onSelect={setCategory} selected={category} />
      </div>

      <div className="cost-grid cost-details-grid">
        <section className="cost-panel">
          <div className="section-heading"><h2>Top services</h2><Database size={17} /></div>
          <div className="table-scroll"><table><thead><tr><th>Service</th><th>Cost</th></tr></thead><tbody>{(data?.top_services || []).filter((item) => item.currency === currency).map((item) => <tr key={item.name}><td>{item.name}</td><td><strong>{money(item.amount, item.currency)}</strong></td></tr>)}</tbody></table></div>
        </section>
        <section className="cost-panel">
          <div className="section-heading"><h2>{category ? `${category} resources` : "Top resources"}</h2>{category && <button className="text-button" onClick={() => setCategory("")}>Clear</button>}</div>
          <div className="table-scroll"><table><thead><tr><th>Resource</th><th>Cloud</th><th>Cost</th></tr></thead><tbody>{resources.filter((item) => item.currency === currency).map((item) => <tr key={`${item.provider}-${item.resource_id}`}><td><strong>{item.resource_name || item.resource_id}</strong><small className="cost-service">{item.service}</small></td><td>{item.provider.toUpperCase()}</td><td><strong>{money(item.amount, item.currency)}</strong></td></tr>)}</tbody></table></div>
          {!resources.length && <p className="cost-table-empty">Resource-level data is not available for this selection.</p>}
        </section>
      </div>

      <section className="cost-panel cost-sources">
        <div className="section-heading"><div><h2>Cost sources</h2><p className="muted">AWS Cost Explorer and GCP detailed billing export.</p></div><Settings2 size={18} /></div>
        <div className="connection-list">
          {(data?.sources || []).map((source) => <article className="cost-source" key={source.connection_id}><div className={`provider-mark ${source.provider}`}>{source.provider.toUpperCase()}</div><div className="connection-info"><div className="connection-title"><strong>{source.connection_name}</strong><span className={`connection-status ${source.status}`}>{source.status}</span></div><small>{source.scope_id}{source.last_synced_at && ` · Synced ${date(source.last_synced_at)}`}</small>{source.last_error && <small className="connection-error">{source.last_error}</small>}{source.provider === "gcp" && <GcpSourceForm source={source} onSaved={reload} />}</div><span className="cost-detail-badge">{source.detail_available ? "Resource detail" : "Service totals"}</span></article>)}
        </div>
      </section>
    </main>
  );
}
