"""Guarded Garmin calendar write-back for validated coach actions.

Default mode is dry-run. Real writes require either:
  --test-one : applies exactly one safe validated calendar action, reads Garmin
               back, verifies the named workout/date, and only then unlocks UI.
  --apply    : requires both writeback_test_passed=true and
               garmin_writeback_enabled=true.

Approved master workouts are never renamed or deleted. ADD/ADJUST/MOVE create or
reuse a named copy such as ThyTrailW3D4, schedule the new copy first, then
unschedule the old calendar entry. Every real action is read back from Garmin.
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
from named_workout import ensure_named_workout

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
    return (action.get("selected_template") or {}).get("workout_id")


def source_master_id(action: dict[str, Any]) -> Any:
    name = str(action.get("action") or "").upper()
    if name in {"ADD", "ADJUST"}:
        return selected_workout(action)
    if name == "MOVE":
        return action.get("source_workout_id")
    return None


def resolve_target_workout(api: Garmin, action: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    master_id = source_master_id(action)
    if not master_id:
        raise RuntimeError("Mangler godkendt master-workout til kalenderændringen.")
    plan_name = str(action.get("plan_name") or "").strip()
    if not plan_name:
        return master_id, {"created": False, "cached": False, "name": None, "source_workout_id": master_id}
    return ensure_named_workout(api, master_id, plan_name)


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


def apply_action(api: Garmin, calendar: dict[str, Any], action: dict[str, Any], resolved_workout_id: Any | None) -> dict[str, Any]:
    name = str(action.get("action") or "").upper()
    target_date = str(action.get("date") or "")[:10]
    source = find_source(calendar, action)

    if name == "ADD":
        return schedule_then_unschedule(api, calendar, resolved_workout_id, target_date, None)
    if name == "MOVE":
        if source is None:
            raise RuntimeError("Kunne ikke entydigt finde det eksisterende kalenderpas, der skal flyttes.")
        return schedule_then_unschedule(api, calendar, resolved_workout_id, target_date, source)
    if name == "ADJUST":
        if source is None:
            raise RuntimeError("Kunne ikke entydigt finde det eksisterende kalenderpas, der skal justeres.")
        return schedule_then_unschedule(api, calendar, resolved_workout_id, target_date, source)
    if name == "REMOVE":
        if source is None:
            raise RuntimeError("Kunne ikke entydigt finde kalenderpasset, der skal fjernes.")
        sid = source.get("scheduled_workout_id")
        if not sid:
            raise RuntimeError("Det planlagte pas mangler scheduled_workout_id og fjernes derfor ikke.")
        api.unschedule_workout(sid)
        return {"old_unscheduled": True}
    raise RuntimeError(f"Ikke understøttet action: {name}")


def fetch_month_items(api: Garmin, day: dt.date) -> list[dict[str, Any]]:
    return extract_items(api.get_scheduled_workouts(day.year, day.month))


def fresh_calendar(api: Garmin, action: dict[str, Any]) -> dict[str, Any]:
    dates = [parse_date(action.get("date")), parse_date(action.get("source_date"))]
    months: set[tuple[int, int]] = {(d.year, d.month) for d in dates if d}
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for year, month in sorted(months):
        for item in extract_items(api.get_scheduled_workouts(year, month)):
            k = (str(item.get("date") or ""), str(item.get("workout_id") or ""), str(item.get("scheduled_workout_id") or ""))
            if k not in seen:
                seen.add(k)
                items.append(item)
    return {"items": items}


def verify_action(
    api: Garmin,
    action: dict[str, Any],
    resolved_workout_id: Any | None,
    original_calendar: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    name = str(action.get("action") or "").upper()
    if name == "REMOVE":
        source = find_source(original_calendar, action)
        if not source or not source.get("scheduled_workout_id"):
            return False, "Mangler gammel kalenderpost til read-back.", fresh_calendar(api, action)
        refreshed = fresh_calendar(api, action)
        old_id = str(source.get("scheduled_workout_id"))
        if any(str(i.get("scheduled_workout_id") or "") == old_id for i in calendar_items(refreshed)):
            return False, "Garmin read-back viser stadig den fjernede kalenderpost.", refreshed
        return True, "Garmin read-back bekræftede fjernelsen.", refreshed

    target = parse_date(action.get("date"))
    expected = str(resolved_workout_id or "")
    if not target or not expected:
        return False, "Mangler mål-dato eller forventet workout-id til verifikation.", fresh_calendar(api, action)

    refreshed = fresh_calendar(api, action)
    target_item = next((
        item for item in calendar_items(refreshed)
        if str(item.get("workout_id") or "") == expected and str(item.get("date") or "")[:10] == target.isoformat()
    ), None)
    if not target_item:
        return False, "Garmin read-back fandt ikke det forventede workout på mål-datoen.", refreshed

    expected_name = str(action.get("plan_name") or "").strip()
    actual_name = str(target_item.get("title") or "").strip()
    if expected_name and actual_name and actual_name != expected_name:
        return False, f"Workout ligger på datoen, men navnet er '{actual_name}' i stedet for '{expected_name}'.", refreshed

    if name in {"MOVE", "ADJUST"}:
        old_item = find_source(original_calendar, action)
        old_id = str((old_item or {}).get("scheduled_workout_id") or "")
        if old_id and any(str(i.get("scheduled_workout_id") or "") == old_id for i in calendar_items(refreshed)):
            return False, "Det nye pas findes, men den gamle kalenderpost er stadig til stede.", refreshed

    return True, "Garmin read-back bekræftede navn, workout og dato.", refreshed


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
        candidates = [a for a in actions if str(a.get("action") or "").upper() in {"ADD", "ADJUST", "MOVE"}]
        if not candidates:
            print("Ingen sikker ADD/ADJUST/MOVE er tilgængelig til write-back-testen endnu.")
            return 4
        # Prefer actions that test both named cloning and calendar scheduling.
        priority = {"ADD": 0, "ADJUST": 1, "MOVE": 2}
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
        original_calendar = calendar
        record = {
            "mode": "test-one" if args.test_one else "auto-apply",
            "action": action,
            "description": describe(action),
        }
        try:
            resolved_id = None
            clone_meta: dict[str, Any] = {}
            if str(action.get("action") or "").upper() != "REMOVE":
                resolved_id, clone_meta = resolve_target_workout(api, action)
            result = apply_action(api, calendar, action, resolved_id)
            verified, verification, refreshed = verify_action(api, action, resolved_id, original_calendar)
            if not verified:
                raise RuntimeError(verification)

            record["success"] = True
            record["resolved_workout_id"] = resolved_id
            record["named_copy"] = clone_meta
            record["result"] = result
            record["verification"] = verification
            append_audit(record)
            calendar = refreshed
            successes += 1
            print(f"OK: {describe(action)}")
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
