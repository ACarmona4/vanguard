export const number = (value) => new Intl.NumberFormat("en-US").format(value);
export const date = (value) =>
  value
    ? new Intl.DateTimeFormat("en-US", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "No information";

export const time = (value) =>
  value
    ? new Intl.DateTimeFormat("en-US", {
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
      }).format(new Date(value))
    : "No information";
