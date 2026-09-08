"""Conversational Garmin test-workout workspace.

One explicitly requested test workout may be created in the Garmin workout library,
updated in place, inspected and deleted. It is never scheduled automatically. Every
write is followed by a Garmin read-back and execution-semantics verification.

Natural-language interpretation lives in training_intent.py; this module is the
constrained Garmin compiler/executor. Garmin may normalize harmless JSON metadata,
so verification compares the execution contract rather than raw DTO equality.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from garminconnect import Garmin

import training_intent
import workout_lab

ROOT = Path(r"C:\GarminCoach")
DATA = ROOT / "data"
STATE = DATA / "active_test_workout.json"
VERIFY_REPORT = DATA / "test_workout_verification.json"
TOKEN_DIR = os.path.expanduser("~/.garminconnect")

SPORT = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}


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


def login() -> Garmin:
    if not Path(TOKEN_DIR).exists():
        raise RuntimeError(f"Garmin tokenmappe mangler: {TOKEN_DIR}")
    api = Garmin(retry_attempts=0)
    api.login(TOKEN_DIR)
    return api


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def master_target(family: str | None) -> dict[str, Any] | None:
    """Reuse the athlete's proven Garmin target style when available."""
    master = workout_lab.find_master(family)
    raw = master.get("raw") if isinstance(master, dict) else None
    if not isinstance(raw, dict):
        return None
    for row in walk(raw.get("workoutSegments", [])):
        if not isinstance(row, dict):
            continue
        step_type = row.get("stepType") if isinstance(row.get("stepType"), dict) else {}
        if step_type.get("stepTypeKey") != "interval":
            continue
        target = row.get("targetType")
        if not isinstance(target, dict):
            continue
        key = str(target.get("workoutTargetTypeKey") or "")
        if not key or key == "no.target":
            continue
        out: dict[str, Any] = {"targetType": copy.deepcopy(target)}
        for field in ("targetValueOne", "targetValueTwo", "zoneNumber"):
            if row.get(field) is not None:
                out[field] = row.get(field)
        return out
    return None


