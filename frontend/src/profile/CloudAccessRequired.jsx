import {
  AlertTriangle,
  ArrowRight,
  CloudCog,
  KeyRound,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";

export default function CloudAccessRequired({ reason, connections, onRetry }) {
  const unavailable = connections.filter((connection) => connection.status === "error");
  const verificationFailed = reason === "verification-error";
  const credentialsFailed = reason === "credentials-error";

  return (
    <main className="cloud-access-page">
      <section className="cloud-access-card" role="status">
        <div className="cloud-access-icon">
          {verificationFailed ? <AlertTriangle size={28} /> : credentialsFailed ? <ShieldAlert size={28} /> : <CloudCog size={28} />}
        </div>
        <span className="eyebrow">CONFIGURACIÓN REQUERIDA</span>
        <h1>
          {verificationFailed
            ? "No pudimos verificar tus conexiones"
            : credentialsFailed
              ? "Las credenciales cloud no están disponibles"
              : "Conecta una cuenta cloud para comenzar"}
        </h1>
        <p>
          {verificationFailed
            ? "La aplicación no pudo consultar el estado de las conexiones. Verifica que la API y PostgreSQL estén disponibles."
            : credentialsFailed
              ? "Todas las conexiones configuradas presentan errores. Actualiza las credenciales para volver a acceder al dashboard y al inventario."
              : "Antes de usar el dashboard, el inventario y las futuras funciones, configura al menos una cuenta de AWS o un proyecto de Google Cloud."}
        </p>

        {credentialsFailed && unavailable.length > 0 && (
          <div className="unavailable-connections">
            {unavailable.map((connection) => (
              <div key={connection.id}>
                <span>{connection.provider.toUpperCase()}</span>
                <strong>{connection.name}</strong>
                <small>{connection.last_error || "No fue posible autenticar la conexión."}</small>
              </div>
            ))}
          </div>
        )}

        <div className="cloud-access-actions">
          <a className="button primary" href="#profile">
            <KeyRound size={15} />
            {credentialsFailed ? "Actualizar credenciales" : "Configurar conexiones"}
            <ArrowRight size={14} />
          </a>
          {verificationFailed && (
            <button className="button" type="button" onClick={onRetry}>
              <RefreshCw size={14} /> Reintentar
            </button>
          )}
        </div>
        <small className="cloud-access-help">
          Las credenciales se validan antes de guardarse y nunca se muestran nuevamente.
        </small>
      </section>
    </main>
  );
}
