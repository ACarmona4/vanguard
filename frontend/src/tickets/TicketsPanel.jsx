import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { date } from "../shared/utils/format";
import Notification from "../shared/components/Notification";
import "./tickets.css";

const LABELS = {open: "Open", in_progress: "In progress", closed: "Closed"};
const fromHash = () => new URLSearchParams(window.location.hash.split("?")[1] || "").get("ticket");

function TicketDetail({ id, onUpdated, onClose }) {
  const { user } = useAuth();
  const admin = ["owner", "admin"].includes(user.role);
  const [data, setData] = useState(null);
  const [members, setMembers] = useState([]);
  const [assignee, setAssignee] = useState("");
  const [due, setDue] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    apiRequest(`tickets/${id}?offset=${offset}`, {signal:controller.signal}).then((value) => {
      if (controller.signal.aborted) return;
      setData(value); setAssignee(value.assignee_id || ""); setDue(value.due_date || "");
    }).catch((failure) => {if (!controller.signal.aborted) setError(failure.message);});
    if (admin) apiRequest("tickets/assignees", {signal:controller.signal}).then(setMembers).catch((failure) => {if (!controller.signal.aborted) setError(failure.message);});
    return () => controller.abort();
  }, [id, revision, offset, admin]);
  const editable = data && data.status !== "closed" && (admin || data.assignee_id === user.id) && ["owner","admin","member"].includes(user.role);
  async function submit(event) {
    event.preventDefault();
    const action = event.nativeEvent.submitter?.value;
    if (!action || busy) return;
    setBusy(true); setError("");
    try {
      await apiRequest(`tickets/${id}`, {method:"PUT",body:JSON.stringify({version:data.version,action,note,...(action === "assign" ? {assignee_id:assignee,due_date:due || null} : {})})});
      setNote(""); setRevision((value) => value + 1); onUpdated();
    } catch (failure) {setError(failure.message);}
    finally {setBusy(false);}
  }
  return <article className="ticket-detail">
    <div className="ticket-heading"><h3>{data?.number || "Ticket"}</h3><button className="button" onClick={onClose}>Close details</button></div>
    <Notification message={error} type="error" />
    {!data ? <p role="status">Loading ticket…</p> : <>
      <h3>{data.title}</h3><p>{data.resource_name} · {data.provider.toUpperCase()} · {LABELS[data.status]}{data.outcome ? ` · ${data.outcome}` : ""}</p>
      <details><summary>Recommendation details</summary><p>{data.snapshot.description}</p><ol>{data.snapshot.actions.map((action) => <li key={action}>{action}</li>)}</ol>
        <a href="#recommendations">Review recommendations →</a>
      </details>
      {editable && <form className="ticket-form" onSubmit={submit}>
        {admin && <div className="ticket-assignment"><label>Assignee<select aria-label="Ticket assignee" value={assignee} onChange={(e) => setAssignee(e.target.value)} disabled={busy}><option value="">Select operator</option>{members.map((member) => <option key={member.id} value={member.id}>{member.full_name}</option>)}</select></label><label>Due date<input aria-label="Ticket due date" type="date" value={due} onChange={(e) => setDue(e.target.value)} disabled={busy} /></label></div>}
        <label>Progress or validation note<textarea aria-label="Ticket note" minLength={3} maxLength={2000} required value={note} onChange={(e) => setNote(e.target.value)} placeholder="Record the change, validation result or reason for cancellation." disabled={busy}/></label>
        <div className="ticket-actions">
          {admin && data.status === "open" && data.snapshot.workflow_status === "detected" && <button className="button primary" value="approve" disabled={busy}>Approve recommendation</button>}
          {data.status === "open" && <button className="button" value="start" disabled={busy || data.snapshot.workflow_status !== "approved"}>Start work</button>}
          {data.status === "in_progress" && <button className="button primary" value="close" disabled={busy}>Record implementation & close</button>}
          <button className="button" value="comment" disabled={busy}>Add note</button>
          {admin && <><button className="button" value="assign" disabled={busy || !assignee}>Save assignment</button><button className="button" value="cancel" disabled={busy}>Cancel ticket</button></>}
        </div>
        {data.status === "open" && <p>Approve the recommendation before starting work. Cloud changes are applied outside Vanguard.</p>}
      </form>}
      <h4>Activity</h4><ol className="ticket-history">{data.events.map((entry) => <li key={entry.id}><strong>{entry.event_type.replaceAll("_"," ")} · {entry.actor_name}</strong><small>{date(entry.created_at)}</small><p>{entry.note}</p></li>)}</ol>
      {data.total_events > 20 && <div className="ticket-actions"><button className="button" disabled={!offset} onClick={() => setOffset(offset-20)}>Newer events</button><button className="button" disabled={offset+20 >= data.total_events} onClick={() => setOffset(offset+20)}>Older events</button></div>}
      <button className="button" onClick={() => setRevision((value) => value + 1)}>Refresh ticket & billing</button>
    </>}
  </article>;
}

