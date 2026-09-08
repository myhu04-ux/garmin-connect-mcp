"""Create/reuse a verified named Garmin workout copy from an approved master.

This module is only used by guarded write-back. Master workouts are never updated.
Coach-owned copies are semantically read back from Garmin before their id can be used
in the calendar. A cached coach copy that drifted may be restored in place, with
rollback metadata returned to the outer transaction layer.
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


def _semantic_verify(expected: dict[str, Any], actual: Any) -> tuple[bool, str]:
    """Late import avoids a module cycle: workout_lab imports helpers from here."""
    if not isinstance(actual, dict):
        return False, "Garmin returnerede ikke workout-data ved read-back."
    import workout_lab  # local import by design

    expected_sig = workout_lab.normalized_signature(workout_lab.semantic_signature(expected))
    actual_sig = workout_lab.normalized_signature(workout_lab.semantic_signature(actual))
    differences = workout_lab.signature_differences(expected_sig, actual_sig)
    expected_name = str(expected.get("workoutName") or "")
    actual_name = str(actual.get("workoutName") or "")
    if actual_name != expected_name:
        differences.insert(0, f"navn {actual_name!r} != {expected_name!r}")
    if differences:
        return False, "; ".join(differences[:6])
    return True, "OK"


def _read(api: Any, workout_id: Any) -> dict[str, Any] | None:
    try:
        value = api.get_workout_by_id(workout_id)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def ensure_named_workout(api: Any, source_workout_id: Any, workout_name: str) -> tuple[Any, dict[str, Any]]:
    """Return only a Garmin id whose execution contract passed live read-back."""
    name = str(workout_name or "").strip()
    if not name:
        raise RuntimeError("Mangler plan-navn til personlig Garmin-workout.")

    master = find_master(source_workout_id)
    if not master:
        raise RuntimeError(f"Master-workout {source_workout_id} findes ikke i det godkendte bibliotek.")
    raw = master.get("raw")
    if not isinstance(raw, dict):
        raise RuntimeError(f"Master-workout {source_workout_id} mangler rå Garmin-struktur.")
    expected = sanitized_copy(raw, name)

    cache = load(CACHE, {})
    entries = cache.get("entries") if isinstance(cache, dict) else None
    if not isinstance(entries, dict):
        entries = {}
    cached = entries.get(name)

    if (
        isinstance(cached, dict)
        and str(cached.get("source_workout_id") or "") == str(source_workout_id or "")
        and cached.get("workout_id")
    ):
        workout_id = cached["workout_id"]
        current = _read(api, workout_id)
        if isinstance(current, dict):
            ok, detail = _semantic_verify(expected, current)
            if ok:
                return workout_id, {
                    "created": False,
                    "updated": False,
                    "cached": True,
                    "readback_ok": True,
                    "name": name,
                    "source_workout_id": source_workout_id,
                }

            # This id is a coach-owned copy (proven by our local cache), never the
            # approved master. Restore it to the current master semantics in place.
            old = copy.deepcopy(current)
            try:
                api.update_workout(workout_id, copy.deepcopy(expected))
                readback = _read(api, workout_id)
                verified, verify_detail = _semantic_verify(expected, readback)
                if not verified:
                    raise RuntimeError(verify_detail)
            except Exception as exc:
                try:
                    api.update_workout(workout_id, old)
                except Exception as rollback_exc:
                    raise RuntimeError(
                        f"Coach-kopien afveg ({detail}); opdatering fejlede ({exc}) og rollback fejlede ({rollback_exc})."
                    ) from exc
                raise RuntimeError(
                    f"Coach-kopien afveg fra master ({detail}); Garmin-opdatering blev rullet tilbage: {exc}"
                ) from exc

            cached = dict(cached)
            cached["verified_at"] = dt.datetime.now().astimezone().isoformat()
            cached["source_title"] = master.get("title")
            entries[name] = cached
            save(CACHE, {"updated_at": dt.datetime.now().astimezone().isoformat(), "entries": entries})
            return workout_id, {
                "created": False,
                "updated": True,
                "cached": False,
                "readback_ok": True,
                "name": name,
                "source_workout_id": source_workout_id,
            }

        # Stale cache id: drop only the local reference, never guess/delete a Garmin
        # object we can no longer read. A fresh verified coach copy is created below.
        entries.pop(name, None)

    result = api.upload_workout(expected)
    if not isinstance(result, dict) or not result.get("workoutId"):
        raise RuntimeError(f"Garmin returnerede ikke workoutId efter upload af {name}.")
    new_id = result["workoutId"]

    readback = _read(api, new_id)
    ok, detail = _semantic_verify(expected, readback)
    if not ok:
        try:
            api.delete_workout(new_id)
        except Exception:
            pass
        raise RuntimeError(f"Den nye Garmin-kopi {name} bestod ikke workout read-back: {detail}")

    entries[name] = {
        "workout_id": new_id,
        "source_workout_id": source_workout_id,
        "source_title": master.get("title"),
        "created_at": dt.datetime.now().astimezone().isoformat(),
        "verified_at": dt.datetime.now().astimezone().isoformat(),
    }
    save(CACHE, {"updated_at": dt.datetime.now().astimezone().isoformat(), "entries": entries})
    return new_id, {
        "created": True,
        "updated": False,
        "cached": False,
        "readback_ok": True,
        "name": name,
        "source_workout_id": source_workout_id,
    }
