"""Generate one exact ISO training week in preview/shadow mode only.

This is the deeper coach path used for benchmark-style weekly planning. It reads the
latest Garmin snapshot, multi-week activity history, personal recovery history,
approved workout library, athlete profile and evidence principles. It never writes
to Garmin. A larger local model proposes the week; deterministic Python validation
owns dates, safety and Garmin-template binding afterwards.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import requests

import coach_preview as base
import coach_preview_v2  # noqa: F401 - installs safer build_context/validate patches
import model_manager

ROOT = Path(r"C:\GarminCoach")
DATA = ROOT / "data"
SNAPSHOT = DATA / "snapshot.json"
HEALTH_HISTORY = DATA / "health_history.json"
OLLAMA_URL = base.OLLAMA_URL


def iso_week_dates(year: int, week: int) -> list[str]:
    monday = dt.date.fromisocalendar(year, week, 1)
    return [(monday + dt.timedelta(days=i)).isoformat() for i in range(7)]


def parse_week(message: str, today: dt.date | None = None) -> tuple[int, int]:
    today = today or dt.date.today()
    match = re.search(r"\buge\s*(\d{1,2})\b", message.casefold())
    if not match:
        raise RuntimeError("Jeg mangler ugenummeret. Skriv fx 'hvordan skal uge 38 se ud?'.")
    week = int(match.group(1))
    if not 1 <= week <= 53:
        raise RuntimeError("Ugenummeret skal være mellem 1 og 53.")
    year_match = re.search(r"\b(20\d{2})\b", message)
    year = int(year_match.group(1)) if year_match else today.year
    dt.date.fromisocalendar(year, week, 1)
    return year, week


def as_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except Exception:
        return None


def load_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    state = base.load(base.STATE, {})
    calendar = base.load(base.CALENDAR, {})
    library = base.load(base.TEMPLATES, {})
    knowledge = base.load(base.KNOWLEDGE, {})
    profile = base.load(base.PROFILE, {})
    if not state:
        raise RuntimeError("Jeg mangler validerede coach/Garmin-data. Kør 'Opdatér coach' først.")
    if not base.template_families(library):
        raise RuntimeError("Jeg mangler det godkendte Garmin-workoutbibliotek.")
    return state, calendar, library, knowledge, profile


def recent_activity_detail(snapshot: dict[str, Any], days: int = 28) -> list[dict[str, Any]]:
    today = dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    rows: list[dict[str, Any]] = []
    for activity in snapshot.get("all_activities", []) if isinstance(snapshot, dict) else []:
        if not isinstance(activity, dict):
            continue
        day = as_date(activity.get("start"))
        if not day or day < start or day > today:
            continue
        distance_m = activity.get("distance_m")
        duration_s = activity.get("duration_s")
        row = {
            "date": day.isoformat(),
            "name": activity.get("name"),
            "type": activity.get("type"),
            "subtype": activity.get("subtype"),
            "km": round(float(distance_m) / 1000.0, 2) if distance_m is not None else None,
            "minutes": round(float(duration_s) / 60.0, 1) if duration_s is not None else None,
            "avg_hr": activity.get("avg_hr"),
            "max_hr": activity.get("max_hr"),
            "elevation_gain_m": activity.get("elevation_gain_m"),
            "training_load": activity.get("training_load"),
            "aerobic_te": activity.get("aerobic_te"),
            "anaerobic_te": activity.get("anaerobic_te"),
        }
        rows.append({key: value for key, value in row.items() if value is not None})
    return sorted(rows, key=lambda item: item["date"])[-45:]


def rolling_training_weeks(snapshot: dict[str, Any], weeks: int = 4) -> list[dict[str, Any]]:
    today = dt.date.today()
    activities = [a for a in snapshot.get("running_activities", []) if isinstance(a, dict)] if isinstance(snapshot, dict) else []
    output: list[dict[str, Any]] = []
    for index in range(weeks):
        end = today - dt.timedelta(days=index * 7)
        start = end - dt.timedelta(days=6)
        selected = [a for a in activities if as_date(a.get("start")) and start <= as_date(a.get("start")) <= end]
        distances = [float(a.get("distance_m") or 0) / 1000.0 for a in selected]
        minutes = [float(a.get("duration_s") or 0) / 60.0 for a in selected]
        elevation = [float(a.get("elevation_gain_m") or 0) for a in selected]
        loads = [float(a.get("training_load")) for a in selected if a.get("training_load") is not None]
        output.append({
            "window": index,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "runs": len(selected),
            "km": round(sum(distances), 1),
            "minutes": round(sum(minutes)),
            "longest_km": round(max(distances), 1) if distances else 0.0,
            "elevation_m": round(sum(elevation)),
            "training_load": round(sum(loads), 1) if loads else None,
        })
    return output


def recent_health_detail(history: dict[str, Any], days: int = 14) -> list[dict[str, Any]]:
    rows = [row for row in history.get("days", []) if isinstance(row, dict) and row.get("date")] if isinstance(history, dict) else []
    rows.sort(key=lambda row: str(row.get("date")))
    fields = (
        "date", "sleep_hours", "sleep_score", "sleep_quality", "resting_hr", "avg_stress",
        "hrv_last_night", "hrv_weekly_avg", "hrv_status", "body_battery_high", "body_battery_low",
        "body_battery_charged", "body_battery_drained",
    )
    return [{key: row.get(key) for key in fields if row.get(key) is not None} for row in rows[-days:]]


def build_week_context(year: int, week: int, user_request: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    state, calendar, library, knowledge, profile = load_inputs()
    dates = iso_week_dates(year, week)
    context = base.build_context(state, calendar, library, knowledge, profile)
    snapshot = base.load(SNAPSHOT, {})
    health_history = base.load(HEALTH_HISTORY, {})

    context["allowed_dates"] = dates
    context["existing_calendar"] = base.upcoming(calendar, dates)
    context["planning_mode"] = "shadow_week"
    context["planning_iso_year"] = year
    context["planning_iso_week"] = week
    context["planning_window"] = {"from": dates[0], "to": dates[-1]}
    context["shadow_user_request"] = str(user_request or "")[:1400]
    context["training_history_4x7d"] = rolling_training_weeks(snapshot, 4)
    context["recent_activity_detail_28d"] = recent_activity_detail(snapshot, 28)
    context["recent_health_detail_14d"] = recent_health_detail(health_history, 14)
    context["health_history_window"] = health_history.get("window") if isinstance(health_history, dict) else None
    context.setdefault("rules", {})["shadow_mode_no_garmin_write"] = True
    context["rules"]["plan_the_whole_allowed_week_not_a_rolling_window"] = True
    context["rules"]["explicit_user_request_is_a_constraint_if_safe"] = True
    context["rules"]["use_multiweek_training_history_not_only_last_7_days"] = True
    context["rules"]["use_health_trend_against_personal_baseline"] = True
    return context, library


def prompt(context: dict[str, Any]) -> str:
    year = context["planning_iso_year"]
    week = context["planning_iso_week"]
    return f"""Du er en erfaren personlig løbetræner. Lav en komplet SHADOW-PLAN for ISO uge {week}, {year}.
