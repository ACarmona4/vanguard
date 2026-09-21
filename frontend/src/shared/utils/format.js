export const number = (value) => new Intl.NumberFormat("es-CO").format(value);
export const date = (value) =>
  value
    ? new Intl.DateTimeFormat("es-CO", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "Sin información";

export const time = (value) =>
  value
    ? new Intl.DateTimeFormat("es-CO", {
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
      }).format(new Date(value))
    : "Sin información";
