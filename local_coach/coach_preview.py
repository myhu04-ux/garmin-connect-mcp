"""Generate a read-only adaptive training preview for the active race goal.

Inputs stay local on the Acer PC:
- C:\GarminCoach\data\snapshot.json (Garmin snapshot)
- local goal JSON from this repository

The script summarizes recent training/recovery, derives conservative guardrails,
and asks a local Ollama model to produce a structured 7-day plan. Nothing is
written to Garmin. If Ollama is unavailable, a deterministic fallback preview
is still produced.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

import requests

DEFAULT_SNAPSHOT = Path(r"C:\GarminCoach\data\snapshot.json")
DEFAULT_GOAL = Path(__file__).parent / "goals" / "thy-trail-2026.json"
DEFAULT_OUTPUT = Path(r"C:\GarminCoach\data\coach_preview.json")
DEFAULT_TEXT = Path(r"C:\GarminCoach\data\coach_preview.txt")
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:4b"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def km(value: Any) -> float:
    try:
        return round(float(value or 0) / 1000.0, 2)
    except Exception:
        return 0.0


def minutes(value: Any) -> float:
    try:
        return round(float(value or 0) / 60.0, 1)
    except Exception:
        return 0.0


def date_of(activity: dict[str, Any]) -> dt.date | None:
    raw = str(activity.get("start") or "")[:10]
    try:
        return dt.date.fromisoformat(raw)
    except Exception:
        return None


def summarize(snapshot: dict[str, Any], goal: dict[str, Any]) -> dict[str, Any]:
    activities = snapshot.get("running_activities") or []
    today = dt.date.today()
    week_ago = today - dt.timedelta(days=6)
    prev_week_start = today - dt.timedelta(days=13)
    prev_week_end = today - dt.timedelta(days=7)

    current_week = [a for a in activities if (date_of(a) and date_of(a) >= week_ago)]
    previous_week = [
        a
        for a in activities
        if date_of(a) and prev_week_start <= date_of(a) <= prev_week_end
    ]

    def group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        distances = [km(a.get("distance_m")) for a in rows]
        durations = [minutes(a.get("duration_s")) for a in rows]
        loads = [float(a.get("training_load")) for a in rows if a.get("training_load") is not None]
        elevation = [float(a.get("elevation_gain_m")) for a in rows if a.get("elevation_gain_m") is not None]
        return {
            "runs": len(rows),
            "distance_km": round(sum(distances), 1),
            "duration_min": round(sum(durations), 0),
            "longest_run_km": round(max(distances), 1) if distances else 0,
            "longest_run_min": round(max(durations), 0) if durations else 0,
            "training_load_sum": round(sum(loads), 1) if loads else None,
            "elevation_gain_m": round(sum(elevation), 0) if elevation else None,
        }

    recent = group_stats(current_week)
    previous = group_stats(previous_week)

    sleep = snapshot.get("sleep") or {}
    wellness = snapshot.get("today") or {}
    readiness = snapshot.get("training_readiness") or []

    signals: list[str] = []
    recovery_state = "green"
    penalty = 0

    sleep_h = sleep.get("sleep_hours")
    sleep_score = sleep.get("sleep_score")
    overnight_hrv = sleep.get("avg_overnight_hrv")
    rhr = wellness.get("resting_hr") or sleep.get("resting_hr")
    rhr_avg = wellness.get("resting_hr_7d_avg")

    if sleep_h is not None:
        if sleep_h < 5.5:
            penalty += 3
            signals.append(f"very short sleep ({sleep_h:.1f} h)")
        elif sleep_h < 6.5:
            penalty += 2
            signals.append(f"short sleep ({sleep_h:.1f} h)")
        elif sleep_h < 7.0:
            penalty += 1
            signals.append(f"sleep slightly short ({sleep_h:.1f} h)")

    if sleep_score is not None:
        if sleep_score < 55:
            penalty += 3
            signals.append(f"low sleep score ({sleep_score})")
        elif sleep_score < 70:
            penalty += 1
            signals.append(f"moderate sleep score ({sleep_score})")

    if rhr is not None and rhr_avg is not None:
        delta = float(rhr) - float(rhr_avg)
        if delta >= 6:
            penalty += 3
            signals.append(f"resting HR {delta:.0f} bpm above 7-day average")
        elif delta >= 3:
            penalty += 1
            signals.append(f"resting HR {delta:.0f} bpm above 7-day average")

    if readiness:
        scores = [r.get("score") for r in readiness if r.get("score") is not None]
        if scores:
            score = float(scores[0])
            if score < 35:
                penalty += 3
                signals.append(f"Garmin readiness low ({score:.0f})")
            elif score < 55:
                penalty += 1
                signals.append(f"Garmin readiness moderate ({score:.0f})")

    if penalty >= 4:
        recovery_state = "red"
    elif penalty >= 2:
        recovery_state = "yellow"

    event_date = dt.date.fromisoformat(goal["event_dates"][0])
    days_to_event = max(0, (event_date - today).days)
    weeks_to_event = round(days_to_event / 7.0, 1)
    if days_to_event <= 10:
        phase = "taper"
    elif days_to_event <= 28:
        phase = "race_specific_peak"
    elif days_to_event <= 56:
        phase = "specific_build"
    else:
        phase = "base_build"

    # Conservative volume envelope. The model may move volume within this band,
    # but should not exceed it without an explicit human-approved goal change.
    reference_km = max(recent["distance_km"], previous["distance_km"], 1.0)
    if recovery_state == "red":
        target_low = reference_km * 0.55
        target_high = reference_km * 0.75
    elif recovery_state == "yellow":
        target_low = reference_km * 0.75
        target_high = reference_km * 0.95
    elif phase == "taper":
        target_low = reference_km * 0.60
        target_high = reference_km * 0.80
    else:
        target_low = reference_km * 0.95
        target_high = reference_km * 1.08

    return {
        "generated_date": today.isoformat(),
        "days_to_event": days_to_event,
        "weeks_to_event": weeks_to_event,
        "phase": phase,
        "recovery_state": recovery_state,
        "recovery_signals": signals,
        "available_recovery_metrics": {
            "sleep_hours": sleep_h,
            "sleep_score": sleep_score,
            "overnight_hrv": overnight_hrv,
            "resting_hr": rhr,
            "resting_hr_7d_avg": rhr_avg,
            "training_readiness_available": bool(readiness),
            "body_battery_current": wellness.get("body_battery_current"),
            "avg_stress": wellness.get("avg_stress"),
        },
        "last_7_days": recent,
        "previous_7_days": previous,
        "safe_next_week_distance_km": {
            "low": round(target_low, 1),
            "high": round(target_high, 1),
        },
    }


def next_monday(today: dt.date) -> dt.date:
    return today + dt.timedelta(days=(7 - today.weekday()) % 7 or 7)


def fallback_plan(summary: dict[str, Any], goal: dict[str, Any]) -> dict[str, Any]:
    """Safe deterministic plan if the local model is unavailable."""
    today = dt.date.today()
    start = next_monday(today)
    state = summary["recovery_state"]
    phase = summary["phase"]
    longest = float(summary["last_7_days"].get("longest_run_min") or 60)

    if state == "red":
        quality = "45 min very easy; no intervals"
        sat = f"{max(50, round(longest * 0.70)):.0f} min easy trail"
        sun = "Rest or 30 min walk"
    elif state == "yellow":
        quality = "45-50 min easy with 4 x 20 s relaxed strides only if legs feel good"
        sat = f"{max(60, round(longest * 0.90)):.0f} min easy trail, practise fueling"
        sun = "35-40 min very easy trail on tired legs"
    else:
        quality = "55-65 min total incl. 6 x 3 min controlled hill effort, easy jog recoveries"
        if phase in {"specific_build", "race_specific_peak"}:
            sat = f"{max(75, round(longest * 1.05)):.0f} min easy trail, hills/uneven terrain, practise fueling"
            sun = "45-55 min easy trail on tired legs; keep intensity low"
        elif phase == "taper":
            sat = f"{max(55, round(longest * 0.70)):.0f} min easy trail"
            sun = "30-40 min easy"
        else:
            sat = f"{max(70, round(longest * 1.05)):.0f} min easy long run"
            sun = "40-45 min easy"

    sessions = [
        {"date": start.isoformat(), "type": "rest", "purpose": "absorb recent training"},
        {"date": (start + dt.timedelta(days=1)).isoformat(), "type": "easy_run", "session": "45-55 min easy", "purpose": "aerobic maintenance"},
        {"date": (start + dt.timedelta(days=3)).isoformat(), "type": "quality", "session": quality, "purpose": "hill strength / running economy without excessive fatigue"},
        {"date": (start + dt.timedelta(days=5)).isoformat(), "type": "long_trail", "session": sat, "purpose": "Thy-specific time on feet and terrain/fueling practice"},
        {"date": (start + dt.timedelta(days=6)).isoformat(), "type": "back_to_back", "session": sun, "purpose": "day-2 resilience for two-day stage race"},
    ]
    return {
        "source": "deterministic_fallback",
        "goal": goal["name"],
        "phase": phase,
        "recovery_state": state,
        "week_start": start.isoformat(),
        "sessions": sessions,
        "coach_note": "Preview only. Garmin write-back remains disabled.",
    }


def model_prompt(summary: dict[str, Any], goal: dict[str, Any]) -> str:
    return f"""You are a conservative endurance running coach planning for ONE athlete.
