"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Conflict, type EventFields, type Occurrence, type Scope, type Slot } from "../lib/api";
import {
  addDays, browserTz, fmtTime, hueFor, isoDate, isoTime, layoutDay, minutesIntoDay, sameDay, startOfWeek,
} from "../lib/dates";
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
    const close = () => setMenu(null);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && (setMenu(null), setDialog(null), setFindTime(false));
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("click", close); window.removeEventListener("keydown", onKey); };
  }, []);

  const createAt = (day: Date, minutes = 9 * 60) => {
    const snapped = Math.floor(minutes / 30) * 30;
    const d = new Date(day);
    d.setMinutes(snapped);
    setDialog({ mode: "create", date: isoDate(day), time: isoTime(d), tz: browserTz() });
  };

  const onGridClick = (day: Date, e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    const y = e.clientY - e.currentTarget.getBoundingClientRect().top;
    createAt(day, y / PX_PER_MIN);
  };

  const save = async (fields: EventFields, scope: Scope) => {
    const saved = dialog?.mode === "edit" ? await api.edit(dialog.occ, scope, fields) : await api.create(fields);
    const fresh = await reload();
    const mine = fresh.filter((c) => c.a.event_id === saved.id || c.b.event_id === saved.id);
    if (mine.length) {
      const other = (c: Conflict) => (c.a.event_id === saved.id ? c.b : c.a).title;
      const names = [...new Set(mine.map(other))];
      setNotice(`Saved, but it conflicts with ${names.map((n) => `"${n}"`).join(", ")} this week.`);
    }
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
            <div key={day.toISOString()} className="day-col" onClick={(e) => onGridClick(day, e)}>
              {layoutDay(occs, day).map(({ occ, top, height, col, cols, continues }) => {
                const start = new Date(occ.start);
                const end = new Date(occ.end);
                const compact = height < 40;
                const clash = conflicted.has(occKey(occ));
                return (
                  <button
                    key={`${occ.event_id}|${occ.original_start}`}
                    className={`event ${occ.modified ? "modified" : ""} ${compact ? "compact" : ""} ${clash ? "conflict" : ""}`}
                    style={{
                      top: top * PX_PER_MIN,
                      height: Math.max(height * PX_PER_MIN - 2, 18),
                      left: `calc(${(col / cols) * 100}% + 2px)`,
                      width: `calc(${100 / cols}% - 4px)`,
                      ["--hue" as string]: hueFor(occ.event_id),
                    }}
                    title={`${occ.title}\n${fmtTime(start)} – ${fmtTime(end)} (${occ.tz})`}
                    onClick={(e) => { e.stopPropagation(); setMenu(null); setDialog({ mode: "edit", occ }); }}
                    onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY, occ }); }}
                  >
                    <span className="ev-title">
                      {clash && <span className="ev-icon" aria-label="conflict">⚠</span>}
                      {occ.recurring && <span className="ev-icon" aria-label="recurring">↻</span>}
                      {occ.title}
                    </span>
                    <span className="ev-time">
                      {continues ? "… " : ""}{fmtTime(start)}{compact ? "" : ` – ${fmtTime(end)}`}
                      {occ.modified && " · edited"}
                    </span>
                  </button>
                );
              })}
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
