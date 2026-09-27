import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Cloud,
  KeyRound,
  LoaderCircle,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  Upload,
} from "lucide-react";
import {
  createConnection,
  deleteConnection,
  listConnections,
  updateConnection,
} from "./api";
import { date } from "../shared/utils/format";

const initialAWS = {
  name: "",
  regions: "us-east-1",
  access_key_id: "",
  secret_access_key: "",
  session_token: "",
};
const initialGCP = { name: "", project_id: "", service_account_json: "" };

function statusName(status) {
  return (
    {
      ready: "Validated",
      syncing: "Syncing",
      success: "Synced",
      partial: "Partial",
      error: "Error",
    }[status] || status
  );
}

export default function AccountSettingsPage() {
  const [connections, setConnections] = useState([]);
  const [provider, setProvider] = useState("aws");
  const [aws, setAWS] = useState(initialAWS);
  const [gcp, setGCP] = useState(initialGCP);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load(signal) {
    try {
      setConnections(await listConnections(signal));
      setError("");
    } catch (failure) {
      if (failure.name !== "AbortError") setError(failure.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, []);

  async function submit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      let payload;
      if (provider === "aws") {
        payload = {
          provider,
          ...aws,
          regions: aws.regions
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean),
          session_token: aws.session_token || null,
        };
      } else {
        let serviceAccount;
        try {
          serviceAccount = JSON.parse(gcp.service_account_json);
        } catch {
          throw new Error("The GCP JSON file or content is invalid.");
        }
        payload = { provider, ...gcp, service_account_json: serviceAccount };
        delete payload.service_account_json_text;
      }
      const saved = editingId
        ? await updateConnection(editingId, payload)
        : await createConnection(payload);
      setConnections((current) =>
        editingId
          ? current.map((item) => (item.id === editingId ? saved : item))
          : [...current, saved],
      );
      setAWS(initialAWS);
      setGCP(initialGCP);
      setEditingId(null);
      setNotice(`${saved.name} was connected and validated.`);
      window.dispatchEvent(new Event("cloud-connections-changed"));
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  async function remove(connection) {
    if (
      !window.confirm(
        `Disconnect ${connection.name}? Its resources will be removed from the inventory.`,
      )
    )
      return;
    try {
      await deleteConnection(connection.id);
      setConnections((current) =>
        current.filter((item) => item.id !== connection.id),
      );
      setNotice(`${connection.name} was disconnected.`);
      setError("");
      window.dispatchEvent(new Event("cloud-connections-changed"));
    } catch (failure) {
      setError(failure.message);
    }
  }

  function readServiceAccount(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () =>
      setGCP((current) => ({
        ...current,
        service_account_json: String(reader.result),
      }));
    reader.readAsText(file);
  }

  function edit(connection) {
    setEditingId(connection.id);
    setProvider(connection.provider);
    setError("");
    setNotice("");
    if (connection.provider === "aws") {
      setAWS({
        ...initialAWS,
        name: connection.name,
        regions: connection.regions.join(", "),
      });
    } else {
      setGCP({
        ...initialGCP,
        name: connection.name,
        project_id: connection.scope_id,
      });
    }
    document
      .querySelector(".connection-form")
      ?.scrollIntoView({ behavior: "smooth" });
  }

  function cancelEdit() {
    setEditingId(null);
    setAWS(initialAWS);
    setGCP(initialGCP);
    setError("");
  }

  return (
    <main id="accounts">
      <section className="page-heading">
        <div>
          <span className="eyebrow">ADMINISTRATOR</span>
          <h1>Profile and cloud connections</h1>
          <p>
            Connect AWS accounts and Google Cloud projects to the inventory.
          </p>
        </div>
      </section>

      <section className="accounts-grid">
        <div className="connection-panel">
          <div className="section-heading">
            <div>
              <h2>Configured connections</h2>
              <p className="muted">
                Secrets are never displayed in the interface again.
              </p>
            </div>
            <span className="count-badge">{connections.length}</span>
          </div>
          {loading ? (
            <div className="empty-connections">
              <LoaderCircle className="spin" /> Loading connections…
            </div>
          ) : connections.length === 0 ? (
            <div className="empty-connections">
              <Cloud size={22} />
              <strong>No clouds connected yet</strong>
              <span>Add the first one using the form.</span>
            </div>
          ) : (
            <div className="connection-list">
              {connections.map((connection) => (
                <article className="connection-card" key={connection.id}>
                  <div className={`provider-mark ${connection.provider}`}>
                    {connection.provider === "aws" ? "AWS" : "GCP"}
                  </div>
                  <div className="connection-info">
                    <div className="connection-title">
                      <strong>{connection.name}</strong>
                      <span
                        className={`connection-status ${connection.status}`}
                      >
                        {statusName(connection.status)}
                      </span>
                    </div>
                    <span className="mono">{connection.scope_id}</span>
                    <small>{connection.identity}</small>
                    <small>
                      Credential: {connection.credential_hint}
                      {connection.regions.length > 0 &&
                        ` · ${connection.regions.join(", ")}`}
                    </small>
                    {connection.last_synced_at && (
                      <small>
                        Last sync: {date(connection.last_synced_at)}
                      </small>
                    )}
                    {connection.last_error && (
                      <small className="connection-error">
                        {connection.last_error}
                      </small>
                    )}
                  </div>
                  <div className="connection-actions">
                    <button
                      className="icon-button"
                      aria-label={`Update ${connection.name}`}
                      onClick={() => edit(connection)}
                    >
                      <Pencil size={15} />
                    </button>
                    <button
                      className="icon-button danger"
                      aria-label={`Disconnect ${connection.name}`}
                      onClick={() => remove(connection)}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>

        <form className="connection-form" onSubmit={submit}>
          <div className="form-heading">
            <span className="form-icon">
              <Plus size={18} />
            </span>
            <div>
              <h2>{editingId ? "Update connection" : "Add connection"}</h2>
              <p>We will validate access before saving it.</p>
            </div>
          </div>
          <div className="provider-tabs">
            <button
              disabled={Boolean(editingId)}
              type="button"
              className={provider === "aws" ? "active" : ""}
              onClick={() => setProvider("aws")}
            >
              Amazon Web Services
            </button>
            <button
              disabled={Boolean(editingId)}
              type="button"
              className={provider === "gcp" ? "active" : ""}
              onClick={() => setProvider("gcp")}
            >
              Google Cloud
            </button>
          </div>

          {provider === "aws" ? (
            <div className="form-fields">
              <label>
                Connection name
                <input
                  required
                  value={aws.name}
                  onChange={(e) => setAWS({ ...aws, name: e.target.value })}
                  placeholder="AWS Production"
                />
              </label>
              <label>
                Regions
                <input
                  required
                  value={aws.regions}
                  onChange={(e) => setAWS({ ...aws, regions: e.target.value })}
                  placeholder="us-east-1, us-west-2"
                />
                <small>Comma-separated.</small>
              </label>
              <label>
                Access key ID
                <input
                  required
                  autoComplete="off"
                  value={aws.access_key_id}
                  onChange={(e) =>
                    setAWS({ ...aws, access_key_id: e.target.value.trim() })
                  }
                />
              </label>
              <label>
                Secret access key
                <input
                  required
                  type="password"
                  autoComplete="new-password"
                  value={aws.secret_access_key}
                  onChange={(e) =>
                    setAWS({ ...aws, secret_access_key: e.target.value })
                  }
                />
              </label>
              <label>
                Session token <span>(optional)</span>
                <textarea
                  value={aws.session_token}
                  onChange={(e) =>
                    setAWS({ ...aws, session_token: e.target.value.trim() })
                  }
                  rows="3"
                />
                <small>
                  Required for temporary credentials that start with ASIA.
                </small>
              </label>
            </div>
          ) : (
            <div className="form-fields">
              <label>
                Connection name
                <input
                  required
                  value={gcp.name}
                  onChange={(e) => setGCP({ ...gcp, name: e.target.value })}
                  placeholder="GCP Project"
                />
              </label>
              <label>
                Project ID
                <input
                  required
                  value={gcp.project_id}
                  onChange={(e) =>
                    setGCP({ ...gcp, project_id: e.target.value.trim() })
                  }
                  placeholder="my-project-123"
                />
              </label>
              <label className="file-field">
                Service account JSON file
                <span className="file-button">
                  <Upload size={15} /> Select JSON
                </span>
                <input
                  required={!gcp.service_account_json}
                  type="file"
                  accept="application/json,.json"
                  onChange={readServiceAccount}
                />
                <small>
                  {gcp.service_account_json
                    ? "JSON loaded and ready to validate."
                    : "The private key is encrypted before it is saved."}
                </small>
              </label>
            </div>
          )}

          <div className="security-note">
            <ShieldCheck size={17} />
            <span>
              <strong>Read-only access.</strong> Use the minimum policy included
              in the project and temporary credentials whenever possible.
            </span>
          </div>
          {error && (
            <div className="error compact" role="alert">
              {error}
            </div>
          )}
          {notice && (
            <div className="success-message" role="status">
              <CheckCircle2 size={15} /> {notice}
            </div>
          )}
          <div className="form-actions">
            {editingId && (
              <button type="button" className="button" onClick={cancelEdit}>
                Cancel
              </button>
            )}
            <button className="button primary full" disabled={saving}>
              {saving ? (
                <>
                  <LoaderCircle size={15} className="spin" /> Validating…
                </>
              ) : (
                <>
                  <KeyRound size={15} />{" "}
                  {editingId ? "Validate and update" : "Validate and connect"}
                </>
              )}
            </button>
          </div>
        </form>
      </section>
    </main>
  );
}
