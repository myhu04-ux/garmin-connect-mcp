"""Tool-aware local coach chat entrypoint.

The athlete speaks ordinary Danish. The agent owns constrained Garmin tools for
workout create/update/delete, calendar placement, Garmin discovery and explicit
enable/disable of adaptive calendar write-back. Workout create/update gets one
automatic self-heal retry using the athlete's approved Garmin master shape before an
error is surfaced.
"""

from __future__ import annotations

import threading

import auto_calendar_control
import coach_chat_fast as fast
import garmin_method_catalog as catalog
import garmin_workout_workspace
import test_workout_calendar
import training_intent
import workout_selfheal


def wants_catalog(message: str) -> bool:
    text = message.casefold()
    phrases = (
        "søg i garmin", "soge i garmin", "find i garmin", "finde i garmin",
        "hvor ligger", "hvor finder", "garmin-funktion", "garmin funktion",
        "garmin struktur", "garmins struktur", "hvilke funktioner",
    )
    return any(p in text for p in phrases)


def catalog_answer(message: str) -> str:
    try:
        api = catalog.login()
        rows = catalog.build_catalog(api)
        catalog.save_catalog(rows)
        matches = catalog.search_rows(rows, message, limit=10)
    except Exception as exc:
        return f"Jeg kunne ikke inspicere Garmin-klienten lige nu: {exc}"

    if not matches:
        return (
            "Jeg har gennemsøgt den installerede Garmin-klients read-only get_* metoder, "
            "men fandt ingen tydelig kandidat til det, du beskrev. Jeg vil ikke gætte på et endpoint."
        )

    lines = ["Jeg har søgt i den Garmin-klient, der faktisk er installeret. Mest relevante read-only kandidater:"]
    for row in matches[:7]:
        sig = row.get("signature") or ""
        doc = row.get("doc") or ""
        lines.append(f"• {row.get('method')}{sig}" + (f" – {doc}" if doc else ""))
    lines.append(
        "Det her er kun discovery. Et nyt endpoint bliver først koblet på som et eksplicit read-only værktøj, "
        "så coachen ikke eksperimenterer blindt mod din Garmin-konto."
    )
    return "\n".join(lines)


def workout_mutation(message: str, operation: str) -> str:
    if operation == "delete_test_workout":
        try:
            state = test_workout_calendar.load(test_workout_calendar.STATE, {})
            if isinstance(state, dict) and (state.get("scheduled_workout_id") or state.get("scheduled_date")):
                test_workout_calendar.unschedule()
        except Exception as exc:
            return f"Jeg sletter ikke workoutet, fordi jeg først skulle fjerne det sikkert fra kalenderen, og det fejlede: {exc}"

    try:
        result = garmin_workout_workspace.handle(message)
        return result or "Workout-handlingen blev gennemført."
    except Exception as first_error:
        if operation in {"create_test_workout", "update_test_workout"}:
            try:
                return workout_selfheal.recover(message)
            except Exception as second_error:
                return (
                    "Jeg prøvede først standardformatet og derefter automatisk Garmin-master-formatet. "
                    f"Begge blev afvist, så jeg stoppede uden at fortsætte blindt. Første fejl: {first_error}. "
                    f"Self-heal: {second_error}"
                )
        return f"Jeg forstod Garmin-handlingen, men gennemførte den ikke: {first_error}"


def answer(message: str) -> str:
    automation = auto_calendar_control.handle(message)
    if automation is not None:
        return automation

    intent = training_intent.deterministic(message)
    operation = str(intent.get("operation") or "")

    if operation in {"create_test_workout", "update_test_workout", "delete_test_workout"}:
        return workout_mutation(message, operation)

    if operation in {"schedule_test_workout", "move_test_workout", "unschedule_test_workout"}:
        try:
            return test_workout_calendar.handle_intent(intent)
        except Exception as exc:
            return f"Jeg forstod kalenderhandlingen, men gennemførte den ikke: {exc}"

    if wants_catalog(message):
        return catalog_answer(message)
    return fast.fast_answer(message)


fast.base.answer = answer

if __name__ == "__main__":
    threading.Thread(target=fast.warm_model, daemon=True).start()
    raise SystemExit(fast.base.main())
