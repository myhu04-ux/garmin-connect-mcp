"""Rollback state for guarded Garmin workout + calendar transactions.

Resolving a target workout may create a new coach-owned workout, update an existing
one, or rename it before the calendar operation begins. This module snapshots the
small amount of state needed to undo that resolution if scheduling/verification later
fails. Approved masters are never deleted or rewritten by rollback.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import named_workout
import planned_workout_compiler as compiler

_PENDING: dict[str, dict[str, Any]] = {}


def _file_snapshot(path: Path) -> dict[str, Any]:
    return {
        "exists": path.exists(),
        "value": compiler.load(path, {}) if path.exists() else {},
    }


def _restore_file(path: Path, snapshot: dict[str, Any]) -> None:
    if snapshot.get("exists"):
        compiler.save(path, copy.deepcopy(snapshot.get("value") or {}))
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _entry_id(payload: Any, name: str) -> Any:
    if not isinstance(payload, dict):
        return None
    entries = payload.get("entries")
    row = entries.get(name) if isinstance(entries, dict) else None
    return row.get("workout_id") if isinstance(row, dict) else None


def _safe_workout(api: Any, workout_id: Any) -> dict[str, Any] | None:
    if workout_id in (None, ""):
        return None
    try:
        row = api.get_workout_by_id(workout_id)
        return copy.deepcopy(row) if isinstance(row, dict) else None
    except Exception:
        return None


def resolve(api: Any, action: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Resolve through the expert compiler while retaining a reversible snapshot."""
    name = str(action.get("plan_name") or "").strip()
    generated_snapshot = _file_snapshot(compiler.CACHE)
    named_snapshot = _file_snapshot(named_workout.CACHE)

    generated_value = generated_snapshot.get("value") or {}
    named_value = named_snapshot.get("value") or {}
    pre_id = _entry_id(generated_value, name) or _entry_id(named_value, name)
    if str(action.get("action") or "").upper() == "MOVE" and action.get("source_workout_id"):
        pre_id = action.get("source_workout_id")
    pre_raw = _safe_workout(api, pre_id)

    resolved_id, meta = compiler.resolve_for_action(api, action)
    meta = dict(meta or {})
    created = bool(meta.get("created"))

    # If the resolver returned an existing id different from the guessed pre-id,
    # take a conservative post-hoc classification: only explicit created=True may
    # ever be deleted during rollback.
    same_existing = pre_id is not None and str(pre_id) == str(resolved_id)
    key = str(resolved_id or "")
    if key:
        _PENDING[key] = {
            "resolved_id": resolved_id,
            "created": created,
            "same_existing": same_existing,
            "old_workout": pre_raw if same_existing and not created else None,
            "generated_cache": generated_snapshot,
            "named_cache": named_snapshot,
            "action": copy.deepcopy(action),
        }
    meta["transaction_pending"] = bool(key)
    return resolved_id, meta


def commit(resolved_id: Any) -> None:
    _PENDING.pop(str(resolved_id or ""), None)


def rollback(api: Any, resolved_id: Any) -> tuple[bool, str]:
    key = str(resolved_id or "")
    pending = _PENDING.pop(key, None)
    if not pending:
        return True, "Ingen workout-ændring krævede rollback."

    errors: list[str] = []
    try:
        if pending.get("created"):
            try:
                api.delete_workout(resolved_id)
            except Exception as exc:
                errors.append(f"kunne ikke slette nyt workout: {exc}")
        elif isinstance(pending.get("old_workout"), dict):
            try:
                api.update_workout(resolved_id, copy.deepcopy(pending["old_workout"]))
                check = api.get_workout_by_id(resolved_id)
                old_name = str(pending["old_workout"].get("workoutName") or "")
                new_name = str(check.get("workoutName") or "") if isinstance(check, dict) else ""
                if old_name and new_name != old_name:
                    errors.append("workout-navnet blev ikke gendannet ved read-back")
            except Exception as exc:
                errors.append(f"kunne ikke gendanne eksisterende workout: {exc}")
    finally:
        try:
            _restore_file(compiler.CACHE, pending.get("generated_cache") or {})
        except Exception as exc:
            errors.append(f"kunne ikke gendanne generated-cache: {exc}")
        try:
            _restore_file(named_workout.CACHE, pending.get("named_cache") or {})
        except Exception as exc:
            errors.append(f"kunne ikke gendanne named-cache: {exc}")

    if errors:
        return False, "; ".join(errors)
    return True, "Workout og lokal cache blev rullet tilbage."


def pending(resolved_id: Any) -> bool:
    return str(resolved_id or "") in _PENDING
