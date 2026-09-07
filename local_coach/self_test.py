"""Offline self-tests for the critical local coach rules.

No Garmin, network or Ollama calls. These tests protect the most important safety
behaviour before the UI is launched after an update.
"""

from __future__ import annotations

import datetime as dt

from coach_preview_v2 import validate
from plan_matcher import match_recent_plan


def iso(days: int) -> str:
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def test_shifted_run_matches() -> None:
    snapshot = {
        "all_activities": [{
            "id": 1,
            "name": "Rolig løbetur",
            "type": "running",
            "start": iso(-1) + " 09:00:00",
            "distance_m": 5100,
        }]
    }
    calendar = {"items": [{"date": iso(-2), "title": "Let løb - 5 km (Zone 2)", "workout_id": 11}]}
    result = match_recent_plan(snapshot, calendar, days_back=7)
    assert result["matched"] == 1, result
    assert result["matches"][0]["date_shift_days"] == 1, result


def test_strength_matches_strength() -> None:
    snapshot = {
        "all_activities": [{
            "id": 2,
            "name": "Styrketræning",
            "type": "strength_training",
            "start": iso(-1) + " 18:00:00",
            "duration_s": 2400,
        }]
    }
    calendar = {"items": [{"date": iso(-1), "title": "Styrke - Benpower & Hoftemobilitet", "workout_id": 22}]}
    result = match_recent_plan(snapshot, calendar, days_back=7)
    assert result["matched"] == 1, result


def base_context(recovery: str = "green") -> dict:
    return {
        "allowed_dates": [iso(1), iso(2), iso(3)],
        "phase": "specific_build",
        "recovery": {"state": recovery},
        "training": {},
        "plan_match": {},
        "event": {},
        "event_focus_points": [],
        "existing_calendar": [{
            "date": iso(1),
            "title": "Let løb - 5 km (Zone 2)",
            "workout_id": 11,
            "scheduled_workout_id": 111,
        }],
        "available_workout_families": {},
        "external_plan_principles": [],
        "athlete_preferences": {},
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


def main() -> int:
    tests = [
        test_shifted_run_matches,
        test_strength_matches_strength,
        test_invalid_adjust_preserves_existing,
        test_red_blocks_hard_add,
    ]
    print("=== GARMIN LOCAL COACH SELF-TEST ===")
    for test in tests:
        test()
        print(f"OK: {test.__name__}")
    print(f"Alle {len(tests)} kritiske tests bestået.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
