r"""Create a validated 7-day coaching proposal without writing to Garmin.

This is the bridge between analysis and later write-back. The language model
may choose coaching actions, but it cannot invent dates or arbitrary workout
structures. Dates come from Python; workouts come from the approved Garmin
library. Existing calendar sessions are the starting point. External plans are
optional evidence, never commands.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import requests

STATE = Path(r"C:\GarminCoach\data\coach_state.json")
CALENDAR = Path(r"C:\GarminCoach\data\scheduled_workouts.json")
TEMPLATES = Path(r"C:\GarminCoach\data\approved_workout_templates.json")
KNOWLEDGE = Path(r"C:\GarminCoach\data\training_knowledge.json")
PROFILE = Path(r"C:\GarminCoach\data\athlete_profile.json")
OUT = Path(r"C:\GarminCoach\data\coach_preview.json")
TEXT_OUT = Path(r"C:\GarminCoach\data\coach_preview.txt")
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"

ALLOWED_ACTIONS = {"KEEP", "MOVE", "ADJUST", "ADD", "REMOVE"}
HARD_FAMILIES = {"quality_interval", "quality_tempo"}
RUN_FAMILIES = {"easy_run", "trail_easy", "quality_interval", "quality_tempo", "long_trail", "back_to_back", "shakeout"}


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def date_of(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except Exception:
        return None


def allowed_dates() -> list[str]:
    today = dt.date.today()
    return [(today + dt.timedelta(days=i)).isoformat() for i in range(1, 8)]


def upcoming(calendar: dict[str, Any], dates: list[str]) -> list[dict[str, Any]]:
    allowed = set(dates)
    rows = []
    for item in calendar.get("items", []):
        d = str(item.get("date") or "")[:10]
        if d not in allowed:
            continue
        rows.append({
            "date": d,
            "title": item.get("title"),
            "workout_id": item.get("workout_id"),
            "scheduled_workout_id": item.get("scheduled_workout_id"),
        })
    return sorted(rows, key=lambda r: (r["date"], str(r.get("title") or "")))


def phase(event: dict[str, Any]) -> str:
    days = event.get("days_to_event")
    try:
        days = int(days)
    except Exception:
        return "unknown"
    if days <= 14:
        return "taper"
    if days <= 35:
        return "race_specific_peak"
    if days <= 70:
        return "specific_build"
    return "base_build"


def compact_knowledge(knowledge: dict[str, Any], event: dict[str, Any]) -> list[dict[str, Any]]:
    etype = str(event.get("event_type") or "").lower()
    course = json.dumps(event.get("course") or {}, ensure_ascii=False).lower()
    relevant_categories = {"weekly_structure", "long_run", "easy_running", "recovery", "cutback", "taper", "fueling", "strength"}
    if "trail" in etype or "trail" in course:
        relevant_categories |= {"specificity", "cross_training"}
    if "marathon" in etype:
        relevant_categories |= {"quality", "specificity"}

    out: list[dict[str, Any]] = []
    for ref_index, ref in enumerate(knowledge.get("references", []) or [], start=1):
        if not isinstance(ref, dict):
            continue
        ref_name = ref.get("reference_name") or ref.get("reference_input") or f"reference {ref_index}"
        for p_index, item in enumerate(ref.get("principles", []) or [], start=1):
            if not isinstance(item, dict) or item.get("confidence") == "low":
                continue
            category = str(item.get("category") or "other")
            if category not in relevant_categories:
                continue
            out.append({
                "tag": f"P{ref_index}.{p_index}",
                "reference": ref_name,
                "category": category,
                "principle": item.get("principle"),
                "evidence": item.get("evidence"),
                "confidence": item.get("confidence"),
            })
    return out[:16]


def template_families(library: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    families = library.get("families") or {}
    return {str(k): v for k, v in families.items() if isinstance(v, list) and v}


def target_mid(item: dict[str, Any]) -> float | None:
    hint = item.get("distance_hint_km") or {}
    try:
        low = float(hint.get("low"))
        high = float(hint.get("high"))
        return (low + high) / 2.0
    except Exception:
        return None


def choose_template(families: dict[str, list[dict[str, Any]]], family: str, target_km: Any = None) -> dict[str, Any] | None:
    rows = families.get(family) or []
    if not rows:
        return None
    if family == "strength_master":
        return rows[0]
    try:
        target = float(target_km)
    except Exception:
        target = None
    if target is None:
        return rows[0]
    ranked = []
    for row in rows:
        mid = target_mid(row)
        ranked.append((abs(mid - target) if mid is not None else 9999.0, row))
    ranked.sort(key=lambda x: x[0])
    return ranked[0][1]


def build_context(state: dict[str, Any], calendar: dict[str, Any], library: dict[str, Any], knowledge: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    dates = allowed_dates()
    event = state.get("event") or {}
    families = template_families(library)
    return {
        "allowed_dates": dates,
        "phase": phase(event),
        "recovery": state.get("recovery") or {},
        "training": state.get("training") or {},
        "plan_match": state.get("plan_match") or {},
        "event": event,
        "event_focus_points": state.get("focus_points") or [],
        "existing_calendar": upcoming(calendar, dates),
        "available_workout_families": {
            family: [
                {
                    "workout_id": row.get("workout_id"),
                    "title": row.get("title"),
                    "distance_hint_km": row.get("distance_hint_km"),
                    "step_count": row.get("step_count"),
                }
                for row in rows
            ]
            for family, rows in families.items()
        },
        "external_plan_principles": compact_knowledge(knowledge, event),
        "athlete_preferences": profile or {},
        "rules": {
            "existing_calendar_is_starting_point": True,
            "do_not_make_up_missed_sessions_automatically": True,
            "dates_must_come_from_allowed_dates": True,
            "new_or_adjusted_workouts_must_use_approved_family": True,
            "strength_must_use_exact_strength_master": True,
            "external_plan_is_inspiration_only": True,
            "event_and_personal_data_override_external_plan": True,
            "avoid_consecutive_hard_days": True,
            "back_to_back_second_day_must_be_easy": True,
        },
    }


def prompt(context: dict[str, Any]) -> str:
    return f"""Du er personlig løbetræner. Lav en ændringsplan for de NÆSTE 7 DAGE.
