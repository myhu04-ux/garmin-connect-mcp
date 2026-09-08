"""Tool-aware local coach chat entrypoint.

The athlete speaks ordinary Danish. Known Garmin actions are routed deterministically
before the LLM, including inflected Danish wording and compound requests. Qwen is only
used for genuine coaching conversation, never to improvise a known Garmin mutation.
"""

from __future__ import annotations

import threading

import auto_calendar_control
import coach_chat_fast as fast
import conversation_router
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


def apply_direct_update(message: str, parsed_intent: dict) -> str:
    """Apply an already parsed test-workout edit without reparsing a compound sentence."""
    try:
        result = garmin_workout_workspace.update_test(parsed_intent)
        return garmin_workout_workspace.describe(result)
    except Exception as first_error:
        try:
            return workout_selfheal.recover(message)
        except Exception as second_error:
            return (
                "Jeg forstod ændringen, men Garmin-verifikationen lykkedes ikke. "
                f"Standardforsøg: {first_error}. Self-heal: {second_error}"
            )


def handle_action_bundle(message: str) -> str | None:
    bundle = conversation_router.route(message)
    if not bundle.get("actionish") and not bundle.get("incomplete_calendar"):
        return None

    if bundle.get("incomplete_calendar"):
        return "Jeg forstår, at du vil flytte eller placere testpasset i kalenderen. Hvilken dag skal det ligge?"

    if bundle.get("delete_workout"):
        return workout_mutation(message, "delete_test_workout")

    replies: list[str] = []

    if bundle.get("update_requested"):
        if bundle.get("has_update_parameters"):
            replies.append(apply_direct_update(message, bundle.get("parsed_intent") or {}))
        else:
            replies.append(
                "Jeg forstår, at testpasset skal være kortere/ændres, men du har ikke angivet hvor meget. "
                "Jeg ændrer derfor ikke selve workoutet på gæt. Skriv fx 'gør den 10 minutter kortere'."
            )

    calendar_op = bundle.get("calendar_operation")
    if calendar_op:
        intent = dict(bundle.get("parsed_intent") or {})
        intent["operation"] = calendar_op
        intent["target_date"] = bundle.get("target_date")
        try:
            replies.append(test_workout_calendar.handle_intent(intent))
        except Exception as exc:
            replies.append(f"Jeg forstod kalenderhandlingen, men gennemførte den ikke: {exc}")

    return "\n\n".join(x for x in replies if x) or None


def answer(message: str) -> str:
    automation = auto_calendar_control.handle(message)
    if automation is not None:
        return automation

    routed = handle_action_bundle(message)
    if routed is not None:
        return routed

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