Planen er kun et forslag og må IKKE skrive til Garmin.

Det her er en benchmark-opgave: planen skal kunne forsvares over for en dygtig personlig træner.
Start med at vurdere belastnings- og restitutionstrenden, og planlæg derefter ugen som en samlet mikrocyklus.

PRIORITET:
1. Faktisk Garmin-træning over de seneste uger, inklusive distance, varighed, højdemeter, load og Training Effect hvor det findes.
2. Helbred/restitution som trend mod atletens egen baseline: søvn, HRV, hvilepuls, stress og Body Battery hvor data findes.
3. Det primære løbsmål og de dokumenterede krav til løbet.
4. Sikker progression fra den seneste faktiske belastning.
5. De empiriske/evidensbaserede principper i external_plan_principles.
6. Eksisterende kalender og brugerens praktiske rammer.
7. Garmin-udfordringer er sekundære og må kun flettes ind, når de passer til hovedmålet.

KRAV:
- Planlæg kun på datoerne i allowed_dates; de udgør hele uge {week}.
- Brug training_history_4x7d og recent_activity_detail_28d. Basér ikke planen kun på sidste uge.
- Brug recent_health_detail_14d sammen med den sammenfattede recovery-vurdering. Ét enkelt døgn må ikke alene styre hele ugen.
- Tænk i samlet ugentlig belastning, nøglepas, restitution mellem nøglepas og event-specificitet.
- Behandl shadow_user_request som brugerens aktuelle ønske/ramme, hvis det er træningsmæssigt forsvarligt.
- Forklar hvert pas med fysiologisk formål og relation til hovedmålet.
- Hviledage er aktive valg; der skal ikke være træning alle syv dage.
- Brug kun godkendte workout-families; Python binder senere til konkrete Garmin-workouts.
- Styrke må kun bruge strength_master.
- Undgå to hårde dage i træk og aggressiv catch-up.
- Ved utilstrækkelige data: vælg konservativt og sig præcis hvad der mangler.
- Brug evidence_tags kun når et konkret princip faktisk understøtter valget.
- Alt brugervendt tekst på dansk.