The active event is a two-day trail stage race. Use the race goal, current phase,
recent Garmin-derived training and recovery signals below. The athlete must be
prepared for 23 km on day 1 and 19 km on day 2, with forest, technical trail,
gravel, dunes/heath, sand/beach and wind exposure.

PRIMARY PRINCIPLES
- Preserve the purpose of the block: specificity for a two-day trail race.
- The plan must adapt to current recovery and recent training rather than follow a rigid template.
- Do not increase total running distance outside the supplied safe distance envelope.
- Avoid consecutive hard days. Back-to-back weekend running is allowed only when both are easy/moderate; day 2 is specifically about tired-leg resilience.
- Include trail/uneven-terrain exposure and fueling practice where appropriate.
- If recovery_state is red, remove quality work and reduce training.
- If recovery_state is yellow, be cautious and make quality optional or reduced.
- Training Readiness may be absent; do NOT treat absence as poor readiness.
- This is PREVIEW ONLY. Never claim anything was written to Garmin.
- Prefer 4 running days unless recent frequency or recovery makes 3 more sensible.
- Write concise Danish text inside JSON values.

ACTIVE GOAL:
{json.dumps(goal, ensure_ascii=False, indent=2)}

CURRENT SUMMARY:
{json.dumps(summary, ensure_ascii=False, indent=2)}

