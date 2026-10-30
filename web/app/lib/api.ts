export type Freq = "WEEKLY" | "MONTHLY";
export type Scope = "this" | "following" | "all";

export interface Rule {
  freq: Freq;
  interval: number;
  byweekday: number[]; // 0=Mon..6=Sun
  until: string | null; // YYYY-MM-DD, inclusive
  anchor?: string | null; // weekly cycle start; server-managed
}

export interface Occurrence {
  event_id: string;
  title: string;
  start: string; // aware ISO
  end: string;
  local_start: string; // naive ISO, in the event's tz
  duration: number; // minutes
  original_start: string; // naive ISO; identifies the occurrence
  tz: string;
  recurring: boolean;
  modified: boolean;
  rule: Rule | null;
}

export interface OccurrenceRef {
  event_id: string;
  original_start: string;
  title: string;
  start: string;
  end: string;
}

export interface Conflict {
  a: OccurrenceRef;
  b: OccurrenceRef;
  start: string; // overlap, UTC ISO
  end: string;
  minutes: number;
  message: string;
}

export interface DayHours {
  start: string; // HH:MM
  end: string;
}

export interface WorkingHours {
  saved?: boolean;
  tz: string;
  days: (DayHours | null)[]; // Mon..Sun
}

export interface Slot {
  start: string; // aware ISO in working-hours tz
  end: string;
  local_start: string; // naive, working-hours tz
  minutes: number;
  tz: string;
}

export interface Suggestions {
  slots: Slot[];
  gaps: Slot[];
  working_hours: WorkingHours;
}

export interface SavedEvent {
  id: string;
}

export interface EventFields {
  title: string;
  start: string; // naive local ISO
  duration: number;
  tz: string;
  rule: Rule | null;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new Error(detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const range = (start: Date, end: Date) =>
  `start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`;

const occPath = (o: Occurrence) =>
  `/api/events/${o.event_id}/occurrences/${encodeURIComponent(o.original_start)}`;

export const api = {
  occurrences: (start: Date, end: Date) => call<Occurrence[]>(`/api/occurrences?${range(start, end)}`),
  conflicts: (start: Date, end: Date) => call<Conflict[]>(`/api/conflicts?${range(start, end)}`),
  workingHours: () => call<WorkingHours>("/api/working-hours"),
  saveWorkingHours: (hours: WorkingHours) =>
    call<WorkingHours>("/api/working-hours", {
      method: "PUT",
      body: JSON.stringify({ tz: hours.tz, days: hours.days }),
    }),
  suggestions: (minutes: number, start: Date, end: Date, limit = 12) =>
    call<Suggestions>(`/api/suggestions?duration=${minutes}&limit=${limit}&${range(start, end)}`),
  create: (fields: EventFields) =>
    call<SavedEvent>("/api/events", { method: "POST", body: JSON.stringify(fields) }),
  edit: (o: Occurrence, scope: Scope, fields: EventFields) => {
    const body =
      scope === "this" && o.recurring
        ? { scope, title: fields.title, start: fields.start, duration: fields.duration }
        : { scope, ...fields };
    return call<SavedEvent>(occPath(o), { method: "PUT", body: JSON.stringify(body) });
  },
  cancel: (o: Occurrence, scope: Scope) =>
    call(`${occPath(o)}?scope=${scope}`, { method: "DELETE" }),
};
