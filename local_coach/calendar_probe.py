"""Read-only probe for scheduled Garmin workouts.

Purpose: show the local coach what is already planned in Garmin before it
suggests changes. This script never creates, edits, schedules or removes workouts.
It fails fast on auth/rate-limit errors so the UI never appears to hang for minutes.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from garminconnect import Garmin

TOKEN_DIR = os.path.expanduser("~/.garminconnect")
OUT = Path(r"C:\GarminCoach\data\scheduled_workouts.json")


def month_pairs(start: dt.date, months_back: int = 1, months_ahead: int = 2) -> list[tuple[int, int]]:
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


def walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def first(d: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = d.get(key)
        if value not in (None, "", []):
            return value
    return None


def extract_items(raw: Any) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    rows: list[dict[str, Any]] = []
    for d in walk_dicts(raw):
        date = first(d, "calendarDate", "date", "startDate", "scheduledDate")
        title = first(d, "workoutName", "title", "name", "itemName")
        workout_id = first(d, "workoutId", "workoutID", "id")
        scheduled_id = first(d, "scheduledWorkoutId", "scheduleId", "calendarItemId")
        item_type = first(d, "itemType", "type", "calendarItemType")
        if not date:
            continue
        date_text = str(date)[:10]
        try:
            dt.date.fromisoformat(date_text)
        except ValueError:
            continue
        text_blob = json.dumps(d, ensure_ascii=False).lower()
        looks_workout = "workout" in text_blob or "training" in text_blob or workout_id is not None or scheduled_id is not None
        if not looks_workout:
            continue
        key = (date_text, str(workout_id or ""), str(title or ""))
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "date": date_text,
            "title": title,
            "workout_id": workout_id,
            "scheduled_workout_id": scheduled_id,
            "item_type": item_type,
        })
    rows.sort(key=lambda x: (x.get("date") or "", str(x.get("title") or "")))
    return rows


def main() -> int:
    if not Path(TOKEN_DIR).exists():
        print(f"ERROR: Garmin token folder missing: {TOKEN_DIR}")
        return 2

    garmin = Garmin(retry_attempts=0)
    try:
        garmin.login(TOKEN_DIR)
    except Exception as exc:
        print(f"ERROR: Garmin calendar login failed: {exc}")
        return 3

    today = dt.date.today()
    raw_months: dict[str, Any] = {}
    all_items: list[dict[str, Any]] = []

    for year, month in month_pairs(today, months_back=1, months_ahead=2):
        key = f"{year:04d}-{month:02d}"
        try:
            raw = garmin.get_scheduled_workouts(year, month)
        except AttributeError:
            print("ERROR: Installed garminconnect version lacks get_scheduled_workouts().")
            return 4
        except Exception as exc:
            print(f"ERROR: Garmin calendar fetch failed for {key}: {exc}")
            return 5
        raw_months[key] = raw
        all_items.extend(extract_items(raw))

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in all_items:
        k = (item["date"], str(item.get("workout_id") or ""), str(item.get("title") or ""))
        if k not in seen:
            seen.add(k)
            unique.append(item)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "read_only": True,
        "source_status": {"garmin_login": "ok", "calendar": "ok", "months": list(raw_months)},
        "items": unique,
        "raw_by_month": raw_months,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== GARMIN CALENDAR PROBE ===")
    print(f"Scheduled workout-like items found: {len(unique)}")
    for item in unique:
        print(f"{item['date']} | {item.get('title') or '(uden titel)'} | workout={item.get('workout_id')} | scheduled={item.get('scheduled_workout_id')}")
    print(f"Saved locally: {OUT}")
    print("No Garmin data was written or changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
