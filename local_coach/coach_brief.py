r"""Create an event-aware Danish coaching brief from local Garmin data.

READ ONLY. Facts and interpretation are kept separate. Recovery is assessed
against the athlete's own history when enough data exist. The local model is
used only to phrase a concise coach summary from structured facts; deterministic
fallback text remains available.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from pathlib import Path
from typing import Any, Callable

import requests

from plan_matcher import match_recent_plan

SNAPSHOT = Path(r"C:\GarminCoach\data\snapshot.json")
CALENDAR = Path(r"C:\GarminCoach\data\scheduled_workouts.json")
HISTORY = Path(r"C:\GarminCoach\data\health_history.json")
ACTIVE_GOAL = Path(r"C:\GarminCoach\data\active_goal.json")
FALLBACK_GOAL = Path(__file__).parent / "goals" / "thy-trail-2026.json"
OUT = Path(r"C:\GarminCoach\data\coach_brief.txt")
JSON_OUT = Path(r"C:\GarminCoach\data\coach_state.json")
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def goal_data() -> dict[str, Any]:
    goal = load(ACTIVE_GOAL, {})
    return goal if goal else load(FALLBACK_GOAL, {})


def date_of(raw: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(raw or "")[:10])
    except Exception:
        return None


def activity_date(a: dict[str, Any]) -> dt.date | None:
    return date_of(a.get("start"))


def running_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return snapshot.get("running_activities") or []


def training_stats(snapshot: dict[str, Any], start_days_ago: int, length: int) -> dict[str, Any]:
    today = dt.date.today()
    end = today - dt.timedelta(days=start_days_ago)
    start = end - dt.timedelta(days=length - 1)
    rows = [a for a in running_rows(snapshot) if activity_date(a) and start <= activity_date(a) <= end]
    distances = [float(a.get("distance_m") or 0) / 1000.0 for a in rows]
    mins = [float(a.get("duration_s") or 0) / 60.0 for a in rows]
    elev = [float(a.get("elevation_gain_m") or 0) for a in rows]
    loads = [float(a.get("training_load")) for a in rows if a.get("training_load") is not None]
    trail_runs = [a for a in rows if "trail" in f"{a.get('name','')} {a.get('type','')} {a.get('subtype','')}".lower()]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "runs": len(rows),
        "km": round(sum(distances), 1),
        "minutes": round(sum(mins)),
        "longest_km": round(max(distances), 1) if distances else 0.0,
        "elevation_m": round(sum(elev)),
        "training_load": round(sum(loads), 1) if loads else None,
        "trail_runs": len(trail_runs),
    }


def strength_count(snapshot: dict[str, Any], days: int = 14) -> int:
    today = dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    return sum(1 for a in snapshot.get("strength_activities", []) if activity_date(a) and activity_date(a) >= start)


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def values(rows: list[dict[str, Any]], getter: Callable[[dict[str, Any]], Any]) -> list[float]:
    out = []
    for row in rows:
        try:
            raw = getter(row)
            if raw is not None:
                out.append(float(raw))
        except Exception:
            pass
    return out


def recovery_analysis(history: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    rows = [r for r in history.get("days", []) if isinstance(r, dict) and r.get("date")]
    rows.sort(key=lambda r: str(r.get("date")))
    recent = rows[-3:]
    baseline = rows[:-3][-21:]
    signals: list[dict[str, Any]] = []
    score = 0

    def compare(name: str, getter: Callable[[dict[str, Any]], Any], mode: str, yellow: float, red: float, unit: str) -> None:
        nonlocal score
        recent_med = median(values(recent, getter))
        base_med = median(values(baseline, getter))
        if recent_med is None or base_med is None:
            return
        delta = recent_med - base_med
        severity = "normal"
        if mode == "lower_bad":
            if delta <= red:
                severity, score = "red", score + 2
            elif delta <= yellow:
                severity, score = "yellow", score + 1
        else:
            if delta >= red:
                severity, score = "red", score + 2
            elif delta >= yellow:
                severity, score = "yellow", score + 1
        signals.append({
            "metric": name,
            "recent_median": round(recent_med, 1),
            "baseline_median": round(base_med, 1),
            "delta": round(delta, 1),
            "unit": unit,
            "severity": severity,
        })

    # Absolute thresholds are deliberately modest and athlete-relative.
    compare("søvn", lambda r: r.get("sleep_hours"), "lower_bad", -0.5, -1.0, "timer")
    compare("søvnscore", lambda r: r.get("sleep_score"), "lower_bad", -7.0, -14.0, "point")
    compare("hvilepuls", lambda r: r.get("resting_hr"), "higher_bad", 3.0, 5.0, "bpm")
    compare("stress", lambda r: r.get("avg_stress"), "higher_bad", 5.0, 10.0, "point")

    # HRV uses relative change because normal levels vary strongly between people.
    recent_hrv = median(values(recent, lambda r: r.get("hrv_last_night") or r.get("sleep_hrv")))
    base_hrv = median(values(baseline, lambda r: r.get("hrv_last_night") or r.get("sleep_hrv")))
    if recent_hrv is not None and base_hrv and base_hrv > 0:
        pct = 100.0 * (recent_hrv - base_hrv) / base_hrv
        severity = "normal"
        if pct <= -20:
            severity, score = "red", score + 2
        elif pct <= -10:
            severity, score = "yellow", score + 1
        signals.append({
            "metric": "HRV",
            "recent_median": round(recent_hrv, 1),
            "baseline_median": round(base_hrv, 1),
            "delta_pct": round(pct, 1),
            "unit": "ms",
            "severity": severity,
        })

    enough = len(rows) >= 7 and len(baseline) >= 4
    if not enough:
        state = "insufficient_history"
    elif score >= 4:
        state = "red"
    elif score >= 2:
        state = "yellow"
    else:
        state = "green"

    # Current Garmin fields are supporting evidence, not substitutes for trend.
    current = snapshot.get("today") or {}
    sleep = snapshot.get("sleep") or {}
    return {
        "history_days": len(rows),
        "baseline_days": len(baseline),
        "state": state,
        "signals": signals,
        "latest": {
            "sleep_hours": sleep.get("sleep_hours"),
            "sleep_score": sleep.get("sleep_score"),
            "hrv": sleep.get("avg_overnight_hrv"),
            "resting_hr": current.get("resting_hr") or sleep.get("resting_hr"),
            "body_battery": current.get("body_battery_current"),
            "stress": current.get("avg_stress"),
        },
    }


def upcoming(calendar: dict[str, Any], days: int = 10) -> list[dict[str, Any]]:
    today = dt.date.today()
    end = today + dt.timedelta(days=days)
    rows = []
    for item in calendar.get("items", []):
        d = date_of(item.get("date"))
        if d and today <= d <= end:
            rows.append({
                "date": d.isoformat(),
                "title": item.get("title"),
                "workout_id": item.get("workout_id"),
            })
    return sorted(rows, key=lambda x: x["date"])


def event_summary(goal: dict[str, Any]) -> dict[str, Any]:
    dates = goal.get("event_dates") or []
    first_date = date_of(dates[0]) if dates else None
    distance = goal.get("distance") if isinstance(goal.get("distance"), dict) else {}
    total = distance.get("total_km") if isinstance(distance, dict) else None
    if total is None:
        total = goal.get("total_distance_km")
    priorities = goal.get("training_priorities") or []
    compact_priorities = []
    for item in priorities:
        if isinstance(item, dict):
            compact_priorities.append({"priority": item.get("priority"), "reason": item.get("reason")})
        else:
            compact_priorities.append({"priority": str(item), "reason": None})
    return {
        "name": goal.get("event_name") or goal.get("name") or "aktivt mål",
        "event_type": goal.get("event_type"),
        "dates": dates,
        "days_to_event": (first_date - dt.date.today()).days if first_date else None,
        "distance_km": total,
        "course": goal.get("course") or {"terrain": goal.get("terrain")},
        "training_priorities": compact_priorities[:10],
        "confidence": goal.get("confidence"),
        "sources": goal.get("sources") or [],
    }


def deterministic_focus(event: dict[str, Any], recent: dict[str, Any], previous: dict[str, Any], strength_14: int) -> list[str]:
    focus: list[str] = []
    etype = str(event.get("event_type") or "").lower()
    course_text = json.dumps(event.get("course") or {}, ensure_ascii=False).lower()
    priorities_text = json.dumps(event.get("training_priorities") or [], ensure_ascii=False).lower()
    combined = etype + " " + course_text + " " + priorities_text

    if "stage" in combined or "back" in combined or "etape" in combined:
        focus.append("Kontrolleret back-to-back træning, så dag 2 kan løbes på trætte ben uden at blive et ekstra hårdt pas.")
    if any(word in combined for word in ("trail", "sand", "strand", "technical", "teknisk", "klit")):
        focus.append("Løb regelmæssigt på løbsspecifikt underlag og ujævnt terræn; styr efter indsats frem for vejtempo.")
    if "marathon" in combined and "road" in combined:
        focus.append("Prioritér lange jævne ture og blokke omkring forventet marathonindsats på asfalt/fladt underlag.")
    if recent.get("longest_km", 0) < previous.get("longest_km", 0) and (event.get("days_to_event") or 999) > 21:
        focus.append("Hold øje med progressionen i langturen; der er ingen grund til at forcere den, men den bør ikke stagnere flere uger i træk.")
    if strength_14 == 0:
        focus.append("Den godkendte styrketræning har ikke været registreret de seneste 14 dage; overvej at få den tilbage uden at placere den tæt på nøglepasset.")
    if any(word in combined for word in ("fuel", "energi", "marathon", "stage", "etape")):
        focus.append("Øv energi- og væskeindtag på de længere pas, så race-day strategien er afprøvet før de sidste uger.")
    return focus[:5]


def ai_summary(state: dict[str, Any], model: str = MODEL) -> dict[str, Any] | None:
    prompt = f"""Du er personlig løbetræner. Skriv et kort, konkret dansk coach-resumé ud fra
