"""Self-healing fallback for conversational CoachTest workout writes.

If Garmin changes the execution contract of a freshly compiled workout, the coach
can retry using the athlete's own approved Garmin master for the requested family.
Only execution fields needed for the requested stimulus are changed; Garmin's proven
DTO shape and targets are otherwise preserved. Every retry is read back and verified.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

import garmin_workout_workspace as workspace
import training_intent
import workout_lab
from named_workout import sanitized_copy


def _step_key(step: dict[str, Any]) -> str:
    st = step.get("stepType") if isinstance(step.get("stepType"), dict) else {}
    return str(st.get("stepTypeKey") or step.get("stepTypeKey") or "").casefold()


def _walk_steps(value: Any):
    if isinstance(value, dict):
        if value.get("stepOrder") is not None or value.get("type") in {"ExecutableStepDTO", "RepeatGroupDTO"}:
            yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _walk_steps(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_steps(child)


def _set_time(step: dict[str, Any], minutes: float) -> None:
    step["endCondition"] = workspace.end_condition("time")
    step["endConditionValue"] = float(minutes) * 60.0


def adapt_master(raw: dict[str, Any], recipe: dict[str, Any], name: str) -> dict[str, Any]:
    candidate = sanitized_copy(raw, name)
    segments = candidate.get("workoutSegments")
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("Den godkendte Garmin-master mangler workoutSegments.")

    warmup = float(recipe.get("warmup_min") or 0)
    reps = max(1, int(recipe.get("repetitions") or 1))
    work = float(recipe.get("work_min") or 0)
    recovery = max(0.0, float(recipe.get("recovery_min") or 0))
    cooldown = float(recipe.get("cooldown_min") or 0)

    all_steps = list(_walk_steps(segments))
    warm_steps = [s for s in all_steps if _step_key(s) == "warmup"]
    cool_steps = [s for s in all_steps if _step_key(s) == "cooldown"]
    repeat_steps = [s for s in all_steps if s.get("type") == "RepeatGroupDTO" or _step_key(s) == "repeat"]

    if warm_steps and warmup > 0:
        _set_time(warm_steps[0], warmup)
    if cool_steps and cooldown > 0:
        _set_time(cool_steps[-1], cooldown)

    if reps > 1:
        if not repeat_steps:
            raise RuntimeError("Garmin-masteren har ingen repeat-gruppe, så den kan ikke sikkert omsættes til gentagne intervaller.")
        group = repeat_steps[0]
        group["numberOfIterations"] = reps
        group["endCondition"] = workspace.end_condition("iterations")
        group["endConditionValue"] = float(reps)
        children = group.get("workoutSteps") if isinstance(group.get("workoutSteps"), list) else []
        intervals = [s for s in children if isinstance(s, dict) and _step_key(s) == "interval"]
        recoveries = [s for s in children if isinstance(s, dict) and _step_key(s) in {"recovery", "rest"}]
        if not intervals:
            raise RuntimeError("Repeat-gruppen i Garmin-masteren mangler et intervaltrin.")
        _set_time(intervals[0], work)
        if recovery > 0:
            if not recoveries:
                raise RuntimeError("Repeat-gruppen mangler et recovery-trin, som kan genbruges sikkert.")
            _set_time(recoveries[0], recovery)
    else:
        intervals = [s for s in all_steps if _step_key(s) == "interval"]
        if not intervals:
            raise RuntimeError("Garmin-masteren mangler et hoved-/intervaltrin.")
        _set_time(intervals[0], work)

    candidate["estimatedDurationInSecs"] = int(round(workspace.total_minutes(recipe) * 60))
    candidate["description"] = (
        f"Formål: {recipe.get('title')}. {recipe.get('stimulus')} "
        "Bygget adaptivt fra en godkendt Garmin-master efter read-back analyse."
    )[:500]
    errors = workout_lab.validation_errors(candidate)
    if errors:
        raise RuntimeError("Fallback-masteren kunne ikke valideres: " + "; ".join(errors[:4]))
    return candidate


def _recipe_for(intent: dict[str, Any], current: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    old = current.get("recipe") if isinstance(current, dict) and isinstance(current.get("recipe"), dict) else None
    name = str((current or {}).get("name") or "") or None
    _, recipe = workspace.build_candidate(intent, old, name)
    workout_name = name or f"CoachTest-{recipe.get('objective') or 'general'}"
    return recipe, workout_name


def recover(message: str) -> str:
    """Retry one failed create/update using the approved Garmin master shape."""
    intent = training_intent.interpret(message, use_model=False)
    if intent.get("operation") not in {"create_test_workout", "update_test_workout"}:
        raise RuntimeError("Self-heal bruges kun til oprettelse eller justering af test-workout.")

    current = workspace.load(workspace.STATE, {})
    recipe, name = _recipe_for(intent, current if isinstance(current, dict) else None)
    family = recipe.get("family")
    master = workout_lab.find_master(str(family or ""))
    raw = master.get("raw") if isinstance(master, dict) else None
    if not isinstance(raw, dict):
        raise RuntimeError(f"Jeg kunne ikke finde en godkendt Garmin-master for {family}.")

    candidate = adapt_master(raw, recipe, name)
    api = workspace.login()

    if isinstance(current, dict) and current.get("workout_id"):
        wid = current["workout_id"]
        existing = api.get_workout_by_id(wid)
        if not isinstance(existing, dict):
            raise RuntimeError("Det aktive test-workout kunne ikke læses før self-heal.")
        payload = copy.deepcopy(existing)
        for key in ("workoutName", "sportType", "estimatedDurationInSecs", "description", "workoutSegments"):
            payload[key] = copy.deepcopy(candidate[key])
        api.update_workout(wid, payload)
        readback = api.get_workout_by_id(wid)
        ok, detail, report = workspace._verify(readback, candidate)
        if not ok:
            try:
                api.update_workout(wid, existing)
            except Exception:
                pass
            raise RuntimeError("Self-heal forsøgte Garmin-master-formatet, men read-back er stadig forskellig: " + detail)
        current.update({
            "recipe": recipe,
            "updated_at": dt.datetime.now().astimezone().isoformat(),
            "readback_ok": True,
            "self_healed": True,
            "master_family": family,
            "verification_report": str(workspace.VERIFY_REPORT),
        })
        workspace.save(workspace.STATE, current)
        result = {"action": "updated", **current, "signature": report.get("actual_execution")}
    else:
        upload = api.upload_workout(candidate)
        if not isinstance(upload, dict) or not upload.get("workoutId"):
            raise RuntimeError("Garmin returnerede ikke workoutId under self-heal upload.")
        wid = upload["workoutId"]
        readback = api.get_workout_by_id(wid)
        ok, detail, report = workspace._verify(readback, candidate)
        if not ok:
            try:
                api.delete_workout(wid)
            except Exception:
                pass
            raise RuntimeError("Self-heal forsøgte Garmin-master-formatet, men read-back er stadig forskellig: " + detail)
        state = {
            "workout_id": wid,
            "name": candidate["workoutName"],
            "recipe": recipe,
            "created_at": dt.datetime.now().astimezone().isoformat(),
            "updated_at": dt.datetime.now().astimezone().isoformat(),
            "readback_ok": True,
            "self_healed": True,
            "master_family": family,
            "verification_report": str(workspace.VERIFY_REPORT),
        }
        workspace.save(workspace.STATE, state)
        result = {"action": "created", **state, "signature": report.get("actual_execution")}

    return "\n".join([
        "Første Garmin-format blev ikke accepteret semantisk, så jeg analyserede read-back og prøvede igen med din godkendte Garmin-master som struktur.",
        workspace.describe(result),
    ])
