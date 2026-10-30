from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta, tzinfo

from .models import Event, EventException, Freq, Occurrence

_DAY = timedelta(days=1)


def localize(local: datetime, tz: tzinfo) -> datetime:
    """Attach a zone to a wall-clock time using zoneinfo's default fold=0.

    Nonexistent times (spring-forward gap) resolve with the pre-transition offset,
    so 02:30 on the gap day lands at 03:30. Ambiguous times (fall-back) pick the
    first, earlier instant. The UTC round-trip normalizes gap times.
    """
    return local.replace(tzinfo=tz).astimezone(UTC).astimezone(tz)


def candidate_dates(event: Event, lo: date, hi: date) -> Iterator[date]:
    """Dates in [lo, hi] on which the rule generates an occurrence, ascending."""
    first = event.start.date()
    rule = event.rule
    if rule is None:
        if lo <= first <= hi:
            yield first
        return
    if rule.until:
        hi = min(hi, rule.until)
    lo = max(lo, first)
    if lo > hi:
        return

    if rule.freq is Freq.WEEKLY:
        anchor = rule.anchor or first - timedelta(days=first.weekday())
        offsets = sorted((wd - anchor.weekday()) % 7 for wd in rule.byweekday)
        step = 7 * rule.interval
        week = anchor + timedelta(days=(lo - anchor).days // step * step)
        while week <= hi:
            for off in offsets:
                d = week + timedelta(days=off)
                if lo <= d <= hi:
                    yield d
            week += timedelta(days=step)
    else:
        months_in = (lo.year - first.year) * 12 + lo.month - first.month
        k = months_in // rule.interval * rule.interval
        while True:
            y, m = divmod(first.month - 1 + k, 12)
            y, m = first.year + y, m + 1
            if date(y, m, 1) > hi:
                return
            try:
                d = date(y, m, first.day)
            except ValueError:  # month has no such day: skip it
                d = None
            if d and lo <= d <= hi:
                yield d
            k += rule.interval


def first_occurrence(event: Event) -> datetime | None:
    """Local start of the earliest generated occurrence, or None if there are none."""
    d = next(candidate_dates(event, event.start.date(), date.max), None)
    return datetime.combine(d, event.start.time()) if d else None


def is_occurrence(event: Event, local: datetime) -> bool:
    """Whether the rule generates an occurrence starting at this local time."""
    if local.time() != event.start.time():
        return False
    return next(candidate_dates(event, local.date(), local.date()), None) is not None


def _occurrence(
    event: Event, original: datetime, start: datetime, duration: timedelta, title: str, modified: bool
) -> Occurrence:
    start_at = localize(start, event.zone).astimezone(UTC)
    end_at = start_at + duration
    return Occurrence(
        event_id=event.id,
        title=title,
        start=start_at,
        end=end_at,
        original_start=original,
        tz=event.tz,
        recurring=event.rule is not None,
        modified=modified,
    )


def expand(
    event: Event,
    exceptions: Mapping[datetime, EventException],
    window_start: datetime,
    window_end: datetime,
) -> list[Occurrence]:
    """Occurrences of `event` overlapping [window_start, window_end), sorted.

    Window bounds must be timezone-aware. Generated occurrences with an exception
    are replaced: cancelled ones vanish, overridden ones appear at their new time
    (even if the original time was outside the window). Exceptions whose
    original_start the rule no longer generates are ignored.
    """
    zone = event.zone
    # Pad by duration (events already running at window_start) and a day each
    # side so DST offset shifts can't drop edge occurrences; the overlap test
    # below trims the excess.
    lo = (window_start - event.duration).astimezone(zone).date() - _DAY
    hi = window_end.astimezone(zone).date() + _DAY

    def overlaps(o: Occurrence) -> bool:
        return o.start < window_end and o.end > window_start

    out = []
    for d in candidate_dates(event, lo, hi):
        local = datetime.combine(d, event.start.time())
        if local in exceptions:
            continue
        occ = _occurrence(event, local, local, event.duration, event.title, modified=False)
        if overlaps(occ):
            out.append(occ)

    for exc in exceptions.values():
        if exc.cancelled or not is_occurrence(event, exc.original_start):
            continue
        occ = _occurrence(
            event,
            exc.original_start,
            exc.start or exc.original_start,
            exc.duration or event.duration,
            exc.title or event.title,
            modified=True,
        )
        if overlaps(occ):
            out.append(occ)

    out.sort(key=lambda o: o.start)
    return out
