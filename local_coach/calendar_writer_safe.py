"""Transactional entry point for guarded Garmin calendar write-back.

This wrapper keeps the existing permission gates from calendar_writer and adds:
1) full-horizon read-back across month boundaries;
2) delayed Garmin consistency retries;
3) expert session specs compiled to verified coach-owned workouts;
4) transaction rollback across BOTH workout-library changes and calendar placement;
5) the explicit one-action test fallback. Normal automatic apply never invents work.
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any

import calendar_consistency
import calendar_writer as base
import writeback_transaction
from calendar_probe import extract_items

_ORIGINAL_ACTIONABLE = base.actionable
_ORIGINAL_VERIFY = base.verify_action
_ORIGINAL_APPLY = base.apply_action


def full_window_calendar(api: Any, action: dict[str, Any]) -> dict[str, Any]:
    today = dt.date.today()
    dates = [
        today,
        today + dt.timedelta(days=7),
        base.parse_date(action.get("date")),
        base.parse_date(action.get("source_date")),
    ]
    months: set[tuple[int, int]] = {(day.year, day.month) for day in dates if day}
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


def _entries_for(api: Any, workout_id: Any, date: Any) -> list[dict[str, Any]]:
    day = base.parse_date(date)
    if not day:
        return []
    wid = str(workout_id or "")
    return [
        item
        for item in extract_items(api.get_scheduled_workouts(day.year, day.month))
        if str(item.get("workout_id") or "") == wid
        and str(item.get("date") or "")[:10] == day.isoformat()
    ]


def _entry_for(api: Any, workout_id: Any, date: str) -> dict[str, Any] | None:
    rows = _entries_for(api, workout_id, date)
    return rows[0] if rows else None


def _sid_present(api: Any, scheduled_id: Any, dates: list[dt.date]) -> dict[str, Any] | None:
    sid = str(scheduled_id or "")
    months = {(day.year, day.month) for day in dates if day}
    for year, month in sorted(months):
        for item in extract_items(api.get_scheduled_workouts(year, month)):
            if str(item.get("scheduled_workout_id") or "") == sid:
                return item
    return None


def _original_scheduled_ids(calendar: dict[str, Any]) -> set[str]:
    return {
        str(item.get("scheduled_workout_id"))
        for item in base.calendar_items(calendar)
        if item.get("scheduled_workout_id") not in (None, "")
    }


def rollback_calendar_state(
    api: Any,
    original_calendar: dict[str, Any],
    new_workout_id: Any,
    target_date: str,
    old_item: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Best-effort restore of the pre-action calendar state.

    Old placement is restored before a newly-created target placement is removed. That
    ordering means a rollback never intentionally leaves the athlete with neither the
    old nor the new session if Garmin is temporarily inconsistent.
    """
    errors: list[str] = []
    old_ready = old_item is None

    if old_item is not None:
        old_wid = old_item.get("workout_id")
        old_date = str(old_item.get("date") or "")[:10]
        if old_wid and old_date:
            if _entry_for(api, old_wid, old_date):
                old_ready = True
            else:
                try:
                    api.schedule_workout(old_wid, old_date)
                    ok, _value = calendar_consistency.wait_present(
                        lambda: _entry_for(api, old_wid, old_date),
                        attempts=8,
                    )
                    old_ready = bool(ok)
                    if not old_ready:
                        errors.append("den oprindelige kalenderpost blev ikke synlig igen")
                except Exception as exc:
                    errors.append(f"kunne ikke gendanne den oprindelige kalenderpost: {exc}")

    original_ids = _original_scheduled_ids(original_calendar)
    if old_ready:
        try:
            target_rows = _entries_for(api, new_workout_id, target_date)
        except Exception as exc:
            target_rows = []
            errors.append(f"kunne ikke læse mål-dato under rollback: {exc}")
        for row in target_rows:
            sid = row.get("scheduled_workout_id")
            if not sid or str(sid) in original_ids:
                continue
            try:
                api.unschedule_workout(sid)
                source_day = base.parse_date((old_item or {}).get("date"))
                target_day = base.parse_date(target_date)
                ok, _ = calendar_consistency.wait_absent(
                    lambda sid=sid: _sid_present(api, sid, [d for d in (source_day, target_day) if d]),
                    attempts=8,
                )
                if not ok:
                    errors.append(f"ny kalenderpost {sid} blev ikke bekræftet fjernet")
            except Exception as exc:
                errors.append(f"kunne ikke fjerne ny kalenderpost {sid}: {exc}")
    elif new_workout_id:
        errors.append("mål-posten blev bevaret, fordi den oprindelige post ikke kunne gendannes sikkert")

    return (not errors), ("Kalenderen blev gendannet." if not errors else "; ".join(errors))


def rollback_everything(
    api: Any,
    original_calendar: dict[str, Any],
    action: dict[str, Any],
    resolved_workout_id: Any,
) -> str:
    old_item = base.find_source(original_calendar, action)
    target_date = str(action.get("date") or "")[:10]
    cal_ok, cal_detail = rollback_calendar_state(
        api,
        original_calendar,
        resolved_workout_id,
        target_date,
        old_item,
    )
    workout_ok, workout_detail = writeback_transaction.rollback(api, resolved_workout_id)
    status = "OK" if cal_ok and workout_ok else "MED FORBEHOLD"
    return f"Rollback {status}: kalender={cal_detail}; workout={workout_detail}"


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

    try:
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
                    raise RuntimeError("Garmin viser stadig den gamle kalenderpost efter gentagne read-back-forsøg.")
                unscheduled = True

        return {
            "scheduled": created_new,
            "already_present": bool(already),
            "schedule_response": schedule_response,
            "scheduled_workout_id": (new_entry or {}).get("scheduled_workout_id") if isinstance(new_entry, dict) else None,
            "old_unscheduled": unscheduled,
        }
    except Exception as exc:
        # calendar is the exact pre-action snapshot passed by calendar_writer.main.
        detail = rollback_calendar_state(api, calendar, new_workout_id, target_date, old_item)
        workout_detail = writeback_transaction.rollback(api, new_workout_id)
        raise RuntimeError(
            f"{exc} | rollback kalender: {detail[1]} | rollback workout: {workout_detail[1]}"
        ) from exc


def apply_with_rollback(
    api: Any,
    calendar: dict[str, Any],
    action: dict[str, Any],
    resolved_workout_id: Any | None,
) -> dict[str, Any]:
    try:
        return _ORIGINAL_APPLY(api, calendar, action, resolved_workout_id)
    except Exception as exc:
        if resolved_workout_id is not None and writeback_transaction.pending(resolved_workout_id):
            detail = rollback_everything(api, calendar, action, resolved_workout_id)
            raise RuntimeError(f"{exc} | {detail}") from exc
        raise


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
        writeback_transaction.commit(resolved_workout_id)
        return value

    if resolved_workout_id is not None and writeback_transaction.pending(resolved_workout_id):
        rollback_detail = rollback_everything(api, original_calendar, action, resolved_workout_id)
        return False, f"{last[1]} | {rollback_detail}", last[2]
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
base.apply_action = apply_with_rollback
base.verify_action = verify_with_retry
base.actionable = actionable_with_safe_test_fallback
base.resolve_target_workout = writeback_transaction.resolve

if __name__ == "__main__":
    raise SystemExit(base.main())
