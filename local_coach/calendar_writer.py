"""Guarded Garmin calendar write-back for validated coach actions.

Default mode is dry-run. Real writes require either:
  --test-one : applies exactly one safe validated calendar action and records that
               the write-back test passed, but does NOT enable automatic writes.
  --apply    : requires both writeback_test_passed=true and
               garmin_writeback_enabled=true in coach_settings.json.

Workouts themselves are never deleted. MOVE/ADJUST schedule the new target first
and only then unschedule the old calendar entry. Every attempted change is
appended to a local audit log.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from garminconnect import Garmin

ROOT = Path(r"C:\GarminCoach")
DATA = ROOT / "data"
PREVIEW = DATA / "coach_preview.json"
CALENDAR = DATA / "scheduled_workouts.json"
SETTINGS = DATA / "coach_settings.json"
AUDIT = DATA / "writeback_audit.jsonl"
TOKEN_DIR = os.path.expanduser("~/.garminconnect")

DEFAULT_SETTINGS = {
    "garmin_writeback_enabled": False,
    "writeback_test_passed": False,
    "allow_auto_remove": False,
    "max_calendar_changes_per_run": 3,
}


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def settings() -> dict[str, Any]:
    current = load(SETTINGS, {})
    merged = dict(DEFAULT_SETTINGS)
    if isinstance(current, dict):
        merged.update(current)
    return merged


def append_audit(record: dict[str, Any]) -> None:
    record = dict(record)
    record["timestamp"] = dt.datetime.now().astimezone().isoformat()
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def calendar_items(calendar: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in calendar.get("items", []) if isinstance(i, dict)]


def same_workout_on_date(calendar: dict[str, Any], workout_id: Any, date: str) -> dict[str, Any] | None:
    wid = str(workout_id or "")
    for item in calendar_items(calendar):
        if str(item.get("workout_id") or "") == wid and str(item.get("date") or "")[:10] == date:
            return item
    return None


def find_source(calendar: dict[str, Any], action: dict[str, Any]) -> dict[str, Any] | None:
    source_date = str(action.get("source_date") or "")[:10]
    source_workout = str(action.get("source_workout_id") or "")
    candidates = []
    for item in calendar_items(calendar):
        if source_date and str(item.get("date") or "")[:10] != source_date:
            continue
        if source_workout and str(item.get("workout_id") or "") != source_workout:
            continue
        candidates.append(item)
    return candidates[0] if len(candidates) == 1 else None


def selected_workout(action: dict[str, Any]) -> Any:
    template = action.get("selected_template") or {}
    return template.get("workout_id")


def actionable(preview: dict[str, Any], allow_remove: bool) -> list[dict[str, Any]]:
    out = []
    for action in preview.get("actions", []) if isinstance(preview.get("actions"), list) else []:
        if not isinstance(action, dict):
            continue
        name = str(action.get("action") or "").upper()
        if name == "KEEP":
            continue
        if name == "REMOVE" and not allow_remove:
            continue
        if name in {"ADD", "ADJUST"} and not selected_workout(action):
            continue
        if name == "MOVE" and not action.get("source_workout_id"):
            continue
        out.append(action)
    return sorted(out, key=lambda a: (str(a.get("date") or ""), str(a.get("action") or "")))


def describe(action: dict[str, Any]) -> str:
    name = str(action.get("action") or "").upper()
    source = action.get("source_title") or action.get("source_workout_id") or "pas"
    chosen = (action.get("selected_template") or {}).get("title")
    if name == "ADD":
        return f"TILFØJ {chosen or action.get('family')} på {action.get('date')}"
    if name == "MOVE":
        return f"FLYT {source} fra {action.get('source_date')} til {action.get('date')}"
    if name == "ADJUST":
        return f"ERSTAT {source} med {chosen or action.get('family')} på {action.get('date')}"
    if name == "REMOVE":
        return f"FJERN {source} fra kalenderen {action.get('source_date')}"
    return name


def login() -> Garmin:
    if not Path(TOKEN_DIR).exists():
        raise RuntimeError(f"Garmin tokenmappe mangler: {TOKEN_DIR}")
    api = Garmin(retry_attempts=0)
    api.login(TOKEN_DIR)
    return api


def schedule_then_unschedule(
    api: Garmin,
    calendar: dict[str, Any],
    new_workout_id: Any,
    target_date: str,
    old_item: dict[str, Any] | None,
) -> dict[str, Any]:
    already = same_workout_on_date(calendar, new_workout_id, target_date)
    scheduled_result: Any = None
    if not already:
        scheduled_result = api.schedule_workout(new_workout_id, target_date)

    unscheduled = False
    if old_item:
        old_scheduled_id = old_item.get("scheduled_workout_id")
        same_entry = (
            str(old_item.get("workout_id") or "") == str(new_workout_id or "")
            and str(old_item.get("date") or "")[:10] == target_date
        )
        if old_scheduled_id and not same_entry:
            api.unschedule_workout(old_scheduled_id)
            unscheduled = True

    return {
        "scheduled": not bool(already),
        "already_present": bool(already),
        "schedule_response": scheduled_result,
        "old_unscheduled": unscheduled,
    }


def apply_action(api: Garmin, calendar: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    name = str(action.get("action") or "").upper()
    target_date = str(action.get("date") or "")[:10]
    source = find_source(calendar, action)

    if name == "ADD":
        wid = selected_workout(action)
        return schedule_then_unschedule(api, calendar, wid, target_date, None)

    if name == "MOVE":
        if source is None:
            raise RuntimeError("Kunne ikke entydigt finde det eksisterende kalenderpas, der skal flyttes.")
        return schedule_then_unschedule(api, calendar, source.get("workout_id"), target_date, source)

    if name == "ADJUST":
        if source is None:
            raise RuntimeError("Kunne ikke entydigt finde det eksisterende kalenderpas, der skal justeres.")
        wid = selected_workout(action)
        return schedule_then_unschedule(api, calendar, wid, target_date, source)

    if name == "REMOVE":
        if source is None:
            raise RuntimeError("Kunne ikke entydigt finde kalenderpasset, der skal fjernes.")
        sid = source.get("scheduled_workout_id")
        if not sid:
            raise RuntimeError("Det planlagte pas mangler scheduled_workout_id og fjernes derfor ikke.")
        api.unschedule_workout(sid)
        return {"old_unscheduled": True}

    raise RuntimeError(f"Ikke understøttet action: {name}")


def set_test_passed() -> None:
    cfg = load(SETTINGS, {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["writeback_test_passed"] = True
    cfg["garmin_writeback_enabled"] = False
    cfg["writeback_test_passed_at"] = dt.datetime.now().astimezone().isoformat()
    save(SETTINGS, cfg)


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--test-one", action="store_true", help="Apply one validated action, then unlock UI write-back toggle")
    mode.add_argument("--apply", action="store_true", help="Apply validated actions if settings explicitly allow it")
    args = parser.parse_args()

    preview = load(PREVIEW, {})
    calendar = load(CALENDAR, {})
    cfg = settings()
    allow_remove = bool(cfg.get("allow_auto_remove")) if args.apply else False
    actions = actionable(preview, allow_remove=allow_remove)

    print("=== GARMIN CALENDAR WRITE-BACK ===")
    print(f"Validerede ændringer: {len(actions)}")
    for i, action in enumerate(actions, start=1):
        print(f"{i}. {describe(action)}")

    if not args.test_one and not args.apply:
        print("DRY RUN: Intet blev ændret i Garmin.")
        return 0

    if args.test_one:
        if cfg.get("writeback_test_passed"):
            print("Write-back testen er allerede markeret som bestået. Ingen ændring udført.")
            return 0
        # Never use REMOVE as the first write-back test.
        candidates = [a for a in actions if str(a.get("action") or "").upper() in {"ADD", "MOVE", "ADJUST"}]
        if not candidates:
            print("Ingen sikker ADD/MOVE/ADJUST er tilgængelig til write-back-testen endnu.")
            return 4
        actions = candidates[:1]

    if args.apply:
        if not cfg.get("writeback_test_passed"):
            print("BLOCKED: Én-kalenderændring-testen er ikke bestået endnu.")
            return 5
        if not cfg.get("garmin_writeback_enabled"):
            print("Garmin write-back er OFF i coach_settings.json. Ingen ændringer udført.")
            return 0
        try:
            max_changes = max(1, min(int(cfg.get("max_calendar_changes_per_run", 3)), 5))
        except Exception:
            max_changes = 3
        actions = actions[:max_changes]

    if not actions:
        print("Ingen ændringer at udføre.")
        return 0

    try:
        api = login()
    except Exception as exc:
        print(f"ERROR: Garmin-login fejlede: {exc}")
        return 6

    failures = 0
    successes = 0
    for action in actions:
        record = {
            "mode": "test-one" if args.test_one else "auto-apply",
            "action": action,
            "description": describe(action),
        }
        try:
            result = apply_action(api, calendar, action)
            record["success"] = True
            record["result"] = result
            append_audit(record)
            successes += 1
            print(f"OK: {describe(action)}")
        except Exception as exc:
            record["success"] = False
            record["error"] = str(exc)[:1000]
            append_audit(record)
            failures += 1
            print(f"FEJL: {describe(action)} -> {exc}")
            # Stop after first error; do not cascade calendar changes.
            break

    if args.test_one and successes == 1 and failures == 0:
        set_test_passed()
        print("WRITE-BACK TEST BESTÅET. Automatisk write-back er stadig OFF, men kan nu låses op i UI'et.")

    print(f"Resultat: {successes} gennemført, {failures} fejl.")
    return 0 if failures == 0 else 7


if __name__ == "__main__":
    raise SystemExit(main())
