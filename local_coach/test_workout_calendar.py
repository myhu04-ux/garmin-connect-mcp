"""Native conversational calendar control for the active CoachTest workout.

Requires garminconnect 0.3.12. Calendar mutations are idempotent and reconcile the
actual Garmin state instead of trusting one locally remembered calendar id.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any

from garminconnect import Garmin, GarminConnectNotFoundError

from calendar_probe import extract_items, first, walk_dicts

ROOT = Path(r"C:\GarminCoach")
DATA = ROOT / "data"
STATE = DATA / "active_test_workout.json"
AUDIT = DATA / "test_workout_calendar_audit.jsonl"
TOKEN_DIR = os.path.expanduser("~/.garminconnect")


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def audit(kind: str, payload: dict[str, Any]) -> None:
    row = {"timestamp": dt.datetime.now().astimezone().isoformat(), "kind": kind, **payload}
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def login() -> Garmin:
    if not Path(TOKEN_DIR).exists():
        raise RuntimeError(f"Garmin tokenmappe mangler: {TOKEN_DIR}")
    api = Garmin(retry_attempts=0)
    api.login(TOKEN_DIR)
    for method in (
        "get_scheduled_workout_by_id",
        "get_scheduled_workouts",
        "schedule_workout",
        "unschedule_workout",
        "get_workout_by_id",
        "delete_workout",
    ):
        if not callable(getattr(api, method, None)):
            raise RuntimeError(f"Garmin-klienten er for gammel og mangler {method}().")
    return api


def parse_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except Exception:
        return None


def validate_target(value: Any) -> dt.date:
    day = parse_date(value)
    if not day:
        raise RuntimeError("Jeg kunne ikke udlede datoen. Skriv fx 'på torsdag' eller '10/9'.")
    today = dt.date.today()
    if day < today:
        raise RuntimeError("Jeg lægger ikke et testpas tilbage i tiden.")
    if day > today + dt.timedelta(days=90):
        raise RuntimeError("Test-kalenderen er begrænset til de næste 90 dage.")
    return day


def active_state() -> dict[str, Any]:
    state = load(STATE, {})
    if not isinstance(state, dict) or not state.get("workout_id"):
        raise RuntimeError("Der findes ikke et aktivt CoachTest-workout endnu.")
    return state


def _month_keys(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    cursor = dt.date(start.year, start.month, 1)
    final = dt.date(end.year, end.month, 1)
    out: list[tuple[int, int]] = []
    while cursor <= final:
        out.append((cursor.year, cursor.month))
        if cursor.month == 12:
            cursor = dt.date(cursor.year + 1, 1, 1)
        else:
            cursor = dt.date(cursor.year, cursor.month + 1, 1)
    return out


def _raw_month_entries(raw: Any, workout_id: Any) -> list[dict[str, Any]]:
    """Extract all concrete scheduled ids, preserving duplicates on the same date."""
    wid = str(workout_id or "")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for d in walk_dicts(raw):
        date = first(d, "calendarDate", "date", "startDate", "scheduledDate")
        row_wid = first(d, "workoutId", "workoutID")
        sid = first(d, "scheduledWorkoutId", "scheduleId", "calendarItemId")
        if not date or row_wid is None or str(row_wid) != wid or not sid:
            continue
        date_text = str(date)[:10]
        try:
            dt.date.fromisoformat(date_text)
        except ValueError:
            continue
        sid_text = str(sid)
        if sid_text in seen:
            continue
        seen.add(sid_text)
        rows.append({
            "date": date_text,
            "title": first(d, "workoutName", "title", "name", "itemName"),
            "workout_id": row_wid,
            "scheduled_workout_id": sid,
        })

    # Some Garmin responses are nested so the normalized extractor may see fields
    # the concrete parent walker does not. Add those rows without collapsing known ids.
    for item in extract_items(raw):
        if str(item.get("workout_id") or "") != wid:
            continue
        sid = item.get("scheduled_workout_id")
        if sid and str(sid) not in seen:
            seen.add(str(sid))
            rows.append(item)

    rows.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("scheduled_workout_id") or "")))
    return rows


def _read_scheduled(api: Garmin, scheduled_id: Any) -> Any | None:
    try:
        return api.get_scheduled_workout_by_id(scheduled_id)
    except GarminConnectNotFoundError:
        return None


def _scheduled_row(raw: Any, workout_id: Any, day: dt.date) -> dict[str, Any] | None:
    wid = str(workout_id or "")
    for row in extract_items(raw):
        if str(row.get("workout_id") or "") == wid and str(row.get("date") or "")[:10] == day.isoformat():
            return row
    # Direct scheduled-resource payloads may not normalize through extract_items.
    for d in walk_dicts(raw):
        date = first(d, "calendarDate", "date", "startDate", "scheduledDate")
        row_wid = first(d, "workoutId", "workoutID")
        if date and row_wid is not None and str(row_wid) == wid and str(date)[:10] == day.isoformat():
            return {"date": str(date)[:10], "workout_id": row_wid}
    return None


def _verify_scheduled(api: Garmin, scheduled_id: Any, workout_id: Any, day: dt.date) -> bool:
    for delay in (0.0, 0.25, 0.5, 1.0, 1.5):
        if delay:
            time.sleep(delay)
        raw = _read_scheduled(api, scheduled_id)
        if raw is not None and _scheduled_row(raw, workout_id, day):
            return True
    return False


def _verify_gone(api: Garmin, scheduled_id: Any) -> bool:
    for delay in (0.0, 0.25, 0.5, 1.0, 1.5, 2.0):
        if delay:
            time.sleep(delay)
        if _read_scheduled(api, scheduled_id) is None:
            return True
    return False


def _discover_live_entries(api: Garmin, workout_id: Any, attempts: int = 3) -> list[dict[str, Any]]:
    """Read a 97-day window and keep only scheduled resources that still exist."""
    today = dt.date.today()
    start = today - dt.timedelta(days=7)
    end = today + dt.timedelta(days=90)
    by_sid: dict[str, dict[str, Any]] = {}

    delays = (0.0, 0.4, 0.8, 1.2)
    for attempt in range(max(1, attempts)):
        if attempt and attempt < len(delays):
            time.sleep(delays[attempt])
        for year, month in _month_keys(start, end):
            raw = api.get_scheduled_workouts(year, month)
            for row in _raw_month_entries(raw, workout_id):
                sid = row.get("scheduled_workout_id")
                if not sid:
                    continue
                if _read_scheduled(api, sid) is not None:
                    by_sid[str(sid)] = row

    rows = list(by_sid.values())
    rows.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("scheduled_workout_id") or "")))
    return rows


def _extract_scheduled_id(raw: Any) -> Any:
    if isinstance(raw, dict):
        for key in ("scheduledWorkoutId", "scheduleId", "calendarItemId"):
            if raw.get(key):
                return raw[key]
        for value in raw.values():
            found = _extract_scheduled_id(value)
            if found:
                return found
    elif isinstance(raw, list):
        for value in raw:
            found = _extract_scheduled_id(value)
            if found:
                return found
    return None


def _schedule_verified(api: Garmin, workout_id: Any, day: dt.date) -> dict[str, Any]:
    result = api.schedule_workout(workout_id, day.isoformat())
    sid = _extract_scheduled_id(result)
    if sid and _verify_scheduled(api, sid, workout_id, day):
        return {"date": day.isoformat(), "workout_id": workout_id, "scheduled_workout_id": sid}

    # Fall back to calendar discovery if schedule_workout did not return the id.
    for row in _discover_live_entries(api, workout_id, attempts=4):
        if str(row.get("date") or "")[:10] == day.isoformat():
            sid = row.get("scheduled_workout_id")
            if sid and _verify_scheduled(api, sid, workout_id, day):
                return row
    raise RuntimeError("Garmin accepterede schedule-kaldet, men den nye kalenderpost kunne ikke verificeres direkte.")


def _state_sids(state: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    if state.get("scheduled_workout_id"):
        ids.add(str(state["scheduled_workout_id"]))
    pending = state.get("calendar_cleanup_pending")
    if isinstance(pending, dict) and pending.get("old_scheduled_workout_id"):
        ids.add(str(pending["old_scheduled_workout_id"]))
    for item in state.get("scheduled_instances") or []:
        if isinstance(item, dict) and item.get("scheduled_workout_id"):
            ids.add(str(item["scheduled_workout_id"]))
    return ids


def _remove_sids(api: Garmin, scheduled_ids: set[str]) -> list[str]:
    removed: list[str] = []
    for sid in sorted(scheduled_ids):
        try:
            api.unschedule_workout(sid)
        except GarminConnectNotFoundError:
            pass
        if not _verify_gone(api, sid):
            raise RuntimeError(f"Garmin kunne ikke bekræfte fjernelsen af kalenderpost {sid}.")
        removed.append(sid)
    return removed


def schedule_or_move(target_date: str) -> dict[str, Any]:
    """Reconcile the active workout to exactly one calendar instance on target_date."""
    state = active_state()
    day = validate_target(target_date)
    wid = state["workout_id"]
    api = login()

    before = _discover_live_entries(api, wid, attempts=3)
    known_sids = _state_sids(state)
    for sid in list(known_sids):
        raw = _read_scheduled(api, sid)
        if raw is None:
            known_sids.discard(sid)

    target_entries = [x for x in before if str(x.get("date") or "")[:10] == day.isoformat()]
    keeper: dict[str, Any] | None = None
    for row in target_entries:
        sid = row.get("scheduled_workout_id")
        if sid and _verify_scheduled(api, sid, wid, day):
            keeper = row
            break
    if keeper is None:
        keeper = _schedule_verified(api, wid, day)

    keeper_sid = str(keeper.get("scheduled_workout_id") or "")
    if not keeper_sid:
        raise RuntimeError("Den nye Garmin-kalenderpost mangler scheduledWorkoutId.")

    # Re-read after scheduling so duplicate target entries or stale local state are
    # cleaned in the same transaction. Move means EXACTLY ONE live instance remains.
    after_schedule = _discover_live_entries(api, wid, attempts=3)
    extras: set[str] = set(known_sids)
    for row in before + after_schedule:
        sid = row.get("scheduled_workout_id")
        if sid and str(sid) != keeper_sid:
            extras.add(str(sid))
    extras.discard(keeper_sid)
    removed = _remove_sids(api, extras) if extras else []

    # One more reconciliation pass catches a duplicate that became visible late.
    late = _discover_live_entries(api, wid, attempts=2)
    late_extras = {
        str(x.get("scheduled_workout_id"))
        for x in late
        if x.get("scheduled_workout_id") and str(x.get("scheduled_workout_id")) != keeper_sid
    }
    if late_extras:
        removed.extend(_remove_sids(api, late_extras))

    if not _verify_scheduled(api, keeper_sid, wid, day):
        raise RuntimeError("Målplaceringen kunne ikke verificeres efter oprydning.")

    state["scheduled_date"] = day.isoformat()
    state["scheduled_workout_id"] = keeper.get("scheduled_workout_id")
    state.pop("scheduled_instances", None)
    state.pop("calendar_cleanup_pending", None)
    state["calendar_verified_at"] = dt.datetime.now().astimezone().isoformat()
    save(STATE, state)

    existed_only_on_target = (
        len(before) == 1
        and str(before[0].get("date") or "")[:10] == day.isoformat()
        and str(before[0].get("scheduled_workout_id") or "") == keeper_sid
        and not removed
    )
    if existed_only_on_target:
        action = "already_scheduled"
    elif before:
        action = "moved"
    else:
        action = "scheduled"

    audit(action, {
        "workout_id": wid,
        "date": day.isoformat(),
        "scheduled_workout_id": keeper.get("scheduled_workout_id"),
        "removed_scheduled_ids": removed,
    })
    return {
        "action": action,
        "date": day.isoformat(),
        "name": state.get("name"),
        "removed_count": len(set(removed)),
    }


def _unschedule_all_with_api(api: Garmin, state: dict[str, Any]) -> list[str]:
    wid = state["workout_id"]
    entries = _discover_live_entries(api, wid, attempts=3)
    ids = _state_sids(state)
    ids.update(str(x.get("scheduled_workout_id")) for x in entries if x.get("scheduled_workout_id"))
    ids.discard("")
    return _remove_sids(api, ids) if ids else []


def unschedule() -> dict[str, Any]:
    state = active_state()
    api = login()
    removed = _unschedule_all_with_api(api, state)

    state.pop("scheduled_date", None)
    state.pop("scheduled_workout_id", None)
    state.pop("scheduled_instances", None)
    state.pop("calendar_cleanup_pending", None)
    state["calendar_verified_at"] = dt.datetime.now().astimezone().isoformat()
    save(STATE, state)
    audit("unscheduled_all", {"workout_id": state.get("workout_id"), "removed_ids": removed})
    return {"action": "unscheduled", "name": state.get("name"), "removed_count": len(removed)}


def _workout_gone(api: Garmin, workout_id: Any) -> bool:
    for delay in (0.0, 0.25, 0.5, 1.0, 1.5):
        if delay:
            time.sleep(delay)
        try:
            api.get_workout_by_id(workout_id)
        except GarminConnectNotFoundError:
            return True
    return False


def delete_completely() -> dict[str, Any]:
    """Remove every calendar instance and then delete the active workout template."""
    state = active_state()
    api = login()
    wid = state["workout_id"]
    name = state.get("name")

    removed = _unschedule_all_with_api(api, state)
    api.delete_workout(wid)
    if not _workout_gone(api, wid):
        raise RuntimeError("Garmin har ikke bekræftet, at selve test-workoutet er slettet fra Træninger.")

    try:
        STATE.unlink()
    except FileNotFoundError:
        pass
    audit("deleted_completely", {"workout_id": wid, "name": name, "removed_ids": removed})
    return {
        "action": "deleted_completely",
        "name": name,
        "workout_id": wid,
        "removed_count": len(removed),
    }


def describe(result: dict[str, Any]) -> str:
    action = result.get("action")
    name = result.get("name") or "testpasset"
    if action == "scheduled":
        return f"Jeg har lagt {name} i Garmin-kalenderen {result.get('date')} og verificeret, at der kun er den tilsigtede placering."
    if action == "moved":
        extra = result.get("removed_count") or 0
        return f"Jeg har flyttet {name} til {result.get('date')}. Måldatoen er verificeret, og {extra} gammel/dobbelt kalenderpost(er) blev fjernet."
    if action == "already_scheduled":
        return f"{name} ligger allerede præcis én gang i Garmin-kalenderen {result.get('date')}; placeringen er verificeret."
    if action == "unscheduled":
        return f"Jeg har fjernet alle kalenderplaceringer for {name} og verificeret dem. Workoutet ligger stadig under Træninger."
    if action == "deleted_completely":
        return f"Jeg har fjernet {result.get('removed_count') or 0} kalenderplacering(er) og slettet {name} helt fra Garmin Træninger. Begge dele er verificeret."
    return "Kalenderhandlingen blev gennemført."


def handle_intent(intent: dict[str, Any]) -> str:
    op = intent.get("operation")
    if op in {"schedule_test_workout", "move_test_workout"}:
        return describe(schedule_or_move(str(intent.get("target_date") or "")))
    if op == "unschedule_test_workout":
        return describe(unschedule())
    if op == "delete_test_workout":
        return describe(delete_completely())
    raise RuntimeError(f"Ikke understøttet test-kalenderhandling: {op}")
