"""Safer validator wrapper for coach_preview.

Uses the existing planning/model logic but fixes validation ordering so an invalid
model suggestion can never make an existing Garmin workout disappear from the
preview. Also applies simple athlete schedule constraints after validation.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import coach_preview as base
from plan_matcher import kind_from_text


def date_of(value: Any) -> dt.date | None:
    return base.date_of(value)


def validate(raw: dict[str, Any], context: dict[str, Any], library: dict[str, Any]) -> dict[str, Any]:
    dates = set(context["allowed_dates"])
    existing = context["existing_calendar"]
    by_workout = {str(i.get("workout_id") or ""): i for i in existing if i.get("workout_id") is not None}
    families = base.template_families(library)
    valid_tags = {p["tag"] for p in context.get("external_plan_principles", [])}
    recovery = str((context.get("recovery") or {}).get("state") or "")

    validated: list[dict[str, Any]] = []
    touched_existing: set[tuple[str, str]] = set()

    for proposal in raw.get("actions", []) if isinstance(raw.get("actions"), list) else []:
        if not isinstance(proposal, dict):
            continue
        name = str(proposal.get("action") or "").upper()
        if name not in base.ALLOWED_ACTIONS:
            continue
        date = str(proposal.get("date") or "")[:10]
        if date not in dates:
            continue

        source_date = str(proposal.get("source_date") or "")[:10] if proposal.get("source_date") else None
        source_workout_id = str(proposal.get("source_workout_id") or "") if proposal.get("source_workout_id") is not None else None
        source = by_workout.get(source_workout_id) if source_workout_id else None
        if source is None and source_date:
            same_day = [i for i in existing if i.get("date") == source_date]
            source = same_day[0] if len(same_day) == 1 else None

        if name in {"KEEP", "MOVE", "ADJUST", "REMOVE"} and source is None:
            continue

        family = str(proposal.get("family") or "") or None
        if name in {"ADD", "ADJUST"}:
            if family not in families:
                continue
            if recovery == "red" and family in base.HARD_FAMILIES:
                continue

        if name == "MOVE" and source is not None:
            sd, nd = date_of(source.get("date")), date_of(date)
            if not sd or not nd or abs((nd - sd).days) > 2:
                continue

        # Only now is the proposal valid enough to claim/touch an existing item.
        if source is not None:
            key = (str(source.get("workout_id") or ""), str(source.get("date") or ""))
            if key in touched_existing:
                continue
            touched_existing.add(key)

        tags = [str(t) for t in proposal.get("evidence_tags", []) if str(t) in valid_tags]
        intensity = str(proposal.get("intensity") or "moderate").lower()
        if family == "strength_master":
            intensity = "strength"
        validated.append({
            "action": name,
            "source_date": source.get("date") if source else None,
            "source_title": source.get("title") if source else None,
            "source_workout_id": source.get("workout_id") if source else None,
            "date": date,
            "family": family,
            "target_km": proposal.get("target_km"),
            "intensity": intensity,
            "reason": str(proposal.get("reason") or "Ingen begrundelse angivet.")[:500],
            "evidence_tags": tags,
        })

    # Model omission or invalid suggestion means KEEP, never silent disappearance.
    for item in existing:
        key = (str(item.get("workout_id") or ""), str(item.get("date") or ""))
        if key not in touched_existing:
            validated.append({
                "action": "KEEP",
                "source_date": item.get("date"),
                "source_title": item.get("title"),
                "source_workout_id": item.get("workout_id"),
                "date": item.get("date"),
                "family": None,
                "target_km": None,
                "intensity": "moderate",
                "reason": "Eksisterende plan beholdes; der var ingen sikker valideret grund til at ændre den.",
                "evidence_tags": [],
            })

    # Avoid consecutive hard changes.
    hard_dates: set[str] = set()
    final_actions: list[dict[str, Any]] = []
    for action in sorted(validated, key=lambda a: (a["date"], a["action"] != "KEEP")):
        family = action.get("family")
        is_hard = family in base.HARD_FAMILIES or action.get("intensity") == "hard"
        d = date_of(action["date"])
        if is_hard and d:
            conflict = any(
                date_of(other) and abs((d - date_of(other)).days) == 1
                for other in hard_dates
            )
            if conflict and action["action"] in {"ADD", "ADJUST"}:
                continue
            hard_dates.add(action["date"])
        final_actions.append(action)

    # Respect a user-specified maximum number of run days for ADDs. Existing days
    # are preserved; the validator simply refuses extra optional running additions.
    prefs = context.get("athlete_preferences") or {}
    try:
        max_run_days = int(prefs.get("max_running_days_per_week")) if prefs.get("max_running_days_per_week") is not None else None
    except Exception:
        max_run_days = None
    if max_run_days:
        run_dates = {
            str(i.get("date")) for i in existing
            if kind_from_text(i.get("title")) != "strength"
        }
        filtered: list[dict[str, Any]] = []
        for action in final_actions:
            family = action.get("family")
            is_new_run = action["action"] == "ADD" and family in base.RUN_FAMILIES
            if is_new_run and action["date"] not in run_dates and len(run_dates) >= max_run_days:
                continue
            if is_new_run:
                run_dates.add(action["date"])
            filtered.append(action)
        final_actions = filtered

    # Bind every ADD/ADJUST to a real approved Garmin workout template.
    for action in final_actions:
        if action["action"] not in {"ADD", "ADJUST"} or not action.get("family"):
            continue
        chosen = base.choose_template(families, action["family"], action.get("target_km"))
        if chosen:
            action["selected_template"] = {
                "workout_id": chosen.get("workout_id"),
                "title": chosen.get("title"),
                "step_count": chosen.get("step_count"),
                "distance_hint_km": chosen.get("distance_hint_km"),
            }

    return {
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


base.validate = validate

if __name__ == "__main__":
    raise SystemExit(base.main())
