"""Expert weekly coach built around the exact-week shadow planner.

This layer is intentionally benchmark-oriented. It enriches the base shadow context
with athlete-specific workout -> next-day recovery examples, asks the larger local
coach model for concrete session specifications, validates the result, scores coaching
quality, and performs one repair round when the first proposal has obvious gaps.
Nothing in this module writes to Garmin.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import requests

import coach_preview as preview
import coach_preview_v2  # noqa: F401 - deterministic validation patches
import model_manager
import shadow_week as base_week

OLLAMA_URL = preview.OLLAMA_URL
ALLOWED_GOALS = {
    "vo2max", "threshold", "endurance", "trail_specificity", "recovery",
    "strength", "marathon_pace", "general",
}


def as_date(value: Any) -> dt.date | None:
    return base_week.as_date(value)


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def personal_response_examples(snapshot: dict[str, Any], history: dict[str, Any], max_rows: int = 12) -> list[dict[str, Any]]:
    """Pair recent activities with next-day recovery observations.

    This is descriptive athlete-specific evidence, not a claim of causality. It gives
    the coach concrete examples of how larger/quality sessions have recently been
    followed by sleep/HRV/RHR/stress signals.
    """
    health_by_date = {
        str(row.get("date"))[:10]: row
        for row in history.get("days", []) if isinstance(row, dict) and row.get("date")
    } if isinstance(history, dict) else {}

    rows: list[dict[str, Any]] = []
    activities = snapshot.get("all_activities", []) if isinstance(snapshot, dict) else []
    for activity in activities:
        if not isinstance(activity, dict):
            continue
        day = as_date(activity.get("start"))
        if not day:
            continue
        next_day = health_by_date.get((day + dt.timedelta(days=1)).isoformat())
        if not isinstance(next_day, dict):
            continue
        distance = _float(activity.get("distance_m"))
        duration = _float(activity.get("duration_s"))
        row = {
            "training_date": day.isoformat(),
            "name": activity.get("name"),
            "type": activity.get("type"),
            "subtype": activity.get("subtype"),
            "km": round(distance / 1000.0, 2) if distance is not None else None,
            "minutes": round(duration / 60.0, 1) if duration is not None else None,
            "training_load": activity.get("training_load"),
            "aerobic_te": activity.get("aerobic_te"),
            "anaerobic_te": activity.get("anaerobic_te"),
            "next_day": {
                key: next_day.get(key)
                for key in (
                    "sleep_hours", "sleep_score", "hrv_last_night", "hrv_weekly_avg",
                    "resting_hr", "avg_stress", "body_battery_high", "body_battery_low",
                )
                if next_day.get(key) is not None
            },
        }
        row = {key: value for key, value in row.items() if value not in (None, {})}
        rows.append(row)
    rows.sort(key=lambda item: item.get("training_date", ""))
    return rows[-max_rows:]


def sanitize_session_spec(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    goal = str(value.get("goal") or "").strip()
    if goal in ALLOWED_GOALS:
        out["goal"] = goal

    bounds = {
        "total_minutes": (15.0, 300.0),
        "target_km": (1.0, 60.0),
        "warmup_min": (0.0, 40.0),
        "repetitions": (1.0, 30.0),
        "work_min": (0.25, 120.0),
        "recovery_min": (0.0, 30.0),
        "cooldown_min": (0.0, 40.0),
    }
    for key, (low, high) in bounds.items():
        number = _float(value.get(key))
        if number is None or not low <= number <= high:
            continue
        out[key] = int(number) if key == "repetitions" else round(number, 2)

    for key, limit in (("effort", 140), ("terrain", 140), ("execution_note", 260)):
        text = str(value.get(key) or "").strip()
        if text:
            out[key] = text[:limit]
    return out


def attach_session_specs(plan: dict[str, Any], raw: dict[str, Any]) -> None:
    """Attach only sanitized specifics to actions that survived deterministic validation."""
    proposals = raw.get("actions", []) if isinstance(raw, dict) and isinstance(raw.get("actions"), list) else []
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        key = (str(proposal.get("date") or "")[:10], str(proposal.get("family") or ""))
        by_key.setdefault(key, []).append(proposal)

    for action in plan.get("actions", []) if isinstance(plan.get("actions"), list) else []:
        if not isinstance(action, dict):
            continue
        key = (str(action.get("date") or "")[:10], str(action.get("family") or ""))
        candidates = by_key.get(key) or []
        if not candidates:
            continue
        proposal = candidates.pop(0)
        spec = sanitize_session_spec(proposal.get("session_spec"))
        if spec:
            action["session_spec"] = spec


def action_family(action: dict[str, Any]) -> str:
    family = action.get("family")
    if family:
        return str(family)
    title = str(action.get("source_title") or "").casefold()
    if "trail" in title and ("lang" in title or "long" in title):
        return "long_trail"
    if "interval" in title or "vo2" in title:
        return "quality_interval"
    if "tempo" in title or "threshold" in title or "tærsk" in title:
        return "quality_tempo"
    if "styr" in title or "strength" in title:
        return "strength_master"
    if "trail" in title:
        return "trail_easy"
    if "løb" in title or "run" in title:
        return "easy_run"
    return "unknown"


def quality_issues(plan: dict[str, Any], context: dict[str, Any]) -> list[str]:
    """Coach-quality checks beyond hard safety validation.

    These are review signals, not medical rules. One model repair round is allowed;
    the deterministic validator remains the final safety authority.
    """
    actions = [a for a in plan.get("actions", []) if isinstance(a, dict)] if isinstance(plan, dict) else []
    training_actions = [a for a in actions if a.get("action") != "REMOVE"]
    run_families = {
        "easy_run", "trail_easy", "quality_interval", "quality_tempo",
        "long_trail", "back_to_back", "shakeout",
    }
    runs = [a for a in training_actions if action_family(a) in run_families]
    hard = [a for a in runs if action_family(a) in {"quality_interval", "quality_tempo"} or a.get("intensity") == "hard"]
    families = {action_family(a) for a in runs}
    issues: list[str] = []

    recovery = str((context.get("recovery") or {}).get("state") or "")
    event_text = json.dumps(context.get("event") or {}, ensure_ascii=False).casefold()
    phase = str(context.get("phase") or "")
    prefs = context.get("athlete_preferences") or {}
    try:
        max_days = int(prefs.get("max_running_days_per_week")) if prefs.get("max_running_days_per_week") is not None else None
    except Exception:
        max_days = None

    if not runs:
        issues.append("Der er ingen validerede løbepas i en hel uge; forklar eller ret det.")
    if len(hard) > 2:
        issues.append("Der er mere end to hårde kvalitetspas i samme uge.")
    if recovery == "yellow" and len(hard) > 1:
        issues.append("Restitutionen er gul, men planen har mere end ét hårdt kvalitetspas.")
    if recovery == "red" and hard:
        issues.append("Restitutionen er rød, men et hårdt kvalitetspas står stadig i planen.")
    if max_days and len({str(a.get('date')) for a in runs}) > max_days:
        issues.append("Planen overskrider brugerens maksimale antal løbedage.")

    trail_goal = any(word in event_text for word in ("trail", "sand", "klit", "terræn", "terrain", "stage", "etape"))
    if trail_goal and phase in {"specific_build", "race_specific_peak"} and recovery != "red":
        if not families.intersection({"long_trail", "back_to_back"}):
            issues.append("Den racespecifikke trailfase mangler et langt/trail-specifikt nøglepas, uden at restitutionen forklarer fravalget.")
        if not families.intersection({"trail_easy", "long_trail", "back_to_back"}):
            issues.append("Trail-målet mangler helt terrænspecifik løbetræning.")

    hard_dates = sorted(as_date(a.get("date")) for a in hard if as_date(a.get("date")))
    for first, second in zip(hard_dates, hard_dates[1:]):
        if first and second and (second - first).days <= 1:
            issues.append("To hårde pas ligger på sammenhængende dage.")
            break

    # Benchmark quality: a non-empty future week should normally have reasons.
    thin_reasons = [a for a in training_actions if len(str(a.get("reason") or "").strip()) < 25]
    if thin_reasons:
        issues.append("Et eller flere pas mangler en reel trænerfaglig begrundelse.")
    return issues


def enriched_context(year: int, week: int, message: str) -> tuple[dict[str, Any], dict[str, Any]]:
    context, library = base_week.build_week_context(year, week, message)
    snapshot = preview.load(base_week.SNAPSHOT, {})
    history = preview.load(base_week.HEALTH_HISTORY, {})
    context["personal_session_response_examples"] = personal_response_examples(snapshot, history)
    context.setdefault("rules", {})["personal_response_examples_are_descriptive_not_causal"] = True
    return context, library


def model_prompt(context: dict[str, Any], repair_issues: list[str] | None = None, previous: dict[str, Any] | None = None) -> str:
    week = context["planning_iso_week"]
    year = context["planning_iso_year"]
    repair = ""
    if repair_issues:
        repair = (
            "\nDIN FØRSTE PLAN HAR DISSE KVALITETSPROBLEMER:\n- "
            + "\n- ".join(repair_issues)
            + "\nRet kun det, der er nødvendigt. Sikkerhed og personlige data går stadig foran checklisten.\n"
            + "FØRSTE FORSLAG:\n"
            + json.dumps(previous or {}, ensure_ascii=False, indent=2)
            + "\n"
        )

    return f"""Du er en erfaren personlig løbetræner. Lav en komplet SHADOW-PLAN for ISO uge {week}, {year}.
