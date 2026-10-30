"use client";

import { useMemo, useState } from "react";
import type { EventFields, Freq, Occurrence, Rule, Scope } from "../lib/api";
import { weekdayOf } from "../lib/dates";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export type DialogState =
  | { mode: "create"; date: string; time: string; tz: string; duration?: number }
  | { mode: "edit"; occ: Occurrence };

interface Props {
  state: DialogState;
  onClose: () => void;
  onSave: (fields: EventFields, scope: Scope) => Promise<void>;
  onDelete?: (scope: Scope) => Promise<void>;
}

export function EventDialog({ state, onClose, onSave, onDelete }: Props) {
  const occ = state.mode === "edit" ? state.occ : null;
  const init = occ
    ? { date: occ.local_start.slice(0, 10), time: occ.local_start.slice(11, 16), tz: occ.tz }
    : { date: state.mode === "create" ? state.date : "", time: state.mode === "create" ? state.time : "", tz: state.mode === "create" ? state.tz : "" };

  const [title, setTitle] = useState(occ?.title ?? "");
  const [date, setDate] = useState(init.date);
  const [time, setTime] = useState(init.time);
  const [duration, setDuration] = useState(occ?.duration ?? (state.mode === "create" ? state.duration : undefined) ?? 60);
  const [tz, setTz] = useState(init.tz);
  const [repeat, setRepeat] = useState<"none" | Freq>(occ?.rule?.freq ?? "none");
  const [interval, setRuleInterval] = useState(occ?.rule?.interval ?? 1);
  const [byweekday, setByweekday] = useState<number[]>(
    occ?.rule?.byweekday.length ? occ.rule.byweekday : [weekdayOf(init.date || "2026-01-05")],
  );
  const [until, setUntil] = useState(occ?.rule?.until ?? "");
  const [scope, setScope] = useState<Scope>("this");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const zones = useMemo(() => Intl.supportedValuesOf("timeZone"), []);
  const recurring = !!occ?.recurring;
  const seriesLocked = recurring && scope === "this";

  // Moving the date moves the weekly pattern with it (Mon -> Tue shifts every
  // selected weekday by one), matching how the server shifts a series.
  const changeDate = (next: string) => {
    if (date && next && repeat === "WEEKLY" && !seriesLocked) {
      const shift = (weekdayOf(next) - weekdayOf(date) + 7) % 7;
      if (shift) setByweekday((cur) => cur.map((d) => (d + shift) % 7).sort());
    }
    setDate(next);
  };

  const toggleDay = (d: number) =>
    setByweekday((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d].sort()));

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const rule: Rule | null =
      repeat === "none"
        ? null
        : { freq: repeat, interval, byweekday: repeat === "WEEKLY" ? byweekday : [], until: until || null };
    run(() => onSave({ title, start: `${date}T${time}`, duration, tz, rule }, scope));
  };

  return (
    <div className="backdrop" onMouseDown={onClose}>
      <form className="dialog" onMouseDown={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>{occ ? "Edit event" : "New event"}</h2>

        {recurring && (
          <fieldset className="scope">
            <legend>Apply to</legend>
            {(["this", "following", "all"] as Scope[]).map((s) => (
              <label key={s}>
                <input type="radio" name="scope" checked={scope === s} onChange={() => setScope(s)} />
                {s === "this" ? "This event" : s === "following" ? "This and following" : "All events"}
              </label>
            ))}
          </fieldset>
        )}

        <label>
          Title
          <input autoFocus required value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <div className="row">
          <label>
            Date
            <input type="date" required value={date} onChange={(e) => changeDate(e.target.value)} />
          </label>
          <label>
            Start
            <input type="time" required value={time} onChange={(e) => setTime(e.target.value)} />
          </label>
          <label>
            Minutes
            <input type="number" min={5} step={5} required value={duration}
              onChange={(e) => setDuration(Number(e.target.value))} />
          </label>
        </div>
        <label>
          Time zone
          <input list="zones" required value={tz} disabled={seriesLocked} onChange={(e) => setTz(e.target.value)} />
          <datalist id="zones">{zones.map((z) => <option key={z} value={z} />)}</datalist>
        </label>

        <fieldset className="repeat" disabled={seriesLocked}>
          <legend>Repeat{seriesLocked && <span className="hint"> (edit the series to change)</span>}</legend>
          <div className="row">
            <select value={repeat} onChange={(e) => setRepeat(e.target.value as "none" | Freq)}>
              <option value="none">Does not repeat</option>
              <option value="WEEKLY">Weekly</option>
              <option value="MONTHLY">Monthly (same day)</option>
            </select>
            {repeat !== "none" && (
              <label className="inline">
                every
                <input type="number" min={1} className="narrow" value={interval}
                  onChange={(e) => setRuleInterval(Number(e.target.value))} />
                {repeat === "WEEKLY" ? "week(s)" : "month(s)"}
              </label>
            )}
          </div>
          {repeat === "WEEKLY" && (
            <div className="weekdays">
              {WEEKDAYS.map((name, i) => (
                <button type="button" key={name} className={byweekday.includes(i) ? "on" : ""}
                  onClick={() => toggleDay(i)}>
                  {name[0]}
                </button>
              ))}
            </div>
          )}
          {repeat !== "none" && (
            <label className="inline">
              until
              <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
              <span className="hint">{until ? "(inclusive)" : "(forever)"}</span>
            </label>
          )}
        </fieldset>

        {error && <p className="error">{error}</p>}

        <div className="actions">
          {occ && onDelete && (
            <button type="button" className="danger" disabled={busy} onClick={() => run(() => onDelete(scope))}>
              Delete
            </button>
          )}
          <span className="spacer" />
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary" disabled={busy}>Save</button>
        </div>
      </form>
    </div>
  );
}
