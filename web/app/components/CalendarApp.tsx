"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Conflict, type EventFields, type Occurrence, type Scope, type Slot } from "../lib/api";
import {
  addDays, browserTz, fmtTime, hueFor, isoDate, isoTime, layoutDay, minutesIntoDay, sameDay, startOfWeek,
} from "../lib/dates";
import { createRange, minutesToTime, resizeEdge, resizeResult } from "../lib/drag";
import { EventDialog, type DialogState } from "./EventDialog";
import { FindTimeDialog } from "./FindTimeDialog";

const HOUR_PX = 48;
const PX_PER_MIN = HOUR_PX / 60;

const occKey = (o: { event_id: string; original_start: string }) => `${o.event_id}|${o.original_start}`;

interface Menu {
  x: number;
  y: number;
  occ: Occurrence;
}

type Drag =
  | { kind: "create"; day: Date; col: HTMLElement; anchor: number; start: number; end: number; moved: boolean }
  | {
      kind: "resize"; occ: Occurrence; day: Date; col: HTMLElement; edge: "top" | "bottom";
      orig: { top: number; bottom: number }; top: number; bottom: number; moved: boolean;
    };

/** A finished resize of a recurring event, waiting for the user to choose a scope. */
interface ScopePick {
  x: number;
  y: number;
  occ: Occurrence;
  start: string;
  duration: number;
}

const gridMinutes = (col: HTMLElement, clientY: number) => (clientY - col.getBoundingClientRect().top) / PX_PER_MIN;

