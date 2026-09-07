"""Guarded Garmin calendar write-back for validated coach actions.

Default mode is dry-run. Real writes require either:
  --test-one : applies exactly one safe validated calendar action, reads Garmin
               back, verifies the result, and only then unlocks the UI toggle.
  --apply    : requires both writeback_test_passed=true and
               garmin_writeback_enabled=true in coach_settings.json.

Workouts themselves are never deleted. MOVE/ADJUST schedule the new target first
and only then unschedule the old calendar entry. Every attempted change is logged.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from garminconnect import Garmin

from calendar_probe import extract_items

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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


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
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def calendar_items(calendar: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in calendar.get("items", []) if isinstance(i, dict)]


def parse_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except Exception:
        return None


def safe_target_date(value: Any) -> bool:
    day = parse_date(value)
    if not day:
        return False
    today = dt.date.today()
    return today + dt.timedelta(days=1) <= day <= today + dt.timedelta(days=7)


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
        if not safe_target_date(action.get("date")):
            continue
        if name == "REMOVE" and not allow_remove:
            continue
        if name in {"ADD", "ADJUST"} and not selected_workout(action):
            continue
        if name == "MOVE" and not action.get("source_workout_id"):
            continue
        if name not in {"ADD", "MOVE", "ADJUST", "REMOVE"}:
            continue
        out.append(action)
    return sorted(out, key=lambda a: (str(a.get("date") or ""), str(a.get("action") or "")))


def describe(action: dict[str, Any]) -> str:
    name = str(action.get("action") or "").upper()
    source = action.get("source_title") or action.get("source_workout_id") or "pas"
    chosen = (action.get("selected_template") or {}).get("title")
    display = action.get("plan_name")
    prefix = f"{display}: " if display else ""
    if name == "ADD":
        return f"{prefix}TILFØJ {chosen or action.get('family')} på {action.get('date')}"
    if name == "MOVE":
        return f"{prefix}FLYT {source} fra {action.get('source_date')} til {action.get('date')}"
    if name == "ADJUST":
        return f"{prefix}ERSTAT {source} med {chosen or action.get('family')} på {action.get('date')}"
    if name == "REMOVE":
        return f"{prefix}FJERN {source} fra kalenderen {action.get('source_date')}"
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
        return schedule_then_unschedule(api, calendar, selected_workout(action), target_date, None)
    if name == "MOVE":
        if source is None:
            raise RuntimeError("Kunne ikke entydigt finde det eksisterende kalenderpas, der skal flyttes.")
        return schedule_then_unschedule(api, calendar, source.get("workout_id"), target_date, source)
    if name == "ADJUST":
        if source is None:
            raise RuntimeError("Kunne ikke entydigt finde det eksisterende kalenderpas, der skal justeres.")
        return schedule_then_unschedule(api, calendar, selected_workout(action), target_date, source)
    if name == "REMOVE":
        if source is None:
            raise RuntimeError("Kunne ikke entydigt finde kalenderpasset, der skal fjernes.")
        sid = source.get("scheduled_workout_id")
        if not sid:
            raise RuntimeError("Det planlagte pas mangler scheduled_workout_id og fjernes derfor ikke.")
        api.unschedule_workout(sid)
        return {"old_unscheduled": True}
    raise RuntimeError(f"Ikke understøttet action: {name}")


def expected_target_workout(action: dict[str, Any]) -> Any:
    name = str(action.get("action") or "").upper()
    if name in {"ADD", "ADJUST"}:
        return selected_workout(action)
    if name == "MOVE":
        return action.get("source_workout_id")
    return None


def fetch_month_items(api: Garmin, day: dt.date) -> list[dict[str, Any]]:
    raw = api.get_scheduled_workouts(day.year, day.month)
    return extract_items(raw)


def verify_action(api: Garmin, action: dict[str, Any]) -> tuple[bool, str]:
    target = parse_date(action.get("date"))
    expected = str(expected_target_workout(action) or "")
    if not target or not expected:
        return False, "Mangler mål-dato eller forventet workout-id til verifikation."

    target_items = fetch_month_items(api, target)
    present = any(
        str(item.get("workout_id") or "") == expected and str(item.get("date") or "")[:10] == target.isoformat()
        for item in target_items
    )
    if not present:
        return False, "Garmin read-back fandt ikke det forventede workout på mål-datoen."

    name = str(action.get("action") or "").upper()
    if name in {"MOVE", "ADJUST"}:
        source = parse_date(action.get("source_date"))
        old_scheduled_id = None
        old_calendar = load(CALENDAR, {})
        old_item = find_source(old_calendar, action)
        if old_item:
            old_scheduled_id = str(old_item.get("scheduled_workout_id") or "")
        if source and source != target and old_scheduled_id:
            source_items = target_items if source.year == target.year and source.month == target.month else fetch_month_items(api, source)
            still_old = any(str(item.get("scheduled_workout_id") or "") == old_scheduled_id for item in source_items)
            if still_old:
                return False, "Det nye pas findes, men den gamle kalenderpost er stadig til stede."

    return True, "Garmin read-back bekræftede kalenderændringen."


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
    mode.add_argument("--test-one", action="store_true")
    mode.add_argument("--apply", action="store_true")
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
        candidates = [a for a in actions if str(a.get("action") or "").upper() in {"ADD", "MOVE", "ADJUST"}]
        if not candidates:
            print("Ingen sikker ADD/MOVE/ADJUST er tilgængelig til write-back-testen endnu.")
            return 4
        # ADD is least disruptive; then MOVE; ADJUST last.
        priority = {"ADD": 0, "MOVE": 1, "ADJUST": 2}
        candidates.sort(key=lambda a: (priority.get(str(a.get("action") or "").upper(), 9), str(a.get("date") or "")))
        actions = candidates[:1]

    if args.apply:
        if not cfg.get("writeback_test_passed"):
            print("BLOCKED: Én-kalenderændring-testen er ikke bestået endnu.")
            return 5
        if not cfg.get("garmin_writeback_enabled"):
            print("Garmin write-back er OFF. Ingen ændringer udført.")
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
            verified = True
            verification = "Ikke ekstra read-back i auto-mode."
            if args.test_one:
                verified, verification = verify_action(api, action)
                if not verified:
                    raise RuntimeError(verification)
            record["success"] = True
            record["result"] = result
            record["verification"] = verification
            append_audit(record)
            successes += 1
            print(f"OK: {describe(action)}")
            if args.test_one:
                print(f"VERIFY: {verification}")
        except Exception as exc:
            record["success"] = False
            record["error"] = str(exc)[:1000]
            append_audit(record)
            failures += 1
            print(f"FEJL: {describe(action)} -> {exc}")
            break

    if args.test_one and successes == 1 and failures == 0:
        set_test_passed()
        print("WRITE-BACK TEST BESTÅET EFTER READ-BACK. Automatisk write-back er stadig OFF, men kan nu låses op i UI'et.")

    print(f"Resultat: {successes} gennemført, {failures} fejl.")
    return 0 if failures == 0 else 7


if __name__ == "__main__":
    raise SystemExit(main())
