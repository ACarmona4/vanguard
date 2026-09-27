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
    throw new Error("Unable to connect to the API. Make sure it is running.");
  }
  if (!response.ok) {
    let detail = "The operation could not be completed.";
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
