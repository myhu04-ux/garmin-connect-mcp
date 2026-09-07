"""Create/reuse a named Garmin workout copy from an approved master.

This module is only used by guarded write-back. Master workouts are never updated.
A new copy removes Garmin-owned IDs exactly as demonstrated by python-garminconnect,
gets the athlete-facing plan name (for example ThyTrailW3D4), and is cached locally
so repeated coach runs reuse the same uploaded copy.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
from typing import Any

DATA = Path(r"C:\GarminCoach\data")
TEMPLATES = DATA / "approved_workout_templates.json"
CACHE = DATA / "named_workouts.json"


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def find_master(workout_id: Any) -> dict[str, Any] | None:
    library = load(TEMPLATES, {})
    target = str(workout_id or "")
    for rows in (library.get("families") or {}).values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and str(row.get("workout_id") or "") == target:
                return row
    return None


def clean_step_ids(value: Any) -> None:
    if isinstance(value, list):
        for child in value:
            clean_step_ids(child)
    elif isinstance(value, dict):
        value.pop("stepId", None)
        for child in value.values():
            if isinstance(child, (dict, list)):
                clean_step_ids(child)


def sanitized_copy(master_raw: dict[str, Any], workout_name: str) -> dict[str, Any]:
    data = copy.deepcopy(master_raw)
    for field in ("workoutId", "ownerId", "updatedDate", "createdDate"):
        data.pop(field, None)
    clean_step_ids(data.get("workoutSegments", []))
    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.0")
    data["createdDate"] = now
    data["updatedDate"] = now
    data["workoutName"] = str(workout_name)[:80]
    return data


def ensure_named_workout(api: Any, source_workout_id: Any, workout_name: str) -> tuple[Any, dict[str, Any]]:
    """Return workout id plus metadata. Upload only when not already cached."""
    name = str(workout_name or "").strip()
    if not name:
        raise RuntimeError("Mangler plan-navn til personlig Garmin-workout.")

    cache = load(CACHE, {})
    entries = cache.get("entries") if isinstance(cache, dict) else None
    if not isinstance(entries, dict):
        entries = {}
    cached = entries.get(name)
    if isinstance(cached, dict) and str(cached.get("source_workout_id") or "") == str(source_workout_id or "") and cached.get("workout_id"):
        return cached["workout_id"], {"created": False, "cached": True, "name": name, "source_workout_id": source_workout_id}

    master = find_master(source_workout_id)
    if not master:
        raise RuntimeError(f"Master-workout {source_workout_id} findes ikke i det godkendte bibliotek.")
    raw = master.get("raw")
    if not isinstance(raw, dict):
        raise RuntimeError(f"Master-workout {source_workout_id} mangler rå Garmin-struktur.")

    payload = sanitized_copy(raw, name)
    result = api.upload_workout(payload)
    if not isinstance(result, dict) or not result.get("workoutId"):
        raise RuntimeError(f"Garmin returnerede ikke workoutId efter upload af {name}.")
    new_id = result["workoutId"]

    entries[name] = {
        "workout_id": new_id,
        "source_workout_id": source_workout_id,
        "source_title": master.get("title"),
        "created_at": dt.datetime.now().astimezone().isoformat(),
    }
    save(CACHE, {"updated_at": dt.datetime.now().astimezone().isoformat(), "entries": entries})
    return new_id, {"created": True, "cached": False, "name": name, "source_workout_id": source_workout_id}