export default function TicketsPanel() {
  const {user} = useAuth();
  const admin = ["owner","admin"].includes(user.role);
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("all");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState(fromHash);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {const changed = () => setSelected(fromHash()); window.addEventListener("hashchange",changed); return () => window.removeEventListener("hashchange",changed);}, []);
  useEffect(() => {
    const controller = new AbortController(); setError("");
    apiRequest(`tickets?status=${status}&limit=20&offset=${offset}`,{signal:controller.signal}).then((value) => {if (!controller.signal.aborted) setData(value);}).catch((failure) => {if (!controller.signal.aborted) setError(failure.message);});
    return () => controller.abort();
  }, [status,offset,revision]);
  return <section className="tickets-panel" aria-labelledby="tickets-heading">
    <div className="ticket-heading"><div><span className="eyebrow">IMPLEMENTATION TRACKING</span><h2 id="tickets-heading">{admin ? "Team tickets" : "Workspace tickets"}</h2><p>Follow changes from recommendation to implementation.</p></div><button className="button" onClick={() => setRevision((value) => value + 1)}>Refresh</button></div>
    <Notification message={error} type="error" />
    <div className="ticket-tabs" role="group" aria-label="Ticket status">{["all","open","in_progress","closed"].map((value) => <button key={value} aria-pressed={status===value} className={status===value ? "active" : ""} onClick={() => {setStatus(value);setOffset(0);}}>{value === "all" ? "All" : LABELS[value]} <span>{data ? value === "all" ? Object.values(data.counts).reduce((a,b) => a+b,0) : data.counts[value] : "—"}</span></button>)}</div>
    {!data ? <p role="status">Loading tickets…</p> : <>
      {!data.items.length && <p className="ticket-empty">No tickets in this view. Approving a recommendation creates a ticket automatically.</p>}
      <div className="ticket-list">{data.items.map((ticket) => <button key={ticket.id} className="ticket-row" onClick={() => {setSelected(ticket.id);window.location.hash=`accounts?ticket=${ticket.id}`;}}><span><small>{ticket.number} · {LABELS[ticket.status]}</small><strong>{ticket.title}</strong><small>{ticket.resource_name} · {ticket.assignee_name || "Unassigned"}{ticket.due_date ? ` · Due ${ticket.due_date}` : ""}</small></span></button>)}</div>
      {data.total > 20 && <div className="ticket-actions"><button className="button" disabled={!offset} onClick={() => setOffset(offset-20)}>Previous</button><span>{offset+1}–{Math.min(offset+20,data.total)} of {data.total}</span><button className="button" disabled={offset+20>=data.total} onClick={() => setOffset(offset+20)}>Next</button></div>}
    </>}
    {selected && <TicketDetail key={selected} id={selected} onUpdated={() => setRevision((value) => value+1)} onClose={() => {setSelected(null);window.location.hash="accounts";}} />}
  </section>;
}
