"""Generate one exact ISO training week in preview/shadow mode only.

The planner uses the latest validated local Garmin state, approved workout library,
athlete profile and evidence principles already stored by the coach. It never writes
to Garmin. The language model proposes a week; coach_preview_v2 performs deterministic
safety/template validation afterwards.
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

ROOT = Path(r"C:\GarminCoach")
DATA = ROOT / "data"
OLLAMA_URL = base.OLLAMA_URL
MODEL = base.MODEL


def iso_week_dates(year: int, week: int) -> list[str]:
    monday = dt.date.fromisocalendar(year, week, 1)
    return [(monday + dt.timedelta(days=i)).isoformat() for i in range(7)]


def parse_week(message: str, today: dt.date | None = None) -> tuple[int, int]:
    today = today or dt.date.today()
    m = re.search(r"\buge\s*(\d{1,2})\b", message.casefold())
    if not m:
        raise RuntimeError("Jeg mangler ugenummeret. Skriv fx 'lav uge 38 i shadow mode'.")
    week = int(m.group(1))
    if not 1 <= week <= 53:
        raise RuntimeError("Ugenummeret skal være mellem 1 og 53.")
    ym = re.search(r"\b(20\d{2})\b", message)
    year = int(ym.group(1)) if ym else today.year
    # Validate ISO combination.
    dt.date.fromisocalendar(year, week, 1)
    return year, week


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


def build_week_context(year: int, week: int) -> tuple[dict[str, Any], dict[str, Any]]:
    state, calendar, library, knowledge, profile = load_inputs()
    dates = iso_week_dates(year, week)
    context = base.build_context(state, calendar, library, knowledge, profile)
    context["allowed_dates"] = dates
    context["existing_calendar"] = base.upcoming(calendar, dates)
    context["planning_mode"] = "shadow_week"
    context["planning_iso_year"] = year
    context["planning_iso_week"] = week
    context["planning_window"] = {"from": dates[0], "to": dates[-1]}
    context.setdefault("rules", {})["shadow_mode_no_garmin_write"] = True
    context["rules"]["plan_the_whole_allowed_week_not_a_rolling_window"] = True
    return context, library


def prompt(context: dict[str, Any]) -> str:
    year = context["planning_iso_year"]
    week = context["planning_iso_week"]
    return f"""Du er en erfaren personlig løbetræner. Lav en komplet SHADOW-PLAN for ISO uge {week}, {year}.
Planen er kun et forslag og må IKKE skrive til Garmin.

BRUG DENNE PRIORITET:
1. Faktisk Garmin-træning og restitution/historiske responser.
2. Det primære løbsmål og de dokumenterede krav til løbet.
3. Sikker progression fra den seneste faktiske belastning.
4. Evidens-/empiriprincipperne i external_plan_principles.
5. Eksisterende kalender og brugerens praktiske rammer.
6. Garmin-udfordringer er kun sekundære mål.

KRAV:
- Planlæg kun på datoerne i allowed_dates. De udgør hele uge {week}.
- Tænk i ugens samlede belastning, ikke syv uafhængige pas.
- Forklar formålet med hvert træningspas fysiologisk og i relation til hovedmålet.
- Brug hvile/restitution når det er den bedste løsning; der skal ikke nødvendigvis være træning alle syv dage.
- Brug kun godkendte workout-families. Python binder senere til konkrete Garmin-workouts.
- Styrke må kun bruge strength_master.
- Undgå to hårde dage i træk og aggressiv catch-up.
- Hvis personlige data er utilstrækkelige, vælg konservativt og sig det i week_assessment.
- Brug evidence_tags når et konkret empirical principle faktisk understøtter valget.
- Alt brugervendt tekst på dansk.

Returner KUN valid JSON:
{{
  "week_assessment": "samlet trænerfaglig vurdering af uge 38 ud fra data og empiri",
  "actions": [
    {{
      "action": "KEEP|MOVE|ADJUST|ADD|REMOVE",
      "source_date": "YYYY-MM-DD eller null",
      "source_workout_id": "eksisterende workout-id eller null",
      "date": "en dato fra allowed_dates",
      "family": "approved family eller null",
      "target_km": null,
      "intensity": "easy|moderate|hard|strength|rest",
      "reason": "hvad passet skal udvikle og hvorfor det ligger her",
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
    payload = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Du er en evidensbaseret løbetræner. Lav kun valid JSON. "
                    "Personlige Garmin-data og sikker progression går foran generiske planer."
                ),
            },
            {"role": "user", "content": prompt(context)},
        ],
        "options": {"temperature": 0.08, "num_predict": 1200, "num_ctx": 8192},
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=180)
    response.raise_for_status()
    return json.loads(response.json().get("message", {}).get("content", ""))


def generate(year: int, week: int) -> dict[str, Any]:
    context, library = build_week_context(year, week)
    try:
        raw = call_model(context)
        source = "ollama"
    except Exception as exc:
        raw = base.deterministic_fallback(context)
        source = f"fallback: {exc}"
    plan = base.validate(raw, context, library)
    plan["source"] = source
    plan["preview_only"] = True
    plan["garmin_writeback"] = False
    plan["planning_iso_year"] = year
    plan["planning_iso_week"] = week
    plan["planning_window"] = context["planning_window"]
    out = DATA / f"shadow_week_{year}_W{week:02d}.json"
    txt = DATA / f"shadow_week_{year}_W{week:02d}.txt"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    txt.write_text(render_chat(plan), encoding="utf-8")
    plan["output_json"] = str(out)
    plan["output_text"] = str(txt)
    return plan


def render_chat(plan: dict[str, Any]) -> str:
    week = plan.get("planning_iso_week")
    window = plan.get("planning_window") or {}
    lines = [
        f"SHADOW-PLAN – UGE {week} ({window.get('from')} til {window.get('to')})",
        f"Vurdering: {plan.get('week_assessment') or 'Ingen samlet vurdering.'}",
        "",
    ]
    weekdays = ["Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn"]
    by_date: dict[str, list[dict[str, Any]]] = {}
    for action in plan.get("actions", []) or []:
        by_date.setdefault(str(action.get("date") or ""), []).append(action)
    dates = iso_week_dates(int(plan.get("planning_iso_year")), int(week))
    for idx, day in enumerate(dates):
        rows = by_date.get(day, [])
        if not rows:
            lines.append(f"{weekdays[idx]} {day}: Hvile / ingen valideret ændring")
            continue
        for row in rows:
            chosen = row.get("selected_template") or {}
            name = row.get("plan_name") or chosen.get("title") or row.get("source_title") or row.get("family") or "Træning"
            focus = row.get("focus") or ""
            reason = row.get("reason") or ""
            lines.append(f"{weekdays[idx]} {day}: {name}" + (f" – {focus}" if focus else ""))
            if reason:
                lines.append(f"  Hvorfor: {reason}")
    lines.extend(["", "Garmin write-back: FRA. Dette er kun shadow mode."])
    return "\n".join(lines)


def handle(message: str) -> str:
    year, week = parse_week(message)
    plan = generate(year, week)
    return render_chat(plan)


if __name__ == "__main__":
    import sys
    msg = " ".join(sys.argv[1:]) or f"lav uge {dt.date.today().isocalendar().week} i shadow mode"
    print(handle(msg))
