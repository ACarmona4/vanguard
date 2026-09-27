import { apiRequest } from "../api/client";

export const listConnections = (signal) =>
  apiRequest("cloud-connections", { signal });

export const createConnection = (payload) =>
  apiRequest("cloud-connections", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateConnection = (id, payload) =>
  apiRequest(`cloud-connections/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });

export const deleteConnection = (id) =>
  apiRequest(`cloud-connections/${id}`, { method: "DELETE" });

export const listDeployments = (signal) =>
  apiRequest("lab-deployments", { signal });

export const deployLab = (connectionId, region) =>
  apiRequest("lab-deployments", {
    method: "POST",
    body: JSON.stringify({ connection_id: connectionId, region: region || null }),
  });

export const destroyLab = (id) =>
  apiRequest(`lab-deployments/${id}`, { method: "DELETE" });
