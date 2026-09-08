"""Compile validated expert session specs into coach-owned Garmin workouts.

This module is only called by guarded calendar write-back. It never changes approved
master workouts. Running ADD/ADJUST actions with a validated session_spec can create or
update a coach-owned structured workout. Strength always remains an exact named copy
of the approved strength master. Every generated workout is read back and compared on
execution semantics before its id is returned to the calendar writer.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import garmin_workout_workspace as workspace
import named_workout
import shadow_week_expert as expert
import workout_lab

DATA = Path(r"C:\GarminCoach\data")
CACHE = DATA / "generated_plan_workouts.json"

CONTINUOUS_FAMILIES = {"easy_run", "trail_easy", "long_trail", "back_to_back", "shakeout"}
QUALITY_FAMILIES = {"quality_interval", "quality_tempo"}


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


def family_goal(family: str, spec: dict[str, Any]) -> str:
    if spec.get("goal"):
        return str(spec["goal"])
    return {
        "quality_interval": "vo2max",
        "quality_tempo": "threshold",
        "long_trail": "endurance",
        "back_to_back": "endurance",
        "trail_easy": "trail_specificity",
        "shakeout": "recovery",
        "easy_run": "general",
    }.get(family, "general")


def selected_master_id(action: dict[str, Any]) -> Any:
    return (action.get("selected_template") or {}).get("workout_id")


def spec_fingerprint(action: dict[str, Any]) -> str:
    payload = {
        "family": action.get("family"),
        "session_spec": expert.sanitize_session_spec(action.get("session_spec")),
        "selected_master_id": selected_master_id(action),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _continuous_distance_candidate(action: dict[str, Any], spec: dict[str, Any], name: str) -> dict[str, Any]:
    target_km = float(spec["target_km"])
    description_bits = [
        str(action.get("focus") or "").strip(),
        str(spec.get("effort") or "").strip(),
        str(spec.get("terrain") or "").strip(),
        str(spec.get("execution_note") or "").strip(),
    ]
    description = " · ".join(x for x in description_bits if x)[:500]
    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": workspace.step_type("interval"),
        "endCondition": workspace.end_condition("distance"),
        "endConditionValue": target_km * 1000.0,
        "targetType": copy.deepcopy(workspace.NO_TARGET),
        "description": (str(spec.get("execution_note") or spec.get("effort") or "Roligt kontrolleret løb"))[:240],
    }
    candidate: dict[str, Any] = {
        "workoutName": name[:80],
        "sportType": copy.deepcopy(workspace.SPORT),
        "description": description,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": copy.deepcopy(workspace.SPORT),
            "workoutSteps": [step],
        }],
    }
    total_minutes = spec.get("total_minutes")
    if total_minutes is not None:
        candidate["estimatedDurationInSecs"] = int(round(float(total_minutes) * 60.0))
    return candidate


def _structured_time_candidate(action: dict[str, Any], spec: dict[str, Any], name: str) -> dict[str, Any]:
    family = str(action.get("family") or "")
    intent: dict[str, Any] = {
        "objective": family_goal(family, spec),
        "duration_min": spec.get("total_minutes"),
        "repetitions": spec.get("repetitions"),
        "work_min": spec.get("work_min"),
        "recovery_min": spec.get("recovery_min"),
        "warmup_min": spec.get("warmup_min"),
        "cooldown_min": spec.get("cooldown_min"),
    }
    candidate, _recipe = workspace.build_candidate(intent, name=name)
    notes = [
        str(candidate.get("description") or "").strip(),
        str(spec.get("effort") or "").strip(),
        str(spec.get("terrain") or "").strip(),
        str(spec.get("execution_note") or "").strip(),
    ]
    candidate["description"] = " · ".join(x for x in notes if x)[:500]
    return candidate


def build_candidate(action: dict[str, Any]) -> dict[str, Any]:
    name = str(action.get("plan_name") or "").strip()
    if not name:
        raise RuntimeError("Mangler plan-navn til genereret Garmin-workout.")
    family = str(action.get("family") or "")
    spec = expert.sanitize_session_spec(action.get("session_spec"))
    if not spec:
        raise RuntimeError("Mangler valideret session_spec til genereret Garmin-workout.")

    repetitions = int(spec.get("repetitions") or 1)
    if family in CONTINUOUS_FAMILIES and spec.get("target_km") is not None and repetitions <= 1:
        candidate = _continuous_distance_candidate(action, spec, name)
    else:
        candidate = _structured_time_candidate(action, spec, name)

    errors = workout_lab.validation_errors(candidate)
    if errors:
        raise RuntimeError("Garmin-kompilering fejlede: " + "; ".join(errors[:5]))
    return candidate


def verify(readback: Any, candidate: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(readback, dict):
        return False, "Garmin returnerede ikke workout-data ved read-back."
    expected = workout_lab.normalized_signature(workout_lab.semantic_signature(candidate))
    actual = workout_lab.normalized_signature(workout_lab.semantic_signature(readback))
    differences = workout_lab.signature_differences(expected, actual)
    expected_name = str(candidate.get("workoutName") or "")
    actual_name = str(readback.get("workoutName") or "")
    if actual_name != expected_name:
        differences.insert(0, f"navn {actual_name!r} != {expected_name!r}")
    if differences:
        return False, "; ".join(differences[:5])
    return True, "OK"


def _cache_entries() -> dict[str, Any]:
    payload = load(CACHE, {})
    rows = payload.get("entries") if isinstance(payload, dict) else None
    return rows if isinstance(rows, dict) else {}


def _save_entries(entries: dict[str, Any]) -> None:
    save(CACHE, {"updated_at": dt.datetime.now().astimezone().isoformat(), "entries": entries})


def _legacy_entry_by_name(name: str) -> dict[str, Any] | None:
    payload = load(named_workout.CACHE, {})
    entries = payload.get("entries") if isinstance(payload, dict) else None
    row = entries.get(name) if isinstance(entries, dict) else None
    return row if isinstance(row, dict) else None


def _update_existing(api: Any, workout_id: Any, candidate: dict[str, Any]) -> None:
    existing = api.get_workout_by_id(workout_id)
    if not isinstance(existing, dict):
        raise RuntimeError("Det eksisterende coach-workout kunne ikke læses før opdatering.")
    payload = copy.deepcopy(existing)
    for key in ("workoutName", "sportType", "description", "workoutSegments"):
        payload[key] = copy.deepcopy(candidate.get(key))
    if candidate.get("estimatedDurationInSecs") is not None:
        payload["estimatedDurationInSecs"] = candidate["estimatedDurationInSecs"]
    else:
        payload.pop("estimatedDurationInSecs", None)

    api.update_workout(workout_id, payload)
    readback = api.get_workout_by_id(workout_id)
    ok, detail = verify(readback, candidate)
    if ok:
        return
    try:
        api.update_workout(workout_id, existing)
    except Exception as rollback_error:
        raise RuntimeError(f"Read-back af ændringen fejlede ({detail}), og rollback fejlede også: {rollback_error}")
    raise RuntimeError(f"Read-back af ændringen fejlede ({detail}); det gamle workout blev gendannet.")


def ensure_generated_workout(api: Any, action: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Create/update/reuse a coach-owned generated running workout."""
    name = str(action.get("plan_name") or "").strip()
    if not name:
        raise RuntimeError("Mangler plan-navn.")
    candidate = build_candidate(action)
    fingerprint = spec_fingerprint(action)
    entries = _cache_entries()
    cached = entries.get(name)

    # Migrate an older named clone with the same coach-owned name when possible.
    if not isinstance(cached, dict) or not cached.get("workout_id"):
        legacy = _legacy_entry_by_name(name)
        if isinstance(legacy, dict) and legacy.get("workout_id"):
            cached = {
                "workout_id": legacy.get("workout_id"),
                "spec_fingerprint": None,
                "migrated_from_named_clone": True,
            }

    if isinstance(cached, dict) and cached.get("workout_id"):
        wid = cached["workout_id"]
        try:
            current = api.get_workout_by_id(wid)
        except Exception:
            current = None
        if isinstance(current, dict):
            if cached.get("spec_fingerprint") == fingerprint:
                ok, detail = verify(current, candidate)
                if ok:
                    return wid, {"created": False, "updated": False, "cached": True, "name": name, "spec_fingerprint": fingerprint}
            _update_existing(api, wid, candidate)
            entries[name] = {
                "workout_id": wid,
                "spec_fingerprint": fingerprint,
                "family": action.get("family"),
                "updated_at": dt.datetime.now().astimezone().isoformat(),
                "source": "expert_session_spec",
            }
            _save_entries(entries)
            return wid, {"created": False, "updated": True, "cached": False, "name": name, "spec_fingerprint": fingerprint}

    result = api.upload_workout(candidate)
    if not isinstance(result, dict) or not result.get("workoutId"):
        raise RuntimeError("Garmin returnerede ikke workoutId efter upload af den genererede træning.")
    wid = result["workoutId"]
    readback = api.get_workout_by_id(wid)
    ok, detail = verify(readback, candidate)
    if not ok:
        try:
            api.delete_workout(wid)
        except Exception:
            pass
        raise RuntimeError(f"Genereret Garmin-workout bestod ikke read-back: {detail}")

    entries[name] = {
        "workout_id": wid,
        "spec_fingerprint": fingerprint,
        "family": action.get("family"),
        "created_at": dt.datetime.now().astimezone().isoformat(),
        "source": "expert_session_spec",
    }
    _save_entries(entries)
    return wid, {"created": True, "updated": False, "cached": False, "name": name, "spec_fingerprint": fingerprint}


