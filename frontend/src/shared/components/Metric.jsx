import { number } from "../utils/format";
export default function Metric({ label, value, icon: Icon, caption, loading }) {
  return (
    <article className="metric">
      <div className="metric-label">
        {label}
        <Icon size={17} />
      </div>
      <div className="metric-value">{loading ? "—" : number(value)}</div>
      <span className="muted">{caption}</span>
    </article>
  );
}