Planen må IKKE skrive til Garmin. Den skal kunne sammenlignes seriøst med rådgivning fra en stærk personlig AI-træner.

ARBEJDSGANG:
1. Vurder træningsbelastningen over flere uger, ikke kun sidste uge.
2. Vurder helbred/restitution som trend mod atletens egen baseline.
3. Se på personal_session_response_examples: hvordan konkrete nylige pas er fulgt af næste dags helbredssignaler. Brug dem som observationer, aldrig som bevis for årsag.
4. Sammenhold dette med løbsmålet, event-specificitet og fasen frem mod løbet.
5. Byg en samlet mikrocyklus med nøglepas, lette pas, styrke/hvile og reel restitution.
6. Brug empiriske/evidensprincipper når de er relevante; generiske planer må aldrig overtrumfe personlige data.

PRIORITET:
- Personlige Garmin-data og helbredstrend.
- Primært løbsmål og dets dokumenterede krav.
- Sikker progression og kontinuitet.
- Empiri/evidens.
- Praktiske rammer og eksisterende kalender.
- Garmin-udfordringer er sekundære.

KRAV:
- Kun datoer i allowed_dates.
- Ingen catch-up-stacking.
- Ikke to hårde dage i træk.
- Styrke bruger kun strength_master.
- Hvile er et legitimt planvalg.
- Hvis data er utilstrækkelige, sig præcis hvad der mangler.
- Hvert træningspas skal have en konkret reason.
- For ADD/ADJUST må du også udfylde session_spec. Den er rådgivende i shadow mode og bruges senere af en separat Garmin-kompilator.

