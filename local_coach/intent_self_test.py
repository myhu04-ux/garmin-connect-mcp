"""Offline tests for conversational training intent and workout compilation.

No Garmin, web or Ollama calls are made. These tests protect the chat semantics that
turn ordinary Danish into a constrained workout specification.
"""

from __future__ import annotations

from garmin_workout_workspace import build_candidate, total_minutes
from training_intent import deterministic, training_recipe
from workout_lab import semantic_signature, validation_errors


def test_create_vo2_intent() -> None:
    intent = deterministic("Lav et test-løb der skal forbedre min VO2-maks")
    assert intent["operation"] == "create_test_workout", intent
    assert intent["objective"] == "vo2max", intent
    recipe = training_recipe(intent)
    assert recipe["family"] == "quality_interval", recipe
    assert recipe["repetitions"] >= 3, recipe


def test_followup_shorter() -> None:
    intent = deterministic("Gør den 10 minutter kortere")
    assert intent["operation"] == "update_test_workout", intent
    assert intent["relative_minutes"] == -10.0, intent


def test_followup_five_by_three() -> None:
    intent = deterministic("Skift til 5 x 3 minutter")
    assert intent["operation"] == "update_test_workout", intent
    assert intent["repetitions"] == 5, intent
    assert intent["work_min"] == 3.0, intent
    assert intent["duration_min"] is None, intent


def test_plan_word_does_not_edit_workspace() -> None:
    intent = deterministic("Gør planen 10 minutter kortere på torsdag")
    assert intent["operation"] == "training_question", intent


def test_vo2_candidate_compiles() -> None:
    intent = deterministic("Lav et test-løb der skal forbedre min VO2-maks")
    candidate, recipe = build_candidate(intent)
    errors = validation_errors(candidate)
    assert not errors, errors
    sig = semantic_signature(candidate)
    assert sig["sport_type_key"] == "running", sig
    assert any(step.get("dto") == "RepeatGroupDTO" for step in sig["steps"]), sig
    assert candidate["estimatedDurationInSecs"] == round(total_minutes(recipe) * 60), candidate


def test_relative_shortening_changes_execution_time() -> None:
    first = deterministic("Lav et test-løb der skal forbedre min VO2-maks")
    _, recipe = build_candidate(first)
    before = total_minutes(recipe)
    edit = deterministic("Gør den 10 minutter kortere")
    candidate, revised = build_candidate(edit, recipe, "CoachTest-vo2max")
    after = total_minutes(revised)
    assert abs((before - after) - 10.0) < 0.05, (before, after, revised)
    assert candidate["estimatedDurationInSecs"] == round(after * 60), candidate


def main() -> int:
    tests = [
        test_create_vo2_intent,
        test_followup_shorter,
        test_followup_five_by_three,
        test_plan_word_does_not_edit_workspace,
        test_vo2_candidate_compiles,
        test_relative_shortening_changes_execution_time,
    ]
    print("=== TRAINER-SPROG / WORKOUT SELF-TEST ===")
    for test in tests:
        test()
        print(f"OK: {test.__name__}")
    print(f"Alle {len(tests)} intent-tests bestået.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
