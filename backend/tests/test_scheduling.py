from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from calcore.models import Freq, Occurrence, Rule
from calcore.scheduling import WorkingHours, find_conflicts, free_gaps, suggest
from calcore.service import Calendar
from calcore.store import JsonStore

NY = ZoneInfo("America/New_York")
TZ = "America/New_York"
MIN = timedelta(minutes=1)


def at(d, h, m=0):
    return datetime(2026, 11, d, h, m)


def ny(d, h, m=0):
    return datetime(2026, 11, d, h, m, tzinfo=NY).astimezone(UTC)


def week(d0=2):
    return ny(d0, 0), ny(d0 + 7, 0)


@pytest.fixture
def cal():
    return Calendar()


# -- conflicts -------------------------------------------------------------------


def test_no_conflicts_when_touching(cal):
    cal.add("A", at(2, 9), timedelta(hours=1), TZ)
    cal.add("B", at(2, 10), timedelta(hours=1), TZ)
    assert cal.conflicts(*week()) == []


def test_partial_overlap_explained(cal):
    cal.add("Lunch", at(2, 12), timedelta(hours=1), TZ)
    cal.add("Review", at(2, 12, 30), timedelta(hours=1), TZ)
    (c,) = cal.conflicts(*week())
    assert (c.a.title, c.b.title, c.minutes) == ("Lunch", "Review", 30)
    assert c.start == ny(2, 12, 30) and c.end == ny(2, 13)
    assert c.explain() == '"Lunch" overlaps "Review" (30 min of overlap)'


def test_containment_and_identical(cal):
    cal.add("Offsite", at(3, 9), timedelta(hours=8), TZ)
    cal.add("Call", at(3, 11), timedelta(minutes=90), TZ)
    cal.add("Call 2", at(3, 11), timedelta(minutes=90), TZ)
    msgs = [c.explain() for c in cal.conflicts(*week())]
    assert msgs == [
        '"Offsite" entirely contains "Call" (1h 30m of overlap)',
        '"Offsite" entirely contains "Call 2" (1h 30m of overlap)',
        '"Call" at exactly the same time as "Call 2" (1h 30m of overlap)',
    ]


def test_recurring_conflicts_and_exceptions(cal):
    standup = cal.add("Standup", at(2, 9), timedelta(minutes=30), TZ, Rule(Freq.WEEKLY, byweekday=(0, 2)))
    cal.add("Dentist", at(4, 9, 15), timedelta(hours=1), TZ)
    assert [c.b.title for c in cal.conflicts(*week())] == ["Dentist"]
    # Moving the Wednesday standup clears it.
    cal.edit(standup.id, at(4, 9), "this", start=at(4, 8))
    assert cal.conflicts(*week()) == []


def test_conflict_across_dst_uses_real_time(cal):
    # 23:00-04:00 EDT->EST is 6 real hours; a 03:30 EST event overlaps it.
    cal.add("Overnight", datetime(2026, 10, 31, 23), timedelta(hours=6), TZ)
    cal.add("Late", datetime(2026, 11, 1, 3, 30), timedelta(minutes=60), TZ)
    (c,) = cal.conflicts(ny(1, 0) - timedelta(days=1), ny(2, 0))
    assert c.minutes == 30


occ_strategy = st.lists(
    st.tuples(st.integers(0, 60 * 24 * 3), st.integers(1, 600)), max_size=30
)


@given(occ_strategy)
def test_conflicts_match_all_pairs_oracle(spans):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    occs = [
        Occurrence(str(i), f"e{i}", base + s * MIN, base + (s + d) * MIN, base.replace(tzinfo=None), "UTC", False, False)
        for i, (s, d) in enumerate(spans)
    ]
    got = {frozenset((c.a.event_id, c.b.event_id)) for c in find_conflicts(occs)}
    want = {
        frozenset((a.event_id, b.event_id))
        for i, a in enumerate(occs) for b in occs[i + 1:]
        if a.start < b.end and b.start < a.end
    }
    assert got == want
    assert len(find_conflicts(occs)) == len(want)


# -- working hours and suggestions --------------------------------------------------


def test_default_hours_are_weekdays_nine_to_five():
    blocks = WorkingHours.default(TZ).blocks(*week())
    assert len(blocks) == 5
    assert blocks[0] == (ny(2, 9), ny(2, 17))


@pytest.mark.parametrize("days", [
    (None,) * 6,
    ((time(17), time(9)),) + (None,) * 6,
])
def test_invalid_working_hours(days):
    with pytest.raises(ValueError):
        WorkingHours("UTC", days)


