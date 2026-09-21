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
    ? "Consultando sincronización…"
    : !collection.enabled
      ? "Sincronización pausada"
      : collection.status === "error"
        ? "No se pudieron sincronizar las nubes. Se reintentará automáticamente."
        : collection.status === "partial"
          ? "Sincronización parcial. Algunos recursos no pudieron consultarse."
          : collection.running
            ? "Sincronizando nubes…"
            : "Sincronización automática activa";
  return (
    <main id="dashboard">
      <section className="page-heading">
        <div>
          <h1>Dashboard</h1>
          <p>Una mirada general a tus recursos en la nube.</p>
        </div>
      </section>
      {error && (
        <div className="error" role="alert">
          {error} Volveremos a consultar automáticamente.
        </div>
      )}
      <section
        className="metrics dashboard-metrics"
        aria-label="Resumen general del inventario"
      >
        <Metric
          label="Recursos inventariados"
          value={summary?.total ?? 0}
          icon={Box}
          caption="En todas tus nubes"
          loading={!data}
        />
        <Metric
          label="Tipos de recurso"
          value={summary?.by_type.length ?? 0}
          icon={Layers3}
          caption="Registrados en el inventario"
          loading={!data}
        />
        <Metric
          label="Regiones"
          value={summary?.by_region.filter((row) => row.value).length ?? 0}
          icon={Globe2}
          caption="Con recursos registrados"
          loading={!data}
        />
      </section>
      <section
        className="dashboard-overview"
        aria-label="Inventario y sincronización"
      >
        <div>
          <h2>Tu inventario, al día</h2>
          <p>Explora los recursos por nube, cuenta y región.</p>
          <a className="button" href="#inventory">
            Ver inventario <ArrowUpRight size={15} />
          </a>
        </div>
        <div className="dashboard-sync" role="status">
          <span>{status}</span>
          <small>
            Última sincronización cloud:{" "}
            {collection?.last_success_at
              ? date(collection.last_success_at)
              : "Pendiente"}
          </small>
        </div>
      </section>
      <UtilizationWidgets />
      <p className="dashboard-future">
        Próximamente: costos, recomendaciones y automatizaciones.
      </p>
    </main>
  );
}
