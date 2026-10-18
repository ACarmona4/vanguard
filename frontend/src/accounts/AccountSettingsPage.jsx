import { useCallback, useEffect, useRef, useState } from "react";
import {
  Cloud,
  CircleCheck,
  FlaskConical,
  KeyRound,
  LoaderCircle,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import {
  createConnection,
  deleteConnection,
  deployLab,
  destroyLab,
  listConnections,
  listDeployments,
  updateConnection,
} from "./api";
import { date } from "../shared/utils/format";
import { useAuth } from "../auth/AuthProvider";
import { apiRequest } from "../api/client";
import Notification from "../shared/components/Notification";

const initialAWS = {
  name: "",
  regions: "us-east-1",
  access_key_id: "",
  secret_access_key: "",
  session_token: "",
};
const initialGCP = {
  name: "",
  project_id: "",
  service_account_json: "",
  credential_filename: "",
};

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
  const auth = useAuth();
  const admin = ["owner", "admin"].includes(auth.user.role);
  const [connections, setConnections] = useState([]);
  const [deployments, setDeployments] = useState([]);
  const [provider, setProvider] = useState("aws");
  const [aws, setAWS] = useState(initialAWS);
  const [gcp, setGCP] = useState(initialGCP);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [pendingDisconnect, setPendingDisconnect] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [pendingLabDestroy, setPendingLabDestroy] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dismissError = useCallback(() => setError(""), []);
  const dismissNotice = useCallback(() => setNotice(""), []);
  const [profile, setProfile] = useState({ full_name: auth.user.full_name, theme: auth.user.theme });
  const [passwords, setPasswords] = useState({ current_password: "", new_password: "" });
  const [labConnection, setLabConnection] = useState("");
  const [labBusy, setLabBusy] = useState(false);
  const gcpFileInput = useRef(null);
  const connectionForm = useRef(null);
  const loadRevision = useRef(0);

  async function load(signal) {
    const revision = ++loadRevision.current;
    try {
      const [savedConnections, savedDeployments] = await Promise.all([
        listConnections(signal),
        listDeployments(signal),
      ]);
      if (signal?.aborted || revision !== loadRevision.current) return;
      setConnections(savedConnections);
      setDeployments(savedDeployments);
      setError("");
    } catch (failure) {
      if (!signal?.aborted && revision === loadRevision.current) setError(failure.message);
    } finally {
      if (!signal?.aborted && revision === loadRevision.current) setLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    setProfile({ full_name: auth.user.full_name, theme: auth.user.theme });
  }, [auth.user.full_name, auth.user.theme]);

  useEffect(() => {
    const active = deployments.some((item) => ["queued", "deploying", "destroying"].includes(item.status));
    if (!active) return undefined;
    const timer = window.setInterval(() => load(), 5000);
    return () => window.clearInterval(timer);
  }, [deployments]);

  async function saveProfile(event) {
    event.preventDefault();
    setError("");
    try {
      await auth.updateProfile(profile);
      setNotice("Profile preferences saved.");
    } catch (failure) {
      setError(failure.message);
    }
  }

  async function changePassword(event) {
    event.preventDefault();
    setError("");
    try {
      await apiRequest("account/password", { method: "PUT", body: JSON.stringify(passwords) });
      setPasswords({ current_password: "", new_password: "" });
      setNotice("Password updated. Other sessions were closed.");
    } catch (failure) {
      setError(failure.message);
    }
  }

  async function createLab() {
    if (!labConnection) return;
    setLabBusy(true);
    setError("");
    try {
      const connection = connections.find((item) => item.id === labConnection);
      const deployment = await deployLab(labConnection, connection?.regions?.[0]);
      loadRevision.current += 1;
      setDeployments((current) => [...current.filter((item) => item.connection_id !== labConnection), deployment]);
      setNotice("Lab deployment started.");
    } catch (failure) {
      setError(failure.message);
    } finally {
      setLabBusy(false);
    }
  }

  async function removeLab(deployment) {
    setLabBusy(true);
    setError("");
    try {
      const updated = await destroyLab(deployment.id);
      loadRevision.current += 1;
      setDeployments((current) => current.map((item) => item.id === updated.id ? updated : item));
      setPendingLabDestroy(null);
      setNotice("Lab deletion started.");
    } catch (failure) {
      setError(failure.message);
    } finally {
      setLabBusy(false);
    }
  }

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
        payload = {
          provider,
          name: gcp.name,
          project_id: gcp.project_id,
          service_account_json: serviceAccount,
        };
      }
      const saved = editingId
        ? await updateConnection(editingId, payload)
        : await createConnection(payload);
      loadRevision.current += 1;
      setConnections((current) =>
        editingId
          ? current.map((item) => (item.id === editingId ? saved : item))
          : [...current, saved],
      );
      setAWS(initialAWS);
      setGCP(initialGCP);
      if (gcpFileInput.current) gcpFileInput.current.value = "";
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
    if (deletingId) return;
    setDeletingId(connection.id);
    setError("");
    setNotice("");
    try {
      await deleteConnection(connection.id);
      loadRevision.current += 1;
      setConnections((current) =>
        current.filter((item) => item.id !== connection.id),
      );
      setDeployments((current) => current.filter((item) => item.connection_id !== connection.id));
      setPendingDisconnect(null);
      if (labConnection === connection.id) setLabConnection("");
      if (editingId === connection.id) cancelEdit();
      setNotice(`${connection.name} was disconnected.`);
      setError("");
      window.dispatchEvent(new Event("cloud-connections-changed"));
    } catch (failure) {
      setError(failure.message);
    } finally {
      setDeletingId(null);
    }
  }

  function readServiceAccount(event) {
    const file = event.target.files?.[0];
    if (!file || gcp.credential_filename) return;
    if (file.size > 128 * 1024) { setError("Credential files must be smaller than 128 KB."); return; }
    const reader = new FileReader();
    reader.onload = () =>
      setGCP((current) => ({
        ...current,
        service_account_json: String(reader.result),
        credential_filename: file.name,
      }));
    reader.readAsText(file);
  }

  function clearServiceAccount(event) {
    event.preventDefault();
    event.stopPropagation();
    setGCP((current) => ({
      ...current,
      service_account_json: "",
      credential_filename: "",
    }));
    if (gcpFileInput.current) gcpFileInput.current.value = "";
  }

  function edit(connection) {
    setPendingDisconnect(null);
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
    connectionForm.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function viewLab(connectionId) {
    setPendingDisconnect(null);
    const deployment = deployments.find((item) => item.connection_id === connectionId);
    const element = document.getElementById(`lab-${deployment?.id}`);
    element?.scrollIntoView({ behavior: "smooth", block: "center" });
    element?.focus({ preventScroll: true });
  }

  function cancelEdit() {
    setEditingId(null);
    setAWS(initialAWS);
    setGCP(initialGCP);
    if (gcpFileInput.current) gcpFileInput.current.value = "";
    setError("");
  }

  return (
    <main id="accounts">
      <section className="page-heading">
        <div>
          <span className="eyebrow">{admin ? "ADMINISTRATOR" : "ACCOUNT"}</span>
          <h1>Profile and cloud connections</h1>
          <p>
            Connect AWS accounts and Google Cloud projects to the inventory.
          </p>
        </div>
      </section>

      <Notification message={error} type="error" onDismiss={dismissError} />
      <Notification message={notice} onDismiss={dismissNotice} />

      <section className="account-center-grid">
        <form className="connection-form" onSubmit={saveProfile}>
          <div className="form-heading"><span className="form-icon"><UserRound size={18} /></span><div><h2>Personal profile</h2><p>Your name and display preference.</p></div></div>
          <div className="form-fields">
            <label>Full name<input required minLength="2" value={profile.full_name} onChange={(event) => setProfile({ ...profile, full_name: event.target.value })} /></label>
            <label>Appearance<select value={profile.theme} onChange={(event) => setProfile({ ...profile, theme: event.target.value })}><option value="light">Light</option><option value="dark">Dark</option></select></label>
          </div>
          <button className="button primary full account-submit">Save profile</button>
        </form>
        <form className="connection-form" onSubmit={changePassword}>
          <div className="form-heading"><span className="form-icon"><KeyRound size={18} /></span><div><h2>Security</h2><p>Changing your password closes other sessions.</p></div></div>
          <div className="form-fields">
            <label>Current password<input required type="password" autoComplete="current-password" value={passwords.current_password} onChange={(event) => setPasswords({ ...passwords, current_password: event.target.value })} /></label>
            <label>New password<input required minLength="10" type="password" autoComplete="new-password" value={passwords.new_password} onChange={(event) => setPasswords({ ...passwords, new_password: event.target.value })} /></label>
          </div>
          <button className="button full account-submit">Update password</button>
        </form>
      </section>

      <section className="accounts-grid">
        <div className="connection-panel">
          <div className="section-heading">
            <div>
              <h2>Configured connections</h2>
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
              {connections.map((connection) => {
                const linkedLab = deployments.find((item) => item.connection_id === connection.id);
                const blockingLab = linkedLab && linkedLab.cleanup_required !== false;
                return (
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
                      {pendingDisconnect === connection.id && <div className="connection-confirm" role="group" aria-label={`Disconnect ${connection.name}`}>
                        <strong>{blockingLab ? "This connection is used by a lab." : `Disconnect ${connection.name}?`}</strong>
                        <p>{blockingLab
                          ? connection.status === "error" ? "Update the credentials, then delete the lab before disconnecting." : "Delete the lab before disconnecting."
                          : `This removes the connection and its inventory from Vanguard. Cloud resources stay in ${connection.provider.toUpperCase()}.`}</p>
                        {!blockingLab && linkedLab && <p>The failed lab record will also be removed.</p>}
                        <Notification message={error} type="error" />
                        <div className="connection-confirm-actions">
                          {blockingLab ? <>
                            <button type="button" className="button" onClick={() => viewLab(connection.id)}>View lab</button>
                            {connection.status === "error" && <button type="button" className="button" onClick={() => edit(connection)}>Update credentials</button>}
                          </> : <button type="button" className="button danger" disabled={!!deletingId} onClick={() => remove(connection)}>{deletingId === connection.id ? "Disconnecting…" : "Disconnect"}</button>}
                          <button type="button" className="button" disabled={!!deletingId} onClick={() => { setPendingDisconnect(null); setError(""); }}>Cancel</button>
                        </div>
                      </div>}
                    </div>
                    {admin && <div className="connection-actions">
                      <button
                        className="icon-button"
                        aria-label={`Update ${connection.name}`}
                        disabled={!!deletingId}
                        onClick={() => edit(connection)}
                      >
                        <Pencil size={15} />
                      </button>
                      <button
                        className="icon-button danger"
                        aria-label={`Disconnect ${connection.name}`}
                        disabled={!!deletingId}
                        onClick={() => { setPendingDisconnect(connection.id); setPendingLabDestroy(null); setError(""); setNotice(""); }}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>}
                  </article>
                );
              })}
            </div>
          )}
        </div>

        {admin && <form ref={connectionForm} className="connection-form" onSubmit={submit}>
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
              <label className={`file-field ${gcp.credential_filename ? "loaded" : ""}`}>
                GCP credential JSON file
                {gcp.credential_filename ? (
                  <span className="file-loaded">
                    <CircleCheck size={16} />
                    <span className="file-loaded-name">{gcp.credential_filename}</span>
                    <button
                      type="button"
                      className="file-remove"
                      aria-label={`Remove ${gcp.credential_filename}`}
                      title="Remove file"
                      onClick={clearServiceAccount}
                    >
                      <X size={15} />
                    </button>
                  </span>
                ) : (
                  <span className="file-button"><Upload size={15} />Select JSON</span>
                )}
                <input
                  ref={gcpFileInput}
                  required={!gcp.service_account_json}
                  type="file"
                  accept="application/json,.json"
                  onChange={readServiceAccount}
                  disabled={Boolean(gcp.credential_filename)}
                />
                <small>
                  {gcp.service_account_json
                    ? "File uploaded."
                    : "Upload a credentials JSON file."}
                </small>
              </label>
            </div>
          )}

          <div className="security-note">
            <ShieldCheck size={17} />
            <span>
              <strong>Read-only access.</strong> Use temporary credentials where possible.
            </span>
          </div>
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
        </form>}
      </section>

      {admin && <section className="lab-panel">
        <div className="section-heading">
          <div><h2>Temporary lab</h2><p className="muted">Deploy or destroy a temporary environment.</p></div>
          <FlaskConical size={20} />
        </div>
        <div className="lab-controls">
          <select value={labConnection} onChange={(event) => setLabConnection(event.target.value)}>
            <option value="">Select a cloud connection</option>
            {connections.filter((connection) => !deployments.some((item) => item.connection_id === connection.id)).map((connection) => <option key={connection.id} value={connection.id}>{connection.name} · {connection.provider.toUpperCase()}</option>)}
          </select>
          <button className="button primary" disabled={!labConnection || labBusy} onClick={createLab}>Deploy lab</button>
        </div>
        <p className="security-note"><ShieldCheck size={17} /><span>Cloud charges apply while the lab is running.</span></p>
        <div className="connection-list">
          {deployments.map((deployment) => {
            const connection = connections.find((item) => item.id === deployment.connection_id);
            return <article className="connection-card" id={`lab-${deployment.id}`} tabIndex={-1} key={deployment.id}>
              <div className={`provider-mark ${deployment.provider}`}>{deployment.provider.toUpperCase()}</div>
              <div className="connection-info"><div className="connection-title"><strong>{connection?.name || "Cloud lab"}</strong><span className={`connection-status ${deployment.status}`}>{deployment.status}</span></div><small>{deployment.region} · Updated {date(deployment.updated_at)}</small>{deployment.last_error && <small className="connection-error">{deployment.last_error}</small>}
                {pendingLabDestroy === deployment.id && <div className="connection-confirm" role="group" aria-label="Delete lab">
                  <strong>Delete this lab and its cloud resources?</strong>
                  <p>All resources created by this lab will be deleted.</p>
                  <Notification message={error} type="error" />
                  <div className="connection-confirm-actions">
                    <button type="button" className="button danger" disabled={labBusy} onClick={() => removeLab(deployment)}>{labBusy ? "Deleting…" : "Delete lab"}</button>
                    <button type="button" className="button" disabled={labBusy} onClick={() => setPendingLabDestroy(null)}>Cancel</button>
                  </div>
                </div>}
              </div>
              <button className="button" disabled={labBusy || ["queued", "deploying", "destroying"].includes(deployment.status)} onClick={() => { setPendingDisconnect(null); setPendingLabDestroy(deployment.id); }}>Delete lab</button>
            </article>;
          })}
        </div>
      </section>}
    </main>
  );
}