Return ONLY valid JSON with this exact top-level structure:
{{
  "source": "ollama",
  "goal": "...",
  "phase": "...",
  "recovery_state": "green|yellow|red",
  "week_start": "YYYY-MM-DD",
  "weekly_distance_target_km": 0.0,
  "week_focus": "...",
  "sessions": [
    {{
      "date": "YYYY-MM-DD",
      "type": "rest|easy_run|quality|long_trail|back_to_back|strength",
      "session": "...",
      "purpose": "...",
      "intensity": "...",
      "garmin_structured_candidate": true
    }}
  ],
  "coach_note": "..."
}}
"""


def call_ollama(summary: dict[str, Any], goal: dict[str, Any], model: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": "Return strict JSON only. Be conservative, specific and evidence-driven."},
            {"role": "user", "content": model_prompt(summary, goal)},
        ],
        "options": {"temperature": 0.2},
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=300)
    response.raise_for_status()
    body = response.json()
    content = body.get("message", {}).get("content", "")
    plan = json.loads(content)
    plan["source"] = "ollama"
    return plan


def render_text(plan: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        "GARMIN LOCAL COACH - PREVIEW ONLY",
        "=" * 44,
        f"Mål: {plan.get('goal', '')}",
        f"Fase: {plan.get('phase', summary.get('phase'))}",
        f"Restitution: {plan.get('recovery_state', summary.get('recovery_state'))}",
        f"Uger til løb: {summary.get('weeks_to_event')}",
        f"Sikker km-ramme næste uge: {summary['safe_next_week_distance_km']['low']} - {summary['safe_next_week_distance_km']['high']} km",
    ]
    if plan.get("weekly_distance_target_km") is not None:
        lines.append(f"Planlagt distance: {plan.get('weekly_distance_target_km')} km")
    if plan.get("week_focus"):
        lines.append(f"Ugens fokus: {plan['week_focus']}")
    lines.append("")
    for session in plan.get("sessions", []):
        lines.append(f"{session.get('date', '')}  [{session.get('type', '')}]")
        if session.get("session"):
            lines.append(f"  {session['session']}")
        if session.get("purpose"):
            lines.append(f"  Formål: {session['purpose']}")
        if session.get("intensity"):
            lines.append(f"  Intensitet: {session['intensity']}")
        lines.append("")
    lines.append(f"Coach: {plan.get('coach_note', '')}")
    lines.append("")
    lines.append("GARMIN WRITE-BACK: OFF")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--goal", type=Path, default=DEFAULT_GOAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--text-output", type=Path, default=DEFAULT_TEXT)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--no-ai", action="store_true")
    args = parser.parse_args()

    if not args.snapshot.exists():
        raise SystemExit(f"Snapshot missing: {args.snapshot}")
    if not args.goal.exists():
        raise SystemExit(f"Goal missing: {args.goal}")

    snapshot = load_json(args.snapshot)
    goal = load_json(args.goal)
    summary = summarize(snapshot, goal)

    plan: dict[str, Any]
    if args.no_ai:
        plan = fallback_plan(summary, goal)
    else:
        try:
            print(f"Asking local Ollama model {args.model}...")
            plan = call_ollama(summary, goal, args.model)
        except Exception as exc:
            print(f"WARNING: Local AI unavailable or invalid response: {exc}")
            print("Using safe deterministic fallback preview instead.")
            plan = fallback_plan(summary, goal)
            plan["ai_error"] = str(exc)[:500]

    envelope = summary["safe_next_week_distance_km"]
    planned = plan.get("weekly_distance_target_km")
    if planned is not None:
        try:
            planned_f = float(planned)
            if planned_f < envelope["low"] or planned_f > envelope["high"]:
                plan["weekly_distance_target_km"] = min(max(planned_f, envelope["low"]), envelope["high"])
                plan["guardrail_adjustment"] = "weekly distance target clamped to safe envelope"
        except Exception:
            pass

    result = {"summary": summary, "plan": plan, "garmin_writeback_enabled": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    text = render_text(plan, summary)
    args.text_output.write_text(text, encoding="utf-8")

    print("\n=== COACH PREVIEW ===")
    print(text)
    print(f"\nJSON saved locally: {args.output}")
    print(f"Text saved locally: {args.text_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
