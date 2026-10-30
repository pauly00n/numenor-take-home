import pytest
from fastapi.testclient import TestClient

from api import create_app
from calcore.service import Calendar
from calcore.store import JsonStore

WEEK = {"start": "2026-11-02T00:00:00-05:00", "end": "2026-11-09T00:00:00-05:00"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Calendar(JsonStore(tmp_path / "cal.json"))))


def create_series(client):
    r = client.post("/api/events", json={
        "title": "Standup", "start": "2026-11-02T09:00", "duration": 30, "tz": "America/New_York",
        "rule": {"freq": "WEEKLY", "byweekday": [0, 2, 4]},
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_create_and_list_week(client):
    create_series(client)
    occs = client.get("/api/occurrences", params=WEEK).json()
    assert [o["local_start"] for o in occs] == [
        "2026-11-02T09:00:00", "2026-11-04T09:00:00", "2026-11-06T09:00:00",
    ]
    assert occs[0]["start"] == "2026-11-02T09:00:00-05:00"
    assert occs[0]["duration"] == 30 and occs[0]["recurring"] is True


def test_edit_this_then_cancel(client):
    eid = create_series(client)
    r = client.put(f"/api/events/{eid}/occurrences/2026-11-04T09:00:00",
                   json={"scope": "this", "start": "2026-11-04T15:00", "title": "Moved"})
    assert r.status_code == 200, r.text
    r = client.delete(f"/api/events/{eid}/occurrences/2026-11-06T09:00:00", params={"scope": "this"})
    assert r.status_code == 204
    occs = client.get("/api/occurrences", params=WEEK).json()
    assert [(o["local_start"], o["title"], o["modified"]) for o in occs] == [
        ("2026-11-02T09:00:00", "Standup", False),
        ("2026-11-04T15:00:00", "Moved", True),
    ]
    assert occs[1]["original_start"] == "2026-11-04T09:00:00"


def test_edit_all_can_clear_rule(client):
    eid = create_series(client)
    r = client.put(f"/api/events/{eid}/occurrences/2026-11-02T09:00:00", json={"scope": "all", "rule": None})
    assert r.status_code == 200 and r.json()["rule"] is None
    assert len(client.get("/api/occurrences", params=WEEK).json()) == 1


def test_errors(client):
    eid = create_series(client)
    bad = client.post("/api/events", json={"title": "x", "start": "2026-11-02T09:00", "duration": 0, "tz": "UTC"})
    assert bad.status_code == 400
    assert client.post("/api/events", json={"title": "x", "start": "2026-11-02T09:00", "duration": 5,
                                            "tz": "Nope/Zone"}).status_code == 400
    assert client.delete(f"/api/events/{eid}/occurrences/2026-11-03T09:00:00").status_code == 404
    assert client.get("/api/occurrences", params={"start": "2026-11-02T00:00", "end": "2026-11-03T00:00"}).status_code == 400


def test_duration_reported_correctly_across_dst(client):
    # Regression: duration was computed as end - start on same-zone aware
    # datetimes (wall-clock subtraction), reporting 5h for a 6h event on
    # fall-back night; saving the edit dialog then shrank the event.
    client.post("/api/events", json={"title": "Overnight", "start": "2026-10-31T23:00", "duration": 360,
                                     "tz": "America/New_York"})
    (occ,) = client.get("/api/occurrences", params={"start": "2026-10-31T00:00:00Z",
                                                     "end": "2026-11-02T00:00:00Z"}).json()
    assert occ["duration"] == 360
    assert occ["start"] == "2026-10-31T23:00:00-04:00"
    assert occ["end"] == "2026-11-01T04:00:00-05:00"


@pytest.mark.parametrize("path_key, body", [
    # Regression: an aware key passed is_occurrence, was stored under a key that
    # never matched, and produced a duplicate occurrence.
    ("2026-11-04T09:00:00-05:00", {"scope": "this", "title": "x"}),
    # Regression: an aware override start had its offset silently dropped.
    ("2026-11-04T09:00:00", {"scope": "this", "start": "2026-11-04T10:00:00Z"}),
    ("2026-11-04T09:00:00", {"scope": "this", "title": "   "}),
])
def test_rejects_bad_occurrence_edits(client, path_key, body):
    eid = create_series(client)
    assert client.put(f"/api/events/{eid}/occurrences/{path_key}", json=body).status_code == 400
    occs = client.get("/api/occurrences", params=WEEK).json()
    assert [o["local_start"] for o in occs] == [
        "2026-11-02T09:00:00", "2026-11-04T09:00:00", "2026-11-06T09:00:00",
    ]


@pytest.mark.parametrize("window", [
    {"start": "9999-12-30T00:00:00Z", "end": "9999-12-31T00:00:00Z"},  # regression: 500 OverflowError
    {"start": "2000-01-01T00:00:00Z", "end": "2100-01-01T00:00:00Z"},
    {"start": "2026-11-02T00:00:00Z", "end": "2026-11-01T00:00:00Z"},
])
def test_rejects_bad_windows(client, window):
    create_series(client)
    assert client.get("/api/occurrences", params=window).status_code == 400


def test_conflicts_endpoint(client):
    create_series(client)
    client.post("/api/events", json={"title": "Dentist", "start": "2026-11-04T09:15", "duration": 60,
                                     "tz": "America/New_York"})
    (c,) = client.get("/api/conflicts", params=WEEK).json()
    assert c["a"]["title"] == "Standup" and c["b"]["title"] == "Dentist"
    assert c["minutes"] == 15
    assert c["a"]["original_start"] == "2026-11-04T09:00:00"
    assert "overlaps" in c["message"]


def test_working_hours_and_suggestions(client):
    got = client.get("/api/working-hours").json()
    assert got["saved"] is False and got["days"][0] == {"start": "09:00", "end": "17:00"} and got["days"][5] is None
    hours = {"tz": "America/New_York", "days": [{"start": "09:00", "end": "17:00"}] * 5 + [None, None]}
    assert client.put("/api/working-hours", json=hours).json()["saved"] is True
    create_series(client)  # 09:00-09:30 M/W/F
    r = client.get("/api/suggestions", params={"duration": 60, **WEEK, "limit": 3}).json()
    assert [s["local_start"] for s in r["slots"]] == [
        "2026-11-02T09:30:00", "2026-11-03T09:00:00", "2026-11-04T09:30:00",
    ]
    assert r["slots"][0]["start"] == "2026-11-02T09:30:00-05:00"


def test_suggestion_validation(client):
    bad = {"tz": "UTC", "days": [{"start": "17:00", "end": "09:00"}] + [None] * 6}
    assert client.put("/api/working-hours", json=bad).status_code == 400
    assert client.put("/api/working-hours", json={"tz": "UTC", "days": [None] * 6}).status_code == 422
    assert client.get("/api/suggestions", params={"duration": 0, **WEEK}).status_code == 400
    assert client.get("/api/suggestions", params={"duration": 30, "start": WEEK["start"],
                                                  "end": "2027-11-02T00:00:00-05:00"}).status_code == 400
