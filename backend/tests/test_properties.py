"""Property-based stress tests: brute-force oracles and invariants under random edits."""

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from calcore.models import Event, Freq, Rule, Scope
from calcore.recurrence import candidate_dates, expand, first_occurrence, is_occurrence
from calcore.service import Calendar
from calcore.store import JsonStore

ZONES = ["UTC", "America/New_York", "Europe/London", "Australia/Lord_Howe", "Asia/Kolkata", "America/Santiago"]

starts = st.builds(
    datetime.combine,
    st.dates(date(2025, 1, 1), date(2027, 12, 31)),
    st.builds(time, st.integers(0, 23), st.sampled_from([0, 15, 30, 45])),
)
durations = st.integers(1, 48 * 4).map(lambda q: timedelta(minutes=15 * q))
weekly = st.builds(
    lambda days, interval: Rule(Freq.WEEKLY, interval, tuple(days)),
    st.sets(st.integers(0, 6), min_size=1),
    st.integers(1, 4),
)
monthly = st.builds(lambda interval: Rule(Freq.MONTHLY, interval), st.integers(1, 13))
rules = st.one_of(st.none(), weekly, monthly)


@st.composite
def events(draw):
    start = draw(starts)
    rule = draw(rules)
    if rule and draw(st.booleans()):
        until = start.date() + timedelta(days=draw(st.integers(0, 400)))
        rule = Rule(rule.freq, rule.interval, rule.byweekday, until)
    return Event("e", "T", start, draw(durations), draw(st.sampled_from(ZONES)), rule)


LONG = (datetime(2024, 1, 1, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC))


def original_starts(cal):
    """Wall-clock keys (gap times stay as written, e.g. 02:30)."""
    return sorted(o.original_start for o in cal.occurrences(*LONG))


@given(events(), st.integers(-10, 10), st.integers(-3, 3), st.data())
def test_shift_all_moves_every_occurrence_exactly(event, days, hours, data):
    # Regression: shifting a weekly interval>1 series across a week boundary
    # broke its cadence; also checks byweekday and until follow the shift.
    assume(first_occurrence(event))
    cal = Calendar()
    e = cal.add(event.title, event.start, event.duration, event.tz, event.rule)
    before = original_starts(cal)
    pick = data.draw(st.sampled_from(before[:50]))
    delta = timedelta(days=days, hours=hours)
    if event.rule and event.rule.freq is Freq.MONTHLY:
        # day-of-month can't shift exactly (months differ); only same-day moves
        assume((pick + delta).date() == pick.date())
    cal.edit(e.id, pick, Scope.ALL, start=pick + delta)
    inner = lambda s: datetime(2024, 2, 1) < s < datetime(2029, 12, 1)
    assert [s for s in original_starts(cal) if inner(s)] == [s + delta for s in before if inner(s + delta)]


@given(events(), st.data())
def test_noop_split_changes_nothing(event, data):
    assume(first_occurrence(event))
    cal = Calendar()
    e = cal.add(event.title, event.start, event.duration, event.tz, event.rule)
    before = [(o.original_start, o.start, o.duration) for o in cal.occurrences(*LONG)]
    pick = data.draw(st.sampled_from(before[:50]))[0]
    cal.edit(e.id, pick, Scope.FOLLOWING, title=event.title)
    assert [(o.original_start, o.start, o.duration) for o in cal.occurrences(*LONG)] == before


