"""Offline quality regression tests for the expert local coach path."""

from __future__ import annotations

import model_manager
import shadow_week_expert as expert


def main() -> int:
    assert model_manager.COACH_MODEL == "qwen3:8b"

    snapshot = {
        "all_activities": [
            {
                "start": "2026-09-05 09:00:00",
                "name": "Trail quality",
                "type": "running",
                "distance_m": 10000,
                "duration_s": 3600,
                "training_load": 120,
                "aerobic_te": 4.0,
            }
        ]
    }
    history = {
        "days": [
            {
                "date": "2026-09-06",
                "sleep_hours": 7.2,
                "hrv_last_night": 54,
                "resting_hr": 49,
                "avg_stress": 24,
            }
        ]
    }
    examples = expert.personal_response_examples(snapshot, history)
    assert len(examples) == 1
    assert examples[0]["next_day"]["hrv_last_night"] == 54

    spec = expert.sanitize_session_spec({
        "goal": "vo2max",
        "total_minutes": 52,
        "repetitions": 5,
        "work_min": 3,
        "recovery_min": 2,
        "effort": "kontrolleret hårdt",
        "target_km": 999,
    })
    assert spec["goal"] == "vo2max"
    assert spec["repetitions"] == 5
    assert "target_km" not in spec

    context = {
        "recovery": {"state": "green"},
        "event": {"event_type": "trail", "course": {"terrain": "sand dunes and trail"}},
        "phase": "specific_build",
        "athlete_preferences": {"max_running_days_per_week": 5},
    }
    weak_plan = {
        "actions": [
            {
                "action": "ADD",
                "date": "2026-09-15",
                "family": "easy_run",
                "intensity": "easy",
                "reason": "Rolig aerob kontinuitet efter restitution.",
            }
        ]
    }
    issues = expert.quality_issues(weak_plan, context)
    assert any("trail" in issue.casefold() or "nøglepas" in issue.casefold() for issue in issues)

    stronger_plan = {
        "actions": [
            {
                "action": "ADD",
                "date": "2026-09-15",
                "family": "quality_interval",
                "intensity": "hard",
                "reason": "Kontrolleret aerob kvalitet med flere restitutionsdage før langturen.",
            },
            {
                "action": "ADD",
                "date": "2026-09-17",
                "family": "trail_easy",
                "intensity": "easy",
                "reason": "Roligt terrænpas for teknik og løbsrelevant underlag uden ekstra kvalitet.",
            },
            {
                "action": "ADD",
                "date": "2026-09-20",
                "family": "long_trail",
                "intensity": "moderate",
                "reason": "Ugens racespecifikke nøglepas med tid på benene og underlagstilvænning.",
            },
        ]
    }
    stronger_issues = expert.quality_issues(stronger_plan, context)
    assert not any("mangler et langt" in issue.casefold() for issue in stronger_issues)
    assert not any("sammenhængende" in issue.casefold() for issue in stronger_issues)

    print("OK: expert coach benchmark tests bestået")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
