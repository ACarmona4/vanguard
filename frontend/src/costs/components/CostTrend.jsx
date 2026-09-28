import { costDay, money } from "../format";

export default function CostTrend({ points = [], currency = "USD", compact = false }) {
  const selected = points.filter((point) => point.currency === currency);
  if (!selected.length) {
    return <div className={`cost-empty-chart ${compact ? "compact" : ""}`}>No cost history yet.</div>;
  }

  const width = 760;
  const height = compact ? 170 : 220;
  const values = selected.map((point) => point.amount);
  const maximum = Math.max(...values, 0.01);
  const minimum = Math.min(...values, 0);
  const range = Math.max(maximum - minimum, 0.01);
  const coordinates = selected.map((point, index) => {
    const x = 16 + (index / Math.max(selected.length - 1, 1)) * (width - 32);
    const y = 18 + ((maximum - point.amount) / range) * (height - 48);
    return [x, y, point];
  });
  const line = coordinates.map(([x, y]) => `${x},${y}`).join(" ");
  const area = `16,${height - 30} ${line} ${width - 16},${height - 30}`;

  return (
    <div className={`cost-chart-wrap ${compact ? "compact" : ""}`}>
      <svg className="cost-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Daily cost trend">
        <line x1="16" y1={height - 30} x2={width - 16} y2={height - 30} className="cost-axis" />
        <polygon points={area} className="cost-area" />
        <polyline points={line} className="cost-line" />
        {coordinates.map(([x, y, point]) => (
          <circle key={`${point.date}-${point.currency}`} cx={x} cy={y} r="3" className="cost-point">
            <title>{`${point.date}: ${money(point.amount, point.currency)}`}</title>
          </circle>
        ))}
      </svg>
      <div className="chart-caption">
        <span>{costDay(selected[0].date)}</span>
        <span>Max. {money(maximum, currency)}</span>
        <span>{costDay(selected.at(-1).date)}</span>
      </div>
    </div>
  );
}