Du må kun arbejde inden for konteksten nedenfor.

VIGTIG PRIORITET:
1. Faktisk restitution og gennemført træning.
2. Det konkrete løb og dets dokumenterede krav.
3. Eksisterende Garmin-kalender og godkendte Garmin-workouts.
4. Eksterne træningsprogrammer er kun inspiration. Brug kun relevante principper.

TRÆNERADFÆRD
- Behold en eksisterende god plan, hvis der ikke er en god grund til at ændre den.
- Et pas flyttet en dag og allerede gennemført er ikke et problem, der skal indhentes.
- Undgå catch-up stacking: missede pas skal normalt ikke presses ind senere.
- Ved dårlig restitution: reducer først intensitet/volumen, behold kontinuitet hvis rimeligt.
- Ved god restitution: progression skal stadig være gradvis.
- Styrke må KUN bruge strength_master.
- Ingen frie workout-opfindelser. Vælg en family, Python vælger det konkrete Garmin-workout.
- Alt brugervendt tekst på dansk.

Returner KUN valid JSON:
{{
  "week_assessment": "kort trænerfaglig vurdering",
  "actions": [
    {{
      "action": "KEEP|MOVE|ADJUST|ADD|REMOVE",
      "source_date": "YYYY-MM-DD eller null",
      "source_workout_id": "eksisterende workout-id eller null",
      "date": "en dato fra allowed_dates",
      "family": "approved family eller null",
      "target_km": null,
      "intensity": "easy|moderate|hard|strength|rest",
      "reason": "kort dansk forklaring",
      "evidence_tags": ["P1.1"]
    }}
  ],
  "focus_next_14_days": ["maks 5 korte punkter"],
  "coach_note": "kort dansk note"
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
            {"role": "system", "content": "Svar kun valid JSON på dansk. Prioritér sikker, konservativ træningsplanlægning."},
            {"role": "user", "content": prompt(context)},
        ],
        "options": {"temperature": 0.1, "num_predict": 1800},
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=240)
    response.raise_for_status()
    return json.loads(response.json().get("message", {}).get("content", ""))


