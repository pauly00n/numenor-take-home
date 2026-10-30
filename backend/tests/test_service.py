from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from calcore.models import Freq, Rule, Scope
from calcore.service import Calendar, NotFound
from calcore.store import JsonStore

NY = ZoneInfo("America/New_York")
HOUR = timedelta(hours=1)
MON = Rule(Freq.WEEKLY, byweekday=(0,))


def week_of(cal, d0=date(2026, 11, 2), days=28):
    start = datetime.combine(d0, datetime.min.time(), NY)
    return cal.occurrences(start, start + timedelta(days=days))


def summary(occs):
    return [(o.local_start, o.title) for o in occs]


@pytest.fixture
def cal():
    return Calendar()


@pytest.fixture
def series(cal):
    return cal.add("Standup", datetime(2026, 11, 2, 9), HOUR, "America/New_York", MON)


def test_add_and_list(cal):
    cal.add("Lunch", datetime(2026, 11, 3, 12), HOUR, "America/New_York")
    assert summary(week_of(cal)) == [(datetime(2026, 11, 3, 12), "Lunch")]


def test_occurrences_requires_aware_window(cal):
    with pytest.raises(ValueError):
        cal.occurrences(datetime(2026, 11, 2), datetime(2026, 11, 3))


def test_overlapping_events_allowed(cal):
    cal.add("A", datetime(2026, 11, 3, 12), HOUR, "America/New_York")
    cal.add("B", datetime(2026, 11, 3, 12, 30), HOUR, "America/New_York")
    assert len(week_of(cal)) == 2


def test_reschedule_single_event(cal):
    e = cal.add("Lunch", datetime(2026, 11, 3, 12), HOUR, "America/New_York")
    cal.edit(e.id, e.start, Scope.THIS, start=datetime(2026, 11, 4, 13), title="Late lunch")
    assert summary(week_of(cal)) == [(datetime(2026, 11, 4, 13), "Late lunch")]


def test_cancel_single_event(cal):
    e = cal.add("Lunch", datetime(2026, 11, 3, 12), HOUR, "America/New_York")
    cal.cancel(e.id, e.start, Scope.THIS)
    assert week_of(cal) == []
    assert e.id not in cal.events


def test_edit_this_occurrence(cal, series):
    cal.edit(series.id, datetime(2026, 11, 9, 9), Scope.THIS, start=datetime(2026, 11, 10, 15))
    assert [o.local_start for o in week_of(cal)] == [
        datetime(2026, 11, 2, 9),
        datetime(2026, 11, 10, 15),
        datetime(2026, 11, 16, 9),
        datetime(2026, 11, 23, 9),
    ]


def test_edit_this_twice_keeps_original_key(cal, series):
    key = datetime(2026, 11, 9, 9)
    cal.edit(series.id, key, Scope.THIS, start=datetime(2026, 11, 10, 15))
    cal.edit(series.id, key, Scope.THIS, title="Renamed")
    occ = [o for o in week_of(cal) if o.original_start == key][0]
    assert (occ.local_start, occ.title) == (datetime(2026, 11, 10, 15), "Renamed")


def test_cancel_this_occurrence(cal, series):
    cal.cancel(series.id, datetime(2026, 11, 9, 9), Scope.THIS)
    assert [o.local_start.day for o in week_of(cal)] == [2, 16, 23]


def test_cancel_overrides_a_previous_override(cal, series):
    key = datetime(2026, 11, 9, 9)
    cal.edit(series.id, key, Scope.THIS, start=datetime(2026, 11, 10, 15))
    cal.cancel(series.id, key, Scope.THIS)
    assert [o.local_start.day for o in week_of(cal)] == [2, 16, 23]


def test_cancel_following(cal, series):
    cal.cancel(series.id, datetime(2026, 11, 16, 9), Scope.FOLLOWING)
    assert [o.local_start.day for o in week_of(cal)] == [2, 9]
    assert cal.events[series.id].rule.until == date(2026, 11, 15)


def test_cancel_following_from_first_deletes_series(cal, series):
    cal.cancel(series.id, series.start, Scope.FOLLOWING)
    assert cal.events == {}


def test_cancel_all(cal, series):
    cal.edit(series.id, datetime(2026, 11, 9, 9), Scope.THIS, title="x")
    cal.cancel(series.id, datetime(2026, 11, 9, 9), Scope.ALL)
    assert cal.events == {} and cal.exceptions == {}