def generated_entry_by_workout_id(workout_id: Any) -> tuple[str, dict[str, Any]] | None:
    target = str(workout_id or "")
    for name, row in _cache_entries().items():
        if isinstance(row, dict) and str(row.get("workout_id") or "") == target:
            return str(name), row
    return None


def rename_generated_workout(api: Any, workout_id: Any, new_name: str) -> tuple[Any, dict[str, Any]]:
    """Rename a coach-generated workout in place when a MOVE changes W/D identity."""
    found = generated_entry_by_workout_id(workout_id)
    if not found:
        raise RuntimeError("Workoutet er ikke registreret som coach-genereret.")
    old_name, row = found
    existing = api.get_workout_by_id(workout_id)
    if not isinstance(existing, dict):
        raise RuntimeError("Det coach-genererede workout kunne ikke læses før omdøbning.")
    if str(existing.get("workoutName") or "") != new_name:
        payload = copy.deepcopy(existing)
        payload["workoutName"] = str(new_name)[:80]
        api.update_workout(workout_id, payload)
        readback = api.get_workout_by_id(workout_id)
        if not isinstance(readback, dict) or str(readback.get("workoutName") or "") != str(new_name)[:80]:
            try:
                api.update_workout(workout_id, existing)
            except Exception:
                pass
            raise RuntimeError("Garmin bekræftede ikke det nye navn ved read-back.")

    entries = _cache_entries()
    entries.pop(old_name, None)
    row = dict(row)
    row["workout_id"] = workout_id
    row["updated_at"] = dt.datetime.now().astimezone().isoformat()
    entries[str(new_name)] = row
    _save_entries(entries)
    return workout_id, {"created": False, "updated": True, "renamed": True, "name": new_name}


