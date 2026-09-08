"""Garmin workout format laboratory.

Default mode is OFFLINE/READ-ONLY: select one approved Garmin master, create a
sanitized personal copy in memory, validate its structure and save a readable
semantic signature locally.

Optional --upload-test uploads only to the Garmin workout library, reads the workout
back, compares its semantic structure, then deletes the temporary test again unless
--keep-uploaded is explicitly supplied. It never schedules anything in the calendar.

The purpose is to prove workout formatting independently before adaptive calendar
write-back is enabled.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from garminconnect import Garmin

from named_workout import load, sanitized_copy

ROOT = Path(r"C:\GarminCoach")
DATA = ROOT / "data"
TEMPLATES = DATA / "approved_workout_templates.json"
OUT = DATA / "workout_lab_result.json"
CANDIDATE = DATA / "workout_lab_candidate.json"
TOKEN_DIR = os.path.expanduser("~/.garminconnect")


def walk_steps(value: Any):
    if isinstance(value, dict):
        if value.get("stepOrder") is not None or value.get("type") in {"ExecutableStepDTO", "RepeatGroupDTO"}:
            yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from walk_steps(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_steps(child)


def nested_key(d: dict[str, Any], outer: str, *keys: str) -> Any:
    obj = d.get(outer)
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if obj.get(key) is not None:
            return obj.get(key)
    return None


def step_signature(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "order": step.get("stepOrder"),
        "dto": step.get("type"),
        "step_type": nested_key(step, "stepType", "stepTypeKey", "key", "name") or step.get("stepTypeKey"),
        "end_condition": nested_key(step, "endCondition", "conditionTypeKey", "key", "name") or step.get("conditionTypeKey"),
        "end_value": step.get("endConditionValue"),
        "target_type": nested_key(step, "targetType", "workoutTargetTypeKey", "key", "name") or step.get("workoutTargetTypeKey"),
        "target_low": step.get("targetValueOne"),
        "target_high": step.get("targetValueTwo"),
        "iterations": step.get("numberOfIterations"),
        "category": step.get("category"),
        "exercise_name": step.get("exerciseName"),
        "weight_value": step.get("weightValue"),
        "description": step.get("description"),
    }


def semantic_signature(raw: dict[str, Any]) -> dict[str, Any]:
    sport = raw.get("sportType") if isinstance(raw.get("sportType"), dict) else {}
    segments = raw.get("workoutSegments") if isinstance(raw.get("workoutSegments"), list) else []
    steps = [step_signature(s) for s in walk_steps(segments)]
    return {
        "sport_type_id": sport.get("sportTypeId"),
        "sport_type_key": sport.get("sportTypeKey"),
        "segment_count": len(segments),
        "steps": steps,
    }


def validation_errors(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["Workout er ikke et JSON-objekt."]
    if not str(raw.get("workoutName") or "").strip():
        errors.append("workoutName mangler.")
    sport = raw.get("sportType")
    if not isinstance(sport, dict) or not sport.get("sportTypeKey"):
        errors.append("sportType/sportTypeKey mangler.")
    segments = raw.get("workoutSegments")
    if not isinstance(segments, list) or not segments:
        errors.append("workoutSegments mangler eller er tom.")
        return errors

    seen_orders: set[tuple[int, Any]] = set()
    for seg_index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            errors.append(f"Segment {seg_index} er ikke et objekt.")
            continue
        steps = segment.get("workoutSteps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"Segment {seg_index} mangler workoutSteps.")
            continue
        for step in walk_steps(steps):
            order = step.get("stepOrder")
            if order is None:
                errors.append(f"Et trin i segment {seg_index} mangler stepOrder.")
            else:
                key = (seg_index, order)
                # Nested repeat children can legitimately have their own order space,
                # so duplicate warning is informational only if exact DTO differs.
                if key in seen_orders and step.get("type") != "RepeatGroupDTO":
                    pass
                seen_orders.add(key)
            if not isinstance(step.get("stepType"), dict):
                errors.append(f"Trin {order} mangler stepType.")
            if step.get("type") != "RepeatGroupDTO" and not isinstance(step.get("endCondition"), dict):
                errors.append(f"Trin {order} mangler endCondition.")

    return list(dict.fromkeys(errors))


def all_masters() -> list[dict[str, Any]]:
    library = load(TEMPLATES, {})
    out = []
    for family, rows in (library.get("families") or {}).items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("raw"), dict):
                continue
            out.append({**row, "family": family})
    return out


def find_master(query: str | None) -> dict[str, Any] | None:
    masters = all_masters()
    if not masters:
        return None
    if not query:
        # Prefer a simple running master for the first lab test.
        preferred = [m for m in masters if m.get("family") in {"easy_run", "trail_easy"}]
        return (preferred or masters)[0]
    q = str(query).strip().casefold()
    exact = [m for m in masters if str(m.get("workout_id") or "").casefold() == q or str(m.get("title") or "").casefold() == q]
    if exact:
        return exact[0]
    contains = [m for m in masters if q in str(m.get("title") or "").casefold() or q == str(m.get("family") or "").casefold()]
    return contains[0] if contains else None


def normalized_signature(sig: dict[str, Any]) -> dict[str, Any]:
    """Remove descriptive fields Garmin may rewrite while keeping execution semantics."""
    data = copy.deepcopy(sig)
    for step in data.get("steps", []):
        if isinstance(step, dict):
            step.pop("description", None)
    return data


def login() -> Garmin:
    if not Path(TOKEN_DIR).exists():
        raise RuntimeError(f"Garmin tokenmappe mangler: {TOKEN_DIR}")
    api = Garmin(retry_attempts=0)
    api.login(TOKEN_DIR)
    return api


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", help="Workout-id, titel eller template-family")
    parser.add_argument("--name", default="LAB-GarminWorkoutFormat")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--upload-test", action="store_true", help="Upload midlertidigt til Garmin workout-bibliotek og læs tilbage")
    parser.add_argument("--keep-uploaded", action="store_true", help="Behold test-workout i Garmin-biblioteket")
    args = parser.parse_args()

    masters = all_masters()
    if args.list:
        print("=== GODKENDTE GARMIN-MASTERS ===")
        for m in masters:
            print(f"{m.get('family')}: {m.get('title')} | workout={m.get('workout_id')} | trin={m.get('step_count')}")
        return 0

    master = find_master(args.master)
    if not master:
        print("ERROR: Kunne ikke finde en godkendt master. Kør full/template-proben først.")
        return 2

    raw = master.get("raw")
    if not isinstance(raw, dict):
        print("ERROR: Masteren mangler rå Garmin-struktur.")
        return 3

    candidate = sanitized_copy(raw, args.name)
    errors = validation_errors(candidate)
    expected_sig = semantic_signature(candidate)
    CANDIDATE.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATE.write_text(json.dumps(candidate, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    result: dict[str, Any] = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "master": {
            "family": master.get("family"),
            "workout_id": master.get("workout_id"),
            "title": master.get("title"),
        },
        "candidate_name": args.name,
        "candidate_validation_ok": not errors,
        "validation_errors": errors,
        "expected_signature": expected_sig,
        "upload_test_requested": bool(args.upload_test),
        "garmin_write": False,
    }

    print("=== GARMIN WORKOUT LAB ===")
    print(f"Master: {master.get('title')} | family={master.get('family')} | workout={master.get('workout_id')}")
    print(f"Testnavn: {args.name}")
    print(f"Sport: {expected_sig.get('sport_type_key')} | segmenter={expected_sig.get('segment_count')} | trin={len(expected_sig.get('steps') or [])}")
    for step in expected_sig.get("steps", []):
        print(
            f"- trin {step.get('order')}: {step.get('step_type')} | {step.get('end_condition')}={step.get('end_value')} | "
            f"target={step.get('target_type')} {step.get('target_low') or ''}-{step.get('target_high') or ''}" +
            (f" | {step.get('category')}/{step.get('exercise_name')}" if step.get('category') or step.get('exercise_name') else "")
        )
    if errors:
        print("FORMATFEJL:")
        for error in errors:
            print(f"- {error}")
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return 4

    if not args.upload_test:
        print("DRY RUN OK: Kandidaten er kun bygget lokalt. Intet blev skrevet til Garmin.")
        print(f"Kandidat: {CANDIDATE}")
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return 0

    api = login()
    uploaded_id = None
    cleanup_ok = None
    try:
        upload_result = api.upload_workout(candidate)
        if not isinstance(upload_result, dict) or not upload_result.get("workoutId"):
            raise RuntimeError("Garmin returnerede ikke workoutId efter test-upload.")
        uploaded_id = upload_result["workoutId"]
        result["garmin_write"] = True
        result["uploaded_workout_id"] = uploaded_id

        readback = api.get_workout_by_id(uploaded_id)
        actual_sig = semantic_signature(readback if isinstance(readback, dict) else {})
        format_match = normalized_signature(expected_sig) == normalized_signature(actual_sig)
        name_match = isinstance(readback, dict) and str(readback.get("workoutName") or "") == str(args.name)
        result["readback_name"] = readback.get("workoutName") if isinstance(readback, dict) else None
        result["actual_signature"] = actual_sig
        result["semantic_format_match"] = format_match
        result["name_match"] = name_match
        result["readback_ok"] = bool(format_match and name_match)

        print(f"UPLOAD: Garmin workout-id {uploaded_id}")
        print(f"READ-BACK navn: {'OK' if name_match else 'FEJL'}")
        print(f"READ-BACK struktur: {'OK' if format_match else 'FEJL'}")
        if not result["readback_ok"]:
            print("Testen bestod ikke. Workout-formatet må ikke bruges til automatisk kalender-writeback endnu.")
            return_code = 5
        else:
            print("FORMATTEST BESTÅET: Garmin læste workoutet tilbage med samme udførelsesstruktur.")
            return_code = 0
    finally:
        if uploaded_id and not args.keep_uploaded:
            try:
                api.delete_workout(uploaded_id)
                cleanup_ok = True
                print("CLEANUP: Midlertidigt test-workout slettet fra Garmin-biblioteket.")
            except Exception as exc:
                cleanup_ok = False
                result["cleanup_error"] = str(exc)[:500]
                print(f"ADVARSEL: Kunne ikke slette test-workout igen: {exc}")
        result["cleanup_ok"] = cleanup_ok
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
