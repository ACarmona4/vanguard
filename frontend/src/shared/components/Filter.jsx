export default function Filter({
  label,
  value,
  options,
  onChange,
  format = (value) => value,
}) {
  return (
    <label className="filter">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Todos</option>
        {options
          .filter((item) => item.value != null)
          .map((item) => (
            <option key={item.value} value={item.value}>
              {format(item.value)}
            </option>
          ))}
      </select>
    </label>
  );
}
