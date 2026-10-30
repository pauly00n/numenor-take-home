from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from calcore.models import Event, EventException, Freq, Rule
from calcore.recurrence import expand, is_occurrence

NY = ZoneInfo("America/New_York")
HOUR = timedelta(hours=1)


def ev(start, rule=None, duration=HOUR, tz="America/New_York"):
    return Event("e1", "Standup", start, duration, tz, rule)


def window(d0, d1, tz=NY):
    return datetime.combine(d0, datetime.min.time(), tz), datetime.combine(d1, datetime.min.time(), tz)


def starts(occs):
    return [o.local_start for o in occs]


def test_single_event_in_and_out_of_window():
    e = ev(datetime(2026, 11, 2, 9))
    assert starts(expand(e, {}, *window(date(2026, 11, 2), date(2026, 11, 3)))) == [datetime(2026, 11, 2, 9)]
    assert expand(e, {}, *window(date(2026, 11, 3), date(2026, 11, 4))) == []


def test_weekly_mwf():
    # 2026-11-02 is a Monday
    e = ev(datetime(2026, 11, 2, 9), Rule(Freq.WEEKLY, byweekday=(0, 2, 4)))
    got = starts(expand(e, {}, *window(date(2026, 11, 2), date(2026, 11, 16))))
    assert [d.day for d in got] == [2, 4, 6, 9, 11, 13]


def test_weekly_interval_two_anchored_on_start_week():
    # Starts Wednesday; Monday of the start week is before start, so it's skipped.
    e = ev(datetime(2026, 11, 4, 9), Rule(Freq.WEEKLY, interval=2, byweekday=(0, 2)))
    got = starts(expand(e, {}, *window(date(2026, 11, 1), date(2026, 12, 1))))
    assert [d.day for d in got] == [4, 16, 18, 30]


def test_until_is_inclusive_date():
    e = ev(datetime(2026, 11, 2, 9), Rule(Freq.WEEKLY, byweekday=(0,), until=date(2026, 11, 16)))
    got = starts(expand(e, {}, *window(date(2026, 11, 1), date(2027, 1, 1))))
    assert [d.day for d in got] == [2, 9, 16]


def test_monthly_skips_missing_days():
    e = ev(datetime(2026, 1, 31, 9), Rule(Freq.MONTHLY))
    got = starts(expand(e, {}, *window(date(2026, 1, 1), date(2026, 8, 1))))
    assert [(d.month, d.day) for d in got] == [(1, 31), (3, 31), (5, 31), (7, 31)]


def test_monthly_interval():
    e = ev(datetime(2026, 1, 15, 9), Rule(Freq.MONTHLY, interval=3))
    got = starts(expand(e, {}, *window(date(2026, 1, 1), date(2027, 1, 1))))
    assert [d.month for d in got] == [1, 4, 7, 10]


def test_monthly_far_window_jumps_ahead():
    e = ev(datetime(2000, 2, 29, 9), Rule(Freq.MONTHLY, interval=12))
    got = starts(expand(e, {}, *window(date(2028, 1, 1), date(2029, 1, 1))))
    assert got == [datetime(2028, 2, 29, 9)]


def test_wall_clock_preserved_across_dst():
    # US DST ends 2026-11-01. 09:00 local stays 09:00; UTC offset changes.
    e = ev(datetime(2026, 10, 26, 9), Rule(Freq.WEEKLY, byweekday=(0,)))
    occs = expand(e, {}, *window(date(2026, 10, 26), date(2026, 11, 3)))
    assert [o.local_start.hour for o in occs] == [9, 9]
    assert [o.start.astimezone(UTC).hour for o in occs] == [13, 14]


def test_spring_forward_gap_time_shifts_to_after_gap():
    # 2026-03-08 02:30 doesn't exist in New York; fold=0 maps it to 03:30 EDT.
    e = ev(datetime(2026, 3, 7, 2, 30), Rule(Freq.WEEKLY, byweekday=(5, 6)))
    occs = expand(e, {}, *window(date(2026, 3, 7), date(2026, 3, 9)))
    assert [(o.local_start.day, o.local_start.hour, o.local_start.minute) for o in occs] == [(7, 2, 30), (8, 3, 30)]
    assert occs[1].original_start == datetime(2026, 3, 8, 2, 30)


def test_fall_back_ambiguous_time_takes_first_instant():
    e = ev(datetime(2026, 11, 1, 1, 30))
    (occ,) = expand(e, {}, *window(date(2026, 11, 1), date(2026, 11, 2)))
    assert occ.zoned_start.utcoffset() == timedelta(hours=-4)  # EDT, the earlier 01:30


def test_duration_is_elapsed_time_across_dst():
    # 00:00 + 4h on fall-back night is 03:00 local (one extra hour on the clock).
    e = ev(datetime(2026, 11, 1, 0), duration=timedelta(hours=4))
    (occ,) = expand(e, {}, *window(date(2026, 11, 1), date(2026, 11, 2)))
    assert occ.zoned_end.replace(tzinfo=None) == datetime(2026, 11, 1, 3)
    assert occ.duration == timedelta(hours=4)


