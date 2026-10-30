from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from calcore.models import Freq, Occurrence, Rule, Scope
from calcore.service import Calendar, NotFound
from calcore.scheduling import Conflict, Slot, WorkingHours
from calcore.store import JsonStore, event_to_dict, hours_to_dict, rule_to_dict

MAX_SUGGEST_WINDOW = timedelta(days=62)


class RuleIn(BaseModel):
    freq: Freq
    interval: int = 1
    byweekday: list[int] = []
    until: date | None = None
    anchor: date | None = None

    def to_rule(self) -> Rule:
        return Rule(self.freq, self.interval, tuple(self.byweekday), self.until, self.anchor)


class EventIn(BaseModel):
    title: str
    start: datetime = Field(description="naive local wall-clock time in tz")
    duration: int = Field(description="minutes")
    tz: str
    rule: RuleIn | None = None


class EditIn(BaseModel):
    scope: Scope
    title: str | None = None
    start: datetime | None = None
    duration: int | None = None
    # tz and rule only apply to scope following/all. `rule` is tri-state:
    # omitted = unchanged, null = make non-recurring.
    tz: str | None = None
    rule: RuleIn | None = None

    def changes(self, recurring_single: bool) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.title is not None:
            out["title"] = self.title
        if self.start is not None:
            out["start"] = self.start
        if self.duration is not None:
            out["duration"] = timedelta(minutes=self.duration)
        if not recurring_single:
            if self.tz is not None:
                out["tz"] = self.tz
            if "rule" in self.model_fields_set:
                out["rule"] = self.rule.to_rule() if self.rule else None
        return out


class DayHoursIn(BaseModel):
    start: time
    end: time


class WorkingHoursIn(BaseModel):
    tz: str
    days: list[DayHoursIn | None] = Field(min_length=7, max_length=7)

    def to_hours(self) -> WorkingHours:
        return WorkingHours(self.tz, tuple((d.start, d.end) if d else None for d in self.days))


def create_app(cal: Calendar) -> FastAPI:
    app = FastAPI(title="Calendar")

    @app.exception_handler(ValueError)
    async def bad_request(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": exc.args[0]}, status_code=404)

    def occurrence_out(o: Occurrence) -> dict[str, Any]:
        event = cal.get(o.event_id)
        return {
            "event_id": o.event_id,
            "title": o.title,
            "start": o.zoned_start.isoformat(),
            "end": o.zoned_end.isoformat(),
            "local_start": o.local_start.isoformat(),
            "duration": int(o.duration.total_seconds() // 60),
            "original_start": o.original_start.isoformat(),
            "tz": o.tz,
            "recurring": o.recurring,
            "modified": o.modified,
            "rule": rule_to_dict(event.rule),
        }

    def ref(o: Occurrence) -> dict[str, Any]:
        return {
            "event_id": o.event_id,
            "original_start": o.original_start.isoformat(),
            "title": o.title,
            "start": o.zoned_start.isoformat(),
            "end": o.zoned_end.isoformat(),
        }

    def conflict_out(c: Conflict) -> dict[str, Any]:
        return {
            "a": ref(c.a),
            "b": ref(c.b),
            "start": c.start.isoformat(),
            "end": c.end.isoformat(),
            "minutes": c.minutes,
            "message": c.explain(),
        }

    def slot_out(s: Slot, tz: str) -> dict[str, Any]:
        zone = ZoneInfo(tz)
        return {
            "start": s.start.astimezone(zone).isoformat(),
            "end": s.end.astimezone(zone).isoformat(),
            "local_start": s.start.astimezone(zone).replace(tzinfo=None).isoformat(),
            "minutes": int((s.end - s.start).total_seconds() // 60),
            "tz": tz,
        }

    def current_hours() -> dict[str, Any]:
        hours = cal.working_hours
        return {"saved": hours is not None, **hours_to_dict(hours or WorkingHours.default())}

    @app.get("/api/conflicts")
    def conflicts(start: datetime, end: datetime) -> list[dict[str, Any]]:
        return [conflict_out(c) for c in cal.conflicts(start, end)]

    @app.get("/api/working-hours")
    def get_working_hours() -> dict[str, Any]:
        return current_hours()

    @app.put("/api/working-hours")
    def put_working_hours(body: WorkingHoursIn) -> dict[str, Any]:
        cal.set_working_hours(body.to_hours())
        return current_hours()

    @app.get("/api/suggestions")
    def suggestions(duration: int, start: datetime, end: datetime, limit: int = 8) -> dict[str, Any]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("window bounds must be timezone-aware")
        if end - start > MAX_SUGGEST_WINDOW:
            raise ValueError("search window is limited to 62 days")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        slots, gaps = cal.suggest(timedelta(minutes=duration), start, end, limit)
        hours = current_hours()
        return {
            "slots": [slot_out(s, hours["tz"]) for s in slots],
            "gaps": [slot_out(g, hours["tz"]) for g in gaps],
            "working_hours": hours,
        }

    @app.get("/api/occurrences")
    def occurrences(start: datetime, end: datetime) -> list[dict[str, Any]]:
        return [occurrence_out(o) for o in cal.occurrences(start, end)]

    @app.get("/api/events")
    def events() -> list[dict[str, Any]]:
        return [event_to_dict(e) for e in cal.events.values()]

    @app.post("/api/events", status_code=201)
    def create(body: EventIn) -> dict[str, Any]:
        event = cal.add(
            body.title,
            body.start,
            timedelta(minutes=body.duration),
            body.tz,
            body.rule.to_rule() if body.rule else None,
        )
        return event_to_dict(event)

    @app.put("/api/events/{event_id}/occurrences/{original_start}")
    def edit(event_id: str, original_start: datetime, body: EditIn) -> dict[str, Any]:
        event = cal.get(event_id)
        recurring_single = body.scope is Scope.THIS and event.rule is not None
        updated = cal.edit(event_id, original_start, body.scope, **body.changes(recurring_single))
        return event_to_dict(updated)

    @app.delete("/api/events/{event_id}/occurrences/{original_start}", status_code=204)
    def cancel(event_id: str, original_start: datetime, scope: Scope = Scope.THIS) -> None:
        cal.cancel(event_id, original_start, scope)

    return app


app = create_app(Calendar(JsonStore(os.environ.get("CALENDAR_DB", "data/calendar.json"))))
