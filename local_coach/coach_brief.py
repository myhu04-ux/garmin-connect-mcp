r"""Create a concise Danish coach brief from local Garmin coach data.

READ ONLY. This module does not write to Garmin.
It deliberately distinguishes between measured facts and interpretation. If a
multi-day health history is not available yet, it says so instead of inventing
trends.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

SNAPSHOT = Path(r"C:\GarminCoach\data\snapshot.json")
CALENDAR = Path(r"C:\GarminCoach\data\scheduled_workouts.json")
TEMPLATES = Path(r"C:\GarminCoach\data\approved_workout_templates.json")
HISTORY = Path(r"C:\GarminCoach\data\health_history.json")
GOAL = Path(__file__).parent / "goals" / "thy-trail-2026.json"
OUT = Path(r"C:\GarminCoach\data\coach_brief.txt")


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def fnum(v: Any, digits: int = 1) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return "?"


def activity_date(a: dict[str, Any]) -> dt.date | None:
    raw = str(a.get("start") or "")[:10]
    try:
        return dt.date.fromisoformat(raw)
    except Exception:
        return None


def recent_training(snapshot: dict[str, Any], days: int = 7) -> dict[str, Any]:
    today = dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    rows = [a for a in snapshot.get("running_activities", []) if activity_date(a) and activity_date(a) >= start]
    km = sum(float(a.get("distance_m") or 0) for a in rows) / 1000
    mins = sum(float(a.get("duration_s") or 0) for a in rows) / 60
    elev = sum(float(a.get("elevation_gain_m") or 0) for a in rows)
    loads = [float(a.get("training_load")) for a in rows if a.get("training_load") is not None]
    longest = max((float(a.get("distance_m") or 0) / 1000 for a in rows), default=0)
    return {
        "runs": len(rows),
        "km": round(km, 1),
        "minutes": round(mins),
        "elevation_m": round(elev),
        "load": round(sum(loads), 1) if loads else None,
        "longest_km": round(longest, 1),
    }


def upcoming(calendar: dict[str, Any], days: int = 10) -> list[dict[str, Any]]:
    today = dt.date.today()
    end = today + dt.timedelta(days=days)
    rows = []
    for item in calendar.get("items", []):
        try:
            d = dt.date.fromisoformat(str(item.get("date"))[:10])
        except Exception:
            continue
        if today <= d <= end:
            rows.append(item)
    return sorted(rows, key=lambda x: x.get("date") or "")


def health_section(snapshot: dict[str, Any], history: dict[str, Any]) -> list[str]:
    sleep = snapshot.get("sleep") or {}
    today = snapshot.get("today") or {}
    lines: list[str] = []

    history_days = history.get("days") if isinstance(history, dict) else None
    if not isinstance(history_days, list) or len(history_days) < 7:
        lines.append("- Historik: endnu ikke nok fler-dages data til en sikker trendvurdering.")
    else:
        lines.append(f"- Historik: {len(history_days)} dage til personlig baseline.")

    if sleep.get("sleep_hours") is not None:
        extra = f", score {sleep.get('sleep_score')}" if sleep.get("sleep_score") is not None else ""
        lines.append(f"- Seneste søvn: {fnum(sleep.get('sleep_hours'))} timer{extra}.")
    if sleep.get("avg_overnight_hrv") is not None:
        lines.append(f"- Seneste natlige HRV: {fnum(sleep.get('avg_overnight_hrv'), 0)} ms.")
    rhr = today.get("resting_hr") or sleep.get("resting_hr")
    if rhr is not None:
        lines.append(f"- Hvilepuls: {fnum(rhr, 0)} bpm.")
    if today.get("body_battery_current") is not None:
        lines.append(f"- Body Battery nu: {fnum(today.get('body_battery_current'), 0)}.")
    if today.get("avg_stress") is not None:
        lines.append(f"- Gennemsnitlig stress: {fnum(today.get('avg_stress'), 0)}.")

    if len(lines) == 1 and lines[0].startswith("- Historik"):
        lines.append("- Der er endnu for få restitutionsfelter til at konkludere noget meningsfuldt.")
    return lines


def main() -> int:
    snapshot = load(SNAPSHOT, {})
    calendar = load(CALENDAR, {})
    history = load(HISTORY, {})
    goal = load(GOAL, {})

    stats = recent_training(snapshot, 7)
    future = upcoming(calendar, 10)

    event_name = goal.get("name", "aktivt mål")
    event_dates = goal.get("event_dates") or []
    days_to = None
    if event_dates:
        try:
            days_to = (dt.date.fromisoformat(event_dates[0]) - dt.date.today()).days
        except Exception:
            pass

    lines = [
        "=== COACH-RESUMÉ ===",
        "",
        "HELbred / RESTITUTION",
        *health_section(snapshot, history),
        "",
        "TRÆNING PÅ SPORET",
        f"- Seneste 7 dage: {stats['runs']} løb, {stats['km']} km, {stats['minutes']} min, længste tur {stats['longest_km']} km.",
    ]
    if stats.get("elevation_m"):
        lines.append(f"- Højdemeter seneste 7 dage: ca. {stats['elevation_m']} m.")
    if stats.get("load") is not None:
        lines.append(f"- Registreret Garmin-træningsbelastning: {stats['load']}.")

    if future:
        lines.append(f"- Planlagte Garmin-workouts næste 10 dage: {len(future)}.")
        for item in future[:6]:
            lines.append(f"  {item.get('date')}: {item.get('title') or 'workout'}")
    else:
        lines.append("- Ingen planlagte workouts blev fundet i den lokale kalenderfil for de næste 10 dage.")

    lines.extend(["", "FOKUS FREMAD"])
    if days_to is not None:
        lines.append(f"- {event_name}: {days_to} dage til første løbsdag.")
    lines.append("- Prioritér Thy-specifik kontinuitet: trail/ujævnt terræn, tid på benene og kontrolleret back-to-back.")
    lines.append("- Bevar den godkendte styrketræning som fast master; AI må ikke opfinde nye styrkeøvelser.")
    if not isinstance(history.get("days") if isinstance(history, dict) else None, list) or len(history.get("days", [])) < 7:
        lines.append("- Næste datamål: opbyg mindst 7-14 dages personlig restitutionshistorik før automatiske helbredsbaserede ændringer aktiveres.")

    lines.extend([
        "",
        "STATUS",
        "- Dette er et trænings-/restitutionsresumé, ikke en medicinsk vurdering.",
        "- Garmin write-back: OFF.",
    ])

    text = "\n".join(lines)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nGemt lokalt: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
