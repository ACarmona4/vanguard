import { useEffect, useState } from "react";
import { Building2, Plus, Trash2 } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { apiRequest } from "../api/client";
import Notification from "../shared/components/Notification";

const roleLabels = { owner: "Owner", admin: "Administrator", member: "Member", viewer: "Viewer" };

export default function OrganizationPage() {
  const auth = useAuth();
  const admin = ["owner", "admin"].includes(auth.user.role);
  const [organizations, setOrganizations] = useState([]);
  const [members, setMembers] = useState([]);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function load(signal) {
    const [workspaces, people] = await Promise.all([
      apiRequest("organizations", { signal }),
      admin ? apiRequest("organizations/members", { signal }) : Promise.resolve([]),
    ]);
    if (!signal?.aborted) { setOrganizations(workspaces); setMembers(people); }
  }
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).catch((failure) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [auth.user.organization_id, admin]);

  async function perform(action, success) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); await load(); setNotice(success); }
    catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  async function switchWorkspace(id) {
    setBusy(true); setError("");
    try { await auth.switchOrganization(id); }
    catch (failure) { setError(failure.message); setBusy(false); }
  }
  return <main>
    <section className="page-heading"><div><h1>Organization</h1><p>{auth.user.organization_name} · {roleLabels[auth.user.role]}</p></div><Building2 size={24} /></section>
    <Notification message={error} type="error" /><Notification message={notice} />
    <section className="organization-grid">
      <article className="dashboard-panel">
        <h2>Your organizations</h2>
        <p className="organization-description">Each organization has its own connections, inventory, costs and recommendations.</p>
        <div className="organization-list">{organizations.map((organization) => <div key={organization.id}>
          <div><strong>{organization.name}</strong><small>{roleLabels[organization.role]}</small></div>
          <button className="button" disabled={busy || organization.id === auth.user.organization_id} onClick={() => switchWorkspace(organization.id)}>{organization.id === auth.user.organization_id ? "Active" : "Switch"}</button>
        </div>)}</div>
        <form className="organization-form" onSubmit={(event) => { event.preventDefault(); perform(async () => { await apiRequest("organizations", { method: "POST", body: JSON.stringify({ name }) }); setName(""); }, "Organization created. Select Switch to open it."); }}>
          <label>New organization<input aria-label="New organization name" required minLength={2} maxLength={120} value={name} onChange={(event) => setName(event.target.value)} /></label>
          <button className="button" disabled={busy}><Plus size={14} /> Create organization</button>
        </form>
      </article>
      <article className="dashboard-panel">
        <h2>Members and access</h2>
        <p className="organization-description">Admins manage connections, members and approvals. Members can review recommendations and record approved changes. Viewers have read-only access.</p>
        {admin ? <>
          <div className="organization-list">{members.map((member) => <div key={member.user_id}>
            <div><strong>{member.full_name}</strong><small>{member.email}</small></div>
            {member.role === "owner" || member.user_id === auth.user.id ? <span>{roleLabels[member.role]}</span> : <>
              <select aria-label={`Role for ${member.email}`} value={member.role} disabled={busy} onChange={(event) => perform(() => apiRequest(`organizations/members/${member.user_id}`, { method: "PUT", body: JSON.stringify({ role: event.target.value }) }), "Role updated. Existing sessions in this organization were closed.")}>
                <option value="admin">Administrator</option><option value="member">Member</option><option value="viewer">Viewer</option>
              </select>
              <button className="icon-button danger" aria-label={`Remove ${member.email}`} disabled={busy} onClick={() => { if (window.confirm(`Remove ${member.email} from this organization?`)) perform(() => apiRequest(`organizations/members/${member.user_id}`, { method: "DELETE" }), "Member removed."); }}><Trash2 size={15} /></button>
            </>}
          </div>)}</div>
          <form className="organization-form" onSubmit={(event) => { event.preventDefault(); perform(async () => { await apiRequest("organizations/members", { method: "POST", body: JSON.stringify({ email, role }) }); setEmail(""); }, "Member added."); }}>
            <label>Registered user's email<input aria-label="Member email" type="email" required maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} /></label>
            <label>Role<select aria-label="New member role" value={role} onChange={(event) => setRole(event.target.value)}><option value="viewer">Viewer</option><option value="member">Member</option><option value="admin">Administrator</option></select></label>
            <button className="button primary" disabled={busy}><Plus size={14} /> Add member</button>
          </form>
        </> : <p className="organization-description">Contact an organization administrator to manage members or connections.</p>}
      </article>
    </section>
  </main>;
}