def test_edit_all_shifts_series_and_its_exceptions(cal, series):
    cal.cancel(series.id, datetime(2026, 11, 16, 9), Scope.THIS)
    # Edit from the 9th occurrence: move to 10:00 for all.
    cal.edit(series.id, datetime(2026, 11, 9, 9), Scope.ALL, start=datetime(2026, 11, 9, 10))
    assert [o.local_start for o in week_of(cal)] == [
        datetime(2026, 11, 2, 10),
        datetime(2026, 11, 9, 10),
        datetime(2026, 11, 23, 10),  # 16th stays cancelled
    ]


def test_edit_all_drops_exceptions_the_new_rule_cannot_generate(cal, series):
    cal.cancel(series.id, datetime(2026, 11, 9, 9), Scope.THIS)
    cal.edit(series.id, series.start, Scope.ALL, rule=Rule(Freq.WEEKLY, byweekday=(1,)))
    # Monday exception is orphaned by a Tuesday-only rule and gets dropped.
    assert cal.exceptions[series.id] == {}


def test_edit_all_remove_rule(cal, series):
    cal.edit(series.id, datetime(2026, 11, 9, 9), Scope.ALL, rule=None)
    assert [o.local_start for o in week_of(cal)] == [datetime(2026, 11, 2, 9)]


def test_edit_following_splits_series(cal, series):
    cal.cancel(series.id, datetime(2026, 11, 23, 9), Scope.THIS)
    tail = cal.edit(
        series.id, datetime(2026, 11, 16, 9), Scope.FOLLOWING, start=datetime(2026, 11, 16, 14), title="Sync"
    )
    assert tail.id != series.id
    assert summary(week_of(cal, days=35)) == [
        (datetime(2026, 11, 2, 9), "Standup"),
        (datetime(2026, 11, 9, 9), "Standup"),
        (datetime(2026, 11, 16, 14), "Sync"),
        # 23rd: cancelled exception moved to the tail and shifted with it
        (datetime(2026, 11, 30, 14), "Sync"),
    ]
    assert cal.events[series.id].rule.until == date(2026, 11, 15)


def test_edit_following_interval_two_keeps_cadence(cal):
    e = cal.add("Biweekly", datetime(2026, 11, 2, 9), HOUR, "America/New_York",
                Rule(Freq.WEEKLY, interval=2, byweekday=(0, 3)))
    cal.edit(e.id, datetime(2026, 11, 16, 9), Scope.FOLLOWING, title="B2")
    got = [(o.local_start.day, o.title) for o in week_of(cal, days=35)]
    assert got == [(2, "Biweekly"), (5, "Biweekly"), (16, "B2"), (19, "B2"), (30, "B2"), (3, "B2")]


def test_edit_following_at_first_occurrence_edits_in_place(cal, series):
    result = cal.edit(series.id, series.start, Scope.FOLLOWING, title="Renamed")
    assert result.id == series.id and len(cal.events) == 1


def test_this_scope_rejects_series_fields(cal, series):
    with pytest.raises(ValueError):
        cal.edit(series.id, series.start, Scope.THIS, rule=None)


def test_unknown_event_and_occurrence(cal, series):
    with pytest.raises(NotFound):
        cal.cancel("nope", series.start, Scope.THIS)
    with pytest.raises(NotFound):
        cal.cancel(series.id, datetime(2026, 11, 3, 9), Scope.THIS)


def test_failed_edit_rolls_back_split(cal, series):
    with pytest.raises(ValueError):
        cal.edit(series.id, datetime(2026, 11, 16, 9), Scope.FOLLOWING, duration=timedelta(0))
    assert list(cal.events) == [series.id]
    assert cal.events[series.id].rule.until is None


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "cal.json"
    cal = Calendar(JsonStore(path))
    e = cal.add("Standup", datetime(2026, 11, 2, 9), HOUR, "America/New_York",
                Rule(Freq.WEEKLY, byweekday=(0, 2), until=date(2026, 12, 31)))
    cal.edit(e.id, datetime(2026, 11, 4, 9), Scope.THIS, start=datetime(2026, 11, 4, 11),
             duration=timedelta(minutes=30), title="Moved")
    cal.cancel(e.id, datetime(2026, 11, 9, 9), Scope.THIS)

    reloaded = Calendar(JsonStore(path))
    assert reloaded.events == cal.events
    assert reloaded.exceptions == cal.exceptions
    assert week_of(reloaded) == week_of(cal)