session_spec schema:
{{
  "goal": "vo2max|threshold|endurance|trail_specificity|recovery|strength|marathon_pace|general",
  "total_minutes": null,
  "target_km": null,
  "warmup_min": null,
  "repetitions": null,
  "work_min": null,
  "recovery_min": null,
  "cooldown_min": null,
  "effort": "kort intensitetsbeskrivelse",
  "terrain": "kort underlagsbeskrivelse",
  "execution_note": "kort praktisk instruktion"
}}

Returner KUN valid JSON:
{{
  "week_assessment": "konkret vurdering af de seneste ugers træning + helbred og konsekvensen for uge {week}",
  "actions": [
    {{
      "action": "KEEP|MOVE|ADJUST|ADD|REMOVE",
      "source_date": "YYYY-MM-DD eller null",
      "source_workout_id": "id eller null",
      "date": "dato fra allowed_dates",
      "family": "approved family eller null",
      "target_km": null,
      "intensity": "easy|moderate|hard|strength|rest",
      "reason": "trænerfaglig begrundelse",
      "evidence_tags": ["P1.1"],
      "session_spec": {{}}
    }}
  ],
  "focus_next_14_days": ["maks 5 punkter"],
  "coach_note": "kort note"
}}
{repair}
KONTEKST:
{json.dumps(context, ensure_ascii=False, indent=2)}
"""


def call_model(context: dict[str, Any], repair_issues: list[str] | None = None, previous: dict[str, Any] | None = None) -> dict[str, Any]:
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
                    "Vær datadrevet, konkret og konservativ ved usikkerhed."
                ),
            },
            {"role": "user", "content": model_prompt(context, repair_issues, previous)},
        ],
        "options": {"temperature": 0.08 if not repair_issues else 0.03, "num_predict": 1500, "num_ctx": 12288},
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=60 * 15)
    response.raise_for_status()
    raw = str(response.json().get("message", {}).get("content", "")).strip()
    if not raw:
        raise RuntimeError("Ekspertmodellen returnerede et tomt svar.")
    return json.loads(raw)


def build_plan(raw: dict[str, Any], context: dict[str, Any], library: dict[str, Any]) -> dict[str, Any]:
    plan = preview.validate(raw, context, library)
    attach_session_specs(plan, raw)
    return plan


def generate(year: int, week: int, message: str) -> dict[str, Any]:
    context, library = enriched_context(year, week, message)
    first_raw = call_model(context)
    first_plan = build_plan(first_raw, context, library)
    first_issues = quality_issues(first_plan, context)

    chosen_raw = first_raw
    chosen_plan = first_plan
    repair_used = False
    if first_issues:
        try:
            repaired_raw = call_model(context, first_issues, first_raw)
            repaired_plan = build_plan(repaired_raw, context, library)
            repaired_issues = quality_issues(repaired_plan, context)
            if len(repaired_issues) < len(first_issues):
                chosen_raw = repaired_raw
                chosen_plan = repaired_plan
                first_issues = repaired_issues
                repair_used = True
        except Exception:
            # The first validated plan remains available; a failed repair must not erase it.
            pass

    chosen_plan["source"] = model_manager.COACH_MODEL
    chosen_plan["preview_only"] = True
    chosen_plan["garmin_writeback"] = False
    chosen_plan["planning_iso_year"] = year
    chosen_plan["planning_iso_week"] = week
    chosen_plan["planning_window"] = context.get("planning_window")
    chosen_plan["quality_review"] = {
        "repair_used": repair_used,
        "remaining_issues": first_issues,
        "passed": not first_issues,
    }
    chosen_plan["benchmark_context"] = {
        "model": model_manager.COACH_MODEL,
        "training_windows": len(context.get("training_history_4x7d") or []),
        "activity_details": len(context.get("recent_activity_detail_28d") or []),
        "health_days": len(context.get("recent_health_detail_14d") or []),
        "session_response_examples": len(context.get("personal_session_response_examples") or []),
        "evidence_principles": len(context.get("external_plan_principles") or []),
    }

    out = base_week.DATA / f"shadow_week_{year}_W{week:02d}.json"
    txt = base_week.DATA / f"shadow_week_{year}_W{week:02d}.txt"
    text = render_chat(chosen_plan)
    out.write_text(json.dumps(chosen_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    txt.write_text(text, encoding="utf-8")
    return chosen_plan


def _spec_line(spec: dict[str, Any]) -> str:
    if not spec:
        return ""
    parts: list[str] = []
    if spec.get("target_km") is not None:
        parts.append(f"{spec['target_km']:g} km")
    elif spec.get("total_minutes") is not None:
        parts.append(f"ca. {spec['total_minutes']:g} min")
    if spec.get("repetitions") and spec.get("work_min"):
        block = f"{spec['repetitions']} × {spec['work_min']:g} min"
        if spec.get("recovery_min") is not None:
            block += f" / {spec['recovery_min']:g} min let pause"
        parts.append(block)
    if spec.get("effort"):
        parts.append(str(spec["effort"]))
    if spec.get("terrain"):
        parts.append(str(spec["terrain"]))
    return " · ".join(parts)


def render_chat(plan: dict[str, Any]) -> str:
    week = int(plan.get("planning_iso_week"))
    year = int(plan.get("planning_iso_year"))
    window = plan.get("planning_window") or {}
    quality = plan.get("quality_review") or {}
    lines = [
        f"SHADOW-PLAN – UGE {week} ({window.get('from')} til {window.get('to')})",
        f"Vurdering: {plan.get('week_assessment') or 'Ingen samlet vurdering.'}",
        "",
    ]
    weekdays = ["Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn"]
    by_date: dict[str, list[dict[str, Any]]] = {}
    for action in plan.get("actions", []) or []:
        if isinstance(action, dict):
            by_date.setdefault(str(action.get("date") or ""), []).append(action)

    for idx, day in enumerate(base_week.iso_week_dates(year, week)):
        rows = by_date.get(day, [])
        if not rows:
            lines.append(f"{weekdays[idx]} {day}: Hvile / ingen valideret træning")
            continue
        for row in rows:
            chosen = row.get("selected_template") or {}
            name = row.get("plan_name") or chosen.get("title") or row.get("source_title") or row.get("family") or "Træning"
            focus = row.get("focus") or ""
            lines.append(f"{weekdays[idx]} {day}: {name}" + (f" – {focus}" if focus else ""))
            spec_text = _spec_line(row.get("session_spec") or {})
            if spec_text:
                lines.append(f"  Indhold: {spec_text}")
            if row.get("reason"):
                lines.append(f"  Hvorfor: {row['reason']}")

    if plan.get("focus_next_14_days"):
        lines.extend(["", "Fokus videre:"])
        lines.extend(f"• {item}" for item in plan.get("focus_next_14_days", [])[:5])
    if quality.get("remaining_issues"):
        lines.extend(["", "Kvalitetskontrol – resterende forbehold:"])
        lines.extend(f"• {item}" for item in quality.get("remaining_issues", []))
    lines.extend([
        "",
        f"Benchmark-input: {plan.get('benchmark_context')}",
        "Garmin write-back: FRA. Dette er kun shadow mode.",
    ])
    return "\n".join(lines)


def handle(message: str) -> str:
    year, week = base_week.parse_week(message)
    plan = generate(year, week, message)
    return render_chat(plan)
