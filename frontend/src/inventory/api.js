import { apiRequest } from "../api/client";

export async function getInventory(path, filters, signal) {
  const params = new URLSearchParams(
    Object.entries(filters).filter(
      ([, value]) => value !== "" && value != null,
    ),
  );
  return apiRequest(`${path}?${params}`, { signal });
}
