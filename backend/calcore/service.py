from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from .models import Event, EventException, Freq, Occurrence, Rule, Scope
from .recurrence import expand, first_occurrence, is_occurrence
from .scheduling import Conflict, Slot, WorkingHours, find_conflicts, suggest
from .store import JsonStore

_UNSET: Any = object()
MAX_WINDOW = timedelta(days=3660)
_SERIES_FIELDS = {"title", "start", "duration", "tz", "rule"}
_OCCURRENCE_FIELDS = {"title", "start", "duration"}


class NotFound(KeyError):
    pass


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _require_naive(dt: datetime, name: str) -> None:
    if dt.tzinfo is not None:
        raise ValueError(f"{name} must be a naive wall-clock time in the event's time zone")


def _require_generates(event: Event) -> Event:
    if first_occurrence(event) is None:
        raise ValueError("this recurrence produces no occurrences (check the weekdays and until date)")
    return event


class Calendar:
    """Events, their exceptions, and the edit/cancel operations on them.

    Every operation addressing a specific occurrence takes (event_id,
    original_start), the naive local start the rule generated. For a
    non-recurring event, original_start is simply its start.
    """

    def __init__(self, store: JsonStore | None = None) -> None:
        self.store = store
        self.events: dict[str, Event] = {}
        self.exceptions: dict[str, dict[datetime, EventException]] = {}
        self.working_hours: WorkingHours | None = None
        if store:
            events, exceptions, self.working_hours = store.load()
            for e in events:
                self.events[e.id] = e
                self.exceptions[e.id] = {}
            for x in exceptions:
                self.exceptions.setdefault(x.event_id, {})[x.original_start] = x

    # -- queries -----------------------------------------------------------

    def get(self, event_id: str) -> Event:
        try:
            return self.events[event_id]
        except KeyError:
            raise NotFound(f"no event {event_id!r}") from None

    def occurrences(self, window_start: datetime, window_end: datetime) -> list[Occurrence]:
        if window_start.tzinfo is None or window_end.tzinfo is None:
            raise ValueError("window bounds must be timezone-aware")
        if window_end <= window_start:
            raise ValueError("window end must be after start")
        if window_end - window_start > MAX_WINDOW:
            raise ValueError("window is limited to 10 years")
        if window_start.year < 2 or window_end.year > 9998:
            raise ValueError("window is out of range")
        out = []
        for e in self.events.values():
            out.extend(expand(e, self.exceptions.get(e.id, {}), window_start, window_end))
        out.sort(key=lambda o: (o.start, o.end, o.title))
        return out

    def conflicts(self, window_start: datetime, window_end: datetime) -> list[Conflict]:
        return find_conflicts(self.occurrences(window_start, window_end))

    def suggest(
        self, duration: timedelta, window_start: datetime, window_end: datetime, limit: int = 8
    ) -> tuple[list[Slot], list[Slot]]:
        """Recommended meeting slots and free gaps within working hours."""
        hours = self.working_hours or WorkingHours.default()
        busy = self.occurrences(window_start, window_end)
        return suggest(busy, hours, duration, window_start, window_end, limit=limit)

    # -- mutations ---------------------------------------------------------

    def set_working_hours(self, hours: WorkingHours) -> None:
        with self._transaction():
            self.working_hours = hours

    def add(
        self, title: str, start: datetime, duration: timedelta, tz: str, rule: Rule | None = None
    ) -> Event:
        event = _require_generates(Event(_new_id(), title, start, duration, tz, rule))
        with self._transaction():
            self.events[event.id] = event
            self.exceptions[event.id] = {}
        return event

    def edit(self, event_id: str, original_start: datetime, scope: Scope, **changes: Any) -> Event:
        """Reschedule or modify an occurrence, the series from it onward, or all of it.

        `changes` may hold title, start (new naive local start of *this*
        occurrence), duration, and, for following/all, tz and rule. For
        following/all, the start change is applied to the series as a shift.
        Returns the event that now owns the occurrence.
        """
        scope = Scope(scope)
        event = self.get(event_id)
        self._require_occurrence(event, original_start)
        if "start" in changes:
            _require_naive(changes["start"], "start")
        allowed = _OCCURRENCE_FIELDS if scope is Scope.THIS and event.rule else _SERIES_FIELDS
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"cannot change {sorted(unknown)} with scope {scope.value!r}")

        with self._transaction():
            if event.rule is None or scope is Scope.ALL:
                return self._edit_series(event, original_start, changes)
            if scope is Scope.THIS:
                return self._edit_one(event, original_start, changes)
            if original_start == first_occurrence(event):  # from the first: whole series
                return self._edit_series(event, original_start, changes)
            tail = self._split(event, original_start)
            return self._edit_series(tail, original_start, changes)

    def cancel(self, event_id: str, original_start: datetime, scope: Scope) -> None:
        """Cancel one occurrence, it and everything after, or the whole event."""
        scope = Scope(scope)
        event = self.get(event_id)
        self._require_occurrence(event, original_start)
        with self._transaction():
            if event.rule is None or scope is Scope.ALL or (
                scope is Scope.FOLLOWING and original_start == first_occurrence(event)
            ):
                del self.events[event.id]
                self.exceptions.pop(event.id, None)
            elif scope is Scope.THIS:
                self.exceptions[event.id][original_start] = EventException(
                    event.id, original_start, cancelled=True
                )
            else:
                self._truncate(event, original_start)

    # -- internals ---------------------------------------------------------

    def _require_occurrence(self, event: Event, original_start: datetime) -> None:
        _require_naive(original_start, "original_start")
        if not is_occurrence(event, original_start):
            raise NotFound(f"event {event.id!r} has no occurrence at {original_start.isoformat()}")

    def _current_start(self, event: Event, original_start: datetime) -> datetime:
        exc = self.exceptions.get(event.id, {}).get(original_start)
        return exc.start if exc and exc.start else original_start

    def _edit_one(self, event: Event, original_start: datetime, changes: dict[str, Any]) -> Event:
        excs = self.exceptions[event.id]
        exc = excs.get(original_start) or EventException(event.id, original_start)
        exc = replace(exc, cancelled=False, **changes)
        excs[original_start] = exc
        return event

    def _edit_series(self, event: Event, original_start: datetime, changes: dict[str, Any]) -> Event:
        new_start = changes.pop("start", _UNSET)
        delta = timedelta(0)
        if new_start is not _UNSET:
            delta = new_start - self._current_start(event, original_start)
        old_rule = event.rule
        rule = changes.get("rule", old_rule)
        if (
            rule and old_rule and rule.anchor is None
            and rule.freq is old_rule.freq is Freq.WEEKLY and rule.interval == old_rule.interval
        ):
            rule = replace(rule, anchor=old_rule.anchor)  # same cadence unless asked otherwise
        # A shift that lands on another day moves the whole pattern with it
        # (until, and for weekly the weekdays and cycle anchor), unless the
        # caller supplied a different rule. Monthly simply adopts the new day.
        day_shift = timedelta(days=((event.start + delta).date() - event.start.date()).days)
        if day_shift and rule == old_rule and rule:
            rule = replace(rule, until=rule.until + day_shift if rule.until else None)
            if rule.freq is Freq.WEEKLY:
                rule = replace(
                    rule,
                    byweekday=tuple((d + day_shift.days) % 7 for d in rule.byweekday),
                    anchor=rule.anchor + day_shift if rule.anchor else None,
                )
        changes["rule"] = rule
        updated = _require_generates(replace(event, start=event.start + delta, **changes))
        self.events[event.id] = updated
        # Keep exceptions attached to the same (shifted) occurrences; drop any
        # the new rule no longer generates.
        old = self.exceptions.get(event.id, {})
        kept = {}
        if updated.rule is not None:
            for key, exc in old.items():
                new_key = key + delta
                if is_occurrence(updated, new_key):
                    kept[new_key] = replace(exc, original_start=new_key)
        self.exceptions[event.id] = kept
        return updated

    def _truncate(self, event: Event, split: datetime) -> None:
        """End the series the day before `split`."""
        assert event.rule is not None
        until = split.date() - timedelta(days=1)
        truncated = replace(event, rule=replace(event.rule, until=until))
        if first_occurrence(truncated) is None:
            del self.events[event.id]
            self.exceptions.pop(event.id, None)
            return
        self.events[event.id] = truncated
        self.exceptions[event.id] = {
            k: x for k, x in self.exceptions[event.id].items() if k.date() < split.date()
        }

    def _split(self, event: Event, split: datetime) -> Event:
        """Fork a new series starting at `split`, ending the original before it."""
        tail = replace(event, id=_new_id(), start=split)
        moved = {
            k: replace(x, event_id=tail.id)
            for k, x in self.exceptions[event.id].items()
            if k.date() >= split.date()
        }
        self._truncate(event, split)
        self.events[tail.id] = tail
        self.exceptions[tail.id] = moved
        return tail

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Roll back in-memory state if the block raises; persist if it succeeds."""
        events = dict(self.events)
        exceptions = {k: dict(v) for k, v in self.exceptions.items()}
        hours = self.working_hours
        try:
            yield
        except BaseException:
            self.events, self.exceptions, self.working_hours = events, exceptions, hours
            raise
        self._save()

    def _save(self) -> None:
        if self.store:
            excs = [x for per_event in self.exceptions.values() for x in per_event.values()]
            self.store.save(list(self.events.values()), excs, self.working_hours)
