import {
  AlertTriangle,
  ArrowRight,
  CloudCog,
  KeyRound,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";

export default function CloudAccessRequired({ reason, connections, onRetry }) {
  const unavailable = connections.filter(
    (connection) => connection.status === "error",
  );
  const verificationFailed = reason === "verification-error";
  const credentialsFailed = reason === "credentials-error";

  return (
    <main className="cloud-access-page">
      <section className="cloud-access-card" role="status">
        <div className="cloud-access-icon">
          {verificationFailed ? (
            <AlertTriangle size={28} />
          ) : credentialsFailed ? (
            <ShieldAlert size={28} />
          ) : (
            <CloudCog size={28} />
          )}
        </div>
        <span className="eyebrow">SETUP REQUIRED</span>
        <h1>
          {verificationFailed
            ? "We could not verify your connections"
            : credentialsFailed
              ? "Cloud credentials are unavailable"
              : "Connect a cloud account to get started"}
        </h1>
        <p>
          {verificationFailed
            ? "The application could not retrieve the connection status. Make sure the API and PostgreSQL are available."
            : credentialsFailed
              ? "All configured connections have errors. Update the credentials to regain access to the dashboard and inventory."
              : "Before using the dashboard, inventory, and utilization, configure at least one AWS account or Google Cloud project."}
        </p>

        {credentialsFailed && unavailable.length > 0 && (
          <div className="unavailable-connections">
            {unavailable.map((connection) => (
              <div key={connection.id}>
                <span>{connection.provider.toUpperCase()}</span>
                <strong>{connection.name}</strong>
                <small>
                  {connection.last_error ||
                    "The connection could not be authenticated."}
                </small>
              </div>
            ))}
          </div>
        )}

        <div className="cloud-access-actions">
          <a className="button primary" href="#accounts">
            <KeyRound size={15} />
            {credentialsFailed ? "Update credentials" : "Configure connections"}
            <ArrowRight size={14} />
          </a>
          {verificationFailed && (
            <button className="button" type="button" onClick={onRetry}>
              <RefreshCw size={14} /> Retry
            </button>
          )}
        </div>
      </section>
    </main>
  );
}