def test_edit_all_moving_to_another_weekday_shifts_byweekday(cal, series):
    # Regression: moving a Monday series to Tuesday with scope=all left
    # byweekday=(0,), so it kept generating Mondays and orphaned every exception.
    cal.cancel(series.id, datetime(2026, 11, 16, 9), Scope.THIS)
    cal.edit(series.id, datetime(2026, 11, 9, 9), Scope.ALL, start=datetime(2026, 11, 10, 9))
    assert cal.events[series.id].rule.byweekday == (1,)
    assert [o.local_start for o in week_of(cal)] == [
        datetime(2026, 11, 3, 9),
        datetime(2026, 11, 10, 9),
        datetime(2026, 11, 24, 9),  # 16th's cancellation moved to the 17th
    ]


def test_edit_all_with_unchanged_rule_payload_still_shifts(cal, series):
    # The UI always sends the rule; an unchanged rule must not block the shift.
    cal.edit(series.id, series.start, Scope.ALL, start=datetime(2026, 11, 4, 9), rule=series.rule)
    assert cal.events[series.id].rule.byweekday == (2,)


def test_edit_all_explicit_new_rule_wins(cal, series):
    cal.edit(series.id, series.start, Scope.ALL, start=datetime(2026, 11, 3, 9),
             rule=Rule(Freq.WEEKLY, byweekday=(1, 3)))
    assert cal.events[series.id].rule.byweekday == (1, 3)


def test_edit_all_multi_day_shift_wraps_week(cal):
    e = cal.add("MWF", datetime(2026, 11, 2, 9), HOUR, "America/New_York",
                Rule(Freq.WEEKLY, byweekday=(0, 2, 6)))
    cal.edit(e.id, datetime(2026, 11, 2, 9), Scope.ALL, start=datetime(2026, 11, 3, 9))
    assert cal.events[e.id].rule.byweekday == (0, 1, 3)  # Sun wraps to Mon


def test_edit_all_time_past_midnight_shifts_weekday(cal, series):
    cal.edit(series.id, series.start, Scope.ALL, start=datetime(2026, 11, 2, 23, 30) + timedelta(hours=1))
    assert cal.events[series.id].rule.byweekday == (1,)


def test_edit_following_to_another_weekday(cal, series):
    tail = cal.edit(series.id, datetime(2026, 11, 16, 9), Scope.FOLLOWING, start=datetime(2026, 11, 18, 9))
    assert tail.rule.byweekday == (2,)
    assert [o.local_start.day for o in week_of(cal)] == [2, 9, 18, 25]


def test_shift_biweekly_across_week_boundary_keeps_cadence(cal):
    # Regression: Sat+Sun every other week, moved +1 day, put the Mondays in the
    # off weeks because cycles were always counted from a calendar Monday.
    e = cal.add("wk", datetime(2026, 11, 7, 9), HOUR, "UTC", Rule(Freq.WEEKLY, 2, (5, 6)))
    cal.edit(e.id, datetime(2026, 11, 7, 9), Scope.ALL, start=datetime(2026, 11, 8, 9))
    got = [o.local_start.date() for o in week_of(cal, d0=date(2026, 11, 1), days=40)]
    assert got == [date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 22), date(2026, 11, 23),
                   date(2026, 12, 6), date(2026, 12, 7)]


def test_rule_edit_with_same_interval_keeps_anchor(cal):
    e = cal.add("wk", datetime(2026, 11, 2, 9), HOUR, "UTC", Rule(Freq.WEEKLY, 2, (0,)))
    cal.edit(e.id, datetime(2026, 11, 16, 9), Scope.ALL, rule=Rule(Freq.WEEKLY, 2, (0, 2)))
    assert [o.local_start.day for o in week_of(cal)] == [2, 4, 16, 18]


def test_sub_minute_durations_rejected(cal):
    # Regression: the store writes whole minutes, so 90s silently became 60s.
    with pytest.raises(ValueError):
        cal.add("x", datetime(2026, 11, 2, 9), timedelta(seconds=90), "UTC")


def test_shift_all_moves_until_with_it(cal):
    e = cal.add("x", datetime(2026, 11, 2, 9), HOUR, "UTC", Rule(Freq.WEEKLY, byweekday=(0,), until=date(2026, 11, 16)))
    cal.edit(e.id, e.start, Scope.ALL, start=datetime(2026, 11, 3, 9))
    assert [o.local_start.day for o in week_of(cal)] == [3, 10, 17]
