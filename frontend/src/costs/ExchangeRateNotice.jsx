import { money } from "./format";

export default function ExchangeRateNotice({ conversion, compact = false }) {
  if (!conversion) return null;
  const incomplete = conversion.status === "partial";
  const stale = conversion.status === "stale";
  return (
    <aside className={incomplete || stale ? "utilization-notice" : "muted"} role="status" aria-label="USD conversion">
      {incomplete && <p>USD totals are incomplete: some billing amounts have no usable exchange rate. {conversion.unconverted.map((item) => money(item.amount, item.currency)).join("; ")} excluded. They remain in the original billing records.</p>}
      {stale && <p>The latest available TRM is being carried forward for uncovered dates. The daily job will retry automatically.</p>}
      {conversion.last_error && <p>{conversion.last_error}</p>}
      {!conversion.comparison_complete && <p>The previous period has missing exchange rates; percentage comparison is unavailable.</p>}
      {!compact && conversion.original_totals.length > 0 && <details><summary>Original billed amounts</summary><p>{conversion.original_totals.map((item) => money(item.amount, item.currency)).join(" · ")}. Original currencies are retained for reconciliation.</p></details>}
    </aside>
  );
}
