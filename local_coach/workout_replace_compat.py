"""Compatibility fallback for Garmin clients without update_workout().

When the installed python-garminconnect version cannot update a workout in place,
replace the active CoachTest workout transactionally:
1) upload and verify the new workout,
2) mirror any existing calendar placements to the new workout and verify them,
3) remove the old calendar placements,
4) switch local state to the new workout,
5) delete the old workout last.

If any step before state switch fails, the newly created workout/calendar placements
are rolled back and the old workout remains authoritative.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from calendar_probe import extract_items

import garmin_workout_workspace as workspace
import training_intent


def supports_inplace_update(api: Any) -> bool:
    return callable(getattr(api, "update_workout", None))


def _month_rows(api: Any, day: dt.date) -> list[dict[str, Any]]:
    return [x for x in extract_items(api.get_scheduled_workouts(day.year, day.month)) if isinstance(x, dict)]


def _calendar_rows_for_workout(api: Any, workout_id: Any, preferred_date: str | None = None) -> list[dict[str, Any]]:
    today = dt.date.today()
    dates = [today, today + dt.timedelta(days=35), today + dt.timedelta(days=70), today + dt.timedelta(days=100)]
    if preferred_date:
        try:
            dates.append(dt.date.fromisoformat(preferred_date[:10]))
        except Exception:
            pass

    wid = str(workout_id or "")
    seen_months: set[tuple[int, int]] = set()
    seen_rows: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    for day in dates:
        month_key = (day.year, day.month)
        if month_key in seen_months:
            continue
        seen_months.add(month_key)
        for item in _month_rows(api, day):
            if str(item.get("workout_id") or "") != wid:
                continue
            key = (str(item.get("scheduled_workout_id") or ""), str(item.get("date") or "")[:10])
            if key not in seen_rows:
                seen_rows.add(key)
                rows.append(item)
    return rows


def _find_new_entry(api: Any, workout_id: Any, date: str) -> dict[str, Any] | None:
    try:
        day = dt.date.fromisoformat(date[:10])
    except Exception:
        return None
    wid = str(workout_id or "")
    for item in _month_rows(api, day):
        if str(item.get("workout_id") or "") == wid and str(item.get("date") or "")[:10] == day.isoformat():
            return item
    return None


def _rollback_new(api: Any, new_workout_id: Any, scheduled_ids: list[Any]) -> None:
    for sid in scheduled_ids:
        if not sid:
            continue
        try:
            api.unschedule_workout(sid)
        except Exception:
            pass
    try:
        api.delete_workout(new_workout_id)
    except Exception:
        pass


def replace_update(intent: dict[str, Any]) -> dict[str, Any]:
    current = workspace.load(workspace.STATE, {})
    if not isinstance(current, dict) or not current.get("workout_id"):
        raise RuntimeError("Der findes ikke et aktivt CoachTest-workout at erstatte.")

    old_workout_id = current["workout_id"]
    old_recipe = current.get("recipe") if isinstance(current.get("recipe"), dict) else None
    candidate, recipe = workspace.build_candidate(intent, old_recipe, current.get("name"))
    api = workspace.login()

    old_readback = api.get_workout_by_id(old_workout_id)
    if not isinstance(old_readback, dict):
        raise RuntimeError("Det gamle CoachTest-workout kunne ikke læses før kompatibilitetsopdateringen.")

    upload = api.upload_workout(candidate)
    if not isinstance(upload, dict) or not upload.get("workoutId"):
        raise RuntimeError("Garmin returnerede ikke workoutId for erstatnings-workoutet.")
    new_workout_id = upload["workoutId"]

    readback = api.get_workout_by_id(new_workout_id)
    ok, detail, report = workspace._verify(readback, candidate)
    if not ok:
        _rollback_new(api, new_workout_id, [])
        raise RuntimeError("Erstatnings-workoutet blev ikke semantisk verificeret: " + detail)

    old_rows = _calendar_rows_for_workout(api, old_workout_id, str(current.get("scheduled_date") or ""))
    new_scheduled_ids: list[Any] = []
    new_entries: list[dict[str, Any]] = []

    # Prove every new calendar placement before touching old placements.
    try:
        for old_row in old_rows:
            date = str(old_row.get("date") or "")[:10]
            if not date:
                continue
            api.schedule_workout(new_workout_id, date)
            new_entry = _find_new_entry(api, new_workout_id, date)
            if not new_entry:
                raise RuntimeError(f"Det nye workout kunne ikke læses tilbage i kalenderen på {date}.")
            new_entries.append(new_entry)
            if new_entry.get("scheduled_workout_id"):
                new_scheduled_ids.append(new_entry.get("scheduled_workout_id"))
    except Exception:
        _rollback_new(api, new_workout_id, new_scheduled_ids)
        raise

    # Only now remove the old calendar placements. Roll back the new ones if removal fails.
    try:
        for old_row in old_rows:
            sid = old_row.get("scheduled_workout_id")
            if sid:
                api.unschedule_workout(sid)
    except Exception as exc:
        _rollback_new(api, new_workout_id, new_scheduled_ids)
        raise RuntimeError(f"Nyt workout var verificeret, men gammel kalenderplacering kunne ikke fjernes; alt nyt blev rullet tilbage: {exc}") from exc

    # Verify old workout no longer has placements in the scanned horizon.
    remaining_old = _calendar_rows_for_workout(api, old_workout_id, str(current.get("scheduled_date") or ""))
    if remaining_old:
        _rollback_new(api, new_workout_id, new_scheduled_ids)
        raise RuntimeError("Garmin viser stadig en gammel kalenderplacering efter flytning; den nye version blev rullet tilbage.")

    current.update({
        "workout_id": new_workout_id,
        "name": candidate["workoutName"],
        "recipe": recipe,
        "updated_at": dt.datetime.now().astimezone().isoformat(),
        "readback_ok": True,
        "compatibility_replaced": True,
        "replaced_workout_id": old_workout_id,
        "verification_report": str(workspace.VERIFY_REPORT),
    })
    if len(new_entries) == 1:
        current["scheduled_date"] = str(new_entries[0].get("date") or "")[:10]
        current["scheduled_workout_id"] = new_entries[0].get("scheduled_workout_id")
    elif not new_entries:
        current.pop("scheduled_date", None)
        current.pop("scheduled_workout_id", None)
    else:
        current["scheduled_instances"] = [
            {"date": str(x.get("date") or "")[:10], "scheduled_workout_id": x.get("scheduled_workout_id")}
            for x in new_entries
        ]

    workspace.save(workspace.STATE, current)

    cleanup_warning = None
    try:
        api.delete_workout(old_workout_id)
    except Exception as exc:
        cleanup_warning = str(exc)[:300]
        current["cleanup_warning"] = cleanup_warning
        workspace.save(workspace.STATE, current)

    result = {"action": "updated", **current, "signature": report.get("actual_execution")}
    if cleanup_warning:
        result["cleanup_warning"] = cleanup_warning
    return result


def recover_message(message: str, parsed_intent: dict[str, Any] | None = None) -> str:
    intent = dict(parsed_intent or training_intent.deterministic(message))
    intent["operation"] = "update_test_workout"
    result = replace_update(intent)
    text = workspace.describe(result)
    if result.get("cleanup_warning"):
        text += "\nDen gamle workout-skabelon kunne ikke slettes automatisk, men den nye version og kalenderplaceringen er verificeret."
    else:
        text += "\nJeg brugte kompatibilitets-self-heal, fordi denne Garmin-klient ikke understøtter update_workout()."
    return text
