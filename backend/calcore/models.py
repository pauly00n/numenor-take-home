from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


class Freq(StrEnum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class Scope(StrEnum):
    THIS = "this"
    FOLLOWING = "following"
    ALL = "all"


def _check_duration(d: timedelta) -> None:
    if d <= timedelta(0):
        raise ValueError("duration must be positive")
    if d % timedelta(minutes=1):
        raise ValueError("duration must be a whole number of minutes")


@dataclass(frozen=True)
class Rule:
    """Recurrence rule.

    WEEKLY: repeats on each day in `byweekday` (0=Mon..6=Sun), every `interval`
    weeks. Weeks are 7-day cycles beginning at `anchor`, which defaults to the
    Monday of the start's week; it only matters when interval > 1 and is kept
    explicit so shifting or splitting a series preserves its cadence exactly.
    MONTHLY: repeats on the start's day-of-month every `interval` months;
    months without that day (e.g. the 31st in April) are skipped.
    `until` is an inclusive date in the event's time zone.
    """

    freq: Freq
    interval: int = 1
    byweekday: tuple[int, ...] = ()
    until: date | None = None
    anchor: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "freq", Freq(self.freq))
        if self.interval < 1:
            raise ValueError("interval must be >= 1")
        days = tuple(sorted(set(self.byweekday)))
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("byweekday values must be 0 (Mon) through 6 (Sun)")
        if self.freq is Freq.WEEKLY and not days:
            raise ValueError("weekly rules need at least one weekday")
        if self.freq is Freq.MONTHLY and days:
            raise ValueError("monthly rules do not take byweekday")
        object.__setattr__(self, "byweekday", days)
        if self.freq is not Freq.WEEKLY or self.interval == 1:
            object.__setattr__(self, "anchor", None)


@dataclass(frozen=True)
class Event:
    """A single event or the master of a recurring series.

    `start` is naive wall-clock time in `tz`, so a 09:00 series stays at 09:00
    local across DST changes.
    """

    id: str
    title: str
    start: datetime
    duration: timedelta
    tz: str
    rule: Rule | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("title is required")
        if self.start.tzinfo is not None:
            raise ValueError("start must be a naive wall-clock time; put the zone in tz")
        _check_duration(self.duration)
        rule = self.rule
        if rule and rule.freq is Freq.WEEKLY and rule.interval > 1 and rule.anchor is None:
            monday = self.start.date() - timedelta(days=self.start.weekday())
            object.__setattr__(self, "rule", replace(rule, anchor=monday))
        try:
            ZoneInfo(self.tz)
        except (KeyError, ValueError):
            raise ValueError(f"unknown time zone {self.tz!r}") from None
        if self.rule and self.rule.until and self.rule.until < self.start.date():
            raise ValueError("until is before the event starts")

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)


@dataclass(frozen=True)
class EventException:
    """Replaces one generated occurrence of a recurring event.

    Keyed by (event_id, original_start), where original_start is the naive local
    start the rule generated. Either cancels it or overrides some of its fields;
    None means "inherit from the series".
    """

    event_id: str
    original_start: datetime
    cancelled: bool = False
    title: str | None = None
    start: datetime | None = None
    duration: timedelta | None = None

    def __post_init__(self) -> None:
        if self.duration is not None:
            _check_duration(self.duration)
        if self.title is not None and not self.title.strip():
            raise ValueError("title is required")
        for name in ("original_start", "start"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is not None:
                raise ValueError(f"{name} must be a naive wall-clock time in the event's time zone")


@dataclass(frozen=True)
class Occurrence:
    """A concrete instance shown on the calendar.

    `start` and `end` are UTC. Python compares and subtracts aware datetimes that
    share a tzinfo by wall-clock time, which is wrong across DST, so local
    times are only produced for display.
    """

    event_id: str
    title: str
    start: datetime
    end: datetime
    original_start: datetime
    tz: str
    recurring: bool
    modified: bool

    @property
    def zoned_start(self) -> datetime:
        return self.start.astimezone(ZoneInfo(self.tz))

    @property
    def zoned_end(self) -> datetime:
        return self.end.astimezone(ZoneInfo(self.tz))

    @property
    def local_start(self) -> datetime:
        return self.zoned_start.replace(tzinfo=None)

    @property
    def duration(self) -> timedelta:
        return self.end - self.start
