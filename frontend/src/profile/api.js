async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`/api/${path}`, {
      ...options,
      headers: options.body
        ? { "Content-Type": "application/json", ...options.headers }
        : options.headers,
    });
  } catch {
    throw new Error("Sin conexión con la API. Verifica que esté en ejecución.");
  }
  if (!response.ok) {
    let detail = "No fue posible completar la operación.";
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // Keep the safe generic message when the response is not JSON.
    }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

export const listConnections = (signal) =>
  request("cloud-connections", { signal });

export const createConnection = (payload) =>
  request("cloud-connections", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateConnection = (id, payload) =>
  request(`cloud-connections/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });

export const deleteConnection = (id) =>
  request(`cloud-connections/${id}`, { method: "DELETE" });