Returner KUN valid JSON:
{{
  "week_assessment": "samlet vurdering af trænings- og helbredstrenden og hvad den betyder for uge {week}",
  "actions": [
    {{
      "action": "KEEP|MOVE|ADJUST|ADD|REMOVE",
      "source_date": "YYYY-MM-DD eller null",
      "source_workout_id": "eksisterende workout-id eller null",
      "date": "en dato fra allowed_dates",
      "family": "approved family eller null",
      "target_km": null,
      "intensity": "easy|moderate|hard|strength|rest",
      "reason": "hvad passet skal udvikle, hvorfor det ligger her, og hvordan det passer til belastningen",
      "evidence_tags": ["P1.1"]
    }}
  ],
  "focus_next_14_days": ["maks 5 korte punkter"],
  "coach_note": "kort samlet note"
}}

KONTEKST:
{json.dumps(context, ensure_ascii=False, indent=2)}
"""


def call_model(context: dict[str, Any]) -> dict[str, Any]:
    ready, info = model_manager.coach_model_ready()
    if not ready:
        raise RuntimeError(info)
    payload = {
        "model": model_manager.COACH_MODEL,
        "stream": False,
        "think": False,
        "format": "json",
        "keep_alive": "45m",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Du er en evidensbaseret personlig løbetræner. Svar kun med valid JSON. "
                    "Personlige Garmin-data, multi-uge trends og sikker progression går foran generiske planer."
                ),
            },
            {"role": "user", "content": prompt(context)},
        ],
        "options": {"temperature": 0.08, "num_predict": 1300, "num_ctx": 12288},
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=60 * 8)
    response.raise_for_status()
    return json.loads(response.json().get("message", {}).get("content", ""))


def generate(year: int, week: int, user_request: str | None = None) -> dict[str, Any]:
    context, library = build_week_context(year, week, user_request)
    raw = call_model(context)
    plan = base.validate(raw, context, library)
    plan["source"] = model_manager.COACH_MODEL
    plan["preview_only"] = True
    plan["garmin_writeback"] = False
    plan["planning_iso_year"] = year
    plan["planning_iso_week"] = week
    plan["planning_window"] = context["planning_window"]
    plan["shadow_user_request"] = context.get("shadow_user_request")
    plan["benchmark_context"] = {
        "training_windows": context.get("training_history_4x7d"),
        "health_days": len(context.get("recent_health_detail_14d") or []),
        "activity_details": len(context.get("recent_activity_detail_28d") or []),
        "model": model_manager.COACH_MODEL,
    }
    out = DATA / f"shadow_week_{year}_W{week:02d}.json"
    txt = DATA / f"shadow_week_{year}_W{week:02d}.txt"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    txt.write_text(render_chat(plan), encoding="utf-8")
    plan["output_json"] = str(out)
    plan["output_text"] = str(txt)
    return plan


def render_chat(plan: dict[str, Any]) -> str:
    week = int(plan.get("planning_iso_week"))
    year = int(plan.get("planning_iso_year"))
    window = plan.get("planning_window") or {}
    lines = [
        f"SHADOW-PLAN – UGE {week} ({window.get('from')} til {window.get('to')})",
        f"Coach-model: {plan.get('source')}",
        f"Vurdering: {plan.get('week_assessment') or 'Ingen samlet vurdering.'}",
        "",
    ]
    weekdays = ["Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn"]
    by_date: dict[str, list[dict[str, Any]]] = {}
    for action in plan.get("actions", []) or []:
        by_date.setdefault(str(action.get("date") or ""), []).append(action)
    for idx, day in enumerate(iso_week_dates(year, week)):
        rows = by_date.get(day, [])
        if not rows:
            lines.append(f"{weekdays[idx]} {day}: Hvile / ingen valideret træning")
            continue
        for row in rows:
            chosen = row.get("selected_template") or {}
            name = row.get("plan_name") or chosen.get("title") or row.get("source_title") or row.get("family") or "Træning"
            focus = row.get("focus") or ""
            reason = row.get("reason") or ""
            lines.append(f"{weekdays[idx]} {day}: {name}" + (f" – {focus}" if focus else ""))
            if reason:
                lines.append(f"  Hvorfor: {reason}")
    if plan.get("focus_next_14_days"):
        lines.extend(["", "Fokus efter ugeplanen:"])
        lines.extend(f"• {item}" for item in plan.get("focus_next_14_days", [])[:5])
    lines.extend(["", "Garmin write-back: FRA. Dette er kun shadow mode."])
    return "\n".join(lines)


def handle(message: str) -> str:
    year, week = parse_week(message)
    plan = generate(year, week, message)
    return render_chat(plan)


if __name__ == "__main__":
    import sys
    msg = " ".join(sys.argv[1:]) or f"hvordan skal uge {dt.date.today().isocalendar().week} se ud?"
    print(handle(msg))
