let csrfToken = "";
let organizationId = "";

export function setSessionContext(value, organization) {
  csrfToken = value || "";
    organizationId = organization || "";
}

export async function apiRequest(path, options = {}) {
  let response;
  const method = options.method || "GET";
  const requestToken = csrfToken;
  const requestOrganization = organizationId;
  try {
    response = await fetch(`/api/${path}`, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(organizationId ? { "X-Organization-ID": organizationId } : {}),
        ...(method !== "GET" && csrfToken
          ? { "X-CSRF-Token": csrfToken }
          : {}),
        ...options.headers,
      },
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("Unable to connect. Please try again.");
  }
  if (response.status === 401 && requestToken === csrfToken && requestOrganization === organizationId) {
    window.dispatchEvent(new Event("auth-expired"));
  }
  if (!response.ok) {
    let detail = "The operation could not be completed.";
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (Array.isArray(payload.detail)) detail = payload.detail.map((item) => item.msg).filter(Boolean).join(". ") || detail;
    } catch {
      // Keep the generic message for non-JSON failures.
    }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}
