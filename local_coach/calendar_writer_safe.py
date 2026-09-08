"""Safer entry point for calendar_writer.

Adds two guards around the core writer:
1) every read-back covers the full 7-day horizon, including month boundaries;
2) if the adaptive plan has no ADD/ADJUST/MOVE yet, --test-one may convert exactly
   one FUTURE KEEP into a same-content named-copy replacement. That tests Garmin
   clone + schedule + read-back without changing the prescribed training itself.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import calendar_writer as base
from calendar_probe import extract_items

_ORIGINAL_ACTIONABLE = base.actionable


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


def actionable_with_safe_test_fallback(preview: dict[str, Any], allow_remove: bool) -> list[dict[str, Any]]:
    actions = _ORIGINAL_ACTIONABLE(preview, allow_remove)
    if actions or allow_remove:
        return actions

    # For --test-one the core writer calls actionable(..., allow_remove=False).
    # If the coach correctly decided KEEP for everything, synthesize ONE harmless
    # same-content replacement of a future KEEP. The existing master workout is
    # cloned under the plan name (e.g. ThyTrailW3D4), scheduled on the same date,
    # then the old calendar entry is removed only after the new one is scheduled.
    today = dt.date.today()
    candidates = []
    for action in preview.get("actions", []) if isinstance(preview.get("actions"), list) else []:
        if not isinstance(action, dict) or str(action.get("action") or "").upper() != "KEEP":
            continue
        day = base.parse_date(action.get("date"))
        workout_id = action.get("source_workout_id")
        plan_name = str(action.get("plan_name") or "").strip()
        if not day or not workout_id or not plan_name:
            continue
        if not (today + dt.timedelta(days=1) <= day <= today + dt.timedelta(days=7)):
            continue
        candidates.append((day, action))

    if not candidates:
        return []
    candidates.sort(key=lambda pair: pair[0])
    source = candidates[0][1]
    return [{
        "action": "ADJUST",
        "source_date": source.get("source_date") or source.get("date"),
        "source_title": source.get("source_title") or source.get("workout_style"),
        "source_workout_id": source.get("source_workout_id"),
        "date": source.get("date"),
        "family": source.get("family"),
        "target_km": source.get("target_km"),
        "intensity": source.get("intensity") or "moderate",
        "reason": "Write-back test: samme Garmin-træning og samme dato; kun en navngivet personlig kopi bruges for at verificere hele kæden sikkert.",
        "plan_name": source.get("plan_name"),
        "focus": source.get("focus"),
        "workout_style": source.get("workout_style") or source.get("source_title"),
        "selected_template": {
            "workout_id": source.get("source_workout_id"),
            "title": source.get("source_title") or source.get("workout_style") or "Eksisterende Garmin-workout",
        },
        "writeback_test_same_content": True,
    }]


base.fresh_calendar = full_window_calendar
base.actionable = actionable_with_safe_test_fallback

if __name__ == "__main__":
    raise SystemExit(base.main())
