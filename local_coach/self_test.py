"""Offline self-tests for the critical local coach rules.

No Garmin, network or Ollama calls. These tests protect the most important safety
behaviour before the UI is launched after an update.
"""

from __future__ import annotations

import datetime as dt

from coach_preview_v2 import decorate, validate
from named_workout import sanitized_copy
from plan_identity import position_for
from plan_matcher import match_recent_plan


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
    ]
    print("=== GARMIN LOCAL COACH SELF-TEST ===")
    for test in tests:
        test()
        print(f"OK: {test.__name__}")
    print(f"Alle {len(tests)} kritiske tests bestået.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
