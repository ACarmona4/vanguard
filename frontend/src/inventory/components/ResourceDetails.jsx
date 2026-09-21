import { useEffect, useRef } from "react";
import { Box, X } from "lucide-react";
import { date } from "../../shared/utils/format";
import { typeName } from "../resourceNames";

export default function ResourceDetails({ resource, onClose }) {
  const dialog = useRef(null);
  useEffect(() => {
    dialog.current.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      aria-labelledby="detail-title"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
    >
      <section className="detail-panel">
        <div className="section-heading">
          <span className="eyebrow">DETALLE DEL RECURSO</span>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Cerrar detalle"
          >
            <X size={20} />
          </button>
        </div>
        <div className="detail-icon">
          <Box size={28} />
        </div>
        <h2 id="detail-title">
          {resource.name || typeName(resource.resource_type)}
        </h2>
        <p className="mono detail-id">{resource.resource_id}</p>
        <dl>
          {[
            ["Nube", resource.provider.toUpperCase()],
            ["Tipo", typeName(resource.resource_type)],
            ["Cuenta / proyecto", resource.scope_id],
            ["Región", resource.region],
            ["Zona", resource.zone],
            ["Estado", resource.status],
            ["Primera detección", date(resource.first_seen_at)],
            ["Última detección", date(resource.last_seen_at)],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value || "Sin información"}</dd>
            </div>
          ))}
        </dl>
        <h3>Atributos</h3>
        {Object.keys(resource.attributes).length ? (
          <pre>{JSON.stringify(resource.attributes, null, 2)}</pre>
        ) : (
          <p className="muted">Este recurso no tiene atributos registrados.</p>
        )}
      </section>
    </dialog>
  );
}