def deterministic_fallback(context: dict[str, Any]) -> dict[str, Any]:
    actions = []
    for item in context["existing_calendar"]:
        actions.append({
            "action": "KEEP",
            "source_date": item["date"],
            "source_workout_id": item.get("workout_id"),
            "date": item["date"],
            "family": None,
            "target_km": None,
            "intensity": "moderate",
            "reason": "Eksisterende Garmin-plan beholdes, fordi fallback ikke har grundlag for en sikker ændring.",
            "evidence_tags": [],
        })
    return {
        "week_assessment": "Den lokale model kunne ikke levere en validerbar ændringsplan. Eksisterende Garmin-plan beholdes uændret.",
        "actions": actions,
        "focus_next_14_days": context.get("event_focus_points") or [],
        "coach_note": "Konservativ fallback: ingen nye pas er tilføjet.",
    }


def validate(raw: dict[str, Any], context: dict[str, Any], library: dict[str, Any]) -> dict[str, Any]:
    dates = set(context["allowed_dates"])
    existing = context["existing_calendar"]
    by_key = {(str(i.get("workout_id") or ""), i["date"]): i for i in existing}
    by_workout = {str(i.get("workout_id") or ""): i for i in existing if i.get("workout_id") is not None}
    families = template_families(library)
    valid_tags = {p["tag"] for p in context.get("external_plan_principles", [])}
    recovery = str((context.get("recovery") or {}).get("state") or "")

    validated: list[dict[str, Any]] = []
    touched_existing: set[tuple[str, str]] = set()

    for action in raw.get("actions", []) if isinstance(raw.get("actions"), list) else []:
        if not isinstance(action, dict):
            continue
        name = str(action.get("action") or "").upper()
        if name not in ALLOWED_ACTIONS:
            continue
        date = str(action.get("date") or "")[:10]
        if date not in dates:
            continue

        source_date = str(action.get("source_date") or "")[:10] if action.get("source_date") else None
        source_workout_id = str(action.get("source_workout_id") or "") if action.get("source_workout_id") is not None else None
        source = None
        if source_workout_id:
            source = by_workout.get(source_workout_id)
        if source is None and source_date:
            for item in existing:
                if item["date"] == source_date:
                    source = item
                    break

        if name in {"KEEP", "MOVE", "ADJUST", "REMOVE"} and source is None:
            continue
        if source is not None:
            key = (str(source.get("workout_id") or ""), source["date"])
            if key in touched_existing:
                continue
            touched_existing.add(key)

        family = str(action.get("family") or "") or None
        if name in {"ADD", "ADJUST"}:
            if family not in families:
                continue
            if family == "strength_master":
                action["intensity"] = "strength"
            if recovery == "red" and family in HARD_FAMILIES:
                continue

        # A two-day move is possible, but further date movement is not accepted here.
        if name == "MOVE" and source is not None:
            sd = date_of(source["date"])
            nd = date_of(date)
            if not sd or not nd or abs((nd - sd).days) > 2:
                continue

        tags = [str(t) for t in action.get("evidence_tags", []) if str(t) in valid_tags]
        validated.append({
            "action": name,
            "source_date": source.get("date") if source else None,
            "source_title": source.get("title") if source else None,
            "source_workout_id": source.get("workout_id") if source else None,
            "date": date,
            "family": family,
            "target_km": action.get("target_km"),
            "intensity": str(action.get("intensity") or "moderate").lower(),
            "reason": str(action.get("reason") or "Ingen begrundelse angivet.")[:500],
            "evidence_tags": tags,
        })

    # Any existing workout the model forgot is KEEP, not silently deleted.
    for item in existing:
        key = (str(item.get("workout_id") or ""), item["date"])
        if key not in touched_existing:
            validated.append({
                "action": "KEEP",
                "source_date": item["date"],
                "source_title": item.get("title"),
                "source_workout_id": item.get("workout_id"),
                "date": item["date"],
                "family": None,
                "target_km": None,
                "intensity": "moderate",
                "reason": "Eksisterende plan beholdes; modellen gav ingen validerbar grund til at ændre den.",
                "evidence_tags": [],
            })

    # Enforce no consecutive hard days by keeping only the earlier validated hard change.
    hard_dates: set[str] = set()
    final_actions: list[dict[str, Any]] = []
    for action in sorted(validated, key=lambda a: (a["date"], a["action"] != "KEEP")):
        family = action.get("family")
        is_hard = family in HARD_FAMILIES or action.get("intensity") == "hard"
        d = date_of(action["date"])
        if is_hard and d:
            if any(abs((d - date_of(existing_date)).days) == 1 for existing_date in hard_dates if date_of(existing_date)):
                if action["action"] in {"ADD", "ADJUST"}:
                    continue
            hard_dates.add(action["date"])
        final_actions.append(action)

    # Bind every ADD/ADJUST to a real approved Garmin workout template.
    for action in final_actions:
        if action["action"] not in {"ADD", "ADJUST"} or not action.get("family"):
            continue
        chosen = choose_template(families, action["family"], action.get("target_km"))
        if chosen:
            action["selected_template"] = {
                "workout_id": chosen.get("workout_id"),
                "title": chosen.get("title"),
                "step_count": chosen.get("step_count"),
                "distance_hint_km": chosen.get("distance_hint_km"),
            }

    result = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "preview_only": True,
        "garmin_writeback": False,
        "phase": context.get("phase"),
        "week_assessment": str(raw.get("week_assessment") or "")[:1200],
        "actions": final_actions,
        "focus_next_14_days": [str(x)[:400] for x in (raw.get("focus_next_14_days") or [])[:5]],
        "coach_note": str(raw.get("coach_note") or "")[:800],
        "external_principles_available": context.get("external_plan_principles", []),
    }
    return result


