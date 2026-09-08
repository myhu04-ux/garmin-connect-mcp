"""Final athlete-facing local coach agent.

Extends the stable tool/model router with Claude-like natural-language research:
- explicit 'jeg vil træne mod ...' -> sourced race research + local goal update;
- explicit 'analysér/brug dette løbeprogram ...' -> sourced plan-principle research.
Research changes only local coach knowledge/profile state. A new adaptive plan is then
started in the normal background pipeline; Garmin mutation remains separately gated.
"""

from __future__ import annotations

import threading

import coach_chat_agent as base
import model_manager
import research_actions
import research_intent


def _start_replan_after_research() -> bool:
    starter = getattr(base.fast.base, "start_replan", None)
    if callable(starter):
        try:
            return bool(starter())
        except Exception:
            return False
    return False


def research_answer(message: str, intent: dict) -> str:
    operation = str(intent.get("operation") or "")
    query = str(intent.get("query") or "").strip()
    if operation == "set_goal":
        base.stage(
            f"8B ekspert undersøger det nye løbsmål '{query}' i kildebaserede webresultater…",
            model_manager.COACH_MODEL,
        )
    else:
        base.stage(
            f"8B ekspert undersøger træningsreferencen '{query[:90]}' og udtrækker brugbare principper…",
            model_manager.COACH_MODEL,
        )

    ready, info = model_manager.coach_model_ready()
    if not ready:
        return info

    try:
        result = research_actions.handle(intent)
    except Exception as exc:
        return (
            "Jeg forstod research-opgaven, men jeg ændrer ikke mål/vidensbibliotek på et halvfærdigt grundlag. "
            f"Researchen kunne ikke afsluttes sikkert: {exc}"
        )

    base.stage("Researchen er gemt. Starter en ny adaptiv vurdering i baggrunden…")
    started = _start_replan_after_research()
    tail = (
        "\n\nDen adaptive plan opdateres nu i baggrunden med den nye viden. Garmin-writeback er fortsat under de normale sikkerhedsgates."
        if started
        else
        "\n\nDen nye viden er gemt. En anden planopdatering kører allerede, så den bliver brugt ved den næste validerede kørsel."
    )
    return result + tail


def answer(message: str) -> str:
    intent = research_intent.parse(message)
    if intent is not None:
        return research_answer(message, intent)
    return base.answer(message)


# The HTTP UI calls coach_chat_ui.answer; patch that callback after importing the
# previous router so all existing deterministic Garmin/tool paths remain intact.
base.fast.base.answer = answer

if __name__ == "__main__":
    threading.Thread(target=model_manager.ensure_all_models_background, daemon=True).start()
    raise SystemExit(base.fast.base.main())
