from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import Occurrence
from .recurrence import localize

_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


# -- conflicts -----------------------------------------------------------------


@dataclass(frozen=True)
class Conflict:
    """Two occurrences whose times overlap. `start`/`end` bound the overlap (UTC)."""

    a: Occurrence
    b: Occurrence
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    def explain(self) -> str:
        if self.a.start == self.b.start and self.a.end == self.b.end:
            how = "at exactly the same time as"
        elif self.a.start <= self.b.start and self.a.end >= self.b.end:
            how = "entirely contains"
        else:
            how = "overlaps"
        mins = self.minutes
        span = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins} min"
        return f'"{self.a.title}" {how} "{self.b.title}" ({span} of overlap)'


def find_conflicts(occurrences: Iterable[Occurrence]) -> list[Conflict]:
    """Every overlapping pair, ordered by overlap start.

    Sweep in start order, keeping only occurrences still running; each new one
    conflicts with all of them. Touching (one ends as the next starts) is fine.
    """
    ordered = sorted(occurrences, key=lambda o: (o.start, o.end))
    active: list[Occurrence] = []
    out = []
    for occ in ordered:
        active = [a for a in active if a.end > occ.start]
        for a in active:
            out.append(Conflict(a, occ, occ.start, min(a.end, occ.end)))
        active.append(occ)
    out.sort(key=lambda c: (c.start, c.end))
    return out


# -- working hours and free time -------------------------------------------------


@dataclass(frozen=True)
class WorkingHours:
    """One (start, end) local range per weekday, Monday first; None = day off.

    Ranges can't cross midnight.
    """

    tz: str
    days: tuple[tuple[time, time] | None, ...]

    def __post_init__(self) -> None:
        if len(self.days) != 7:
            raise ValueError("working hours need an entry for each of the 7 weekdays")
        for d in self.days:
            if d is not None and d[0] >= d[1]:
                raise ValueError("working hours must end after they start (no overnight ranges)")
        try:
            ZoneInfo(self.tz)
        except (KeyError, ValueError):
            raise ValueError(f"unknown time zone {self.tz!r}") from None

    @classmethod
    def default(cls, tz: str = "UTC") -> WorkingHours:
        nine_to_five = (time(9), time(17))
        return cls(tz, (nine_to_five,) * 5 + (None, None))

    def blocks(self, window_start: datetime, window_end: datetime) -> list[tuple[datetime, datetime]]:
        """Working intervals (UTC) clipped to the window."""
        zone = ZoneInfo(self.tz)
        day = window_start.astimezone(zone).date()
        last = window_end.astimezone(zone).date()
        out = []
        while day <= last:
            hours = self.days[day.weekday()]
            if hours:
                s = localize(datetime.combine(day, hours[0]), zone).astimezone(UTC)
                e = localize(datetime.combine(day, hours[1]), zone).astimezone(UTC)
                s, e = max(s, window_start), min(e, window_end)
                if s < e:
                    out.append((s, e))
            day += timedelta(days=1)
        return out


@dataclass(frozen=True)
class Slot:
    start: datetime  # UTC
    end: datetime


def _merge(intervals: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def free_gaps(
    busy: Iterable[Occurrence], hours: WorkingHours, window_start: datetime, window_end: datetime
) -> list[Slot]:
    """Working time in the window not covered by any busy occurrence."""
    taken = _merge((o.start, o.end) for o in busy)
    gaps = []
    for bs, be in hours.blocks(window_start, window_end):
        cursor = bs
        for ts, te in taken:
            if te <= cursor:
                continue
            if ts >= be:
                break
            if ts > cursor:
                gaps.append(Slot(cursor, ts))
            cursor = max(cursor, te)
        if cursor < be:
            gaps.append(Slot(cursor, be))
    return gaps


def _ceil(dt: datetime, step: timedelta) -> datetime:
    # Every real UTC offset is a multiple of 15 min, so UTC alignment is also
    # local alignment for steps that divide 15 min or are multiples of it.
    rem = (dt - _EPOCH) % step
    return dt if not rem else dt + (step - rem)


def suggest(
    busy: Iterable[Occurrence],
    hours: WorkingHours,
    duration: timedelta,
    window_start: datetime,
    window_end: datetime,
    *,
    step: timedelta = timedelta(minutes=15),
    limit: int = 8,
) -> tuple[list[Slot], list[Slot]]:
    """(recommended slots, free gaps) for a meeting of `duration`.

    Recommends the earliest step-aligned start in each free gap long enough
    to hold the meeting, so suggestions spread across distinct free blocks
    rather than listing 09:00, 09:15, 09:30...
    """
    if duration <= timedelta(0):
        raise ValueError("duration must be positive")
    gaps = free_gaps(busy, hours, window_start, window_end)
    slots = []
    for gap in gaps:
        start = _ceil(gap.start, step)
        if start + duration <= gap.end:
            slots.append(Slot(start, start + duration))
            if len(slots) >= limit:
                break
    return slots, gaps
