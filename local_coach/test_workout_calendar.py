"""Native conversational calendar control for the active CoachTest workout.

Requires garminconnect 0.3.12. Calendar writes are verified against the concrete
scheduled-workout resource, not only the eventually-consistent monthly listing.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any

from garminconnect import Garmin, GarminConnectNotFoundError

from calendar_probe import extract_items

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
    for method in ("get_scheduled_workout_by_id", "schedule_workout", "unschedule_workout"):
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


def _month_items(api: Garmin, day: dt.date) -> list[dict[str, Any]]:
    return [x for x in extract_items(api.get_scheduled_workouts(day.year, day.month)) if isinstance(x, dict)]


def _month_entry(api: Garmin, workout_id: Any, day: dt.date) -> dict[str, Any] | None:
    wid = str(workout_id or "")
    matches = [
        x for x in _month_items(api, day)
        if str(x.get("workout_id") or "") == wid and str(x.get("date") or "")[:10] == day.isoformat()
    ]
    return matches[0] if len(matches) == 1 else None


def _scheduled_row(raw: Any, workout_id: Any, day: dt.date) -> dict[str, Any] | None:
    wid = str(workout_id or "")
    rows = extract_items(raw)
    for row in rows:
        if str(row.get("workout_id") or "") == wid and str(row.get("date") or "")[:10] == day.isoformat():
            return row
    return None


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


def _read_scheduled(api: Garmin, scheduled_id: Any) -> Any | None:
    try:
        return api.get_scheduled_workout_by_id(scheduled_id)
    except GarminConnectNotFoundError:
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


def _discover_new_entry(api: Garmin, workout_id: Any, day: dt.date) -> dict[str, Any] | None:
    for delay in (0.0, 0.25, 0.5, 1.0, 1.5):
        if delay:
            time.sleep(delay)
        row = _month_entry(api, workout_id, day)
        if row and row.get("scheduled_workout_id"):
            sid = row["scheduled_workout_id"]
            if _verify_scheduled(api, sid, workout_id, day):
                return row
    return None


def _schedule_verified(api: Garmin, workout_id: Any, day: dt.date) -> dict[str, Any]:
    result = api.schedule_workout(workout_id, day.isoformat())
    sid = _extract_scheduled_id(result)
    if sid and _verify_scheduled(api, sid, workout_id, day):
        return {
            "date": day.isoformat(),
            "workout_id": workout_id,
            "scheduled_workout_id": sid,
        }
    row = _discover_new_entry(api, workout_id, day)
    if not row:
        raise RuntimeError("Garmin accepterede schedule-kaldet, men den nye kalenderpost kunne ikke verificeres direkte.")
    return row


def schedule_or_move(target_date: str) -> dict[str, Any]:
    state = active_state()
    day = validate_target(target_date)
    wid = state["workout_id"]
    api = login()

    old_date = parse_date(state.get("scheduled_date"))
    old_sid = state.get("scheduled_workout_id")

    if old_sid and old_date == day and _verify_scheduled(api, old_sid, wid, day):
        return {"action": "already_scheduled", "date": day.isoformat(), "name": state.get("name")}

    existing = _month_entry(api, wid, day)
    if existing and existing.get("scheduled_workout_id") and _verify_scheduled(api, existing["scheduled_workout_id"], wid, day):
        new_entry = existing
    else:
        new_entry = _schedule_verified(api, wid, day)

    new_sid = new_entry.get("scheduled_workout_id")
    if not new_sid:
        raise RuntimeError("Den nye Garmin-kalenderpost mangler scheduledWorkoutId.")

    # New placement is proven. Remove the old concrete scheduled resource only now.
    if old_sid and str(old_sid) != str(new_sid):
        try:
            api.unschedule_workout(old_sid)
        except GarminConnectNotFoundError:
            pass
        if not _verify_gone(api, old_sid):
            # Do not delete the proven new placement. Preserve it and mark cleanup
            # pending instead of bouncing between two states.
            state["scheduled_date"] = day.isoformat()
            state["scheduled_workout_id"] = new_sid
            state["calendar_cleanup_pending"] = {
                "old_scheduled_workout_id": old_sid,
                "old_date": old_date.isoformat() if old_date else None,
            }
            save(STATE, state)
            raise RuntimeError(
                "Det nye pas er verificeret på måldatoen, men Garmin har endnu ikke bekræftet fjernelsen af den gamle kalenderpost. "
                "Jeg har beholdt den nye placering og markeret den gamle til oprydning i stedet for at rulle frem og tilbage."
            )

    state["scheduled_date"] = day.isoformat()
    state["scheduled_workout_id"] = new_sid
    state.pop("calendar_cleanup_pending", None)
    state["calendar_verified_at"] = dt.datetime.now().astimezone().isoformat()
    save(STATE, state)
    action = "moved" if old_sid and str(old_sid) != str(new_sid) else "scheduled"
    audit(action, {"workout_id": wid, "date": day.isoformat(), "scheduled_workout_id": new_sid})
    return {"action": action, "date": day.isoformat(), "name": state.get("name")}


def unschedule() -> dict[str, Any]:
    state = active_state()
    api = login()
    sid = state.get("scheduled_workout_id")
    if not sid:
        state.pop("scheduled_date", None)
        state.pop("calendar_cleanup_pending", None)
        save(STATE, state)
        return {"action": "not_scheduled", "name": state.get("name")}

    try:
        api.unschedule_workout(sid)
    except GarminConnectNotFoundError:
        pass
    if not _verify_gone(api, sid):
        raise RuntimeError("Garmin har ikke bekræftet, at kalenderposten er fjernet.")

    state.pop("scheduled_date", None)
    state.pop("scheduled_workout_id", None)
    state.pop("calendar_cleanup_pending", None)
    state["calendar_verified_at"] = dt.datetime.now().astimezone().isoformat()
    save(STATE, state)
    audit("unscheduled", {"workout_id": state.get("workout_id"), "removed_id": sid})
    return {"action": "unscheduled", "name": state.get("name")}


def describe(result: dict[str, Any]) -> str:
    action = result.get("action")
    name = result.get("name") or "testpasset"
    if action == "scheduled":
        return f"Jeg har lagt {name} i Garmin-kalenderen {result.get('date')} og verificeret den konkrete kalenderpost."
    if action == "moved":
        return f"Jeg har flyttet {name} til {result.get('date')} og verificeret både den nye placering og fjernelsen af den gamle."
    if action == "already_scheduled":
        return f"{name} ligger allerede i Garmin-kalenderen {result.get('date')}; den konkrete kalenderpost er verificeret."
    if action == "unscheduled":
        return f"Jeg har fjernet {name} fra Garmin-kalenderen og verificeret, at scheduled-workout-id'et ikke længere findes."
    if action == "not_scheduled":
        return f"{name} har ingen kendt kalenderplacering lige nu."
    return "Kalenderhandlingen blev gennemført."


def handle_intent(intent: dict[str, Any]) -> str:
    op = intent.get("operation")
    if op in {"schedule_test_workout", "move_test_workout"}:
        return describe(schedule_or_move(str(intent.get("target_date") or "")))
    if op == "unschedule_test_workout":
        return describe(unschedule())
    raise RuntimeError(f"Ikke understøttet test-kalenderhandling: {op}")
