"""Offline self-tests for the critical local coach rules.

No Garmin, network or Ollama calls. These tests protect the most important safety
behaviour before the UI is launched after an update.
"""

from __future__ import annotations

import datetime as dt

from challenge_probe import collect_items
from coach_preview_v2 import decorate, validate
from named_workout import sanitized_copy
from plan_identity import position_for
from plan_matcher import match_recent_plan
from training_intent import deterministic, parse_target_date
from workout_lab import normalized_signature, semantic_signature, signature_differences, validation_errors


def iso(days: int) -> str:
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def test_shifted_run_matches() -> None:
    snapshot = {"all_activities": [{
        "id": 1,
        "name": "Rolig løbetur",
        "type": "running",
        "start": iso(-1) + " 09:00:00",
        "distance_m": 5100,
    }]}
    calendar = {"items": [{"date": iso(-2), "title": "Let løb - 5 km (Zone 2)", "workout_id": 11}]}
    result = match_recent_plan(snapshot, calendar, days_back=7)
    assert result["matched"] == 1, result
    assert result["matches"][0]["date_shift_days"] == 1, result


def test_strength_matches_strength() -> None:
    snapshot = {"all_activities": [{
        "id": 2,
        "name": "Styrketræning",
        "type": "strength_training",
        "start": iso(-1) + " 18:00:00",
        "duration_s": 2400,
    }]}
    calendar = {"items": [{"date": iso(-1), "title": "Styrke - Benpower & Hoftemobilitet", "workout_id": 22}]}
    result = match_recent_plan(snapshot, calendar, days_back=7)
    assert result["matched"] == 1, result


def test_today_unmatched_is_pending() -> None:
    snapshot = {"all_activities": []}
    calendar = {"items": [{"date": iso(0), "title": "Let løb - 5 km (Zone 2)", "workout_id": 11}]}
    result = match_recent_plan(snapshot, calendar, days_back=7)
    assert result["pending"] == 1, result
    assert result["missed"] == 0, result
    assert result["planned_workouts"] == 0, result


def base_context(recovery: str = "green") -> dict:
    monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    return {
        "allowed_dates": [iso(0), iso(1), iso(2), iso(3)],
        "phase": "specific_build",
        "recovery": {"state": recovery},
        "training": {},
        "plan_match": {},
        "event": {"name": "Thy Trail"},
        "event_focus_points": [],
        "existing_calendar": [{
            "date": iso(1),
            "title": "Let løb - 5 km (Zone 2)",
            "workout_id": 11,
            "scheduled_workout_id": 111,
        }],
        "available_workout_families": {},
        "external_plan_principles": [],
        "athlete_preferences": {
            "plan_code": "ThyTrail",
            "plan_anchor_monday": monday.isoformat(),
            "plan_anchor_week": 3,
        },
        "rules": {},
    }


def library() -> dict:
    return {"families": {
        "easy_run": [{"workout_id": 11, "title": "Let løb - 5 km (Zone 2)", "step_count": 3}],
        "quality_interval": [{"workout_id": 33, "title": "Bakkeintervaller", "step_count": 5}],
        "strength_master": [{"workout_id": 22, "title": "Styrke - Benpower & Hoftemobilitet", "step_count": 15}],
    }}


def test_invalid_adjust_preserves_existing() -> None:
    raw = {"actions": [{
        "action": "ADJUST",
        "source_date": iso(1),
        "source_workout_id": 11,
        "date": iso(1),
        "family": "invented_workout",
        "reason": "model hallucination",
    }]}
    plan = validate(raw, base_context(), library())
    assert len(plan["actions"]) == 1, plan
    assert plan["actions"][0]["action"] == "KEEP", plan
    assert plan["actions"][0]["source_workout_id"] == 11, plan


def test_red_blocks_hard_add() -> None:
    raw = {"actions": [{
        "action": "ADD",
        "date": iso(2),
        "family": "quality_interval",
        "intensity": "hard",
        "reason": "should be blocked",
    }]}
    plan = validate(raw, base_context("red"), library())
    assert all(a.get("family") != "quality_interval" for a in plan["actions"]), plan