def resolve_for_action(api: Any, action: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Resolve a safe target workout for ADD/ADJUST/MOVE."""
    name = str(action.get("plan_name") or "").strip()
    family = str(action.get("family") or "")
    action_name = str(action.get("action") or "").upper()

    if action_name == "MOVE":
        source_id = action.get("source_workout_id")
        if not source_id:
            raise RuntimeError("MOVE mangler source_workout_id.")
        if name and generated_entry_by_workout_id(source_id):
            return rename_generated_workout(api, source_id, name)
        # If the source is an older named clone, clone again from its approved master.
        legacy_payload = load(named_workout.CACHE, {})
        legacy_entries = legacy_payload.get("entries") if isinstance(legacy_payload, dict) else None
        if isinstance(legacy_entries, dict):
            for row in legacy_entries.values():
                if isinstance(row, dict) and str(row.get("workout_id") or "") == str(source_id):
                    master_id = row.get("source_workout_id")
                    if master_id and name:
                        return named_workout.ensure_named_workout(api, master_id, name)
        if name:
            try:
                return named_workout.ensure_named_workout(api, source_id, name)
            except Exception:
                pass
        return source_id, {"created": False, "updated": False, "name": None, "source_workout_id": source_id}

    master_id = selected_master_id(action)
    if not master_id:
        raise RuntimeError("Mangler godkendt master-workout.")
    if family == "strength_master" or not action.get("session_spec"):
        return named_workout.ensure_named_workout(api, master_id, name) if name else (master_id, {"created": False, "cached": False})
    return ensure_generated_workout(api, action)
