"""Offline regression tests for natural-language Garmin action routing."""

from __future__ import annotations

import datetime as dt

import conversation_router


def test_user_move_phrase() -> None:
    result = conversation_router.route("Det er godkendt, og kan du så også flytte den til fredag")
    assert result["calendar_operation"] == "move_test_workout", result
    assert result["target_date"], result
    assert not result["update_requested"], result


def test_user_correction_phrase() -> None:
    result = conversation_router.route(
        "Jeg tror du misforstår, det er vores coach-test løb der skal være kortere, og det er den skal flyttes fra torsdag til fredag."
    )
    assert result["calendar_operation"] == "move_test_workout", result
    assert result["update_requested"], result
    assert not result["has_update_parameters"], result


def test_compound_edit_and_move() -> None:
    result = conversation_router.route("Gør coach-testløbet 10 minutter kortere og flyt den fra torsdag til fredag")
    assert result["calendar_operation"] == "move_test_workout", result
    assert result["update_requested"], result
    assert result["has_update_parameters"], result
    assert result["parsed_intent"]["relative_minutes"] == -10, result


def test_terse_edit_followup() -> None:
    result = conversation_router.route("10 minutter kortere")
    assert result["update_requested"], result
    assert result["has_update_parameters"], result
    assert result["parsed_intent"]["relative_minutes"] == -10, result


def test_destination_wins_over_source() -> None:
    original = conversation_router.training_intent.parse_target_date
    try:
        def fixed_parse(text: str):
            return original(text, dt.date(2026, 9, 8))
        conversation_router.training_intent.parse_target_date = fixed_parse
        result = conversation_router.route("flyttes fra torsdag til fredag")
        assert result["target_date"] == "2026-09-11", result
    finally:
        conversation_router.training_intent.parse_target_date = original


def test_put_phrase() -> None:
    result = conversation_router.route("Put den i kalenderen til på torsdag")
    assert result["calendar_operation"] == "schedule_test_workout", result
    assert result["target_date"], result


def main() -> int:
    tests = [
        test_user_move_phrase,
        test_user_correction_phrase,
        test_compound_edit_and_move,
        test_terse_edit_followup,
        test_destination_wins_over_source,
        test_put_phrase,
    ]
    print("=== COACH ROUTER SELF-TEST ===")
    for test in tests:
        test()
        print(f"OK: {test.__name__}")
    print(f"Alle {len(tests)} router-tests bestået.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
