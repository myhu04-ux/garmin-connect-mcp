"""Deterministic structured coach analysis.

This v2 deliberately does not call Ollama. It computes the factual coaching state;
the language model is used later, once, in coach_voice.py after the validated
7-day preview exists. This removes a redundant model pass and keeps facts separate
from phrasing.
"""

from __future__ import annotations

import datetime as dt
import json

import coach_brief as base
from plan_matcher import match_recent_plan


def main() -> int:
    snapshot = base.load(base.SNAPSHOT, {})
    calendar = base.load(base.CALENDAR, {})
    history = base.load(base.HISTORY, {})
    goal = base.goal_data()

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
        "focus_points": base.deterministic_focus(event, recent, previous, strength_14),
        "garmin_writeback_enabled": False,
        "analysis_mode": "deterministic_v2",
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
    print(f"Coach-state: {base.JSON_OUT}")
    print("Ingen AI-formulering blev kørt i dette trin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