def test_plan_week_day_name() -> None:
    profile = {"plan_code": "ThyTrail", "plan_anchor_monday": "2026-09-07", "plan_anchor_week": 3}
    pos = position_for("2026-09-10", profile, {"name": "Thy Trail"})
    assert pos["name"] == "ThyTrailW3D4", pos
    next_week = position_for("2026-09-14", profile, {"name": "Thy Trail"})
    assert next_week["name"] == "ThyTrailW4D1", next_week


def test_preview_gets_focus_and_plan_name() -> None:
    raw = {"actions": [{
        "action": "KEEP",
        "source_date": iso(1),
        "source_workout_id": 11,
        "date": iso(1),
        "reason": "Godt placeret roligt pas.",
    }]}
    plan = validate(raw, base_context(), library())
    action = plan["actions"][0]
    assert action.get("plan_name", "").startswith("ThyTrailW"), action
    assert action.get("focus"), action


def test_double_session_names_are_unique() -> None:
    ctx = base_context()
    day = iso(2)
    actions = [
        {"date": day, "source_title": "Let løb - 5 km (Zone 2)", "family": None},
        {"date": day, "source_title": "Styrke - Benpower & Hoftemobilitet", "family": "strength_master"},
    ]
    decorate(actions, ctx)
    names = {a.get("plan_name") for a in actions}
    assert len(names) == 2, actions
    assert any(str(n).endswith("R") for n in names), names
    assert any(str(n).endswith("S") for n in names), names


def has_step_id(value) -> bool:
    if isinstance(value, dict):
        if "stepId" in value:
            return True
        return any(has_step_id(v) for v in value.values())
    if isinstance(value, list):
        return any(has_step_id(v) for v in value)
    return False


def test_named_clone_sanitizes_without_mutating_master() -> None:
    master = {
        "workoutId": 123,
        "ownerId": 456,
        "workoutName": "Langtur Trail 16-18 km",
        "createdDate": "old",
        "updatedDate": "old",
        "workoutSegments": [{
            "segmentOrder": 1,
            "workoutSteps": [
                {"stepId": 1, "stepOrder": 1},
                {"stepId": 2, "stepOrder": 2, "child": {"stepId": 3}},
            ],
        }],
    }
    clone = sanitized_copy(master, "ThyTrailW3D4")
    assert clone["workoutName"] == "ThyTrailW3D4", clone
    assert "workoutId" not in clone and "ownerId" not in clone, clone
    assert not has_step_id(clone), clone
    assert master["workoutId"] == 123, master
    assert has_step_id(master), master


def test_in_progress_badge_shape_is_detected() -> None:
    raw = [{
        "badgeId": 909,
        "badgeName": "10 km weekend",
        "badgeProgressValue": 4.2,
        "badgeGoalValue": 10.0,
        "earnPeriodStartDate": iso(-1),
        "earnPeriodEndDate": iso(2),
        "badgeStatus": "IN_PROGRESS",
    }]
    rows = collect_items(raw, "in_progress_badges")
    assert len(rows) == 1, rows
    assert rows[0]["name"] == "10 km weekend", rows
    assert rows[0]["goal"] == 10.0 and rows[0]["progress"] == 4.2, rows
    assert abs(rows[0]["remaining"] - 5.8) < 0.001, rows


