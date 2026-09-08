"""Conversational calendar control for the active CoachTest workout.

Only the explicitly created active test workout can be scheduled from chat. The
module schedules first, reads Garmin back, and only then removes an old placement
when moving. It never touches unrelated calendar entries.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from garminconnect import Garmin

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
    return api


def parse_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except Exception:
        return None


def validate_target(value: Any) -> dt.date:
    day = parse_date(value)
    if not day:
        raise RuntimeError("Jeg kunne ikke udlede hvilken dato du mente. Skriv fx 'på torsdag' eller '10/9'.")
    today = dt.date.today()
    if day < today:
        raise RuntimeError("Jeg lægger ikke et testpas tilbage i tiden.")
    if day > today + dt.timedelta(days=90):
        raise RuntimeError("Test-kalenderen er begrænset til de næste 90 dage.")
    return day


def month_items(api: Garmin, day: dt.date) -> list[dict[str, Any]]:
    return [x for x in extract_items(api.get_scheduled_workouts(day.year, day.month)) if isinstance(x, dict)]


def entry_for(api: Garmin, workout_id: Any, day: dt.date) -> dict[str, Any] | None:
    wid = str(workout_id or "")
    for item in month_items(api, day):
        if str(item.get("workout_id") or "") == wid and str(item.get("date") or "")[:10] == day.isoformat():
            return item
    return None


def entries_for_workout(api: Garmin, workout_id: Any, dates: list[dt.date]) -> list[dict[str, Any]]:
    wid = str(workout_id or "")
    months = sorted({(d.year, d.month) for d in dates})
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for year, month in months:
        for item in extract_items(api.get_scheduled_workouts(year, month)):
            if not isinstance(item, dict) or str(item.get("workout_id") or "") != wid:
                continue
            key = str(item.get("scheduled_workout_id") or f"{item.get('date')}:{item.get('workout_id')}")
            if key not in seen:
                seen.add(key)
                rows.append(item)
    return rows


def active_state() -> dict[str, Any]:
    state = load(STATE, {})
    if not isinstance(state, dict) or not state.get("workout_id"):
        raise RuntimeError("Der findes ikke et aktivt CoachTest-workout endnu. Bed træneren om at lave test-løbet først.")
    return state


def schedule_or_move(target_date: str) -> dict[str, Any]:
    state = active_state()
    day = validate_target(target_date)
    wid = state["workout_id"]
    api = login()

    already = entry_for(api, wid, day)
    old_date = parse_date(state.get("scheduled_date"))
    old_sid = state.get("scheduled_workout_id")

    if already and (not old_sid or str(already.get("scheduled_workout_id") or "") == str(old_sid)):
        state["scheduled_date"] = day.isoformat()
        state["scheduled_workout_id"] = already.get("scheduled_workout_id")
        save(STATE, state)
        return {"action": "already_scheduled", "date": day.isoformat(), "name": state.get("name")}

    if not already:
        api.schedule_workout(wid, day.isoformat())
        already = entry_for(api, wid, day)
        if not already:
            raise RuntimeError("Garmin svarede på schedule-kaldet, men testpasset kunne ikke findes igen på datoen.")

    new_sid = already.get("scheduled_workout_id")

    # Moving is transactional: prove the new placement first, then remove the old.
    if old_sid and str(old_sid) != str(new_sid):
        try:
            api.unschedule_workout(old_sid)
        except Exception as exc:
            # Roll back the newly created placement so failure does not leave duplicates.
            if new_sid:
                try:
                    api.unschedule_workout(new_sid)
                except Exception:
                    pass
            raise RuntimeError(f"Det nye pas blev oprettet, men den gamle kalenderpost kunne ikke fjernes; ændringen blev forsøgt rullet tilbage: {exc}") from exc

        if old_date:
            old_after = entry_for(api, wid, old_date)
            if old_after and str(old_after.get("scheduled_workout_id") or "") == str(old_sid):
                if new_sid:
                    try:
                        api.unschedule_workout(new_sid)
                    except Exception:
                        pass
                raise RuntimeError("Garmin read-back viser stadig den gamle kalenderpost; den nye placering blev rullet tilbage.")

    state["scheduled_date"] = day.isoformat()
    state["scheduled_workout_id"] = new_sid
    state["calendar_verified_at"] = dt.datetime.now().astimezone().isoformat()
    save(STATE, state)
    action = "moved" if old_sid and str(old_sid) != str(new_sid) else "scheduled"
    audit(action, {"workout_id": wid, "date": day.isoformat(), "scheduled_workout_id": new_sid})
    return {"action": action, "date": day.isoformat(), "name": state.get("name")}


def unschedule() -> dict[str, Any]:
    state = active_state()
    wid = state["workout_id"]
    api = login()

    today = dt.date.today()
    dates = [today + dt.timedelta(days=31 * i) for i in range(4)]
    known = parse_date(state.get("scheduled_date"))
    if known:
        dates.append(known)
    rows = entries_for_workout(api, wid, dates)

    if not rows and not state.get("scheduled_workout_id"):
        state.pop("scheduled_date", None)
        state.pop("scheduled_workout_id", None)
        save(STATE, state)
        return {"action": "not_scheduled", "name": state.get("name")}

    ids = {str(x.get("scheduled_workout_id")) for x in rows if x.get("scheduled_workout_id")}
    if state.get("scheduled_workout_id"):
        ids.add(str(state["scheduled_workout_id"]))
    for sid in ids:
        api.unschedule_workout(sid)

    verify_rows = entries_for_workout(api, wid, dates)
    if verify_rows:
        raise RuntimeError("Garmin read-back viser stadig mindst én kalenderpost for testpasset.")

    state.pop("scheduled_date", None)
    state.pop("scheduled_workout_id", None)
    state["calendar_verified_at"] = dt.datetime.now().astimezone().isoformat()
    save(STATE, state)
    audit("unscheduled", {"workout_id": wid, "removed_ids": sorted(ids)})
    return {"action": "unscheduled", "name": state.get("name")}


def describe(result: dict[str, Any]) -> str:
    action = result.get("action")
    name = result.get("name") or "testpasset"
    if action == "scheduled":
        return f"Jeg har lagt {name} i Garmin-kalenderen {result.get('date')} og læst kalenderen tilbage: placeringen er verificeret."
    if action == "moved":
        return f"Jeg har flyttet {name} til {result.get('date')}. Den nye placering blev verificeret, før den gamle blev fjernet."
    if action == "already_scheduled":
        return f"{name} ligger allerede i Garmin-kalenderen {result.get('date')}; jeg har verificeret placeringen."
    if action == "unscheduled":
        return f"Jeg har fjernet {name} fra Garmin-kalenderen og kontrolleret, at kalenderposten er væk. Workoutet ligger stadig under Træninger."
    if action == "not_scheduled":
        return f"{name} ligger ikke i Garmin-kalenderen lige nu."
    return "Kalenderhandlingen blev gennemført."


def handle_intent(intent: dict[str, Any]) -> str:
    op = intent.get("operation")
    if op in {"schedule_test_workout", "move_test_workout"}:
        return describe(schedule_or_move(str(intent.get("target_date") or "")))
    if op == "unschedule_test_workout":
        return describe(unschedule())
    raise RuntimeError(f"Ikke understøttet test-kalenderhandling: {op}")
