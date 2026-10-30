"""Populate the calendar with demo events around the current week.

    uv run python seed.py [--reset]
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from calcore.models import Freq, Rule, Scope
from calcore.service import Calendar
from calcore.store import JsonStore

TZ = os.environ.get("SEED_TZ", "America/New_York")


def main() -> None:
    path = Path(os.environ.get("CALENDAR_DB", "data/calendar.json"))
    if "--reset" in sys.argv and path.exists():
        path.unlink()
    cal = Calendar(JsonStore(path))

    today = date.today()
    monday = today - timedelta(days=today.weekday())
    at = lambda d, h, m=0: datetime.combine(d, datetime.min.time()) + timedelta(hours=h, minutes=m)
    mins = lambda n: timedelta(minutes=n)

    standup = cal.add("Standup", at(monday - timedelta(days=7), 9, 30), mins(15), TZ,
                      Rule(Freq.WEEKLY, byweekday=(0, 1, 2, 3, 4)))
    # Exceptions: skip Wednesday's, move Friday's later.
    cal.cancel(standup.id, at(monday + timedelta(days=2), 9, 30), Scope.THIS)
    cal.edit(standup.id, at(monday + timedelta(days=4), 9, 30), Scope.THIS,
             start=at(monday + timedelta(days=4), 11), title="Standup (moved)")

    cal.add("1:1 with Sam", at(monday + timedelta(days=1), 14), mins(30), TZ,
            Rule(Freq.WEEKLY, interval=2, byweekday=(1,)))
    cal.add("Design review", at(monday + timedelta(days=3), 13), mins(90), TZ)
    cal.add("Lunch w/ team", at(monday + timedelta(days=3), 12, 30), mins(60), TZ)
    cal.add("Rent due", at(monday.replace(day=1), 8), mins(30), TZ, Rule(Freq.MONTHLY))
    cal.add("Late deploy", at(monday + timedelta(days=4), 22), mins(180), TZ)

    print(f"seeded {len(cal.events)} events into {path}")


if __name__ == "__main__":
    main()