def render(plan: dict[str, Any]) -> str:
    lines = [
        "=== 7-DAGES COACH-FORSLAG ===",
        "",
        f"Fase: {plan.get('phase')}",
        f"Vurdering: {plan.get('week_assessment')}",
        "",
        "GARMIN-KALENDER",
    ]
    labels = {"KEEP": "BEHOLD", "MOVE": "FLYT", "ADJUST": "JUSTÉR", "ADD": "TILFØJ", "REMOVE": "FJERN"}
    for action in plan.get("actions", []):
        label = labels.get(action["action"], action["action"])
        source = action.get("source_title") or ""
        chosen = action.get("selected_template") or {}
        title = chosen.get("title") or source or action.get("family") or "pas"
        suffix = ""
        if action["action"] == "MOVE" and action.get("source_date"):
            suffix = f" (fra {action['source_date']})"
        lines.append(f"- {action['date']}: {label} – {title}{suffix}")
        lines.append(f"    Hvorfor: {action.get('reason')}")
        if action.get("evidence_tags"):
            lines.append(f"    Inspirationskilder: {', '.join(action['evidence_tags'])}")

    if plan.get("focus_next_14_days"):
        lines.extend(["", "FOKUS NÆSTE 14 DAGE"])
        for item in plan["focus_next_14_days"]:
            lines.append(f"- {item}")

    lines.extend(["", "STATUS", "- Alle nye/justerede pas er bundet til godkendte Garmin-skabeloner.", "- Garmin write-back: OFF."])
    return "\n".join(lines)


def main() -> int:
    state = load(STATE, {})
    calendar = load(CALENDAR, {})
    library = load(TEMPLATES, {})
    knowledge = load(KNOWLEDGE, {})
    profile = load(PROFILE, {})
    if not state:
        print("ERROR: Mangler coach_state.json. Kør coach_brief.py først.")
        return 2
    if not template_families(library):
        print("ERROR: Mangler godkendt workout-bibliotek. Kør workout_style_probe.py og build_template_library.py først.")
        return 3

    context = build_context(state, calendar, library, knowledge, profile)
    try:
        raw = call_model(context)
        source = "ollama"
    except Exception as exc:
        print(f"WARNING: Lokal model kunne ikke lave plan: {exc}")
        raw = deterministic_fallback(context)
        source = "fallback"

    plan = validate(raw, context, library)
    plan["source"] = source
    text = render(plan)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    TEXT_OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nJSON: {OUT}")
    print(f"Tekst: {TEXT_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