KUN data nedenfor. Du må ikke opfinde helbredstrends, træninger eller løbsfakta.
Hvis historikken er utilstrækkelig, sig det. Et flyttet pas, som matcher +/-1 dag,
skal omtales som gennemført med forskudt dato - ikke som et misset pas.

Returner KUN JSON:
{{
  "helbred": "2-4 sætninger",
  "traening": "2-4 sætninger",
  "fokus": "2-4 sætninger",
  "opmaerksomhed": ["maks 4 korte punkter"]
}}

DATA:
{json.dumps(state, ensure_ascii=False, indent=2)}
"""
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": "Svar kun med valid JSON på dansk. Vær konservativ og datadrevet."},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0.1, "num_predict": 900},
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=180)
        response.raise_for_status()
        return json.loads(response.json().get("message", {}).get("content", ""))
    except Exception:
        return None


def fallback_narrative(state: dict[str, Any]) -> dict[str, Any]:
    recovery = state["recovery"]
    recent = state["training"]["last_7_days"]
    previous = state["training"]["previous_7_days"]
    match = state["plan_match"]
    event = state["event"]

    if recovery["state"] == "insufficient_history":
        health = f"Der er {recovery['history_days']} dages brugbar restitutionshistorik. Det er endnu for lidt til at kalde en sikker personlig trend; de aktuelle Garmin-tal vises derfor som observationer og ikke som diagnose eller trend."
    else:
        label = {"green": "stabil", "yellow": "lidt presset", "red": "presset"}.get(recovery["state"], recovery["state"])
        health = f"Din restitution ser samlet {label} ud i forhold til din egen baseline. Vurderingen bygger på {recovery['baseline_days']} baseline-dage og de seneste dages søvn, HRV, hvilepuls og stress, hvor data findes."

    training = (
        f"De seneste 7 dage har du løbet {recent['km']} km fordelt på {recent['runs']} ture mod {previous['km']} km ugen før. "
        f"Længste tur var {recent['longest_km']} km. Af {match['planned_workouts']} planlagte pas i matchvinduet er {match['matched']} sikkert matchet som gennemført; forskudte pas tæller med."
    )
    focus = " ".join(state.get("focus_points") or [f"Fokus styres af kravene til {event['name']} og din aktuelle restitution."])
    attention = []
    if match.get("uncertain"):
        attention.append(f"{match['uncertain']} planmatch er usikkert og bliver ikke brugt som facit.")
    if match.get("missed"):
        attention.append(f"{match['missed']} planlagte pas har intet sikkert aktivitet-match endnu.")
    return {"helbred": health, "traening": training, "fokus": focus, "opmaerksomhed": attention}


def main() -> int:
    snapshot = load(SNAPSHOT, {})
    calendar = load(CALENDAR, {})
    history = load(HISTORY, {})
    goal = goal_data()

    recent = training_stats(snapshot, 0, 7)
    previous = training_stats(snapshot, 7, 7)
    recovery = recovery_analysis(history, snapshot)
    match = match_recent_plan(snapshot, calendar, days_back=14)
    event = event_summary(goal)
    strength_14 = strength_count(snapshot, 14)
    future = upcoming(calendar, 10)

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
        "focus_points": deterministic_focus(event, recent, previous, strength_14),
        "garmin_writeback_enabled": False,
    }

    narrative = ai_summary(state) or fallback_narrative(state)
    state["narrative"] = narrative

    lines = [
        "=== COACH-RESUMÉ ===",
        "",
        "HELBRED / RESTITUTION",
        narrative.get("helbred", ""),
        "",
        "TRÆNING PÅ SPORET",
        narrative.get("traening", ""),
        "",
        "FOKUS FREMAD",
        narrative.get("fokus", ""),
    ]
    attention = narrative.get("opmaerksomhed") or []
    if attention:
        lines.extend(["", "OPMÆRKSOMHED"])
        for item in attention[:4]:
            lines.append(f"- {item}")

    lines.extend(["", "PLAN VS. GENNEMFØRT"])
    for m in match.get("matches", [])[-8:]:
        shift = int(m.get("date_shift_days") or 0)
        timing = "samme dag" if shift == 0 else f"{abs(shift)} dag{'e' if abs(shift) != 1 else ''} {'senere' if shift > 0 else 'tidligere'}"
        distance = f", {m.get('completed_km')} km" if m.get("completed_km") is not None else ""
        lines.append(f"✓ {m.get('planned_date')} {m.get('planned_title')} -> {m.get('completed_date')} ({timing}){distance}")
    for item in match.get("uncertain_matches", [])[:4]:
        lines.append(f"? {item.get('planned_date')} {item.get('planned_title')} -> usikkert match ({item.get('best_candidate')})")
    for item in match.get("misses", [])[:4]:
        lines.append(f"– {item.get('planned_date')} {item.get('planned_title')} -> intet sikkert match fundet")

    lines.extend(["", "NÆSTE GARMIN-PAS"])
    if future:
        for item in future[:8]:
            lines.append(f"- {item['date']}: {item.get('title') or 'workout'}")
    else:
        lines.append("- Ingen planlagte workouts fundet de næste 10 dage.")

    if event.get("sources"):
        lines.extend(["", "AKTIVT MÅL"])
        lines.append(f"- {event['name']} | {event.get('days_to_event')} dage til løbet | kildeconfidence: {event.get('confidence') or 'legacy/manual'}")

    lines.extend(["", "STATUS", "- Garmin write-back: OFF.", "- Resuméet er træningsvejledning, ikke en medicinsk vurdering."])

    text = "\n".join(lines)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    JSON_OUT.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text)
    print(f"\nGemt lokalt: {OUT}")
    print(f"Coach-state: {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