def oracle_dates(event, lo, hi):
    """Walk every day and test the rule's definition directly."""
    first, rule = event.start.date(), event.rule
    out, d = [], max(lo, first)
    while d <= hi:
        if rule is None:
            ok = d == first
        elif rule.until and d > rule.until:
            break
        elif rule.freq is Freq.WEEKLY:
            anchor = rule.anchor or first - timedelta(days=first.weekday())
            ok = d.weekday() in rule.byweekday and ((d - anchor).days // 7) % rule.interval == 0
        else:
            months = (d.year - first.year) * 12 + d.month - first.month
            ok = d.day == first.day and months % rule.interval == 0
        if ok:
            out.append(d)
        d += timedelta(days=1)
    return out


@given(events(), st.dates(date(2024, 6, 1), date(2028, 6, 1)), st.integers(0, 500))
def test_candidate_dates_match_oracle(event, lo, span):
    hi = lo + timedelta(days=span)
    assert list(candidate_dates(event, lo, hi)) == oracle_dates(event, lo, hi)


@given(events(), st.datetimes(datetime(2025, 1, 1), datetime(2028, 1, 1)), st.integers(1, 60 * 24 * 60),
       st.integers(1, 60 * 24 * 60))
def test_window_split_is_consistent(event, a, m1, m2):
    a = a.replace(tzinfo=UTC)
    b, c = a + timedelta(minutes=m1), a + timedelta(minutes=m1 + m2)
    key = lambda o: o.original_start
    whole = {key(o): o for o in expand(event, {}, a, c)}
    parts = {key(o): o for o in expand(event, {}, a, b) + expand(event, {}, b, c)}
    assert whole == parts


@given(events(), st.datetimes(datetime(2025, 1, 1), datetime(2028, 1, 1)), st.integers(1, 60 * 24 * 90))
def test_expanded_occurrences_are_generated_and_overlap(event, a, minutes):
    a = a.replace(tzinfo=UTC)
    b = a + timedelta(minutes=minutes)
    occs = expand(event, {}, a, b)
    assert occs == sorted(occs, key=lambda o: o.start)
    for o in occs:
        assert o.start < b and o.end > a
        assert is_occurrence(event, o.original_start)
        assert o.end - o.start == event.duration


# -- random operation sequences ------------------------------------------------

WINDOW = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC))
op_starts = st.builds(
    datetime.combine,
    st.dates(date(2026, 1, 1), date(2026, 3, 20)),
    st.builds(time, st.integers(0, 23), st.sampled_from([0, 30])),
)
ops = st.lists(
    st.one_of(
        st.tuples(st.just("add"), op_starts, durations, st.sampled_from(ZONES), rules),
        st.tuples(st.just("edit"), st.integers(0, 50), st.sampled_from(list(Scope)),
                  st.fixed_dictionaries({}, optional={
                      "start": op_starts, "duration": durations, "title": st.sampled_from(["A", "B"]),
                      "rule": rules,
                  })),
        st.tuples(st.just("cancel"), st.integers(0, 50), st.sampled_from(list(Scope))),
    ),
    min_size=1,
    max_size=25,
)


def check_invariants(cal):
    for eid, event in cal.events.items():
        # every live event still produces something
        assert next(candidate_dates(event, date.min, date.max), None) is not None, event
        for key, exc in cal.exceptions.get(eid, {}).items():
            assert exc.event_id == eid and exc.original_start == key
            assert is_occurrence(event, key), ("orphan exception kept", event, exc)
    assert set(cal.exceptions) <= set(cal.events)
    occs = cal.occurrences(*WINDOW)
    keys = [(o.event_id, o.original_start) for o in occs]
    assert len(keys) == len(set(keys))


def snapshot(cal):
    return Counter((o.title, o.start, o.end) for o in cal.occurrences(*WINDOW))


@settings(suppress_health_check=[HealthCheck.too_slow])
@given(ops)
def test_random_operations_keep_invariants(sequence):
    cal = Calendar()
    for op in sequence:
        occs = cal.occurrences(*WINDOW)
        before = snapshot(cal)
        try:
            if op[0] == "add":
                _, start, dur, tz, rule = op
                cal.add("T", start, dur, tz, rule)
            elif occs:
                target = occs[op[1] % len(occs)]
                scope = op[2]
                target_key = (target.title, target.start, target.end)
                if op[0] == "cancel":
                    cal.cancel(target.event_id, target.original_start, scope)
                    if scope is Scope.THIS or not target.recurring:
                        after = snapshot(cal)
                        before[target_key] -= 1
                        assert after == +before, "cancel(this) must remove exactly one occurrence"
                else:
                    changes = dict(op[3])
                    if scope is Scope.THIS and target.recurring:
                        changes.pop("rule", None)
                    cal.edit(target.event_id, target.original_start, scope, **changes)
                    if scope is Scope.THIS and target.recurring:
                        after = snapshot(cal)
                        before[target_key] -= 1
                        # everything except the edited occurrence is untouched
                        assert +(before - after) == Counter(), "edit(this) changed other occurrences"
        except ValueError:
            pass  # rejected input is fine; state must still be valid
        check_invariants(cal)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(ops)
def test_random_operations_roundtrip_through_store(tmp_path_factory, sequence):
    path = tmp_path_factory.mktemp("cal") / "cal.json"
    cal = Calendar(JsonStore(path))
    for op in sequence:
        occs = cal.occurrences(*WINDOW)
        try:
            if op[0] == "add":
                cal.add("T", op[1], op[2], op[3], op[4])
            elif occs:
                t = occs[op[1] % len(occs)]
                if op[0] == "cancel":
                    cal.cancel(t.event_id, t.original_start, op[2])
                else:
                    changes = dict(op[3])
                    if op[2] is Scope.THIS and t.recurring:
                        changes.pop("rule", None)
                    cal.edit(t.event_id, t.original_start, op[2], **changes)
        except ValueError:
            pass
    reloaded = Calendar(JsonStore(path))
    assert reloaded.events == cal.events
    assert reloaded.exceptions == cal.exceptions
