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
          <span className="eyebrow">RESOURCE DETAILS</span>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Close details"
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
            ["Cloud", resource.provider.toUpperCase()],
            ["Type", typeName(resource.resource_type)],
            ["Account / project", resource.scope_id],
            ["Region", resource.region],
            ["Zone", resource.zone],
            ["Status", resource.status],
            ["First detected", date(resource.first_seen_at)],
            ["Last detected", date(resource.last_seen_at)],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value || "No information"}</dd>
            </div>
          ))}
        </dl>
        <h3>Attributes</h3>
        {Object.keys(resource.attributes).length ? (
          <pre>{JSON.stringify(resource.attributes, null, 2)}</pre>
        ) : (
          <p className="muted">This resource has no registered attributes.</p>
        )}
      </section>
    </dialog>
  );
}
