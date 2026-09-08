"""Deeper local coaching conversation using the larger expert model.

This path is read-only. It is used for questions that require multi-week synthesis,
health/recovery trends, progress toward the primary event, or training rationale. It
shares the same rich context as the expert shadow-week planner but returns natural
Danish prose rather than a week JSON plan.
"""

from __future__ import annotations

import datetime as dt
import json

import requests

import model_manager
import shadow_week_expert

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"


def context_for(message: str) -> dict:
    today = dt.date.today()
    iso = today.isocalendar()
    context, _library = shadow_week_expert.enriched_context(iso.year, iso.week, message)
    # Exact current-week planning fields are irrelevant in ordinary deep conversation.
    return {
        "recovery": context.get("recovery"),
        "training_history_4x7d": context.get("training_history_4x7d"),
        "recent_activity_detail_28d": context.get("recent_activity_detail_28d"),
        "recent_health_detail_14d": context.get("recent_health_detail_14d"),
        "personal_session_response_examples": context.get("personal_session_response_examples"),
        "plan_match": context.get("plan_match"),
        "event": context.get("event"),
        "event_focus_points": context.get("event_focus_points"),
        "existing_calendar": context.get("existing_calendar"),
        "garmin_challenges": context.get("garmin_challenges"),
        "athlete_preferences": context.get("athlete_preferences"),
        "athlete_notes": context.get("athlete_notes"),
        "external_plan_principles": context.get("external_plan_principles"),
    }


def answer(message: str) -> str:
    ready, info = model_manager.coach_model_ready()
    if not ready:
        return info
    context = context_for(message)
    prompt = f"""Du er brugerens personlige løbetræner. Besvar spørgsmålet som en erfaren, datadrevet coach.

REGLER:
- Brug kun de vedlagte personlige data til påstande om brugeren.
- Se på flere ugers træning, ikke kun seneste uge, når spørgsmålet kræver trend.
- Se på helbred/restitution som trend mod brugerens egen baseline; diagnosticér ikke sygdom/skade.
- personal_session_response_examples viser observationer efter konkrete pas, ikke dokumenteret årsag.
- Primært løbsmål og sikker progression går foran Garmin-udfordringer.
- Eksterne træningsprincipper er evidens/inspiration, ikke en plan der skal kopieres.
- Hvis data ikke er tilstrækkelige, sig konkret hvad der mangler.
- Forklar både konklusion, hvorfor og hvad det betyder praktisk.
- Svar naturligt dansk. Ingen teknisk JSON og ingen falske påstande om at have ændret Garmin.

BRUGERENS SPØRGSMÅL:
{message[:1800]}

COACH-DATA:
{json.dumps(context, ensure_ascii=False, indent=2)}
"""
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model_manager.COACH_MODEL,
            "stream": False,
            "think": False,
            "keep_alive": "45m",
            "messages": [
                {
                    "role": "system",
                    "content": "Du er en erfaren evidensbaseret personlig løbetræner. Vær konkret, kritisk og datadrevet.",
                },
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.15, "num_predict": 850, "num_ctx": 12288},
        },
        timeout=60 * 15,
    )
    response.raise_for_status()
    text = str(response.json().get("message", {}).get("content", "")).strip()
    if not text:
        raise RuntimeError("Ekspertcoachen returnerede et tomt svar.")
    return text
