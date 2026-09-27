import { useEffect, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  Box,
  ChevronLeft,
  ChevronRight,
  Cloud,
  Database,
  Globe2,
  Layers3,
  Server,
  SlidersHorizontal,
} from "lucide-react";
import { getInventory } from "./api";
import { typeName } from "./resourceNames";
import { date, number, time } from "../shared/utils/format";
import Metric from "../shared/components/Metric";
import Filter from "../shared/components/Filter";
import ResourceDetails from "./components/ResourceDetails";
import Notification from "../shared/components/Notification";

const PAGE_SIZE = 10;
const AUTO_REFRESH_MS = 60_000;
const emptyFilters = { resource_type: "", region: "", scope_id: "" };
const tabs = [
  { id: "all", name: "All clouds" },
  { id: "aws", name: "AWS" },
  { id: "gcp", name: "Google Cloud" },
];

export default function InventoryPage() {
  const [provider, setProvider] = useState("all");
  const [filters, setFilters] = useState(emptyFilters);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    const interval = window.setInterval(
      () => setRefresh((value) => value + 1),
      AUTO_REFRESH_MS,
    );
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const query = { provider, ...filters };
    Promise.all([
      getInventory(
        "resources",
        { ...query, limit: PAGE_SIZE, offset },
        controller.signal,
      ),
      getInventory("summary", query, controller.signal),
      getInventory("summary", { provider }, controller.signal),
      getInventory("collection-status", {}, controller.signal),
    ])
      .then(([page, summary, options, collection]) => {
        if (!controller.signal.aborted)
          setData({
            page,
            summary,
            options,
            collection,
          });
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(failure.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [provider, filters, offset, refresh]);

  function changeCloud(value) {
    setProvider(value);
    setFilters(emptyFilters);
    setOffset(0);
  }
  function changeFilter(key, value) {
    setFilters((previous) => ({ ...previous, [key]: value }));
    setOffset(0);
  }
  const visible = Boolean(data);
  const initialLoading = loading && !data;
  const summary = visible
    ? data.summary
    : { total: 0, by_type: [], by_region: [], by_account: [] };
  const options = data?.options || {
    by_type: [],
    by_region: [],
    by_account: [],
  };
  const activeFilters = Object.values(filters).some(Boolean);
  const topTypes = [...summary.by_type]
    .sort((a, b) => b.count - a.count)
    .slice(0, 4);
  const collection = data?.collection;
  const lastCloudUpdate = collection?.last_success_at;

  return (
    <>
      <main id="inventory">
        <section className="page-heading">
          <div>
            <h1>Resource Inventory</h1>
            <p>Browse and filter resources by cloud, type, and region.</p>
          </div>
          <div className="refresh-controls">
            <span className="refresh-status" aria-live="polite">
              <span
                className={loading ? "refresh-dot loading" : "refresh-dot"}
              />
              {lastCloudUpdate
                ? `${collection.running ? "Syncing clouds… · Last update" : "Inventory updated"}: ${time(lastCloudUpdate)}`
                : loading
                  ? "Checking status…"
                  : collection?.status === "error"
                    ? "Cloud sync failed"
                    : "Waiting for cloud sync"}
            </span>
          </div>
        </section>

        <div className="cloud-tabs" aria-label="Filter by cloud">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              aria-pressed={provider === tab.id}
              className={provider === tab.id ? "active" : ""}
              onClick={() => changeCloud(tab.id)}
            >
              {tab.id === "all" ? <Layers3 size={16} /> : <Cloud size={16} />}
              {tab.name}
              {tab.id === "gcp" && <span className="tab-label">GCP</span>}
            </button>
          ))}
        </div>

        <Notification
          message={error && `Failed to load resources. ${error}`}
          type="error"
        />
        <section className="metrics" aria-label="Inventory summary">
          <Metric
            label="Total resources"
            value={summary.total}
            icon={Box}
            caption="In the saved inventory"
            loading={initialLoading}
          />
          <Metric
            label="Resource types"
            value={summary.by_type.length}
            icon={Layers3}
            caption="Registered types"
            loading={initialLoading}
          />
          <Metric
            label="Regions"
            value={summary.by_region.filter((row) => row.value).length}
            icon={Globe2}
            caption="With registered resources"
            loading={initialLoading}
          />
          <Metric
            label="Accounts and projects"
            value={summary.by_account.filter((row) => row.value).length}
            icon={Cloud}
            caption="In this selection"
            loading={initialLoading}
          />
        </section>

        <section className="resource-section" aria-busy={loading}>
          <div className="section-heading">
            <div className="section-title">
              <h2>Resources</h2>
              {visible && (
                <span className="count-badge">{number(data.page.total)}</span>
              )}
            </div>
            <span className="table-hint">
              <Database size={13} /> Saved inventory
            </span>
          </div>
          <div className="filters">
            <SlidersHorizontal size={17} className="filter-icon" />
            <Filter
              label="Resource type"
              value={filters.resource_type}
              options={options.by_type}
              format={typeName}
              onChange={(value) => changeFilter("resource_type", value)}
            />
            <Filter
              label="Region"
              value={filters.region}
              options={options.by_region}
              onChange={(value) => changeFilter("region", value)}
            />
            <Filter
              label="Account / project"
              value={filters.scope_id}
              options={options.by_account}
              onChange={(value) => changeFilter("scope_id", value)}
            />
            {activeFilters && (
              <button
                className="text-button"
                onClick={() => {
                  setFilters(emptyFilters);
                  setOffset(0);
                }}
              >
                Clear filters
              </button>
            )}
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Resource</th>
                  <th>Cloud</th>
                  <th>Type</th>
                  <th>Region</th>
                  <th>Status</th>
                  <th>
                    <span className="sr-only">Details</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {initialLoading
                  ? Array.from({ length: 5 }, (_, index) => (
                      <tr key={index} className="skeleton-row">
                        <td colSpan="6">
                          <div className="skeleton" />
                        </td>
                      </tr>
                    ))
                  : visible &&
                    data.page.items.map((resource) => (
                      <tr key={resource.id}>
                        <td>
                          <div className="resource-cell">
                            <span className="resource-icon">
                              <Server size={17} />
                            </span>
                            <div>
                              <button
                                className="resource-name"
                                onClick={() => setSelected(resource)}
                              >
                                {resource.name ||
                                  typeName(resource.resource_type)}
                              </button>
                              <div
                                className="resource-id mono"
                                title={resource.resource_id}
                              >
                                {resource.resource_id}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td>
                          <span className="provider-badge">
                            {resource.provider.toUpperCase()}
                          </span>
                        </td>
                        <td>{typeName(resource.resource_type)}</td>
                        <td className="mono region">
                          {resource.region || "No region"}
                        </td>
                        <td>
                          {resource.status ? (
                            <span className="status">
                              <span />
                              {resource.status}
                            </span>
                          ) : (
                            <span className="muted">No status</span>
                          )}
                        </td>
                        <td>
                          <button
                            className="icon-button"
                            aria-label={`View details for ${resource.name || resource.resource_id}`}
                            onClick={() => setSelected(resource)}
                          >
                            <ArrowUpRight size={17} />
                          </button>
                        </td>
                      </tr>
                    ))}
              </tbody>
            </table>
          </div>
          {!loading && !error && data?.page.items.length === 0 && (
            <div className="empty-state">
              <Cloud size={32} />
              <h3>No resources</h3>
              <p>
                {provider === "gcp" && !activeFilters
                  ? "No Google Cloud resources are registered."
                  : "No results match the selected filters."}
              </p>
            </div>
          )}
          <div className="table-footer">
            <span>
              {visible && data.page.total
                ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, data.page.total)} of ${number(data.page.total)} resources`
                : initialLoading
                  ? "Loading inventory…"
                  : "0 resources shown"}
            </span>
            <div className="pagination">
              <button
                aria-label="Previous page"
                disabled={!visible || offset === 0}
                onClick={() =>
                  setOffset((value) => Math.max(0, value - PAGE_SIZE))
                }
              >
                <ChevronLeft size={16} />
              </button>
              <span>Page {Math.floor(offset / PAGE_SIZE) + 1}</span>
              <button
                aria-label="Next page"
                disabled={
                  !visible || offset + PAGE_SIZE >= (data?.page.total || 0)
                }
                onClick={() => setOffset((value) => value + PAGE_SIZE)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        </section>

        <section className="bottom-grid">
          <article className="distribution">
            <div className="section-heading">
              <h3>Distribution by type</h3>
              <ArrowDownRight size={18} />
            </div>
            {topTypes.length ? (
              topTypes.map((row) => (
                <div className="distribution-row" key={row.value}>
                  <span>{typeName(row.value)}</span>
                  <div className="bar-track">
                    <div
                      style={{
                        width: `${(row.count / summary.total) * 100}%`,
                      }}
                    />
                  </div>
                  <strong>{row.count}</strong>
                </div>
              ))
            ) : (
              <p className="muted">
                {loading ? "Loading distribution…" : "No data to display."}
              </p>
            )}
          </article>
          <article className="inventory-note">
            <span className="note-icon">
              <Database size={20} />
            </span>
            <h3>Data updates</h3>
            <p>
              Configured connections are checked automatically, and the
              inventory is saved to PostgreSQL every minute.
            </p>
            <span>
              Last successful sync:{" "}
              {lastCloudUpdate ? date(lastCloudUpdate) : "—"}
            </span>
          </article>
        </section>
        <footer className="page-footer">
          <span>
            VANGUARD <span className="footer-divider">/</span> INVENTORY
          </span>
        </footer>
      </main>{" "}
      {selected && (
        <ResourceDetails
          resource={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
