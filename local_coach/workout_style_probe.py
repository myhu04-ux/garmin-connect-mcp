r"""Read existing Garmin workout structures to learn the athlete's workout style.

READ ONLY: this script does not create, edit, schedule, unschedule, or delete
anything in Garmin Connect. It scans scheduled workouts around the current date,
extracts workout IDs, fetches detailed workout definitions when supported by the
installed garminconnect version, and stores them locally for later template use.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Callable

from garminconnect import Garmin

TOKEN_DIR = os.path.expanduser("~/.garminconnect")
OUT = Path(r"C:\GarminCoach\data\workout_styles_raw.json")


def month_pairs(start: dt.date, months_back: int = 2, months_ahead: int = 2) -> list[tuple[int, int]]:
    first = dt.date(start.year, start.month, 1)
    pairs: list[tuple[int, int]] = []
    for offset in range(-months_back, months_ahead + 1):
        year = first.year
        month = first.month + offset
        while month < 1:
            year -= 1
            month += 12
        while month > 12:
            year += 1
            month -= 12
        pairs.append((year, month))
    return pairs


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def first(d: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = d.get(key)
        if value not in (None, "", []):
            return value
    return None


def extract_scheduled(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for d in walk(raw):
        workout_id = first(d, "workoutId", "workoutID")
        if workout_id is None:
            continue
        date = first(d, "calendarDate", "date", "scheduledDate", "startDate")
        title = first(d, "workoutName", "title", "name", "itemName")
        scheduled_id = first(d, "scheduledWorkoutId", "scheduleId", "calendarItemId")
        date_text = str(date)[:10] if date else ""
        key = (str(workout_id), date_text, str(title or ""))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "workout_id": workout_id,
                "date": date_text or None,
                "title": title,
                "scheduled_workout_id": scheduled_id,
            }
        )
    return rows


def find_detail_fetcher(client: Garmin) -> tuple[str | None, Callable[[Any], Any] | None]:
    # Method names vary across garminconnect versions/forks. Prefer the most
    # specific known methods and discover safely instead of assuming one API.
    candidates = [
        "get_workout_by_id",
        "get_workout",
        "get_workout_details",
    ]
    for name in candidates:
        fn = getattr(client, name, None)
        if callable(fn):
            return name, fn
    return None, None


def step_summary(detail: Any) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if not isinstance(detail, dict):
        return steps
    for d in walk(detail):
        order = first(d, "stepOrder", "order")
        step_type = d.get("stepType") if isinstance(d.get("stepType"), dict) else {}
        end_condition = d.get("endCondition") if isinstance(d.get("endCondition"), dict) else {}
        target_type = d.get("targetType") if isinstance(d.get("targetType"), dict) else {}
        if order is None and not step_type:
            continue
        row = {
            "order": order,
            "step_type": first(step_type, "stepTypeKey", "key", "name") or first(d, "stepTypeKey"),
            "end_condition": first(end_condition, "conditionTypeKey", "key", "name") or first(d, "conditionTypeKey"),
            "end_value": first(d, "endConditionValue", "durationValue", "distanceValue"),
            "target_type": first(target_type, "workoutTargetTypeKey", "key", "name") or first(d, "workoutTargetTypeKey"),
            "target_low": first(d, "targetValueOne", "targetLow"),
            "target_high": first(d, "targetValueTwo", "targetHigh"),
            "description": first(d, "description", "stepDescription"),
        }
        if any(v is not None for v in row.values()):
            steps.append(row)
    # Nested walking can create duplicates; keep stable unique rows.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in steps:
        key = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def main() -> int:
    if not Path(TOKEN_DIR).exists():
        print(f"ERROR: Garmin token folder missing: {TOKEN_DIR}")
        return 2

    garmin = Garmin(retry_attempts=0)
    garmin.login(TOKEN_DIR)

    scheduled: list[dict[str, Any]] = []
    for year, month in month_pairs(dt.date.today(), months_back=2, months_ahead=2):
        try:
            raw = garmin.get_scheduled_workouts(year, month)
        except Exception as exc:
            print(f"WARNING: calendar {year:04d}-{month:02d} failed: {exc}")
            continue
        scheduled.extend(extract_scheduled(raw))

    # De-duplicate by workout id + scheduled date.
    dedup: list[dict[str, Any]] = []
    seen_sched: set[tuple[str, str]] = set()
    for row in scheduled:
        key = (str(row.get("workout_id")), str(row.get("date") or ""))
        if key not in seen_sched:
            seen_sched.add(key)
            dedup.append(row)

    method_name, fetcher = find_detail_fetcher(garmin)
    details: list[dict[str, Any]] = []
    fetched_ids: set[str] = set()

    if fetcher:
        for row in dedup:
            workout_id = row.get("workout_id")
            wid = str(workout_id)
            if not workout_id or wid in fetched_ids:
                continue
            fetched_ids.add(wid)
            try:
                detail = fetcher(workout_id)
            except Exception as exc:
                details.append({"workout_id": workout_id, "error": str(exc)[:500]})
                continue
            details.append(
                {
                    "workout_id": workout_id,
                    "title": first(detail, "workoutName", "name", "title") if isinstance(detail, dict) else None,
                    "sport": (detail.get("sportType") or {}).get("sportTypeKey") if isinstance(detail, dict) else None,
                    "estimated_duration_s": first(detail, "estimatedDurationInSecs", "estimatedDuration") if isinstance(detail, dict) else None,
                    "steps": step_summary(detail),
                    "raw": detail,
                }
            )

    available_methods = sorted(
        name for name in dir(garmin)
        if "workout" in name.lower() and callable(getattr(garmin, name, None))
    )

    result = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "read_only": True,
        "detail_method": method_name,
        "scheduled": dedup,
        "details": details,
        "available_workout_methods": available_methods,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== GARMIN WORKOUT STYLE PROBE ===")
    print(f"Planlagte workout-poster fundet: {len(dedup)}")
    print(f"Detalje-metode: {method_name or 'ingen kendt metode fundet'}")
    print(f"Workout-detaljer hentet: {len(details)}")
    for item in details[:20]:
        if item.get("error"):
            print(f"workout={item.get('workout_id')} | FEJL: {item['error']}")
        else:
            print(
                f"workout={item.get('workout_id')} | {item.get('title') or '(uden titel)'} | "
                f"sport={item.get('sport')} | trin={len(item.get('steps') or [])}"
            )
    if not method_name:
        print("Tilgaengelige workout-metoder:")
        for name in available_methods:
            print(f"  - {name}")
    print(f"Gemt lokalt: {OUT}")
    print("Intet blev skrevet eller aendret i Garmin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
