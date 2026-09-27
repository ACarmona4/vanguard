let csrfToken = "";

export function setCsrfToken(value) {
  csrfToken = value || "";
}

export async function apiRequest(path, options = {}) {
  let response;
  const method = options.method || "GET";
  try {
    response = await fetch(`/api/${path}`, {
      credentials: "same-origin",
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" && csrfToken
          ? { "X-CSRF-Token": csrfToken }
          : {}),
        ...options.headers,
      },
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("Unable to connect to the API. Make sure it is running.");
  }
  if (response.status === 401) {
    window.dispatchEvent(new Event("auth-expired"));
  }
  if (!response.ok) {
    let detail = "The operation could not be completed.";
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // Keep the generic message for non-JSON failures.
    }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}