def test_event_spanning_window_start_is_included():
    e = ev(datetime(2026, 11, 1, 23), duration=timedelta(hours=3))
    occs = expand(e, {}, *window(date(2026, 11, 2), date(2026, 11, 3)))
    assert starts(occs) == [datetime(2026, 11, 1, 23)]


def test_window_in_other_timezone():
    tokyo = ZoneInfo("Asia/Tokyo")
    e = ev(datetime(2026, 11, 2, 9))  # 09:00 NY = 23:00 Tokyo same day
    assert len(expand(e, {}, *window(date(2026, 11, 2), date(2026, 11, 3), tokyo))) == 1
    assert expand(e, {}, *window(date(2026, 11, 3), date(2026, 11, 4), tokyo)) == []


def test_cancelled_exception_removes_occurrence():
    e = ev(datetime(2026, 11, 2, 9), Rule(Freq.WEEKLY, byweekday=(0,)))
    key = datetime(2026, 11, 9, 9)
    excs = {key: EventException("e1", key, cancelled=True)}
    got = starts(expand(e, excs, *window(date(2026, 11, 1), date(2026, 11, 20))))
    assert [d.day for d in got] == [2, 16]


def test_override_moved_into_window_from_outside():
    e = ev(datetime(2026, 11, 2, 9), Rule(Freq.WEEKLY, byweekday=(0,)))
    key = datetime(2026, 11, 16, 9)
    excs = {key: EventException("e1", key, start=datetime(2026, 11, 5, 14), title="Moved")}
    occs = expand(e, excs, *window(date(2026, 11, 2), date(2026, 11, 9)))
    assert [(o.local_start, o.title, o.modified) for o in occs] == [
        (datetime(2026, 11, 2, 9), "Standup", False),
        (datetime(2026, 11, 5, 14), "Moved", True),
    ]
    assert occs[1].original_start == key


def test_override_moved_out_of_window():
    e = ev(datetime(2026, 11, 2, 9), Rule(Freq.WEEKLY, byweekday=(0,)))
    key = datetime(2026, 11, 2, 9)
    excs = {key: EventException("e1", key, start=datetime(2026, 11, 20, 9))}
    assert expand(e, excs, *window(date(2026, 11, 2), date(2026, 11, 3))) == []


def test_override_duration_only():
    e = ev(datetime(2026, 11, 2, 9), Rule(Freq.WEEKLY, byweekday=(0,)))
    key = datetime(2026, 11, 2, 9)
    excs = {key: EventException("e1", key, duration=timedelta(minutes=15))}
    (occ,) = expand(e, excs, *window(date(2026, 11, 2), date(2026, 11, 3)))
    assert occ.end - occ.start == timedelta(minutes=15)


def test_orphaned_exception_is_ignored():
    e = ev(datetime(2026, 11, 2, 9), Rule(Freq.WEEKLY, byweekday=(0,)))
    key = datetime(2026, 11, 3, 9)  # a Tuesday: the rule never generates it
    excs = {key: EventException("e1", key, start=datetime(2026, 11, 3, 10))}
    got = starts(expand(e, excs, *window(date(2026, 11, 2), date(2026, 11, 9))))
    assert got == [datetime(2026, 11, 2, 9)]


def test_is_occurrence():
    e = ev(datetime(2026, 11, 2, 9), Rule(Freq.WEEKLY, interval=2, byweekday=(0,)))
    assert is_occurrence(e, datetime(2026, 11, 16, 9))
    assert not is_occurrence(e, datetime(2026, 11, 9, 9))  # off-week
    assert not is_occurrence(e, datetime(2026, 11, 16, 10))  # wrong time
    assert not is_occurrence(e, datetime(2026, 10, 26, 9))  # before start


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(freq=Freq.WEEKLY),  # no weekdays
        dict(freq=Freq.WEEKLY, byweekday=(7,)),
        dict(freq=Freq.WEEKLY, byweekday=(0,), interval=0),
        dict(freq=Freq.MONTHLY, byweekday=(0,)),
    ],
)
def test_invalid_rules(kwargs):
    with pytest.raises(ValueError):
        Rule(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(title=" "),
        dict(duration=timedelta(0)),
        dict(tz="Mars/Olympus"),
        dict(start=datetime(2026, 11, 2, 9, tzinfo=UTC)),
        dict(rule=Rule(Freq.WEEKLY, byweekday=(0,), until=date(2026, 11, 1))),
    ],
)
def test_invalid_events(kwargs):
    base = dict(id="x", title="T", start=datetime(2026, 11, 2, 9), duration=HOUR, tz="UTC")
    with pytest.raises(ValueError):
        Event(**{**base, **kwargs})
