// Pure drag math for the week grid, in minutes from local midnight.

export const SNAP = 15;
export const DAY_MIN = 24 * 60;

const clamp = (m: number) => Math.max(0, Math.min(DAY_MIN, m));
export const snapDown = (m: number) => Math.floor(m / SNAP) * SNAP;
export const snapUp = (m: number) => Math.ceil(m / SNAP) * SNAP;
export const snapNearest = (m: number) => Math.round(m / SNAP) * SNAP;

/** Range covered when dragging from the slot at `anchor` to the cursor; always >= one slot. */
export function createRange(anchor: number, cursor: number): [number, number] {
  const c = clamp(cursor);
  const start = Math.min(anchor, snapDown(c));
  const end = Math.min(DAY_MIN, Math.max(anchor + SNAP, snapUp(c)));
  return [start, end];
}

/** New position of the dragged edge, snapped and kept at least one slot from the other edge. */
export function resizeEdge(edge: "top" | "bottom", cursor: number, top: number, bottom: number): number {
  const m = snapNearest(clamp(cursor));
  return edge === "top" ? Math.max(0, Math.min(m, bottom - SNAP)) : Math.min(DAY_MIN, Math.max(m, top + SNAP));
}

/** Add minutes to a naive ISO datetime ("YYYY-MM-DDTHH:MM[:SS]") without any time zone. */
export function addMinutesNaive(iso: string, minutes: number): string {
  const t = Date.parse(`${iso.length === 16 ? `${iso}:00` : iso.slice(0, 19)}Z`);
  return new Date(t + minutes * 60_000).toISOString().slice(0, 19);
}

/**
 * Translate a resized block back to the occurrence's own fields.
 * `localStart`/`duration` describe the occurrence (event time zone); the
 * top/bottom pairs are grid minutes before and after the drag.
 */
export function resizeResult(
  localStart: string,
  duration: number,
  orig: { top: number; bottom: number },
  next: { top: number; bottom: number },
): { start: string; duration: number } {
  const dTop = next.top - orig.top;
  const dBottom = next.bottom - orig.bottom;
  return { start: addMinutesNaive(localStart, dTop), duration: duration + dBottom - dTop };
}

export const minutesToTime = (m: number) =>
  `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
