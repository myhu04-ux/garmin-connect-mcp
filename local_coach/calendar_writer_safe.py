"""Entry point that gives calendar_writer a full 7-day read-back window.

This keeps multi-action verification correct when the next seven days cross a
month boundary. Core write logic remains in calendar_writer.py.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import calendar_writer as base
from calendar_probe import extract_items


def full_window_calendar(api: Any, action: dict[str, Any]) -> dict[str, Any]:
    today = dt.date.today()
    dates = [
        today,
        today + dt.timedelta(days=7),
        base.parse_date(action.get("date")),
        base.parse_date(action.get("source_date")),
    ]
    months: set[tuple[int, int]] = {(d.year, d.month) for d in dates if d}
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for year, month in sorted(months):
        for item in extract_items(api.get_scheduled_workouts(year, month)):
            key = (
                str(item.get("date") or ""),
                str(item.get("workout_id") or ""),
                str(item.get("scheduled_workout_id") or ""),
            )
            if key not in seen:
                seen.add(key)
                items.append(item)
    return {"items": items}


base.fresh_calendar = full_window_calendar

if __name__ == "__main__":
    raise SystemExit(base.main())
