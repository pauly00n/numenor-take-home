from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from .models import Event, EventException, Rule
from .scheduling import WorkingHours


def _minutes(td: timedelta) -> int:
    return int(td.total_seconds() // 60)


def rule_to_dict(rule: Rule | None) -> dict[str, Any] | None:
    if rule is None:
        return None
    return {
        "freq": rule.freq.value,
        "interval": rule.interval,
        "byweekday": list(rule.byweekday),
        "until": rule.until.isoformat() if rule.until else None,
        "anchor": rule.anchor.isoformat() if rule.anchor else None,
    }


def rule_from_dict(d: dict[str, Any] | None) -> Rule | None:
    if d is None:
        return None
    return Rule(
        freq=d["freq"],
        interval=d.get("interval", 1),
        byweekday=tuple(d.get("byweekday", ())),
        until=date.fromisoformat(d["until"]) if d.get("until") else None,
        anchor=date.fromisoformat(d["anchor"]) if d.get("anchor") else None,
    )


def event_to_dict(e: Event) -> dict[str, Any]:
    return {
        "id": e.id,
        "title": e.title,
        "start": e.start.isoformat(),
        "duration": _minutes(e.duration),
        "tz": e.tz,
        "rule": rule_to_dict(e.rule),
    }


def event_from_dict(d: dict[str, Any]) -> Event:
    return Event(
        id=d["id"],
        title=d["title"],
        start=datetime.fromisoformat(d["start"]),
        duration=timedelta(minutes=d["duration"]),
        tz=d["tz"],
        rule=rule_from_dict(d.get("rule")),
    )


def exception_to_dict(x: EventException) -> dict[str, Any]:
    return {
        "event_id": x.event_id,
        "original_start": x.original_start.isoformat(),
        "cancelled": x.cancelled,
        "title": x.title,
        "start": x.start.isoformat() if x.start else None,
        "duration": _minutes(x.duration) if x.duration else None,
    }


def exception_from_dict(d: dict[str, Any]) -> EventException:
    return EventException(
        event_id=d["event_id"],
        original_start=datetime.fromisoformat(d["original_start"]),
        cancelled=d.get("cancelled", False),
        title=d.get("title"),
        start=datetime.fromisoformat(d["start"]) if d.get("start") else None,
        duration=timedelta(minutes=d["duration"]) if d.get("duration") else None,
    )


def hours_to_dict(h: WorkingHours) -> dict[str, Any]:
    return {
        "tz": h.tz,
        "days": [
            {"start": d[0].strftime("%H:%M"), "end": d[1].strftime("%H:%M")} if d else None
            for d in h.days
        ],
    }


def hours_from_dict(d: dict[str, Any]) -> WorkingHours:
    return WorkingHours(
        tz=d["tz"],
        days=tuple(
            (time.fromisoformat(x["start"]), time.fromisoformat(x["end"])) if x else None
            for x in d["days"]
        ),
    )


class JsonStore:
    """Whole-file JSON persistence. Writes are atomic (temp file + rename)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> tuple[list[Event], list[EventException], WorkingHours | None]:
        if not self.path.exists():
            return [], [], None
        data = json.loads(self.path.read_text())
        events = [event_from_dict(e) for e in data.get("events", [])]
        exceptions = [exception_from_dict(x) for x in data.get("exceptions", [])]
        hours = hours_from_dict(data["working_hours"]) if data.get("working_hours") else None
        return events, exceptions, hours

    def save(
        self, events: list[Event], exceptions: list[EventException], hours: WorkingHours | None = None
    ) -> None:
        data = {
            "events": [event_to_dict(e) for e in events],
            "exceptions": [exception_to_dict(x) for x in exceptions],
            "working_hours": hours_to_dict(hours) if hours else None,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".cal-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)
