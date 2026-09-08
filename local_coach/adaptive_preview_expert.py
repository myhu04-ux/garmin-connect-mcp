"""Expert rolling 7-day adaptive preview used by the normal coach pipeline.

The heavy model is only called when the relevant coaching input changes or --force is
requested. The output schema remains coach_preview.json so the existing deterministic
integrity/dashboard/calendar layers continue to work. Nothing here writes to Garmin.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import requests

import coach_preview as preview
import coach_preview_v2  # noqa: F401 - patches build_context/validate
import model_manager
import shadow_week as base_week
import shadow_week_expert as expert

OUT = preview.OUT
TEXT_OUT = preview.TEXT_OUT
OLLAMA_URL = preview.OLLAMA_URL


def load_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    state = preview.load(preview.STATE, {})
    calendar = preview.load(preview.CALENDAR, {})
    library = preview.load(preview.TEMPLATES, {})
    knowledge = preview.load(preview.KNOWLEDGE, {})
    profile = preview.load(preview.PROFILE, {})
    if not state:
        raise RuntimeError("coach_state.json mangler")
    if not preview.template_families(library):
        raise RuntimeError("godkendt workout-bibliotek mangler")
    return state, calendar, library, knowledge, profile


def build_context() -> tuple[dict[str, Any], dict[str, Any]]:
    state, calendar, library, knowledge, profile = load_inputs()
    context = preview.build_context(state, calendar, library, knowledge, profile)
    snapshot = preview.load(base_week.SNAPSHOT, {})
    history = preview.load(base_week.HEALTH_HISTORY, {})
    dates = list(context.get("allowed_dates") or [])
    context["planning_mode"] = "adaptive_rolling_7d"
    context["planning_window"] = {
        "from": dates[0] if dates else None,
        "to": dates[-1] if dates else None,
    }
    context["training_history_4x7d"] = base_week.rolling_training_weeks(snapshot, 4)
    context["recent_activity_detail_28d"] = base_week.recent_activity_detail(snapshot, 28)
    context["recent_health_detail_14d"] = base_week.recent_health_detail(history, 14)
    context["personal_session_response_examples"] = expert.personal_response_examples(snapshot, history)
    rules = context.setdefault("rules", {})
    rules["use_multiweek_training_history_not_only_last_7_days"] = True
    rules["use_health_trend_against_personal_baseline"] = True
    rules["personal_response_examples_are_descriptive_not_causal"] = True
    return context, library


def fingerprint(context: dict[str, Any]) -> str:
    """Stable hash of coaching-relevant inputs; generated_at timestamps are excluded."""
    relevant = {
        "planning_window": context.get("planning_window"),
        "recovery": context.get("recovery"),
        "training_history_4x7d": context.get("training_history_4x7d"),
        "recent_activity_detail_28d": context.get("recent_activity_detail_28d"),
        "recent_health_detail_14d": context.get("recent_health_detail_14d"),
        "personal_session_response_examples": context.get("personal_session_response_examples"),
        "plan_match": context.get("plan_match"),
        "event": context.get("event"),
        "event_focus_points": context.get("event_focus_points"),
        "existing_calendar": context.get("existing_calendar"),
        "garmin_challenges": context.get("garmin_challenges"),
        "athlete_preferences": context.get("athlete_preferences"),
        "athlete_notes": context.get("athlete_notes"),
        "external_plan_principles": context.get("external_plan_principles"),
        "available_workout_families": context.get("available_workout_families"),
    }
    raw = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cached_plan(expected_fingerprint: str) -> dict[str, Any] | None:
    current = preview.load(OUT, {})
    if not isinstance(current, dict):
        return None
    if current.get("input_fingerprint") != expected_fingerprint:
        return None
    if current.get("planning_mode") != "adaptive_rolling_7d":
        return None
    return current


def model_prompt(context: dict[str, Any], repair_issues: list[str] | None = None, previous: dict[str, Any] | None = None) -> str:
    repair = ""
    if repair_issues:
        repair = (
            "\nFØRSTE FORSLAG HAR DISSE KVALITETSPROBLEMER:\n- "
            + "\n- ".join(repair_issues)
            + "\nRet kun det nødvendige. Sikkerhed/personlige data har højere prioritet end checklisten.\n"
            + "FØRSTE FORSLAG:\n"
            + json.dumps(previous or {}, ensure_ascii=False, indent=2)
            + "\n"
        )

    return f"""Du er en erfaren personlig løbetræner. Lav den adaptive plan for de NÆSTE 7 DAGE.
Dette er en planlægningsmotor, ikke hyggesnak. Du skal kunne begrunde planen ud fra data.

ARBEJDSGANG:
1. Vurder træningen over fire rullende 7-dages vinduer og de konkrete aktiviteter fra 28 dage.
2. Vurder 14 dages helbred/restitution sammen med atletens egen baseline.
3. Brug personal_session_response_examples som observationer af, hvad der er fulgt efter konkrete pas; antag ikke årsag.
4. Sammenhold med det primære løbsmål, løbets krav og fasen frem mod løbet.
5. Byg én sammenhængende mikrocyklus. Et pas skal passe både til dagen før og dagen efter.
6. Empiri/evidens må støtte beslutningen, men må ikke overtrumfe personlige data.

PRIORITET:
- restitution/helbredstrend
- primært løbsmål
- faktisk træning og sikker progression
- brugerens praktiske rammer/noter
- empiri/evidens
- Garmin-udfordringer som sekundært mål

