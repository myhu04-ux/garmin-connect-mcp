"""Tool-aware local coach chat entrypoint.

The athlete speaks ordinary Danish. Known Garmin actions are routed deterministically
before the LLM. Garmin mutations use the native APIs required by garminconnect 0.3.12;
legacy replacement fallbacks are deliberately not used.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading

import auto_calendar_control
import coach_chat_fast as fast
import conversation_router
import garmin_method_catalog as catalog
import garmin_workout_workspace
import test_workout_calendar
import training_intent
import workout_selfheal


def wants_self_update(message: str) -> bool:
    text = " ".join(message.casefold().strip().split())
    phrases = (
        "opdater dig selv",
        "opdater programmet",
        "hent seneste version",
        "hent den nyeste version",
        "installer seneste version",
    )
    return any(p in text for p in phrases)


def start_self_update() -> str:
    script = Path(__file__).with_name("self_update.ps1")
    if not script.exists():
        return "Selvopdateringsscriptet mangler endnu. Kør den seneste installation én gang manuelt."
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.exists():
        return f"Jeg kunne ikke finde PowerShell på {powershell}."
    flags = 0
    if os.name == "nt":
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(
            [
                str(powershell),
                "-NoProfile",
                "-WindowStyle", "Hidden",
                "-ExecutionPolicy", "Bypass",
                "-File", str(script),
            ],
            cwd=str(script.parent.parent),
            creationflags=flags,
            close_fds=True,
        )
    except Exception as exc:
        return f"Jeg kunne ikke starte selvopdateringen: {exc}"
    return (
        "Jeg starter selvopdateringen nu. Jeg henter kun fra coachens faste GitHub-branch, "
        "opgraderer de fastlåste afhængigheder, kører tests og genstarter kun, hvis de består. "
        "Chatten kan forsvinde kortvarigt."
    )


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
            "men fandt ingen tydelig kandidat. Jeg vil ikke gætte på et endpoint."
        )

    lines = ["Jeg har søgt i den Garmin-klient, der faktisk er installeret. Mest relevante read-only kandidater:"]
    for row in matches[:7]:
        sig = row.get("signature") or ""
        doc = row.get("doc") or ""
        lines.append(f"• {row.get('method')}{sig}" + (f" – {doc}" if doc else ""))
    return "\n".join(lines)


def workout_mutation(message: str, operation: str) -> str:
    # Full delete is a Garmin-state reconciliation, not a language-model task:
    # remove every live calendar instance for the active workout, then delete the
    # workout template itself and verify both operations.
    if operation == "delete_test_workout":
        try:
            return test_workout_calendar.describe(test_workout_calendar.delete_completely())
        except Exception as exc:
            return f"Jeg forstod, at træningen skulle slettes helt, men Garmin kunne ikke gennemføre det sikkert: {exc}"

    try:
        result = garmin_workout_workspace.handle(message)
        return result or "Workout-handlingen blev gennemført."
    except Exception as first_error:
        if operation == "create_test_workout":
            try:
                return workout_selfheal.recover(message)
            except Exception as second_error:
                return (
                    "Jeg prøvede standardformatet og derefter din godkendte Garmin-master. "
                    f"Begge blev afvist, så jeg stoppede. Første fejl: {first_error}. "
                    f"Master-forsøg: {second_error}"
                )
        return f"Garmin-handlingen blev ikke gennemført: {first_error}"


def apply_direct_update(parsed_intent: dict) -> str:
    """Update the active test workout in place using Garmin's native PUT endpoint."""
    try:
        result = garmin_workout_workspace.update_test(parsed_intent)
        return garmin_workout_workspace.describe(result)
    except Exception as exc:
        return f"Jeg forstod ændringen, men Garmin kunne ikke gennemføre den sikkert: {exc}"


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
            replies.append(apply_direct_update(bundle.get("parsed_intent") or {}))
        else:
            replies.append(
                "Jeg forstår, at testpasset skal ændres, men mangler en konkret ændring. "
                "Skriv fx 'gør den 10 minutter kortere'."
            )

    calendar_op = bundle.get("calendar_operation")
    if calendar_op:
        intent = dict(bundle.get("parsed_intent") or {})
        intent["operation"] = calendar_op
        intent["target_date"] = bundle.get("target_date")
        try:
            replies.append(test_workout_calendar.handle_intent(intent))
        except Exception as exc:
            replies.append(f"Jeg forstod kalenderhandlingen, men Garmin kunne ikke gennemføre den sikkert: {exc}")

    return "\n\n".join(x for x in replies if x) or None


def answer(message: str) -> str:
    if wants_self_update(message):
        return start_self_update()

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
            return f"Jeg forstod kalenderhandlingen, men Garmin kunne ikke gennemføre den sikkert: {exc}"

    if wants_catalog(message):
        return catalog_answer(message)
    return fast.fast_answer(message)


fast.base.answer = answer

if __name__ == "__main__":
    threading.Thread(target=fast.warm_model, daemon=True).start()
    raise SystemExit(fast.base.main())
