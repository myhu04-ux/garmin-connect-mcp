"""Deterministic structured coach analysis.

This v2 deliberately does not call Ollama. It computes the factual coaching state;
the language model is used later, once the validated 7-day preview exists. Garmin
challenges are included as secondary goals: recovery, event specificity and safe
progression always have higher priority.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import coach_brief as base
from plan_matcher import match_recent_plan

CHALLENGES = Path(r"C:\GarminCoach\data\garmin_challenges.json")
CHAT_NOTES = Path(r"C:\GarminCoach\data\coach_chat_notes.json")


def load_local(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def compact_challenges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("active", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "name": item.get("name"),
            "end_date": item.get("end_date"),
            "days_remaining": item.get("days_remaining"),
            "goal": item.get("goal"),
            "progress": item.get("progress"),
            "completion_pct": item.get("completion_pct"),
            "unit": item.get("unit"),
            "metric": item.get("metric"),
        })
    return rows[:12]


def challenge_focus(challenges: list[dict[str, Any]]) -> list[str]:
    """Create only gentle secondary reminders; never prescribe catch-up load."""
    focus: list[str] = []
    for item in challenges[:4]:
        name = str(item.get("name") or "Garmin-udfordring")
        pct = item.get("completion_pct")
        days = item.get("days_remaining")
        remaining = None
        try:
            goal = float(item.get("goal"))
            progress = float(item.get("progress") or 0)
            remaining = max(0.0, goal - progress)
        except Exception:
            pass

        detail = []
        if pct is not None:
            detail.append(f"{float(pct):.0f}% gennemført")
        if days is not None:
            detail.append(f"{int(days)} dage tilbage")
        if remaining is not None and item.get("unit"):
            detail.append(f"ca. {remaining:g} {item.get('unit')} mangler")

        suffix = " · ".join(detail)
        focus.append(
            f"Garmin-udfordring som sekundært mål: {name}"
            + (f" ({suffix})" if suffix else "")
            + ". Brug kun allerede passende træning eller sikker ekstra let aktivitet; jagt aldrig udfordringen på bekostning af restitution eller løbsmålet."
        )
    return focus


def active_chat_notes(payload: dict[str, Any]) -> list[str]:
    notes = []
    for row in payload.get("notes", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict) or row.get("active") is False:
            continue
        text = str(row.get("text") or "").strip()
        if text:
            notes.append(text[:500])
    return notes[-12:]


def main() -> int:
    snapshot = base.load(base.SNAPSHOT, {})
    calendar = base.load(base.CALENDAR, {})
    history = base.load(base.HISTORY, {})
    goal = base.goal_data()
    challenge_data = load_local(CHALLENGES, {})
    chat_notes_data = load_local(CHAT_NOTES, {})

    if not snapshot:
        print("ERROR: snapshot.json mangler.")
        return 2
    if not calendar:
        print("ERROR: scheduled_workouts.json mangler.")
        return 3

    recent = base.training_stats(snapshot, 0, 7)
    previous = base.training_stats(snapshot, 7, 7)
    recovery = base.recovery_analysis(history, snapshot)
    match = match_recent_plan(snapshot, calendar, days_back=14)
    event = base.event_summary(goal)
    strength_14 = base.strength_count(snapshot, 14)
    future = base.upcoming(calendar, 10)
    challenges = compact_challenges(challenge_data)
    athlete_notes = active_chat_notes(chat_notes_data)

    primary_focus = base.deterministic_focus(event, recent, previous, strength_14)
    secondary_focus = challenge_focus(challenges)

    state = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "recovery": recovery,
        "training": {
            "last_7_days": recent,
            "previous_7_days": previous,
            "strength_sessions_last_14_days": strength_14,
        },
        "plan_match": match,
        "upcoming_workouts": future,
        "event": event,
        "garmin_challenges": challenges,
        "athlete_notes": athlete_notes,
        "focus_points": primary_focus + secondary_focus,
        "garmin_writeback_enabled": False,
        "analysis_mode": "deterministic_v2",
        "priority_order": [
            "recovery_and_health_signals",
            "primary_event_goal",
            "safe_progression_and_actual_training",
            "athlete_constraints",
            "garmin_challenges_secondary",
        ],
    }

    # Temporary deterministic wording. coach_voice.py replaces this after the
    # validated 7-day plan has been generated.
    state["narrative"] = base.fallback_narrative(state)

    base.JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    base.JSON_OUT.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== STRUKTURERET COACH-ANALYSE ===")
    print(f"Restitution: {recovery.get('state')} | historikdage: {recovery.get('history_days', 0)}")
    print(f"Seneste 7 dage: {recent.get('runs', 0)} ture / {recent.get('km', 0)} km")
    print(
        f"Planmatch: {match.get('matched', 0)}/{match.get('planned_workouts', 0)} afgjorte | "
        f"pending i dag: {match.get('pending', 0)} | usikre: {match.get('uncertain', 0)}"
    )
    print(f"Mål: {event.get('name')} | {event.get('days_to_event')} dage")
    print(f"Aktive Garmin-udfordringer: {len(challenges)}")
    print(f"Aktive coach-noter fra lokal chat: {len(athlete_notes)}")
    print(f"Coach-state: {base.JSON_OUT}")
    print("Ingen AI-formulering blev kørt i dette trin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
