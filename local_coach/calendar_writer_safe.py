"""Safer entry point for calendar_writer.

Adds guards around the core writer:
1) every read-back covers the full 7-day horizon, including month boundaries;
2) schedule/move is transactional and waits for Garmin read-back convergence;
3) final action verification retries because Garmin calendar reads can be stale;
4) expert ADD/ADJUST actions may resolve to a verified coach-generated structured
   workout; strength still uses the approved master copy;
5) only during explicit --test-one, if the adaptive plan has no ADD/ADJUST/MOVE,
   one FUTURE KEEP may be replaced by a same-content named copy. Normal automatic
   apply never synthesizes changes.
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any

import calendar_consistency
import calendar_writer as base
import planned_workout_compiler
from calendar_probe import extract_items

_ORIGINAL_ACTIONABLE = base.actionable
_ORIGINAL_VERIFY = base.verify_action


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


def _entry_for(api: Any, workout_id: Any, date: str) -> dict[str, Any] | None:
    day = base.parse_date(date)
    if not day:
        return None
    wid = str(workout_id or "")
    for item in extract_items(api.get_scheduled_workouts(day.year, day.month)):
        if str(item.get("workout_id") or "") == wid and str(item.get("date") or "")[:10] == day.isoformat():
            return item
    return None


def _sid_present(api: Any, scheduled_id: Any, dates: list[dt.date]) -> dict[str, Any] | None:
    sid = str(scheduled_id or "")
    months = {(d.year, d.month) for d in dates if d}
    for year, month in sorted(months):
        for item in extract_items(api.get_scheduled_workouts(year, month)):
            if str(item.get("scheduled_workout_id") or "") == sid:
                return item
    return None


def transactional_schedule_then_unschedule(
    api: Any,
    calendar: dict[str, Any],
    new_workout_id: Any,
    target_date: str,
    old_item: dict[str, Any] | None,
) -> dict[str, Any]:
    already = base.same_workout_on_date(calendar, new_workout_id, target_date)
    created_new = False
    schedule_response: Any = None
    new_entry = already

    if not already:
        schedule_response = api.schedule_workout(new_workout_id, target_date)
        ok, value = calendar_consistency.wait_present(
            lambda: _entry_for(api, new_workout_id, target_date),
            attempts=8,
        )
        if not ok or not isinstance(value, dict):
            raise RuntimeError("Garmin accepterede schedule-kaldet, men det nye pas blev ikke synligt ved gentaget read-back.")
        new_entry = value
        created_new = True

    unscheduled = False
    if old_item:
        old_scheduled_id = old_item.get("scheduled_workout_id")
        same_entry = (
            str(old_item.get("workout_id") or "") == str(new_workout_id or "")
            and str(old_item.get("date") or "")[:10] == target_date
        )
        if old_scheduled_id and not same_entry:
            api.unschedule_workout(old_scheduled_id)
            source_day = base.parse_date(old_item.get("date"))
            target_day = base.parse_date(target_date)
            ok, _ = calendar_consistency.wait_absent(
                lambda: _sid_present(api, old_scheduled_id, [d for d in (source_day, target_day) if d]),
                attempts=8,
            )
            if not ok:
                if created_new and isinstance(new_entry, dict) and new_entry.get("scheduled_workout_id"):
                    try:
                        api.unschedule_workout(new_entry["scheduled_workout_id"])
                    except Exception:
                        pass
                raise RuntimeError("Garmin viser stadig den gamle kalenderpost efter gentagne read-back-forsøg.")
            unscheduled = True

    return {
        "scheduled": created_new,
        "already_present": bool(already),
        "schedule_response": schedule_response,
        "scheduled_workout_id": (new_entry or {}).get("scheduled_workout_id") if isinstance(new_entry, dict) else None,
        "old_unscheduled": unscheduled,
    }


def verify_with_retry(
    api: Any,
    action: dict[str, Any],
    resolved_workout_id: Any | None,
    original_calendar: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    last: tuple[bool, str, dict[str, Any]] = (False, "Ingen read-back endnu.", {"items": []})

    def read_once() -> tuple[bool, str, dict[str, Any]]:
        nonlocal last
        last = _ORIGINAL_VERIFY(api, action, resolved_workout_id, original_calendar)
        return last

    ok, value = calendar_consistency.wait_for_value(
        read_once,
        lambda result: bool(result and result[0]),
        attempts=8,
    )
    if ok and isinstance(value, tuple):
        return value
    return last


def actionable_with_safe_test_fallback(preview: dict[str, Any], allow_remove: bool) -> list[dict[str, Any]]:
    actions = _ORIGINAL_ACTIONABLE(preview, allow_remove)
    if actions or "--test-one" not in sys.argv:
        return actions

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
base.schedule_then_unschedule = transactional_schedule_then_unschedule
base.verify_action = verify_with_retry
base.actionable = actionable_with_safe_test_fallback
# Expert-generated running specs are compiled and read-back verified here. This
# replacement preserves the old named-master path for strength and actions without
# session_spec, and keeps MOVE semantics safe for coach-owned generated workouts.
base.resolve_target_workout = planned_workout_compiler.resolve_for_action

if __name__ == "__main__":
    raise SystemExit(base.main())
