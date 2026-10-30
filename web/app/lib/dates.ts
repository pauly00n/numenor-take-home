import type { Occurrence } from "./api";

export const browserTz = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

export function startOfWeek(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); // Monday
  return x;
}

export function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

export const sameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

const pad = (n: number) => String(n).padStart(2, "0");
export const isoDate = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const isoTime = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

/** Monday=0 weekday of a YYYY-MM-DD string. */
export function weekdayOf(date: string): number {
  const [y, m, d] = date.split("-").map(Number);
  return (new Date(y, m - 1, d).getDay() + 6) % 7;
}

export const minutesIntoDay = (d: Date) => d.getHours() * 60 + d.getMinutes();

export const fmtTime = (d: Date) => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

export interface Block {
  occ: Occurrence;
  top: number; // minutes from midnight
  height: number; // minutes
  col: number;
  cols: number;
  continues: boolean; // started on a previous day
}

/** Clip occurrences to one day and lay out overlapping ones side by side. */
export function layoutDay(occs: Occurrence[], day: Date): Block[] {
  const dayStart = day.getTime();
  const dayEnd = addDays(day, 1).getTime();
  const segs = occs
    .map((occ) => {
      const s = new Date(occ.start).getTime();
      const e = new Date(occ.end).getTime();
      if (s >= dayEnd || e <= dayStart) return null;
      const cs = Math.max(s, dayStart);
      const ce = Math.min(e, dayEnd);
      const top = cs === dayStart ? 0 : minutesIntoDay(new Date(cs));
      const bottom = ce === dayEnd ? 24 * 60 : minutesIntoDay(new Date(ce));
      return { occ, top, height: bottom - top, s: cs, e: ce, continues: s < dayStart };
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)
    .sort((a, b) => a.s - b.s || b.e - a.e);

  // Greedy column assignment within clusters of transitively overlapping blocks.
  const out: Block[] = [];
  let cluster: (Block & { e: number })[] = [];
  let clusterEnd = -Infinity;
  const flush = () => {
    const cols = Math.max(0, ...cluster.map((b) => b.col)) + 1;
    cluster.forEach((b) => out.push({ ...b, cols }));
    cluster = [];
  };
  for (const seg of segs) {
    if (seg.s >= clusterEnd && cluster.length) flush();
    const used = new Set(cluster.filter((b) => b.e > seg.s).map((b) => b.col));
    let col = 0;
    while (used.has(col)) col++;
    cluster.push({ occ: seg.occ, top: seg.top, height: seg.height, col, cols: 1, continues: seg.continues, e: seg.e });
    clusterEnd = Math.max(clusterEnd, seg.e);
  }
  if (cluster.length) flush();
  return out;
}

/** Stable pleasant hue per series. */
export function hueFor(id: string): number {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h % 360;
}
