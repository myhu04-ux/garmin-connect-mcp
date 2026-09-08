"""Garmin workout format laboratory.

Default mode is OFFLINE/READ-ONLY: select one approved Garmin master, create a
sanitized personal copy in memory, validate its structure and save a readable
semantic signature locally.

Optional --upload-test uploads only to the Garmin workout library, reads the workout
back, compares its execution semantics, then deletes the temporary test again unless
--keep-uploaded is explicitly supplied. It never schedules anything in the calendar.

Garmin may rewrite harmless DTO metadata/default values on save. Verification therefore
compares an execution contract (sport, ordered step types, duration/distance,
repetitions and meaningful targets), not byte-for-byte JSON.
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
        "zone_number": step.get("zoneNumber"),
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


def _key(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().casefold().replace("_", ".").replace("-", ".")
    while ".." in text:
        text = text.replace("..", ".")
    aliases = {
        "none": "no.target",
        "no.target": "no.target",
        "notarget": "no.target",
        "heart.rate.zone": "heart.rate.zone",
        "heartrate.zone": "heart.rate.zone",
        "pace.zone": "pace.zone",
        "speed.zone": "speed.zone",
        "lap.button": "lap.button",
    }
    return aliases.get(text, text)


def _num(value: Any) -> int | float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except Exception:
        return None
    if abs(number) < 1e-9:
        return 0
    rounded = round(number, 4)
    return int(rounded) if float(rounded).is_integer() else rounded


def normalized_signature(sig: dict[str, Any]) -> dict[str, Any]:
    """Return Garmin-save-stable execution semantics.

    Garmin routinely injects IDs, DTO defaults, target placeholders and numeric type
    changes when saving a workout. Those are not execution changes. We intentionally
    ignore stepOrder/DTO/description and non-applicable repeat-group defaults, while
    preserving ordered step sequence, timing/distance, repetitions and meaningful
    targets/exercise fields.
    """
    sport_key = _key(sig.get("sport_type_key"))
    out: dict[str, Any] = {
        "sport": sport_key or _num(sig.get("sport_type_id")),
        "segment_count": int(sig.get("segment_count") or 0),
        "steps": [],
    }

    for source in sig.get("steps", []) if isinstance(sig.get("steps"), list) else []:
        if not isinstance(source, dict):
            continue
        step_type = _key(source.get("step_type")) or "unknown"
        row: dict[str, Any] = {"step_type": step_type}

        if step_type == "repeat":
            row["iterations"] = int(_num(source.get("iterations")) or _num(source.get("end_value")) or 0)
            out["steps"].append(row)
            continue

        end_condition = _key(source.get("end_condition"))
        if end_condition:
            row["end_condition"] = end_condition
        end_value = _num(source.get("end_value"))
        if end_value is not None:
            row["end_value"] = end_value

        target_type = _key(source.get("target_type")) or "no.target"
        row["target_type"] = target_type
        if target_type != "no.target":
            low = _num(source.get("target_low"))
            high = _num(source.get("target_high"))
            zone = _num(source.get("zone_number"))
            if low is not None:
                row["target_low"] = low
            if high is not None:
                row["target_high"] = high
            if zone is not None:
                row["zone_number"] = zone

        # Strength semantics are meaningful when present; Garmin may omit empty values.
        category = _key(source.get("category"))
        exercise_name = _key(source.get("exercise_name"))
        weight = _num(source.get("weight_value"))
        if category:
            row["category"] = category
        if exercise_name:
            row["exercise_name"] = exercise_name
        if weight not in (None, 0):
            row["weight_value"] = weight

        out["steps"].append(row)
    return out


def signature_differences(expected: dict[str, Any], actual: dict[str, Any], limit: int = 8) -> list[str]:
    """Human-readable differences between two normalized execution contracts."""
    diffs: list[str] = []
    if expected.get("sport") != actual.get("sport"):
        diffs.append(f"sport: forventet {expected.get('sport')}, Garmin {actual.get('sport')}")
    if expected.get("segment_count") != actual.get("segment_count"):
        diffs.append(f"segmenter: forventet {expected.get('segment_count')}, Garmin {actual.get('segment_count')}")

    exp_steps = expected.get("steps") or []
    act_steps = actual.get("steps") or []
    if len(exp_steps) != len(act_steps):
        diffs.append(f"antal udførelsestrin: forventet {len(exp_steps)}, Garmin {len(act_steps)}")

    for index, (exp, act) in enumerate(zip(exp_steps, act_steps), start=1):
        if exp == act:
            continue
        keys = sorted(set(exp) | set(act))
        changed = [f"{key} {exp.get(key)!r}->{act.get(key)!r}" for key in keys if exp.get(key) != act.get(key)]
        diffs.append(f"trin {index}: " + ", ".join(changed[:5]))
        if len(diffs) >= limit:
            break
    return diffs[:limit]


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
        preferred = [m for m in masters if m.get("family") in {"easy_run", "trail_easy"}]
        return (preferred or masters)[0]
    q = str(query).strip().casefold()
    exact = [m for m in masters if str(m.get("workout_id") or "").casefold() == q or str(m.get("title") or "").casefold() == q]
    if exact:
        return exact[0]
    contains = [m for m in masters if q in str(m.get("title") or "").casefold() or q == str(m.get("family") or "").casefold()]
    return contains[0] if contains else None


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
        "master": {"family": master.get("family"), "workout_id": master.get("workout_id"), "title": master.get("title")},
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
    norm = normalized_signature(expected_sig)
    print(f"Sport: {norm.get('sport')} | segmenter={norm.get('segment_count')} | trin={len(norm.get('steps') or [])}")
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
    return_code = 0
    try:
        upload_result = api.upload_workout(candidate)
        if not isinstance(upload_result, dict) or not upload_result.get("workoutId"):
            raise RuntimeError("Garmin returnerede ikke workoutId efter test-upload.")
        uploaded_id = upload_result["workoutId"]
        result["garmin_write"] = True
        result["uploaded_workout_id"] = uploaded_id

        readback = api.get_workout_by_id(uploaded_id)
        actual_sig = semantic_signature(readback if isinstance(readback, dict) else {})
        expected_norm = normalized_signature(expected_sig)
        actual_norm = normalized_signature(actual_sig)
        differences = signature_differences(expected_norm, actual_norm)
        format_match = not differences
        name_match = isinstance(readback, dict) and str(readback.get("workoutName") or "") == str(args.name)
        result.update({
            "readback_name": readback.get("workoutName") if isinstance(readback, dict) else None,
            "actual_signature": actual_sig,
            "expected_execution": expected_norm,
            "actual_execution": actual_norm,
            "differences": differences,
            "semantic_format_match": format_match,
            "name_match": name_match,
            "readback_ok": bool(format_match and name_match),
        })

        print(f"UPLOAD: Garmin workout-id {uploaded_id}")
        print(f"READ-BACK navn: {'OK' if name_match else 'FEJL'}")
        print(f"READ-BACK udførelse: {'OK' if format_match else 'FEJL'}")
        if differences:
            for diff in differences:
                print(f"- {diff}")
        if not result["readback_ok"]:
            print("Testen bestod ikke. Workout-formatet må ikke bruges til automatisk kalender-writeback endnu.")
            return_code = 5
        else:
            print("FORMATTEST BESTÅET: Garmin læste workoutet tilbage med samme udførelsesstruktur.")
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
