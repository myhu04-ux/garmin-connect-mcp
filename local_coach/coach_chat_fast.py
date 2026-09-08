"""Fast entrypoint for the direct local Garmin coach chat.

The full dashboard pipeline may use a larger planning context. Direct chat should
feel conversational, so this wrapper sends only the facts needed for a short answer,
keeps Qwen warm in Ollama, and limits answer length. It does not change Garmin.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import requests

import coach_chat_ui as base


def pick(mapping: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    return {k: mapping.get(k) for k in keys if mapping.get(k) is not None}


def compact_challenge(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    # Garmin has used several field names for different challenge types. Keep only
    # athlete-relevant progress/deadline fields if they exist.
    keys = (
        "challengeId", "badgeChallengeId", "challengeName", "name", "title",
        "description", "startDate", "endDate", "challengeStartDate", "challengeEndDate",
        "progress", "progressValue", "goal", "goalValue", "target", "targetValue",
        "unit", "unitKey", "activityType", "activityTypeKey", "completed",
    )
    return pick(item, keys)


def fast_context() -> dict[str, Any]:
    state = base.load(base.STATE, {})
    preview = base.load(base.PREVIEW, {})
    goal = base.load(base.GOAL, {})
    profile = base.load(base.PROFILE, {})
    challenges = base.load(base.CHALLENGES, {})
    notes = base.load(base.NOTES, {})

    recovery = state.get("recovery") or {}
    training = state.get("training") or {}
    match = state.get("plan_match") or {}
    event = state.get("event") or goal or {}

    upcoming = []
    for action in (preview.get("actions") or [])[:7]:
        if not isinstance(action, dict):
            continue
        upcoming.append({
            "date": action.get("date"),
            "name": action.get("plan_name"),
            "focus": action.get("focus"),
            "style": action.get("workout_style"),
            "reason": str(action.get("reason") or "")[:180],
        })

    active = challenges.get("active") if isinstance(challenges, dict) else []
    challenge_rows = [compact_challenge(x) for x in (active or [])[:8]]
    challenge_rows = [x for x in challenge_rows if x]

    active_notes = []
    for row in (notes.get("notes") or [])[-8:] if isinstance(notes, dict) else []:
        if isinstance(row, dict) and row.get("active") is not False and row.get("text"):
            active_notes.append(str(row["text"])[:220])

    return {
        "recovery": {
            "state": recovery.get("state"),
            "history_days": recovery.get("history_days"),
            "latest": pick(recovery.get("latest"), ("sleep_hours", "sleep_score", "hrv", "resting_hr", "body_battery", "stress")),
            "signals": (recovery.get("signals") or [])[:4],
        },
        "training": {
            "last_7_days": training.get("last_7_days") or {},
            "previous_7_days": training.get("previous_7_days") or {},
            "strength_sessions_last_14_days": training.get("strength_sessions_last_14_days"),
        },
        "plan_match": pick(match, ("matched", "planned_workouts", "pending", "uncertain", "missed")),
        "event": pick(event, ("name", "event_name", "days_to_event", "event_type", "distance_km", "dates", "course")),
        "upcoming": upcoming,
        "garmin_challenges": challenge_rows,
        "preferences": pick(profile, ("max_running_days_per_week", "preferred_long_run_days", "max_weekday_session_minutes", "notes", "plan_code", "plan_anchor_week")),
        "planning_notes": active_notes[-6:],
    }


def fast_answer(message: str) -> str:
    context = fast_context()
    history = base.history_rows()[-4:]
    messages = [{
        "role": "system",
        "content": (
            "Du er en erfaren personlig løbetræner. Svar på naturligt dansk, kort og konkret. "
            "Brug kun de givne fakta om løberen. Forklar kort hvorfor. Primært løbsmål, restitution "
            "og sikker progression går foran Garmin-udfordringer. Garmin-udfordringer må kun flettes "
            "ind, når de passer naturligt i den sikre træning. Du ændrer ikke Garmin fra chatten."
        ),
    }]
    messages.extend({"role": x["role"], "content": str(x["content"])[:800]} for x in history)
    messages.append({
        "role": "user",
        "content": "DATA=" + json.dumps(context, ensure_ascii=False, separators=(",", ":")) + "\nSPØRGSMÅL=" + message[:1000],
    })
    payload = {
        "model": base.MODEL,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "messages": messages,
        "options": {
            "temperature": 0.2,
            "num_predict": 220,
            "num_ctx": 4096,
        },
    }
    response = requests.post(base.OLLAMA_URL, json=payload, timeout=70)
    response.raise_for_status()
    text = str(response.json().get("message", {}).get("content", "")).strip()
    return text or "Jeg kunne ikke formulere et kort svar ud fra de lokale data."


def warm_model() -> None:
    """Load the model while the user is opening the page, so first reply is faster."""
    try:
        requests.post(
            base.OLLAMA_URL,
            json={
                "model": base.MODEL,
                "stream": False,
                "think": False,
                "keep_alive": "30m",
                "messages": [{"role": "user", "content": "Svar kun OK"}],
                "options": {"num_predict": 1, "num_ctx": 2048},
            },
            timeout=90,
        )
    except Exception:
        pass


base.answer = fast_answer

if __name__ == "__main__":
    threading.Thread(target=warm_model, daemon=True).start()
    raise SystemExit(base.main())
