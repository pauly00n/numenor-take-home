// Run with: npm test  (node's built-in runner; Node strips the TS types)
import assert from "node:assert/strict";
import { test } from "node:test";
import { addMinutesNaive, createRange, minutesToTime, resizeEdge, resizeResult } from "./drag.ts";

test("createRange snaps outward and is at least one slot", () => {
  assert.deepEqual(createRange(600, 600), [600, 615]); // click-sized
  assert.deepEqual(createRange(600, 683), [600, 690]); // drag down to 11:23 -> 11:30
  assert.deepEqual(createRange(600, 547), [540, 615]); // drag up to 09:07 -> 09:00, keeps anchor slot
  assert.deepEqual(createRange(1410, 5000), [1410, 1440]); // clamped at midnight
  assert.deepEqual(createRange(15, -40), [0, 30]);
});

test("resizeEdge snaps to nearest slot and keeps a minimum length", () => {
  assert.equal(resizeEdge("bottom", 787, 720, 780), 780); // 13:07 -> 13:00
  assert.equal(resizeEdge("bottom", 800, 720, 780), 795); // 13:20 -> 13:15
  assert.equal(resizeEdge("bottom", 700, 720, 780), 735); // can't go above top + 15
  assert.equal(resizeEdge("top", 790, 720, 780), 765); // can't pass bottom - 15
  assert.equal(resizeEdge("top", -30, 720, 780), 0);
  assert.equal(resizeEdge("bottom", 2000, 720, 780), 1440);
});

test("addMinutesNaive ignores time zones and crosses days", () => {
  assert.equal(addMinutesNaive("2026-11-01T01:30:00", 60), "2026-11-01T02:30:00"); // no DST meddling
  assert.equal(addMinutesNaive("2026-10-31T23:45", 30), "2026-11-01T00:15:00");
  assert.equal(addMinutesNaive("2026-03-01T00:10:00", -15), "2026-02-28T23:55:00");
});

test("resizeResult: bottom edge changes duration only", () => {
  assert.deepEqual(
    resizeResult("2026-10-29T13:00:00", 90, { top: 780, bottom: 870 }, { top: 780, bottom: 900 }),
    { start: "2026-10-29T13:00:00", duration: 120 },
  );
});

test("resizeResult: top edge moves start and keeps end", () => {
  assert.deepEqual(
    resizeResult("2026-10-29T13:00:00", 90, { top: 780, bottom: 870 }, { top: 750, bottom: 870 }),
    { start: "2026-10-29T12:30:00", duration: 120 },
  );
});

test("resizeResult works in the event's zone even when the grid is in another", () => {
  // Event at 09:00 Tokyo shown at 19:00 the previous day in New York; drag top up 1h.
  assert.deepEqual(
    resizeResult("2026-10-30T09:00:00", 60, { top: 1140, bottom: 1200 }, { top: 1080, bottom: 1200 }),
    { start: "2026-10-30T08:00:00", duration: 120 },
  );
});

test("minutesToTime", () => {
  assert.equal(minutesToTime(0), "00:00");
  assert.equal(minutesToTime(615), "10:15");
});
