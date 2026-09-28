export function money(amount, currency = "USD") {
  const value = Number(amount) || 0;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: Math.abs(value) < 10 ? 2 : 0,
  }).format(value);
}

export function costDay(value) {
  return value
    ? new Intl.DateTimeFormat("en-US", { dateStyle: "medium" }).format(
        new Date(`${value}T00:00:00`),
      )
    : "—";
}