def test_suggest_skips_busy_and_spreads_across_gaps(cal):
    cal.add("Standup", at(2, 9), timedelta(minutes=30), TZ, Rule(Freq.WEEKLY, byweekday=(0, 1, 2, 3, 4)))
    cal.add("Busy", at(2, 10), timedelta(hours=5), TZ)  # Mon 10:00-15:00
    cal.set_working_hours(WorkingHours.default(TZ))
    slots, gaps = cal.suggest(timedelta(minutes=45), ny(2, 0), ny(3, 0))
    # Mon gaps: 09:30-10:00 (too short), 15:00-17:00
    assert [(g.start, g.end) for g in gaps] == [(ny(2, 9, 30), ny(2, 10)), (ny(2, 15), ny(2, 17))]
    assert [(s.start, s.end) for s in slots] == [(ny(2, 15), ny(2, 15, 45))]


def test_suggest_aligns_to_quarter_hour(cal):
    cal.add("Odd", at(2, 9), timedelta(minutes=7), TZ)
    cal.set_working_hours(WorkingHours.default(TZ))
    slots, _ = cal.suggest(timedelta(minutes=30), ny(2, 0), ny(3, 0))
    assert slots[0].start == ny(2, 9, 15)


def test_suggest_respects_window_start(cal):
    cal.set_working_hours(WorkingHours.default(TZ))
    slots, _ = cal.suggest(timedelta(minutes=30), ny(2, 13, 5), ny(3, 0))
    assert slots[0].start == ny(2, 13, 15)


def test_suggest_none_on_weekend_only_window(cal):
    cal.set_working_hours(WorkingHours.default(TZ))
    assert cal.suggest(timedelta(minutes=30), ny(7, 0), ny(9, 0)) == ([], [])


def test_working_hours_across_dst_are_local():
    # Mon 2026-11-02 is after the fall-back; 9-5 stays 9-5 local.
    hours = WorkingHours(TZ, ((time(9), time(17)),) * 7)
    blocks = hours.blocks(ny(1, 0) - timedelta(days=1), ny(3, 0))
    assert [b[0].astimezone(NY).hour for b in blocks] == [9, 9, 9]
    assert [(b[1] - b[0]) for b in blocks] == [timedelta(hours=8)] * 3


def test_working_hours_persist(tmp_path):
    path = tmp_path / "c.json"
    hours = WorkingHours("Europe/London", ((time(8, 30), time(16)),) * 4 + (None,) * 3)
    Calendar(JsonStore(path)).set_working_hours(hours)
    assert Calendar(JsonStore(path)).working_hours == hours


@given(
    occ_strategy,
    st.integers(15, 240),
    st.lists(st.one_of(st.none(), st.tuples(st.integers(0, 22), st.integers(1, 12))), min_size=7, max_size=7),
    st.sampled_from(["UTC", TZ, "Asia/Kolkata", "Australia/Lord_Howe"]),
)
def test_suggestions_are_free_aligned_and_earliest(spans, minutes, day_spec, tz):
    days = tuple(None if d is None else (time(d[0]), time(min(d[0] + d[1], 23), 59)) for d in day_spec)
    hours = WorkingHours(tz, days)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    busy = [
        Occurrence(str(i), "x", base + s * MIN, base + (s + d) * MIN, base.replace(tzinfo=None), "UTC", False, False)
        for i, (s, d) in enumerate(spans)
    ]
    ws, we = base, base + timedelta(days=5)
    duration = timedelta(minutes=minutes)
    slots, gaps = suggest(busy, hours, duration, ws, we, limit=50)
    blocks = hours.blocks(ws, we)
    for g in gaps:
        assert any(bs <= g.start and g.end <= be for bs, be in blocks)
        assert not any(o.start < g.end and g.start < o.end for o in busy)
    for s in slots:
        assert s.end - s.start == duration
        assert (s.start - datetime(2000, 1, 1, tzinfo=UTC)) % timedelta(minutes=15) == timedelta(0)
        gap = next(g for g in gaps if g.start <= s.start and s.end <= g.end)
        assert s.start - gap.start < timedelta(minutes=15)  # earliest aligned start in its gap
    # completeness: every gap that fits an aligned meeting got a slot
    fitting = [g for g in gaps if g.start + (-(g.start - datetime(2000, 1, 1, tzinfo=UTC)) % timedelta(minutes=15)) + duration <= g.end]
    assert len(slots) == min(len(fitting), 50)
