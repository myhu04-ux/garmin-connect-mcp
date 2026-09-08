"""Tool-aware local coach chat entrypoint.

Extends coach_chat_fast with Garmin discovery and a tightly scoped calendar tool for
the active CoachTest workout. The athlete speaks ordinary Danish. Read-only discovery
never invokes unknown endpoints; explicit calendar requests can only schedule/move/
unschedule the one active test workout and are verified by Garmin read-back.
"""

from __future__ import annotations

import threading

import coach_chat_fast as fast
import garmin_method_catalog as catalog
import test_workout_calendar
import training_intent


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


def answer(message: str) -> str:
    intent = training_intent.deterministic(message)
    if intent.get("operation") in {"schedule_test_workout", "move_test_workout", "unschedule_test_workout"}:
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