export default function CalendarApp() {
  const [weekStart, setWeekStart] = useState(() => startOfWeek(new Date()));
  const [occs, setOccs] = useState<Occurrence[]>([]);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [showConflicts, setShowConflicts] = useState(false);
  const [findTime, setFindTime] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [menu, setMenu] = useState<Menu | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [tz, setTz] = useState("");
  const [drag, setDrag] = useState<Drag | null>(null);
  const [scopePick, setScopePick] = useState<ScopePick | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const suppressClick = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);

  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)), [weekStart]);

  const reload = useCallback(async (): Promise<Conflict[]> => {
    try {
      const end = addDays(weekStart, 7);
      const [o, c] = await Promise.all([api.occurrences(weekStart, end), api.conflicts(weekStart, end)]);
      setOccs(o);
      setConflicts(c);
      setError(null);
      return c;
    } catch (e) {
      setError(`Could not load events: ${e instanceof Error ? e.message : e}. Is the API running on :8000?`);
      return [];
    }
  }, [weekStart]);

  const conflicted = useMemo(() => {
    const keys = new Set<string>();
    conflicts.forEach((c) => { keys.add(occKey(c.a)); keys.add(occKey(c.b)); });
    return keys;
  }, [conflicts]);

  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 7000);
    return () => window.clearTimeout(t);
  }, [notice]);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    setTz(browserTz());
    scroller.current?.scrollTo({ top: 7.5 * HOUR_PX });
    const t = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(t);
  }, []);
  useEffect(() => {
    const close = () => {
      if (suppressClick.current) return; // the click that ends a drag
      setMenu(null);
      setScopePick(null);
      dragRef.current = null;
      setDrag(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setMenu(null);
      setDialog(null);
      setFindTime(false);
      setScopePick(null);
      dragRef.current = null; // also aborts a drag in progress; pointerup then finds nothing
      setDrag(null);
    };
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("click", close); window.removeEventListener("keydown", onKey); };
  }, []);

  const createAt = (day: Date, minutes = 9 * 60, duration?: number) => {
    const d = new Date(day);
    d.setMinutes(Math.floor(minutes / 15) * 15);
    setDialog({ mode: "create", date: isoDate(day), time: isoTime(d), tz: browserTz(), duration });
  };

  const warnConflicts = (fresh: Conflict[], id: string) => {
    const mine = fresh.filter((c) => c.a.event_id === id || c.b.event_id === id);
    if (!mine.length) return;
    const names = [...new Set(mine.map((c) => (c.a.event_id === id ? c.b : c.a).title))];
    setNotice(`Saved, but it conflicts with ${names.map((n) => `"${n}"`).join(", ")} this week.`);
  };

  const save = async (fields: EventFields, scope: Scope) => {
    const saved = dialog?.mode === "edit" ? await api.edit(dialog.occ, scope, fields) : await api.create(fields);
    warnConflicts(await reload(), saved.id);
  };

  // -- dragging ------------------------------------------------------------
  // Listeners live on window for the life of one drag so the pointer can leave
  // the element (or the column) without losing it; state mirrors into a ref so
  // they always see the latest values.

  const updateDrag = (next: Drag | null) => {
    dragRef.current = next;
    setDrag(next);
  };

  const beginDrag = (initial: Drag) => {
    updateDrag(initial);
    const move = (e: PointerEvent) => {
      const cur = dragRef.current;
      if (!cur) return;
      const m = gridMinutes(cur.col, e.clientY);
      if (cur.kind === "create") {
        const [start, end] = createRange(cur.anchor, m);
        updateDrag({ ...cur, start, end, moved: cur.moved || end - start > 15 });
      } else {
        const pos = resizeEdge(cur.edge, m, cur.top, cur.bottom);
        const next = cur.edge === "top" ? { ...cur, top: pos } : { ...cur, bottom: pos };
        updateDrag({ ...next, moved: next.top !== cur.orig.top || next.bottom !== cur.orig.bottom });
      }
    };
    const stop = (e: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      if (e.type === "pointercancel") updateDrag(null);
      else finishDrag(e.clientX, e.clientY);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
  };

  const finishDrag = (x: number, y: number) => {
    const d = dragRef.current;
    if (!d) return;
    if (d.kind === "create") {
      updateDrag(null);
      // A click (no drag) gets the default hour; a drag gets exactly its range.
      createAt(d.day, d.start, d.moved ? d.end - d.start : undefined);
      return;
    }
    if (!d.moved) {
      updateDrag(null); // treat as a click on the event
      return;
    }
    suppressClick.current = true;
    window.setTimeout(() => { suppressClick.current = false; }, 0);
    const { start, duration } = resizeResult(d.occ.local_start, d.occ.duration, d.orig, d);
    if (d.occ.recurring) setScopePick({ x, y, occ: d.occ, start, duration }); // keep the preview up
    else commitResize(d.occ, "this", start, duration);
  };

  const commitResize = async (occ: Occurrence, scope: Scope, start: string, duration: number) => {
    setScopePick(null);
    try {
      const saved = await api.edit(occ, scope, { title: occ.title, start, duration, tz: occ.tz, rule: occ.rule });
      warnConflicts(await reload(), saved.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      updateDrag(null);
    }
  };

  const onColumnPointerDown = (day: Date, e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || e.target !== e.currentTarget || scopePick) return;
    e.preventDefault();
    const col = e.currentTarget;
    const anchor = Math.floor(gridMinutes(col, e.clientY) / 15) * 15;
    beginDrag({ kind: "create", day, col, anchor, start: anchor, end: anchor + 15, moved: false });
  };

  const onHandlePointerDown = (
    occ: Occurrence, day: Date, edge: "top" | "bottom", top: number, bottom: number, e: React.PointerEvent,
  ) => {
    if (e.button !== 0 || scopePick) return;
    e.preventDefault();
    e.stopPropagation();
    const col = (e.currentTarget as HTMLElement).closest(".day-col") as HTMLElement;
    beginDrag({ kind: "resize", occ, day, col, edge, orig: { top, bottom }, top, bottom, moved: false });
  };

  const pickSlot = (slot: Slot) => {
    setFindTime(false);
    const [date, time] = [slot.local_start.slice(0, 10), slot.local_start.slice(11, 16)];
    setWeekStart(startOfWeek(new Date(slot.start)));
    setDialog({ mode: "create", date, time, tz: slot.tz, duration: slot.minutes });
  };

  const openConflict = (c: Conflict) => {
    const occ = occs.find((o) => occKey(o) === occKey(c.b));
    if (occ) setDialog({ mode: "edit", occ });
  };

  const remove = async (occ: Occurrence, scope: Scope) => {
    await api.cancel(occ, scope);
    await reload();
  };

  const menuDelete = async (scope: Scope) => {
    if (!menu) return;
    try { await remove(menu.occ, scope); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  const weekLabel = `${days[0].toLocaleDateString([], { month: "short", day: "numeric" })} – ${days[6].toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`;

  return (
    <main className="app">
      <header className="toolbar">
        <h1>Calendar</h1>
        <div className="nav">
          <button onClick={() => setWeekStart(startOfWeek(new Date()))}>Today</button>
          <button aria-label="Previous week" onClick={() => setWeekStart(addDays(weekStart, -7))}>‹</button>
          <button aria-label="Next week" onClick={() => setWeekStart(addDays(weekStart, 7))}>›</button>
          <span className="week-label">{weekLabel}</span>
        </div>
        <span className="tz-note">Times in {tz}</span>
        <button
          className={`conflict-pill ${conflicts.length ? "has" : ""}`}
          onClick={() => setShowConflicts((v) => !v)}
          aria-expanded={showConflicts}
        >
          {conflicts.length ? `⚠ ${conflicts.length} conflict${conflicts.length > 1 ? "s" : ""}` : "No conflicts"}
        </button>
        <button onClick={() => setFindTime(true)}>Find a time</button>
        <button className="primary" onClick={() => createAt(days.find((d) => sameDay(d, now)) ?? days[0])}>
          + New event
        </button>
      </header>

      {error && <div className="banner">{error}</div>}
      {notice && (
        <div className="banner warn" role="status">
          {notice}
          <button className="link" onClick={() => { setShowConflicts(true); setNotice(null); }}>Show conflicts</button>
        </div>
      )}

      {showConflicts && (
        <aside className="conflicts">
          <div className="conflicts-head">
            <strong>Conflicts this week</strong>
            <button className="link" onClick={() => setShowConflicts(false)}>Close</button>
          </div>
          {conflicts.length === 0 ? (
            <p className="hint">Nothing overlaps this week.</p>
          ) : (
            <ul>
              {conflicts.map((c) => {
                const s = new Date(c.start);
                return (
                  <li key={`${occKey(c.a)}~${occKey(c.b)}`}>
                    <button onClick={() => openConflict(c)}>
                      <span className="when">
                        {s.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })} ·{" "}
                        {fmtTime(s)}–{fmtTime(new Date(c.end))}
                      </span>
                      <span>{c.message}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>
      )}

      <div className="grid-head">
        <div className="gutter" />
        {days.map((d) => (
          <div key={d.toISOString()} className={`day-head ${sameDay(d, now) ? "today" : ""}`}>
            <span className="dow">{d.toLocaleDateString([], { weekday: "short" })}</span>
            <span className="dom">{d.getDate()}</span>
          </div>
        ))}
      </div>

      <div className="scroller" ref={scroller}>
        <div className="grid" style={{ height: 24 * HOUR_PX }}>
          <div className="gutter">
            {Array.from({ length: 24 }, (_, h) => (
              <div key={h} className="hour-label" style={{ top: h * HOUR_PX }}>
                {h === 0 ? "" : new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: "numeric" })}
              </div>
            ))}
          </div>
          {days.map((day) => (
            <div
              key={day.toISOString()}
              className={`day-col ${drag ? "dragging" : ""}`}
              onPointerDown={(e) => onColumnPointerDown(day, e)}
            >
              {layoutDay(occs, day).map((block) => {
                const { occ, col, cols, continues, endsHere } = block;
                const resizing = drag?.kind === "resize" && occKey(drag.occ) === occKey(occ) && sameDay(drag.day, day);
                const top = resizing ? drag.top : block.top;
                const height = resizing ? drag.bottom - drag.top : block.height;
                const start = resizing
                  ? new Date(new Date(occ.start).getTime() + (drag.top - drag.orig.top) * 60_000)
                  : new Date(occ.start);
                const end = resizing
                  ? new Date(new Date(occ.end).getTime() + (drag.bottom - drag.orig.bottom) * 60_000)
                  : new Date(occ.end);
                const compact = height < 40;
                const clash = conflicted.has(occKey(occ));
                return (
                  <button
                    key={`${occ.event_id}|${occ.original_start}`}
                    className={`event ${occ.modified ? "modified" : ""} ${compact ? "compact" : ""} ${clash ? "conflict" : ""} ${resizing ? "resizing" : ""}`}
                    style={{
                      top: top * PX_PER_MIN,
                      height: Math.max(height * PX_PER_MIN - 2, 18),
                      left: `calc(${(col / cols) * 100}% + 2px)`,
                      width: `calc(${100 / cols}% - 4px)`,
                      ["--hue" as string]: hueFor(occ.event_id),
                    }}
                    title={`${occ.title}\n${fmtTime(start)} – ${fmtTime(end)} (${occ.tz})`}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (suppressClick.current || scopePick) return;
                      setMenu(null);
                      setDialog({ mode: "edit", occ });
                    }}
                    onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY, occ }); }}
                  >
                    <span className="ev-title">
                      {clash && <span className="ev-icon" aria-label="conflict">⚠</span>}
                      {occ.recurring && <span className="ev-icon" aria-label="recurring">↻</span>}
                      {occ.title}
                    </span>
                    <span className="ev-time">
                      {continues ? "… " : ""}{fmtTime(start)}{compact && !resizing ? "" : ` – ${fmtTime(end)}`}
                      {occ.modified && " · edited"}
                    </span>
                    {!continues && (
                      <span
                        className="handle top"
                        aria-label="Drag to change start"
                        onPointerDown={(e) => onHandlePointerDown(occ, day, "top", block.top, block.top + block.height, e)}
                      />
                    )}
                    {endsHere && (
                      <span
                        className="handle bottom"
                        aria-label="Drag to change end"
                        onPointerDown={(e) => onHandlePointerDown(occ, day, "bottom", block.top, block.top + block.height, e)}
                      />
                    )}
                  </button>
                );
              })}
              {drag?.kind === "create" && sameDay(drag.day, day) && (
                <div className="ghost" style={{ top: drag.start * PX_PER_MIN, height: (drag.end - drag.start) * PX_PER_MIN }}>
                  {fmtTime(new Date(`${isoDate(day)}T${minutesToTime(drag.start)}`))} –{" "}
                  {drag.end >= 24 * 60 ? "12:00 AM" : fmtTime(new Date(`${isoDate(day)}T${minutesToTime(drag.end)}`))}
                </div>
              )}
              {sameDay(day, now) && <div className="now-line" style={{ top: minutesIntoDay(now) * PX_PER_MIN }} />}
            </div>
          ))}
        </div>
      </div>

      {menu && (
        <ul
          className="menu"
          style={{ top: Math.min(menu.y, window.innerHeight - 180), left: Math.min(menu.x, window.innerWidth - 220) }} onClick={(e) => e.stopPropagation()}>
          <li><button onClick={() => { setDialog({ mode: "edit", occ: menu.occ }); setMenu(null); }}>Edit…</button></li>
          {menu.occ.recurring ? (
            <>
              <li><button onClick={() => { menuDelete("this"); setMenu(null); }}>Delete this event</button></li>
              <li><button onClick={() => { menuDelete("following"); setMenu(null); }}>Delete this and following</button></li>
              <li><button className="danger" onClick={() => { menuDelete("all"); setMenu(null); }}>Delete entire series</button></li>
            </>
          ) : (
            <li><button className="danger" onClick={() => { menuDelete("this"); setMenu(null); }}>Delete</button></li>
          )}
        </ul>
      )}

      {scopePick && (
        <ul
          className="menu"
          style={{ top: Math.min(scopePick.y, window.innerHeight - 190), left: Math.min(scopePick.x, window.innerWidth - 220) }}
          onClick={(e) => e.stopPropagation()}
        >
          <li className="menu-label">Apply new time to</li>
          {(["this", "following", "all"] as Scope[]).map((s) => (
            <li key={s}>
              <button onClick={() => commitResize(scopePick.occ, s, scopePick.start, scopePick.duration)}>
                {s === "this" ? "This event" : s === "following" ? "This and following" : "All events"}
              </button>
            </li>
          ))}
          <li><button onClick={() => { setScopePick(null); updateDrag(null); }}>Cancel</button></li>
        </ul>
      )}

      {findTime && <FindTimeDialog onClose={() => setFindTime(false)} onPick={pickSlot} />}

      {dialog && (
        <EventDialog
          key={dialog.mode === "edit" ? `${dialog.occ.event_id}|${dialog.occ.original_start}` : "new"}
          state={dialog}
          onClose={() => setDialog(null)}
          onSave={save}
          onDelete={dialog.mode === "edit" ? (scope) => remove(dialog.occ, scope) : undefined}
        />
      )}
    </main>
  );
}