KRAV:
- Kun allowed_dates.
- Behold gode eksisterende pas uden grund til ændring.
- Missede/forskudte pas indhentes ikke automatisk.
- Ingen to hårde dage i træk.
- Styrke må kun bruge strength_master.
- Nye/justerede pas skal bruge en approved family.
- Hvile er et legitimt valg.
- Hvert ændret/nyt pas skal have en konkret reason.
- session_spec er rådgivende træningsindhold, senere kontrolleret af Garmin-kompilatoren.

session_spec:
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
  "terrain": "kort underlag",
  "execution_note": "kort praktisk instruktion"
}}

Returner KUN valid JSON:
{{
  "week_assessment": "konkret vurdering af belastning + helbredstrend og konsekvensen for de næste 7 dage",
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
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model_manager.COACH_MODEL,
            "stream": False,
            "think": False,
            "format": "json",
            "keep_alive": "45m",
            "messages": [
                {
                    "role": "system",
                    "content": "Du er en evidensbaseret personlig løbetræner. Svar kun med valid JSON. Vær datadrevet og kritisk.",
                },
                {"role": "user", "content": model_prompt(context, repair_issues, previous)},
            ],
            "options": {"temperature": 0.08 if not repair_issues else 0.03, "num_predict": 1500, "num_ctx": 12288},
        },
        timeout=60 * 15,
    )
    response.raise_for_status()
    text = str(response.json().get("message", {}).get("content", "")).strip()
    if not text:
        raise RuntimeError("Ekspertmodellen returnerede et tomt plansvar.")
    return json.loads(text)


def fallback_plan(context: dict[str, Any], library: dict[str, Any], error: Exception) -> dict[str, Any]:
    raw = preview.deterministic_fallback(context)
    plan = preview.validate(raw, context, library)
    plan["source"] = "deterministic_fallback"
    plan["expert_unavailable"] = str(error)[:500]
    return plan


def render(plan: dict[str, Any]) -> str:
    lines = [
        "=== ADAPTIV 7-DAGES PLAN ===",
        f"Vurdering: {plan.get('week_assessment') or ''}",
        "",
    ]
    for action in plan.get("actions", []) or []:
        if not isinstance(action, dict):
            continue
        chosen = action.get("selected_template") or {}
        name = action.get("plan_name") or chosen.get("title") or action.get("source_title") or action.get("family") or "pas"
        lines.append(f"- {action.get('date')}: {name} [{action.get('action')}] – {action.get('focus') or ''}")
        spec = expert.sanitize_session_spec(action.get("session_spec"))
        if spec:
            lines.append("    Indhold: " + json.dumps(spec, ensure_ascii=False, separators=(", ", ": ")))
        if action.get("reason"):
            lines.append(f"    Hvorfor: {action.get('reason')}")
    quality = plan.get("quality_review") or {}
    if quality.get("remaining_issues"):
        lines.append("Kvalitetsforbehold: " + "; ".join(quality.get("remaining_issues") or []))
    lines.append("Garmin write-back udføres ikke af denne planlægningsfil.")
    return "\n".join(lines)


def generate(force: bool = False) -> dict[str, Any]:
    context, library = build_context()
    fp = fingerprint(context)
    if not force:
        cached = cached_plan(fp)
        if cached is not None:
            print("Ekspertplan genbrugt: relevante data er uændrede.")
            print(render(cached))
            return cached

    try:
        first_raw = call_model(context)
        first_plan = preview.validate(first_raw, context, library)
        expert.attach_session_specs(first_plan, first_raw)
        issues = expert.quality_issues(first_plan, context)
        chosen_plan = first_plan
        repair_used = False
        if issues:
            try:
                repaired_raw = call_model(context, issues, first_raw)
                repaired_plan = preview.validate(repaired_raw, context, library)
                expert.attach_session_specs(repaired_plan, repaired_raw)
                repaired_issues = expert.quality_issues(repaired_plan, context)
                if len(repaired_issues) < len(issues):
                    chosen_plan = repaired_plan
                    issues = repaired_issues
                    repair_used = True
            except Exception:
                pass
        plan = chosen_plan
        plan["source"] = model_manager.COACH_MODEL
        plan["quality_review"] = {"repair_used": repair_used, "remaining_issues": issues, "passed": not issues}
    except Exception as exc:
        existing = preview.load(OUT, {})
        # During first background model download, keeping a previously validated
        # plan is safer than blocking the whole dashboard. On first-ever run use a
        # deterministic KEEP-only fallback.
        if isinstance(existing, dict) and existing.get("actions"):
            plan = existing
            plan["expert_refresh_warning"] = str(exc)[:500]
            plan["source"] = plan.get("source") or "previous_validated_preview"
        else:
            plan = fallback_plan(context, library, exc)
            plan["quality_review"] = {"repair_used": False, "remaining_issues": ["Ekspertmodellen var ikke klar; kun konservativ fallback vises."], "passed": False}

    plan["generated_at"] = dt.datetime.now().astimezone().isoformat()
    plan["planning_mode"] = "adaptive_rolling_7d"
    plan["planning_window"] = context.get("planning_window")
    plan["input_fingerprint"] = fp
    plan["preview_only"] = True
    plan["garmin_writeback"] = False
    plan["benchmark_context"] = {
        "model": model_manager.COACH_MODEL,
        "training_windows": len(context.get("training_history_4x7d") or []),
        "activity_details": len(context.get("recent_activity_detail_28d") or []),
        "health_days": len(context.get("recent_health_detail_14d") or []),
        "session_response_examples": len(context.get("personal_session_response_examples") or []),
        "evidence_principles": len(context.get("external_plan_principles") or []),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    TEXT_OUT.write_text(render(plan), encoding="utf-8")
    print(render(plan))
    return plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    generate(force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
