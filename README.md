# Calendar

A small calendar with recurring events and exceptions: a Python core (stdlib `datetime` + `zoneinfo`, no scheduling libraries), a FastAPI JSON API, and a Next.js week-view UI.

```
backend/
  calcore/models.py      Event, Rule, EventException, Occurrence
  calcore/recurrence.py  rule expansion, exception application, DST handling
  calcore/service.py     Calendar: add / edit / cancel with this|following|all scopes
  calcore/scheduling.py  conflict detection, working hours, free-slot suggestions
  calcore/store.py       JSON file persistence (atomic writes)
  api.py                 FastAPI routes
  seed.py                demo data around the current week
  tests/                 pytest + hypothesis suite
web/                     Next.js app (week grid, create/edit dialog, right-click menu,
                         conflicts panel, find-a-time dialog)
```

## Running

Requires Python 3.12+ with [uv](https://docs.astral.sh/uv/), and Node 20+.

```sh
# terminal 1: API on :8000
cd backend
uv sync
uv run python seed.py --reset    # optional demo data (run before starting the API)
uv run uvicorn api:app --port 8000

# terminal 2: UI on :3000 (proxies /api to :8000)
cd web
npm install
npm run dev
```

Open http://localhost:3000.

- **Create:** click **+ New event**, or click an empty slot in the grid to prefill that time (1 hour). Or **drag** down or up on empty grid to select a range in 15-minute steps; releasing opens the dialog prefilled with that range.
- **Resize:** drag an event's **bottom edge** to change its duration, or its **top edge** to change its start while keeping the end. Snaps to 15 minutes with a live preview.
  - Non-recurring events save on release. Recurring ones ask *This event / This and following / All events* at the cursor.
  - Esc mid-drag, or Cancel, puts it back.
  - Edges cut off at midnight have no handle.
- **Edit / reschedule:** click an event. For recurring events, pick *This event*, *This and following*, or *All events*.
- **Cancel:** right-click an event, or use Delete in the edit dialog.
- **Navigate:** use ‹ / › / Today to move between weeks. The grid scrolls vertically through the day.
- **Conflicts:** overlapping events get a red ring and a ⚠. The toolbar pill opens a panel explaining each overlap for the week. Saving an event that clashes shows a heads-up.
- **Find a time:** pick a meeting length and range. Working hours default to Mon-Fri 9-5 in your time zone, can be edited inline, and are saved on search. Click a suggestion to open a prefilled new event.

Data lives in `backend/data/calendar.json` (override with `CALENDAR_DB`). The API loads it at startup, so restart the API after running `seed.py`.

Tests:

```sh
cd backend && uv run pytest                              # ~2s
cd backend && HYPOTHESIS_PROFILE=heavy uv run pytest     # ~35s, 5000 examples per property
cd web && npm test                                       # drag math (node's built-in test runner)
```

## Data model

```
Event(id, title, start, duration, tz, rule=None)
Rule(freq: WEEKLY|MONTHLY, interval=1, byweekday=(), until: date|None, anchor: date|None)
EventException(event_id, original_start, cancelled=False, title=None, start=None, duration=None)
```

- `start` is a **naive wall-clock time in `tz`**. Occurrences are generated in local time and only then made timezone-aware, so a 09:00 series stays at 09:00 across DST changes.
- **WEEKLY** repeats on each `byweekday` (0=Mon..6=Sun) every `interval` weeks. There is no separate "every N days" or plain-weekly mode: "weekly on Monday" is `byweekday=(0,)`, "daily" is all seven days.
  - Interval weeks are 7-day cycles starting at `anchor`, which defaults to the Monday of the start's week.
  - The anchor is managed by the server and stored explicitly so that shifting or splitting a series keeps its cadence exact. It only matters when `interval > 1`.
- **MONTHLY** repeats on `start`'s day of month every `interval` months. **Months without that day are skipped**, so a series starting on the 31st only lands in 31-day months.
- `until` is an **inclusive date** in the event's time zone. There is no `count`.
- Weekdays in the start week that fall before `start` are not generated.
- A rule that generates **no occurrences at all** (e.g. Mondays only, starting Tuesday, until Sunday) is rejected. A "following" cancel that would leave a series empty deletes it.

### Exceptions

There is one exceptions table, keyed by **`(event_id, original_start)`**, the naive local start the rule generated. An exception either cancels that occurrence or overrides some of its fields (`None` = inherit from the series). With one record per occurrence, a cancel and an override can never conflict: whichever was written last is the record.

When expanding a query window:

1. Generate rule occurrences, padding the window by the event's duration so events already in progress at the window start are included.
2. Drop every occurrence that has an exception.
3. Add back non-cancelled overrides whose *new* time overlaps the window, regardless of where the original was. This makes an occurrence moved from next week into this week show up.

An exception whose `original_start` the rule no longer generates (an **orphan**, e.g. after changing weekdays) is ignored during expansion and deleted when the series is edited.

### Edit and cancel scopes

| scope | edit | cancel |
|---|---|---|
| this | writes or updates an exception (title/start/duration only) | writes a cancelled exception |
| following | splits the series: the original gets `until = day before`, a new event starts at this occurrence with the changes applied, and later exceptions move to it | sets `until = day before` and drops later exceptions |
| all | edits the master. A start change is applied as a shift (new time minus this occurrence's current time) to the whole series: start, exception keys, `until`, and for weekly, `byweekday` and the cycle anchor. Moving the Monday occurrence of a MWF series to Tuesday gives TThSa. A caller that sends a *different* rule gets exactly that rule. A monthly series moved to another day adopts the new day-of-month | deletes the event and its exceptions |

Editing or cancelling "following" from the first *generated* occurrence is the same as "all". Non-recurring events ignore scope. Every operation is transactional: if validation fails partway through (e.g. after a split), in-memory state rolls back and nothing is written.

## Time zone assumptions

- **Nonexistent local times** (spring-forward gap) and **ambiguous local times** (fall-back overlap) use zoneinfo's default **`fold=0`**:
  - A gap time is interpreted with the pre-transition offset. In New York, 02:30 on spring-forward day becomes **03:30 EDT**.
  - An ambiguous time resolves to the **first (earlier) instant**. In New York, 01:30 on fall-back day is **01:30 EDT**.
- `duration` is elapsed time, not wall-clock time. A 4-hour event starting at 00:00 on fall-back night ends at 03:00 local.
- Each event keeps its own `tz`. The UI shows everything in the browser's time zone and edits each event's fields in that event's zone.
- `original_start` stays the generated wall-clock time (e.g. 02:30) even when the displayed start shifted (03:30), so exception keys stay stable.

## Other assumptions

- Overlapping events are allowed, laid out side by side, and flagged as conflicts. Touching events (one ends exactly when the next starts) don't conflict. Different occurrences of the same series can conflict with each other.
- Durations must be positive whole minutes, since the store persists minutes. Titles are required.
- Query windows are limited to 10 years, and suggestion windows to 62 days.
- Single-user, single-process. The API holds state in memory and rewrites the JSON file atomically after every mutation. Nothing coordinates multiple API processes.
- In "this" scope, time zone and recurrence can't be changed. Those are series properties, so the dialog disables them.

## Extensions

### Conflict detection

`find_conflicts` sweeps the window's occurrences in start order, keeping only the ones still running. Each new occurrence conflicts with every active one. That's O(n log n + k) for k overlapping pairs, verified against a brute-force all-pairs oracle.

Each conflict reports the overlap interval, its length, and a plain-English explanation. The explanation distinguishes *overlaps*, *entirely contains*, and *at exactly the same time as*. Comparisons use UTC instants, so overlaps across DST are measured in real time.

### Meeting time recommendations

- **Working hours:** one local range per weekday in a chosen time zone (default Mon-Fri 9-5). Persisted alongside events. Ranges can't cross midnight.
- **How suggestions are found:**
  1. Each day's working range is turned into UTC (DST-aware).
  2. Busy occurrences are merged and subtracted, leaving free **gaps**.
  3. The earliest 15-minute-aligned start in each gap that fits the meeting is recommended.

  One pick per gap spreads suggestions across distinct free blocks instead of listing 9:00, 9:15, 9:30. Gaps are also returned, so the UI shows the full "free 9:45-3:00, 4:00-5:00" context. Suggestions never start in the past.
- **Properties checked:** every slot lies within working hours, overlaps no busy time, is aligned, and is the earliest in its gap, and every gap that can fit the meeting gets a slot.

## Testing and bugs found

The core is covered by unit tests plus hypothesis property tests:

- a day-by-day brute-force oracle for rule expansion
- splitting a window doesn't change the results
- shifting a series moves every occurrence by exactly that amount
- a no-op split changes nothing
- random add/edit/cancel sequences across every scope keep the invariants: no orphaned exceptions, no empty series, no duplicate occurrences, "this" edits touch only their target, and persistence round-trips

These found real bugs, each now covered by a regression test:

1. **Moving a weekly series to another weekday with "all" didn't move it.** `byweekday` stayed put, and the shifted exceptions were deleted as orphans. (Reported by Paul.)
2. **Same-zone datetime math is wall-clock math in Python.** `end - start` on fall-back night reported 5h for a 6h event, and the edit dialog would then save the shorter duration. Occurrences now carry UTC instants and convert only for display.
3. **Rules that generate nothing were accepted**, leaving invisible ghost events. "First occurrence" checks also assumed the start date was itself an occurrence.
4. **Shifting an every-other-week series across a week boundary broke its cadence.** Fixed with the explicit cycle anchor.
5. **`until` didn't move with a shifted series**, which dropped occurrences or rejected the edit.
6. **An aware `original_start` was accepted.** It was stored under a key that never matched, producing a duplicate occurrence. An aware override start had its offset silently dropped.
7. **Sub-minute durations were silently truncated** by the store, and blank override titles were accepted.
8. **A query window near the year 9999 crashed the API** with an `OverflowError` (500).

## Completed

- Add, reschedule, and cancel single events.
- Weekly (multi-day, intervals) and monthly recurrence with an inclusive `until`.
- Exceptions: cancel or override a single occurrence, edit or cancel this-and-following (series split), edit or cancel all.
- DST-correct expansion, plus cross-timezone viewing.
- JSON persistence, REST API, and a week-view UI with a create/edit dialog and right-click menu.
- Conflict detection with explanations (panel, in-grid markers, heads-up on save).
- Meeting time recommendations within configurable working hours.
- Drag to create, and drag event edges to resize, in 15-minute steps. The drag math is a pure module with unit tests, and the interactions were checked end to end in Chromium.
- pytest + hypothesis suite (97 tests) covering recurrence math, DST edge cases, exceptions, scopes, rollback, persistence, scheduling, and the API.

## Time spent

_TODO(paul): fill in._

## Next

- **Multi-attendee search:** the same gap math over the union of several calendars' busy time, intersected with each attendee's working hours.
- The save heads-up only checks the visible week. Checking the saved event's own dates would catch a clash after moving it to another week.
- **Performance for large calendars:** index events by active date range (start to until) so a window query skips series that can't intersect it.
- Drag an event's body to move it (including across days), plus a month view. Resizing an event by dragging into another day isn't supported yet; edges stay within their column.
- RFC 5545 import/export (`RRULE`/`EXDATE`/`RECURRENCE-ID` map almost 1:1 onto this model).
