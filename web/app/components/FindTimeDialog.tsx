"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type Slot, type Suggestions, type WorkingHours } from "../lib/api";
import { addDays, browserTz, fmtTime } from "../lib/dates";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const LENGTHS = [15, 30, 45, 60, 90, 120];
const RANGES = [
  { days: 7, label: "Next 7 days" },
  { days: 14, label: "Next 14 days" },
  { days: 31, label: "Next 31 days" },
];

interface Props {
  onClose: () => void;
  onPick: (slot: Slot) => void;
}

const dayLabel = (d: Date) => d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });

export function FindTimeDialog({ onClose, onPick }: Props) {
  const [minutes, setMinutes] = useState(30);
  const [rangeDays, setRangeDays] = useState(7);
  const [hours, setHours] = useState<WorkingHours | null>(null);
  const [dirty, setDirty] = useState(false);
  const [showHours, setShowHours] = useState(false);
  const [result, setResult] = useState<Suggestions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const zones = useMemo(() => Intl.supportedValuesOf("timeZone"), []);

  useEffect(() => {
    api.workingHours()
      .then((h) => {
        // Unsaved defaults come back in UTC; the viewer's own zone is the better default.
        setHours(h.saved ? h : { ...h, tz: browserTz() });
        setDirty(!h.saved);
        setShowHours(!h.saved);
      })
      .catch((e) => setError(e.message));
  }, []);

  const editHours = (fn: (h: WorkingHours) => WorkingHours) => {
    setHours((h) => (h ? fn(h) : h));
    setDirty(true);
    setResult(null);
  };

  const setDay = (i: number, value: WorkingHours["days"][number]) =>
    editHours((h) => ({ ...h, days: h.days.map((d, j) => (j === i ? value : d)) }));

  const search = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!hours) return;
    setBusy(true);
    setError(null);
    try {
      if (dirty) {
        setHours(await api.saveWorkingHours(hours));
        setDirty(false);
      }
      const now = new Date();
      setResult(await api.suggestions(minutes, now, addDays(now, rangeDays)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // Group recommended slots and free gaps by the viewer's local day.
  const byDay = useMemo(() => {
    if (!result) return [];
    const groups = new Map<string, { date: Date; slots: Slot[]; gaps: Slot[] }>();
    const bucket = (iso: string) => {
      const d = new Date(iso);
      const key = d.toDateString();
      if (!groups.has(key)) groups.set(key, { date: new Date(d.getFullYear(), d.getMonth(), d.getDate()), slots: [], gaps: [] });
      return groups.get(key)!;
    };
    result.slots.forEach((s) => bucket(s.start).slots.push(s));
    result.gaps.forEach((g) => bucket(g.start).gaps.push(g));
    return [...groups.values()].filter((g) => g.slots.length).sort((a, b) => +a.date - +b.date);
  }, [result]);

  return (
    <div className="backdrop" onMouseDown={onClose}>
      <form className="dialog wide" onMouseDown={(e) => e.stopPropagation()} onSubmit={search}>
        <h2>Find a time</h2>

        <div className="row">
          <label>
            Meeting length
            <select value={minutes} onChange={(e) => { setMinutes(Number(e.target.value)); setResult(null); }}>
              {LENGTHS.map((m) => (
                <option key={m} value={m}>{m < 60 ? `${m} min` : `${m / 60} hr${m > 60 ? "s" : ""}`}</option>
              ))}
            </select>
          </label>
          <label>
            Search
            <select value={rangeDays} onChange={(e) => { setRangeDays(Number(e.target.value)); setResult(null); }}>
              {RANGES.map((r) => <option key={r.days} value={r.days}>{r.label}</option>)}
            </select>
          </label>
          <button type="submit" className="primary" disabled={busy || !hours}>
            {busy ? "Searching…" : "Find times"}
          </button>
        </div>

        {hours && (
          <fieldset className="hours">
            <legend>
              <button type="button" className="link" onClick={() => setShowHours((v) => !v)}>
                Working hours {showHours ? "▾" : "▸"}
              </button>
              {!showHours && <span className="hint"> {summarize(hours)}</span>}
            </legend>
            {showHours && (
              <>
                {hours.days.map((d, i) => (
                  <div className="hours-row" key={WEEKDAYS[i]}>
                    <label className="inline">
                      <input type="checkbox" checked={!!d}
                        onChange={(e) => setDay(i, e.target.checked ? { start: "09:00", end: "17:00" } : null)} />
                      <span className="day-name">{WEEKDAYS[i]}</span>
                    </label>
                    {d ? (
                      <>
                        <input type="time" value={d.start} onChange={(e) => setDay(i, { ...d, start: e.target.value })} />
                        <span className="hint">to</span>
                        <input type="time" value={d.end} onChange={(e) => setDay(i, { ...d, end: e.target.value })} />
                      </>
                    ) : (
                      <span className="hint">Off</span>
                    )}
                  </div>
                ))}
                <label>
                  Time zone
                  <input list="ft-zones" value={hours.tz} onChange={(e) => editHours((h) => ({ ...h, tz: e.target.value }))} />
                  <datalist id="ft-zones">{zones.map((z) => <option key={z} value={z} />)}</datalist>
                </label>
                {dirty && <span className="hint">Saved when you search.</span>}
              </>
            )}
          </fieldset>
        )}

        {error && <p className="error">{error}</p>}

        {result && (
          <div className="results">
            {byDay.length === 0 ? (
              <p className="hint">No {minutes}-minute openings in your working hours. Try a longer range or shorter meeting.</p>
            ) : (
              byDay.map(({ date, slots, gaps }) => (
                <div className="result-day" key={date.toISOString()}>
                  <div className="result-date">{dayLabel(date)}</div>
                  <div className="result-slots">
                    {slots.map((s) => (
                      <button type="button" key={s.start} className="slot" onClick={() => onPick(s)}>
                        {fmtTime(new Date(s.start))} – {fmtTime(new Date(s.end))}
                      </button>
                    ))}
                  </div>
                  <div className="hint">
                    Free {gaps.map((g) => `${fmtTime(new Date(g.start))}–${fmtTime(new Date(g.end))}`).join(", ")}
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        <div className="actions">
          <span className="spacer" />
          <button type="button" onClick={onClose}>Close</button>
        </div>
      </form>
    </div>
  );
}

function summarize(h: WorkingHours): string {
  const on = h.days.map((d, i) => (d ? i : -1)).filter((i) => i >= 0);
  const same = on.every((i) => h.days[i]!.start === h.days[on[0]]!.start && h.days[i]!.end === h.days[on[0]]!.end);
  if (!on.length) return "none set";
  const days = on.length === 5 && on.join() === "0,1,2,3,4" ? "Mon–Fri" : on.map((i) => WEEKDAYS[i]).join(", ");
  return same ? `${days} ${h.days[on[0]]!.start}–${h.days[on[0]]!.end} (${h.tz})` : `${days} (${h.tz})`;
}
