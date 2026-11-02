import test from "node:test";
import assert from "node:assert/strict";
import { money } from "../src/costs/format.js";

test("USD consolidation keeps cents for both small and large cloud costs", () => {
  assert.equal(money(123.45, "USD"), "$123.45");
  assert.equal(money(0.12, "USD"), "$0.12");
  assert.equal(money(-12.34, "USD"), "-$12.34");
});

test("original COP amounts remain distinguishable in reconciliation", () => {
  assert.match(money(3182.99, "COP"), /COP/);
  assert.match(money(3182.99, "COP"), /3,182\.99/);
});
