"""Offline regression tests for benchmark-style weekly coaching routing."""

from __future__ import annotations

import datetime as dt

import coach_chat_agent
import shadow_week


def main() -> int:
    today = dt.date(2026, 9, 8)
    year, week = shadow_week.parse_week(
        "Hvordan skal uge 38 se ud på baggrund af de sidste ugers træning og mit helbred?",
        today=today,
    )
    assert (year, week) == (2026, 38), (year, week)

    phrases = [
        "Hvordan skal uge 38 se ud på baggrund af de sidste ugers træning og mit helbred?",
        "Lav en træningsplan for uge 38 ud fra min restitution og de seneste uger.",
        "Planlæg uge 38 ud fra empiri og mine Garmin-data.",
        "Hvordan bør jeg træne i uge 38?",
    ]
    for phrase in phrases:
        assert coach_chat_agent.wants_shadow_week(phrase), phrase

    assert not coach_chat_agent.wants_shadow_week("Hvordan er min restitution i dag?")
    print("OK: naturlige uge-38 benchmark-prompts routes til shadow-week expert coach")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