def end_condition(kind: str) -> dict[str, Any]:
    if kind == "time":
        return {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
    if kind == "distance":
        return {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
    if kind == "iterations":
        return {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}
    raise ValueError(kind)


def step_type(kind: str) -> dict[str, Any]:
    ids = {"warmup": (1, 1), "cooldown": (2, 2), "interval": (3, 3), "recovery": (4, 4), "repeat": (6, 6)}
    ident, order = ids[kind]
    return {"stepTypeId": ident, "stepTypeKey": kind, "displayOrder": order}


def executable(kind: str, minutes: float, order: int, target: dict[str, Any] | None = None, description: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": step_type(kind),
        "endCondition": end_condition("time"),
        "endConditionValue": float(minutes) * 60.0,
        "targetType": copy.deepcopy((target or {}).get("targetType") or NO_TARGET),
    }
    for field in ("targetValueOne", "targetValueTwo", "zoneNumber"):
        if target and target.get(field) is not None:
            row[field] = target[field]
    if description:
        row["description"] = description[:240]
    return row


def repeat_group(repetitions: int, work_min: float, recovery_min: float, target: dict[str, Any] | None) -> dict[str, Any]:
    children = [executable("interval", work_min, 2, target, "Kontrolleret kvalitetsinterval")]
    if recovery_min > 0:
        children.append(executable("recovery", recovery_min, 3, None, "Rolig aktiv pause"))
    return {
        "type": "RepeatGroupDTO",
        "stepOrder": 2,
        "stepType": step_type("repeat"),
        "numberOfIterations": int(repetitions),
        "workoutSteps": children,
        "endCondition": end_condition("iterations"),
        "endConditionValue": float(repetitions),
        "smartRepeat": False,
    }


def total_minutes(recipe: dict[str, Any]) -> float:
    warm = float(recipe.get("warmup_min") or 0)
    reps = int(recipe.get("repetitions") or 1)
    work = float(recipe.get("work_min") or 0)
    recovery = float(recipe.get("recovery_min") or 0)
    cool = float(recipe.get("cooldown_min") or 0)
    # Garmin executes both children on every repeat, including the final recovery.
    return warm + reps * (work + recovery) + cool


def adjust_total(recipe: dict[str, Any], relative_minutes: float | None, requested_total: float | None) -> None:
    """Honor duration edits while preserving the key stimulus as long as possible."""
    current = total_minutes(recipe)
    desired = requested_total if requested_total is not None else (current + relative_minutes if relative_minutes else None)
    if desired is None:
        return
    desired = max(20.0, float(desired))
    delta = desired - current
    cool = float(recipe.get("cooldown_min") or 0)
    warm = float(recipe.get("warmup_min") or 0)
    recovery = float(recipe.get("recovery_min") or 0)
    work = float(recipe.get("work_min") or 0)
    reps = max(1, int(recipe.get("repetitions") or 1))

    if delta < 0:
        remove = min(-delta, max(0.0, cool - 5.0))
        cool -= remove
        delta += remove
        remove = min(-delta, max(0.0, warm - 8.0))
        warm -= remove
        delta += remove
        if delta < -0.01 and recovery > 1.0:
            per_rep = min(recovery - 1.0, (-delta) / reps)
            recovery -= per_rep
            delta += per_rep * reps
        if delta < -0.01 and work > 1.0:
            per_rep = min(work - 1.0, (-delta) / reps)
            work -= per_rep
            delta += per_rep * reps
    elif delta > 0:
        add_warm = min(delta, 10.0)
        warm += add_warm
        delta -= add_warm
        cool += max(0.0, delta)

    recipe["warmup_min"] = round(warm, 2)
    recipe["cooldown_min"] = round(cool, 2)
    recipe["recovery_min"] = round(recovery, 2)
    recipe["work_min"] = round(work, 2)


def build_candidate(intent: dict[str, Any], existing_spec: dict[str, Any] | None = None, name: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if existing_spec:
        recipe = dict(existing_spec)
        fresh = training_intent.training_recipe(intent)
        if intent.get("objective") and intent.get("objective") != "general":
            recipe = fresh
        else:
            for key in ("warmup_min", "repetitions", "work_min", "recovery_min", "cooldown_min"):
                if intent.get(key) is not None:
                    recipe[key] = intent[key]
    else:
        recipe = training_intent.training_recipe(intent)

    adjust_total(recipe, intent.get("relative_minutes"), intent.get("duration_min"))
    objective = str(recipe.get("objective") or intent.get("objective") or "general")
    workout_name = str(name or f"CoachTest-{objective}")[:80]
    target = master_target(recipe.get("family"))

    warm = float(recipe.get("warmup_min") or 0)
    reps = max(1, int(recipe.get("repetitions") or 1))
    work = float(recipe.get("work_min") or 0)
    recovery = max(0.0, float(recipe.get("recovery_min") or 0))
    cool = float(recipe.get("cooldown_min") or 0)

    steps: list[dict[str, Any]] = []
    if warm > 0:
        steps.append(executable("warmup", warm, 1, None, "Rolig opvarmning"))
    if reps > 1:
        steps.append(repeat_group(reps, work, recovery, target))
        cooldown_order = 3
    else:
        steps.append(executable("interval", work, 2, target, recipe.get("stimulus")))
        cooldown_order = 3
    if cool > 0:
        steps.append(executable("cooldown", cool, cooldown_order, None, "Roligt nedjog"))

    description = f"Formål: {recipe.get('title')}. {recipe.get('stimulus')}"
    candidate = {
        "workoutName": workout_name,
        "sportType": copy.deepcopy(SPORT),
        "estimatedDurationInSecs": int(round(total_minutes(recipe) * 60)),
        "description": description[:500],
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": copy.deepcopy(SPORT),
            "workoutSteps": steps,
        }],
    }
    errors = workout_lab.validation_errors(candidate)
    if errors:
        raise RuntimeError("Workout-kompilering fejlede: " + "; ".join(errors[:5]))
    return candidate, recipe


def _verify(readback: Any, candidate: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    expected_raw = workout_lab.semantic_signature(candidate)
    actual_raw = workout_lab.semantic_signature(readback if isinstance(readback, dict) else {})
    expected = workout_lab.normalized_signature(expected_raw)
    actual = workout_lab.normalized_signature(actual_raw)
    differences = workout_lab.signature_differences(expected, actual)
    name_expected = str(candidate.get("workoutName") or "")
    name_actual = str(readback.get("workoutName") or "") if isinstance(readback, dict) else ""
    name_match = bool(name_actual == name_expected)

    report = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "name_expected": name_expected,
        "name_actual": name_actual,
        "name_match": name_match,
        "expected_execution": expected,
        "actual_execution": actual,
        "differences": differences,
        "readback_ok": bool(isinstance(readback, dict) and name_match and not differences),
    }
    save(VERIFY_REPORT, report)

    if not isinstance(readback, dict):
        return False, "Garmin returnerede ikke workout-data ved read-back.", report
    if differences:
        detail = "; ".join(differences[:4])
        return False, f"Garmin ændrede udførelsesstrukturen ved read-back: {detail}", report
    if not name_match:
        return False, f"Garmin read-back havde navnet {name_actual!r}, forventet {name_expected!r}.", report
    return True, "OK", report


def create_or_replace_test(intent: dict[str, Any]) -> dict[str, Any]:
    current = load(STATE, {})
    if isinstance(current, dict) and current.get("workout_id"):
        return update_test(intent, force_objective=True)

    candidate, recipe = build_candidate(intent)
    api = login()
    result = api.upload_workout(candidate)
    if not isinstance(result, dict) or not result.get("workoutId"):
        raise RuntimeError("Garmin returnerede ikke workoutId efter upload.")
    wid = result["workoutId"]
    readback = api.get_workout_by_id(wid)
    ok, detail, report = _verify(readback, candidate)
    if not ok:
        cleanup_error = None
        try:
            api.delete_workout(wid)
        except Exception as exc:
            cleanup_error = str(exc)[:300]
        if cleanup_error:
            raise RuntimeError(
                detail + f" Test-workout id {wid} kunne ikke slettes automatisk og kan ligge tilbage i Garmin: {cleanup_error}"
            )
        raise RuntimeError(detail + " Det afviste test-workout blev slettet igen fra Garmin.")

    state = {
        "workout_id": wid,
        "name": candidate["workoutName"],
        "recipe": recipe,
        "created_at": dt.datetime.now().astimezone().isoformat(),
        "updated_at": dt.datetime.now().astimezone().isoformat(),
        "readback_ok": True,
        "verification_report": str(VERIFY_REPORT),
    }
    save(STATE, state)
    return {"action": "created", **state, "signature": report.get("actual_execution")}


def update_test(intent: dict[str, Any], force_objective: bool = False) -> dict[str, Any]:
    current = load(STATE, {})
    if not isinstance(current, dict) or not current.get("workout_id"):
        if force_objective:
            return create_or_replace_test(intent)
        raise RuntimeError("Der findes ikke et aktivt CoachTest-workout endnu. Bed mig først om at lave et test-løb.")

    wid = current["workout_id"]
    old_recipe = current.get("recipe") if isinstance(current.get("recipe"), dict) else None
    candidate, recipe = build_candidate(intent, old_recipe, current.get("name"))
    api = login()
    try:
        existing = api.get_workout_by_id(wid)
    except Exception as exc:
        # State can become stale if the athlete manually deletes the test workout.
        try:
            STATE.unlink()
        except FileNotFoundError:
            pass
        if force_objective:
            return create_or_replace_test(intent)
        raise RuntimeError(f"Det aktive test-workout findes ikke længere i Garmin: {exc}") from exc
    if not isinstance(existing, dict):
        raise RuntimeError("Det aktive test-workout kunne ikke læses fra Garmin.")

    payload = copy.deepcopy(existing)
    for key in ("workoutName", "sportType", "estimatedDurationInSecs", "description", "workoutSegments"):
        payload[key] = copy.deepcopy(candidate[key])
    api.update_workout(wid, payload)
    readback = api.get_workout_by_id(wid)
    ok, detail, report = _verify(readback, candidate)
    if not ok:
        rollback_ok = False
        rollback_error = None
        try:
            api.update_workout(wid, existing)
            rollback_ok = True
        except Exception as exc:
            rollback_error = str(exc)[:300]
        if rollback_ok:
            raise RuntimeError(detail + " Ændringen blev rullet tilbage til den tidligere test-workout.")
        raise RuntimeError(detail + f" Rollback fejlede, så kontrollér test-workoutet i Garmin manuelt: {rollback_error}")

    current.update({
        "recipe": recipe,
        "updated_at": dt.datetime.now().astimezone().isoformat(),
        "readback_ok": True,
        "verification_report": str(VERIFY_REPORT),
    })
    save(STATE, current)
    return {"action": "updated", **current, "signature": report.get("actual_execution")}


def delete_test() -> dict[str, Any]:
    current = load(STATE, {})
    if not isinstance(current, dict) or not current.get("workout_id"):
        return {"action": "nothing_to_delete"}
    wid = current["workout_id"]
    api = login()
    api.delete_workout(wid)
    try:
        STATE.unlink()
    except FileNotFoundError:
        pass
    return {"action": "deleted", "workout_id": wid, "name": current.get("name")}


def describe(result: dict[str, Any]) -> str:
    action = result.get("action")
    if action == "deleted":
        return f"Jeg har slettet test-workoutet {result.get('name')} fra Garmin Træninger."
    if action == "nothing_to_delete":
        return "Der ligger ikke noget aktivt CoachTest-workout, som jeg kan slette."
    verb = "oprettet" if action == "created" else "justeret"
    recipe = result.get("recipe") or {}
    total = total_minutes(recipe)
    line = f"Samlet ca. {total:.0f} min: {recipe.get('warmup_min')} min opvarmning"
    reps = int(recipe.get("repetitions") or 1)
    if reps > 1:
        line += f", {reps} × {recipe.get('work_min')} min arbejde med {recipe.get('recovery_min')} min aktiv pause"
    else:
        line += f", {recipe.get('work_min')} min hoveddel"
    line += f" og {recipe.get('cooldown_min')} min nedjog."
    return "\n".join([
        f"Jeg har {verb} {result.get('name')} direkte i Garmin Træninger og læst det tilbage: udførelsen er verificeret.",
        f"Formål: {recipe.get('title')}.",
        line,
        "Det er kun oprettet under Træninger; jeg har ikke lagt det i kalenderen.",
    ])


def handle(message: str) -> str | None:
    intent = training_intent.interpret(message)
    op = intent.get("operation")
    if op == "create_test_workout":
        return describe(create_or_replace_test(intent))
    if op == "update_test_workout":
        return describe(update_test(intent))
    if op == "delete_test_workout":
        return describe(delete_test())
    return None
