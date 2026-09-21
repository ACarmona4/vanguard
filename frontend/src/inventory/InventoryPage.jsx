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

const PAGE_SIZE = 10;
const AUTO_REFRESH_MS = 60_000;
const emptyFilters = { resource_type: "", region: "", scope_id: "" };
const tabs = [
  { id: "all", name: "Todas las nubes" },
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
            <h1>Inventario de Recursos</h1>
            <p>Consulta y filtra los recursos por nube, tipo y región.</p>
          </div>
          <div className="refresh-controls">
            <span className="refresh-status" aria-live="polite">
              <span
                className={loading ? "refresh-dot loading" : "refresh-dot"}
              />
              {lastCloudUpdate
                ? `${collection.running ? "Sincronizando nubes… · Última" : "Inventario actualizado"}: ${time(lastCloudUpdate)}`
                : loading
                  ? "Consultando estado…"
                  : collection?.status === "error"
                    ? "Error al sincronizar las nubes"
                    : "Esperando sincronización cloud"}
            </span>
          </div>
        </section>

        <div className="cloud-tabs" aria-label="Filtrar por nube">
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

        {error && (
          <div className="error" role="alert">
            <strong>Error al cargar los recursos.</strong>
            <p>{error}</p>
            <p>Volveremos a consultar automáticamente en el próximo ciclo.</p>
          </div>
        )}
        <section className="metrics" aria-label="Resumen del inventario">
          <Metric
            label="Recursos totales"
            value={summary.total}
            icon={Box}
            caption="En el inventario guardado"
            loading={initialLoading}
          />
          <Metric
            label="Tipos de recurso"
            value={summary.by_type.length}
            icon={Layers3}
            caption="Tipos registrados"
            loading={initialLoading}
          />
          <Metric
            label="Regiones"
            value={summary.by_region.filter((row) => row.value).length}
            icon={Globe2}
            caption="Con recursos registrados"
            loading={initialLoading}
          />
          <Metric
            label="Cuentas y proyectos"
            value={summary.by_account.filter((row) => row.value).length}
            icon={Cloud}
            caption="En esta selección"
            loading={initialLoading}
          />
        </section>

        <section className="resource-section" aria-busy={loading}>
          <div className="section-heading">
            <div className="section-title">
              <h2>Recursos</h2>
              {visible && (
                <span className="count-badge">{number(data.page.total)}</span>
              )}
            </div>
            <span className="table-hint">
              <Database size={13} /> Inventario guardado
            </span>
          </div>
          <div className="filters">
            <SlidersHorizontal size={17} className="filter-icon" />
            <Filter
              label="Tipo de recurso"
              value={filters.resource_type}
              options={options.by_type}
              format={typeName}
              onChange={(value) => changeFilter("resource_type", value)}
            />
            <Filter
              label="Región"
              value={filters.region}
              options={options.by_region}
              onChange={(value) => changeFilter("region", value)}
            />
            <Filter
              label="Cuenta / proyecto"
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
                Limpiar filtros
              </button>
            )}
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Recurso</th>
                  <th>Nube</th>
                  <th>Tipo</th>
                  <th>Región</th>
                  <th>Estado</th>
                  <th>
                    <span className="sr-only">Detalle</span>
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
                          {resource.region || "Sin región"}
                        </td>
                        <td>
                          {resource.status ? (
                            <span className="status">
                              <span />
                              {resource.status}
                            </span>
                          ) : (
                            <span className="muted">Sin estado</span>
                          )}
                        </td>
                        <td>
                          <button
                            className="icon-button"
                            aria-label={`Ver detalle de ${resource.name || resource.resource_id}`}
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
              <h3>Sin recursos</h3>
              <p>
                {provider === "gcp" && !activeFilters
                  ? "No hay recursos de Google Cloud registrados."
                  : "No hay resultados para los filtros seleccionados."}
              </p>
            </div>
          )}
          <div className="table-footer">
            <span>
              {visible && data.page.total
                ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, data.page.total)} de ${number(data.page.total)} recursos`
                : initialLoading
                  ? "Cargando inventario…"
                  : "0 recursos mostrados"}
            </span>
            <div className="pagination">
              <button
                aria-label="Página anterior"
                disabled={!visible || offset === 0}
                onClick={() =>
                  setOffset((value) => Math.max(0, value - PAGE_SIZE))
                }
              >
                <ChevronLeft size={16} />
              </button>
              <span>Página {Math.floor(offset / PAGE_SIZE) + 1}</span>
              <button
                aria-label="Página siguiente"
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
              <h3>Distribución por tipo</h3>
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
                {loading ? "Cargando distribución…" : "Sin datos para mostrar."}
              </p>
            )}
          </article>
          <article className="inventory-note">
            <span className="note-icon">
              <Database size={20} />
            </span>
            <h3>Actualización de datos</h3>
            <p>
              Las conexiones configuradas se consultan automáticamente y el
              inventario se guarda en PostgreSQL cada minuto.
            </p>
            <span>
              Última sincronización exitosa:{" "}
              {lastCloudUpdate ? date(lastCloudUpdate) : "—"}
            </span>
          </article>
        </section>
        <footer className="page-footer">
          <span>
            VANGUARD <span className="footer-divider">/</span> INVENTARIO
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
