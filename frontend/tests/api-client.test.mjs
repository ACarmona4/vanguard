import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { apiRequest, setSessionContext } from "../src/api/client.js";

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
afterEach(() => {
  globalThis.fetch = originalFetch;
  globalThis.window = originalWindow;
  setSessionContext(null, null);
});

test("workspace requests bind their organization and keep responses out of browser caches", async () => {
  setSessionContext("csrf-a", "company-a");
  let sent;
  globalThis.fetch = async (_path, options) => { sent = options; return new Response("{}", { status: 200 }); };
  await apiRequest("resources");
  assert.equal(sent.headers["X-Organization-ID"], "company-a");
  assert.equal(sent.headers["X-CSRF-Token"], undefined);
  assert.equal(sent.cache, "no-store");
  await apiRequest("organizations", { method: "POST", body: JSON.stringify({ name: "Acme" }) });
  assert.equal(sent.headers["X-CSRF-Token"], "csrf-a");
});

test("an old request cannot expire the session after switching organizations", async () => {
  let complete;
  let expired = 0;
  globalThis.window = { dispatchEvent: () => { expired += 1; } };
  globalThis.fetch = () => new Promise((resolve) => { complete = resolve; });
  setSessionContext("csrf-a", "company-a");
  const pending = apiRequest("resources");
  setSessionContext("csrf-b", "company-b");
  complete(new Response(JSON.stringify({ detail: "Expired" }), { status: 401 }));
  await assert.rejects(pending, /Expired/);
  assert.equal(expired, 0);
});

test("a current expired session does notify authentication", async () => {
  let expired = 0;
  globalThis.window = { dispatchEvent: () => { expired += 1; } };
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "Expired" }), { status: 401 });
  setSessionContext("csrf-a", "company-a");
  await assert.rejects(apiRequest("resources"), /Expired/);
  assert.equal(expired, 1);
});

test("validation errors produce readable messages", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: [{ msg: "Use a valid email" }] }), { status: 422 });
  await assert.rejects(apiRequest("auth/login"), /Use a valid email/);
});