def test_workout_lab_accepts_running_structure() -> None:
    workout = {
        "workoutName": "LAB-Test",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeKey": "warmup"},
                    "endCondition": {"conditionTypeKey": "time"},
                    "endConditionValue": 600.0,
                    "targetType": {"workoutTargetTypeKey": "heart.rate.zone"},
                    "targetValueOne": 2,
                    "targetValueTwo": 2,
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 2,
                    "stepType": {"stepTypeKey": "interval"},
                    "endCondition": {"conditionTypeKey": "distance"},
                    "endConditionValue": 5000.0,
                    "targetType": {"workoutTargetTypeKey": "heart.rate.zone"},
                    "targetValueOne": 2,
                    "targetValueTwo": 2,
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 3,
                    "stepType": {"stepTypeKey": "cooldown"},
                    "endCondition": {"conditionTypeKey": "lap.button"},
                    "endConditionValue": 0.0,
                    "targetType": {"workoutTargetTypeKey": "no.target"},
                },
            ],
        }],
    }
    errors = validation_errors(workout)
    assert not errors, errors
    sig = semantic_signature(workout)
    assert sig["sport_type_key"] == "running", sig
    assert len(sig["steps"]) == 3, sig
    assert sig["steps"][1]["end_condition"] == "distance", sig
    assert sig["steps"][1]["end_value"] == 5000.0, sig


def test_garmin_metadata_normalization_is_not_execution_change() -> None:
    before = {
        "sport_type_key": "running",
        "segment_count": 1,
        "steps": [{
            "order": 1,
            "dto": "ExecutableStepDTO",
            "step_type": "interval",
            "end_condition": "time",
            "end_value": 240.0,
            "target_type": None,
            "target_low": None,
            "target_high": None,
            "zone_number": None,
            "iterations": None,
            "category": None,
            "exercise_name": None,
            "weight_value": None,
            "description": "vores tekst",
        }],
    }
    after = {
        "sport_type_key": "running",
        "segment_count": 1,
        "steps": [{
            "order": 99,
            "dto": "ExecutableStepDTO",
            "step_type": "interval",
            "end_condition": "time",
            "end_value": 240,
            "target_type": "no.target",
            "target_low": 0,
            "target_high": 0,
            "zone_number": None,
            "iterations": None,
            "category": None,
            "exercise_name": None,
            "weight_value": 0,
            "description": "Garmin normaliserede teksten",
        }],
    }
    expected = normalized_signature(before)
    actual = normalized_signature(after)
    assert signature_differences(expected, actual) == [], (expected, actual)


def test_natural_test_workout_intents() -> None:
    created = deterministic("lav et testløb der skal forbedre min VO2 maks")
    assert created["operation"] == "create_test_workout", created
    assert created["objective"] == "vo2max", created
    shorter = deterministic("gør den 10 minutter kortere")
    assert shorter["operation"] == "update_test_workout", shorter
    assert shorter["relative_minutes"] == -10, shorter
    repeats = deterministic("skift til 5 x 3 min")
    assert repeats["operation"] == "update_test_workout", repeats
    assert repeats["repetitions"] == 5 and repeats["work_min"] == 3, repeats


def test_natural_calendar_intents() -> None:
    today = dt.date(2026, 9, 8)  # Tuesday
    assert parse_target_date("læg den på torsdag", today) == "2026-09-10"
    schedule = deterministic("læg den i kalenderen på torsdag")
    assert schedule["operation"] == "schedule_test_workout", schedule
    assert schedule["target_date"], schedule
    move = deterministic("flyt den til fredag")
    assert move["operation"] == "move_test_workout", move
    assert move["target_date"], move
    remove = deterministic("fjern den fra kalenderen")
    assert remove["operation"] == "unschedule_test_workout", remove


def main() -> int:
    tests = [
        test_shifted_run_matches,
        test_strength_matches_strength,
        test_today_unmatched_is_pending,
        test_invalid_adjust_preserves_existing,
        test_red_blocks_hard_add,
        test_plan_week_day_name,
        test_preview_gets_focus_and_plan_name,
        test_double_session_names_are_unique,
        test_named_clone_sanitizes_without_mutating_master,
        test_in_progress_badge_shape_is_detected,
        test_workout_lab_accepts_running_structure,
        test_garmin_metadata_normalization_is_not_execution_change,
        test_natural_test_workout_intents,
        test_natural_calendar_intents,
    ]
    print("=== GARMIN LOCAL COACH SELF-TEST ===")
    for test in tests:
        test()
        print(f"OK: {test.__name__}")
    print(f"Alle {len(tests)} kritiske tests bestået.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
