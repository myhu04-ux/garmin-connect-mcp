r"""Generate a read-only adaptive training preview for the active race goal.

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
from pathlib import Path
from typing import Any

import requests

DEFAULT_SNAPSHOT = Path(r"C:\GarminCoach\data\snapshot.json")
DEFAULT_GOAL = Path(__file__).parent / "goals" / "thy-trail-2026.json"
DEFAULT_OUTPUT = Path(r"C:\GarminCoach\data\coach_preview.json")
DEFAULT_TEXT = Path(r"C:\GarminCoach\data\coach_preview.txt")
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"


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
        quality = "45 min meget roligt; ingen intervaller"
        sat = f"{max(50, round(longest * 0.70)):.0f} min rolig trail"
        sun = "Hvile eller 30 min gang"
    elif state == "yellow":
        quality = "45-50 min roligt med 4 x 20 s lette stigningsløb, kun hvis benene føles gode"
        sat = f"{max(60, round(longest * 0.90)):.0f} min rolig trail, øv energiindtag"
        sun = "35-40 min meget rolig trail på trætte ben"
    else:
        quality = "55-65 min i alt inkl. 6 x 3 min kontrolleret bakke, rolig jog som pause"
        if phase in {"specific_build", "race_specific_peak"}:
            sat = f"{max(75, round(longest * 1.05)):.0f} min rolig trail, bakker/ujævnt terræn, øv energiindtag"
            sun = "45-55 min rolig trail på trætte ben; lav intensitet"
        elif phase == "taper":
            sat = f"{max(55, round(longest * 0.70)):.0f} min rolig trail"
            sun = "30-40 min roligt"
        else:
            sat = f"{max(70, round(longest * 1.05)):.0f} min rolig lang tur"
            sun = "40-45 min roligt"

    sessions = [
        {"date": start.isoformat(), "type": "rest", "purpose": "absorbere den seneste træning"},
        {"date": (start + dt.timedelta(days=1)).isoformat(), "type": "easy_run", "session": "45-55 min roligt", "purpose": "aerob vedligeholdelse"},
        {"date": (start + dt.timedelta(days=3)).isoformat(), "type": "quality", "session": quality, "purpose": "bakkestyrke og løbeøkonomi uden unødig træthed"},
        {"date": (start + dt.timedelta(days=5)).isoformat(), "type": "long_trail", "session": sat, "purpose": "Thy-specifik tid på benene samt terræn- og energiøvelse"},
        {"date": (start + dt.timedelta(days=6)).isoformat(), "type": "back_to_back", "session": sun, "purpose": "robusthed til dag 2 i etapeløbet"},
    ]
    return {
        "source": "deterministic_fallback",
        "goal": goal["name"],
        "phase": phase,
        "recovery_state": state,
        "week_start": start.isoformat(),
        "sessions": sessions,
        "coach_note": "Kun preview. Garmin write-back er fortsat slået fra.",
    }


def model_prompt(summary: dict[str, Any], goal: dict[str, Any]) -> str:
    return f"""Du er en konservativ udholdenhedstræner for EN løber.
Målet er et todages trail-etapeløb. Brug løbsmålet, den aktuelle fase, de seneste
Garmin-data og restitutionssignaler nedenfor. Løberen skal forberedes til 23 km
dag 1 og 19 km dag 2 med skov, teknisk trail, grus, klit/hede, sand/strand og vind.

REGLER
- Bevar blokkens formål: specifik forberedelse til et todages trailløb.
- Tilpas planen til restitution og nylig træning; brug ikke en rigid skabelon.
- Hold samlet løbedistance inden for den angivne sikre km-ramme.
- Undgå to hårde dage i træk. Back-to-back weekend er kun rolig/moderat; dag 2 træner løb på trætte ben.
- Brug trail/ujævnt terræn og energi-/væskeøvelse hvor relevant.
- Ved red restitution fjernes kvalitet og træningen reduceres.
- Ved yellow restitution skal kvalitet være reduceret eller valgfri.
- Manglende Training Readiness er IKKE det samme som dårlig restitution.
- Dette er KUN PREVIEW. Påstå aldrig at noget er skrevet til Garmin.
- Foretræk 4 løbedage, medmindre nylig frekvens eller restitution taler for 3.
- Skriv kort og konkret dansk i JSON-værdierne.

AKTIVT MÅL:
{json.dumps(goal, ensure_ascii=False, indent=2)}

AKTUEL STATUS:
{json.dumps(summary, ensure_ascii=False, indent=2)}

Returner KUN gyldig JSON med præcis denne topstruktur:
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
        "think": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": "Returner kun streng gyldig JSON. Vær konservativ, konkret og datadrevet."},
            {"role": "user", "content": model_prompt(summary, goal)},
        ],
        "options": {
            "temperature": 0.15,
            "num_predict": 1200,
        },
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=180)
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
            print(f"Asking local Ollama model {args.model} (thinking disabled)...")
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
