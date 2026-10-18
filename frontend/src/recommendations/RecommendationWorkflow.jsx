import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { apiRequest } from "../api/client";
import Notification from "../shared/components/Notification";
import { date } from "../shared/utils/format";
import { metricValue } from "../utilization/api";

import { EVENT_LABELS, IMPACT_LABELS, PRIORITY_LABELS, STATE_LABELS } from "./labels";

export default function RecommendationWorkflow({ finding, onUpdated }) {
  const { user } = useAuth();
  const operator = ["owner", "admin", "member"].includes(user.role);
  const admin = ["owner", "admin"].includes(user.role);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [relevance, setRelevance] = useState(finding.business_relevance);
  const [impact, setImpact] = useState(finding.economic_impact);
  const request = useRef(null);
  useEffect(() => () => request.current?.abort(), []);
  const current = detail?.finding || finding;
  const closed = ["executed", "rejected"].includes(current.workflow_status);
  const criteriaChanged = relevance !== current.business_relevance || impact !== current.economic_impact;

  async function load(offset = 0) {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError("");
    try {
      const result = await apiRequest(`recommendations/${finding.id}?limit=20&offset=${offset}`, { signal: controller.signal });
      if (!controller.signal.aborted) {
        setDetail(result);
        setRelevance(result.finding.business_relevance);
        setImpact(result.finding.economic_impact);
      }
    } catch (failure) { if (!controller.signal.aborted) setError(failure.message); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }

  async function submit(event) {
    event.preventDefault();
    const action = event.nativeEvent.submitter?.value;
    if (!action || busy) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError("");
    const priority = action === "priority";
    const payload = { version: current.version, note, ...(priority ? { business_relevance: relevance, economic_impact: impact } : { action }) };
    try {
      await apiRequest(`recommendations/${finding.id}/${priority ? "priority" : "decisions"}`, {
        method: priority ? "PUT" : "POST", body: JSON.stringify(payload), signal: controller.signal,
      });
      if (!controller.signal.aborted) onUpdated(priority ? "Priority updated." : "Decision saved.");
    } catch (failure) { if (!controller.signal.aborted) setError(failure.message); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }

  return <details className="recommendation-workflow" onToggle={(event) => { if (event.target === event.currentTarget && event.currentTarget.open && !detail && !busy) load(); }}>
    <summary>Review and history</summary>
    <Notification message={error} type="error" />
    <p>Cost impact: <strong>{IMPACT_LABELS[current.economic_impact]}</strong> · Business relevance: <strong>{PRIORITY_LABELS[current.business_relevance]}</strong></p>
    <button className="button" type="button" disabled={busy} onClick={() => load(detail?.offset || 0)}>Refresh details</button>
    {operator && current.workflow_status === "approved" && <p>Record the result after applying the approved change.</p>}
    {operator && detail && !closed && <form className="recommendation-workflow-form" onSubmit={submit}>
      <div className="recommendation-criteria">
        <label>Business relevance<select aria-label="Business relevance" value={relevance} onChange={(event) => setRelevance(event.target.value)} disabled={busy}>
          {["low", "medium", "high"].map((value) => <option key={value} value={value}>{IMPACT_LABELS[value]}</option>)}
        </select></label>
        <label>Cost impact<select aria-label="Cost impact" value={impact} onChange={(event) => setImpact(event.target.value)} disabled={busy}>
          {["unknown", "low", "medium", "high"].map((value) => <option key={value} value={value}>{IMPACT_LABELS[value]}</option>)}
        </select></label>
      </div>
      <label>Reason or change details<textarea aria-label="Reason or change details" required minLength={3} maxLength={2000} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Explain the decision or add a change ticket." disabled={busy} /></label>
      <div className="recommendation-workflow-actions">
        <button className="button" value="priority" disabled={busy}>Save priority</button>
        {admin && current.workflow_status === "detected" && <button className="button primary" value="approve" disabled={busy || criteriaChanged}>Approve action</button>}
        {admin && <button className="button danger" value="reject" disabled={busy || criteriaChanged}>Reject action</button>}
        {current.workflow_status === "approved" && <button className="button primary" value="execute" disabled={busy || criteriaChanged}>Record execution</button>}
      </div>
      {criteriaChanged && <p>Save the priority changes first.</p>}
      {!admin && current.workflow_status === "detected" && <p>An administrator must approve this action first.</p>}
    </form>}
    {!operator && <p>You have read-only access.</p>}
    {closed && <p>This recommendation is closed.</p>}
    {busy && <p role="status">Updating…</p>}
    {detail && <>
      <ol className="recommendation-timeline">{detail.events.map((event) => <li key={event.id}>
        <strong>{EVENT_LABELS[event.event_type] || event.event_type}</strong> · {event.actor_name} · {date(event.created_at)}
        <p>{event.note}</p>
        <details><summary>Details at this step</summary>
          <p>{event.snapshot.resource_name} · {event.snapshot.cloud_resource_id} · {event.snapshot.title}</p>
          <p>{event.snapshot.description}</p>
          <p>{metricValue(event.snapshot.evidence.value, event.snapshot.evidence.unit)} · Threshold {metricValue(event.snapshot.evidence.threshold, event.snapshot.evidence.unit)} · {event.snapshot.evidence.samples} readings / {event.snapshot.evidence.window}</p>
          <p>{event.snapshot.evidence.explanation}</p>
          <ol>{event.snapshot.actions.map((action) => <li key={action}>{action}</li>)}</ol>
          <p>Status: {STATE_LABELS[event.snapshot.workflow_status]} · Priority: {PRIORITY_LABELS[event.snapshot.priority]}</p>
          <p>Cost impact: {IMPACT_LABELS[event.snapshot.economic_impact]} · Business relevance: {PRIORITY_LABELS[event.snapshot.business_relevance]}</p>
        </details>
      </li>)}</ol>
      {detail.total_events > 20 && <div className="table-footer"><span>{detail.offset + 1}–{Math.min(detail.offset + detail.limit, detail.total_events)} of {detail.total_events} events</span><div className="pagination">
        <button type="button" disabled={busy || detail.offset === 0} onClick={() => load(detail.offset - 20)}>Previous events</button>
        <button type="button" disabled={busy || detail.offset + 20 >= detail.total_events} onClick={() => load(detail.offset + 20)}>Next events</button>
      </div></div>}
    </>}
  </details>;
}
