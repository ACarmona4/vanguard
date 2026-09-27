import UtilizationWidgets from "../utilization/UtilizationWidgets";
import { useEffect, useState } from "react";
import { ArrowUpRight, Box, Globe2, Layers3 } from "lucide-react";
import { getInventory } from "../inventory/api";
import Metric from "../shared/components/Metric";
import { date } from "../shared/utils/format";

export default function DashboardPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
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
  const status = !collection
    ? "Checking sync status…"
    : !collection.enabled
      ? "Sync paused"
      : collection.status === "error"
        ? "Cloud sync failed. It will retry automatically."
        : collection.status === "partial"
          ? "Partial sync. Some resources could not be retrieved."
          : collection.running
            ? "Syncing clouds…"
            : "Automatic sync active";
  return (
    <main id="dashboard">
      <section className="page-heading">
        <div>
          <h1>Dashboard</h1>
          <p>An overview of your cloud resources.</p>
        </div>
      </section>
      {error && (
        <div className="error" role="alert">
          {error} We will retry automatically.
        </div>
      )}
      <section
        className="metrics dashboard-metrics"
        aria-label="General inventory summary"
      >
        <Metric
          label="Inventoried resources"
          value={summary?.total ?? 0}
          icon={Box}
          caption="Across all your clouds"
          loading={!data}
        />
        <Metric
          label="Resource types"
          value={summary?.by_type.length ?? 0}
          icon={Layers3}
          caption="Registered in the inventory"
          loading={!data}
        />
        <Metric
          label="Regions"
          value={summary?.by_region.filter((row) => row.value).length ?? 0}
          icon={Globe2}
          caption="With registered resources"
          loading={!data}
        />
      </section>
      <section
        className="dashboard-overview"
        aria-label="Inventory and synchronization"
      >
        <div>
          <h2>Your inventory, up to date</h2>
          <p>Explore resources by cloud, account, and region.</p>
          <a className="button" href="#inventory">
            View inventory <ArrowUpRight size={15} />
          </a>
        </div>
        <div className="dashboard-sync" role="status">
          <span>{status}</span>
          <small>
            Last cloud sync:{" "}
            {collection?.last_success_at
              ? date(collection.last_success_at)
              : "Pending"}
          </small>
        </div>
      </section>
      <UtilizationWidgets />
      <p className="dashboard-future">
        Coming soon: costs, recommendations, and automations.
      </p>
    </main>
  );
}
