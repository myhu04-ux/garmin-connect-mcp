"""Safer validator wrapper for coach_preview.

The language model proposes coaching actions; Python owns dates, safety, athlete
constraints, plan naming and approved Garmin templates. Today's session can be
shown, but new/changed sessions are never written for today retroactively.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import coach_preview as base
from plan_identity import position_for
from plan_matcher import kind_from_text

FOCUS_LABELS = {
    "easy_run": "Rolig aerob træning og kontinuitet",
    "easy": "Rolig aerob træning og kontinuitet",
    "trail_easy": "Rolig trail, teknik og sikkert fodarbejde",
    "trail": "Trailteknik og terræntilvænning",
    "quality_interval": "Bakkestyrke, løbeøkonomi og kontrolleret kvalitet",
    "quality_tempo": "Kontrolleret tempo og udholdenhed ved højere indsats",
    "long_trail": "Tid på benene, trailspecificitet og energiindtag",
    "back_to_back": "Løb på trætte ben og robusthed til næste løbsdag",
    "strength_master": "Benstyrke, stabilitet og hoftemobilitet",
    "strength": "Benstyrke, stabilitet og hoftemobilitet",
    "recovery_cross_training": "Aktiv restitution uden ekstra løbebelastning",
    "shakeout": "Let gennemløb og friske ben",
    "unknown": "Dagens planlagte træningsformål",
}

NON_RUN_KINDS = {"strength", "recovery_cross_training", "cycling"}
RED_DOWNGRADE = {
    "quality_interval": "easy_run",
    "quality_tempo": "easy_run",
    "long_trail": "trail_easy",
    "back_to_back": "trail_easy",
}


def date_of(value: Any) -> dt.date | None:
    return base.date_of(value)


def planning_dates() -> list[str]:
    today = dt.date.today()
    return [(today + dt.timedelta(days=i)).isoformat() for i in range(7)]


def inferred_family(action: dict[str, Any]) -> str:
    if action.get("family"):
        return str(action["family"])
    return kind_from_text(action.get("source_title"))


def is_run_family(family: str | None) -> bool:
    return bool(family) and family not in NON_RUN_KINDS and family != "unknown"


def is_run_title(title: Any) -> bool:
    kind = kind_from_text(title)
    return kind not in NON_RUN_KINDS


def focus_for(action: dict[str, Any]) -> str:
    return FOCUS_LABELS.get(inferred_family(action), FOCUS_LABELS["unknown"])


def decorate(actions: list[dict[str, Any]], context: dict[str, Any]) -> None:
    profile = context.get("athlete_preferences") or {}
    event = context.get("event") or {}
    for action in actions:
        try:
            pos = position_for(action["date"], profile, event)
            action["plan_name"] = pos["name"]
            action["plan_week"] = pos["week"]
            action["plan_day"] = pos["day"]
        except Exception:
            action["plan_name"] = None
        action["focus"] = focus_for(action)
        chosen = action.get("selected_template") or {}
        action["workout_style"] = chosen.get("title") or action.get("source_title") or inferred_family(action)


def choose_and_bind(actions: list[dict[str, Any]], families: dict[str, list[dict[str, Any]]]) -> None:
    for action in actions:
        if action["action"] not in {"ADD", "ADJUST"} or not action.get("family"):
            continue
        chosen = base.choose_template(families, action["family"], action.get("target_km"))
        if chosen:
            action["selected_template"] = {
                "workout_id": chosen.get("workout_id"),
                "title": chosen.get("title"),
                "step_count": chosen.get("step_count"),
                "estimated_duration_s": chosen.get("estimated_duration_s"),
                "distance_hint_km": chosen.get("distance_hint_km"),
            }


def validate(raw: dict[str, Any], context: dict[str, Any], library: dict[str, Any]) -> dict[str, Any]:
    dates = set(context["allowed_dates"])
    today = dt.date.today().isoformat()
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

        # Today's existing session can be shown/kept. New or changed target sessions
        # are only accepted from tomorrow onward.
        if date == today and name != "KEEP":
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

        # Only a genuinely valid proposal may claim an existing calendar item.
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

    # Omitted/invalid suggestions are KEEP by default, never silent deletion.
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

    # Deterministic red-state safety override for future key/long sessions.
    if recovery == "red":
        for action in validated:
            fam = inferred_family(action)
            if action.get("action") == "KEEP" and action.get("date") != today and fam in RED_DOWNGRADE:
                replacement = RED_DOWNGRADE[fam]
                if replacement in families:
                    action["action"] = "ADJUST"
                    action["family"] = replacement
                    action["target_km"] = None
                    action["intensity"] = "easy"
                    action["reason"] = "Restitutionen er rød. Nøglebelastningen nedgraderes deterministisk til et godkendt roligt pas; kontinuitet bevares uden at jagte kvalitet."

    # One running session per day unless an existing session is deliberately replaced.
    existing_run_dates = {str(i.get("date")) for i in existing if is_run_title(i.get("title"))}
    occupied_new_run_dates: set[str] = set()
    one_per_day: list[dict[str, Any]] = []
    for action in sorted(validated, key=lambda a: (a["date"], a["action"] != "KEEP")):
        fam = inferred_family(action)
        source_is_run = is_run_title(action.get("source_title")) if action.get("source_title") else False
        target_is_run = is_run_family(fam)

        if action["action"] == "ADD" and target_is_run:
            if action["date"] in existing_run_dates or action["date"] in occupied_new_run_dates:
                continue
            occupied_new_run_dates.add(action["date"])
        elif action["action"] == "MOVE" and source_is_run:
            source_date = str(action.get("source_date") or "")
            if action["date"] != source_date and action["date"] in existing_run_dates:
                continue
            if action["date"] in occupied_new_run_dates:
                continue
            occupied_new_run_dates.add(action["date"])
        elif action["action"] == "ADJUST" and target_is_run:
            # Replacement on the same source date is allowed; moving is not part of ADJUST.
            occupied_new_run_dates.add(action["date"])
        one_per_day.append(action)
    validated = one_per_day

    # Avoid consecutive hard days, including existing hard KEEP sessions.
    hard_dates: set[str] = set()
    final_actions: list[dict[str, Any]] = []
    for action in sorted(validated, key=lambda a: (a["date"], a["action"] != "KEEP")):
        fam = inferred_family(action)
        is_hard = fam in base.HARD_FAMILIES or action.get("intensity") == "hard"
        day = date_of(action["date"])
        if is_hard and day:
            conflict = any(date_of(other) and abs((day - date_of(other)).days) == 1 for other in hard_dates)
            if conflict and action["action"] in {"ADD", "ADJUST"}:
                continue
            hard_dates.add(action["date"])
        final_actions.append(action)

    # Respect maximum run days for optional ADDs. Existing plan is not silently cut.
    prefs = context.get("athlete_preferences") or {}
    try:
        max_run_days = int(prefs.get("max_running_days_per_week")) if prefs.get("max_running_days_per_week") is not None else None
    except Exception:
        max_run_days = None
    if max_run_days:
        run_dates = {str(i.get("date")) for i in existing if is_run_title(i.get("title"))}
        filtered: list[dict[str, Any]] = []
        for action in final_actions:
            fam = inferred_family(action)
            is_new_run = action["action"] == "ADD" and is_run_family(fam)
            if is_new_run and action["date"] not in run_dates and len(run_dates) >= max_run_days:
                continue
            if is_new_run:
                run_dates.add(action["date"])
            filtered.append(action)
        final_actions = filtered

    choose_and_bind(final_actions, families)

    # Enforce maximum weekday duration when Garmin exposes estimated duration.
    try:
        max_minutes = int(prefs.get("max_weekday_session_minutes")) if prefs.get("max_weekday_session_minutes") is not None else None
    except Exception:
        max_minutes = None
    if max_minutes:
        bounded: list[dict[str, Any]] = []
        for action in final_actions:
            day = date_of(action.get("date"))
            chosen = action.get("selected_template") or {}
            duration_s = chosen.get("estimated_duration_s")
            over = False
            if day and day.weekday() < 5 and action.get("action") in {"ADD", "ADJUST"} and duration_s is not None:
                try:
                    over = float(duration_s) / 60.0 > max_minutes
                except Exception:
                    over = False
            if not over:
                bounded.append(action)
        final_actions = bounded

    decorate(final_actions, context)

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


# Override the base planning horizon before base.main() builds the context.
base.allowed_dates = planning_dates
base.validate = validate

if __name__ == "__main__":
    raise SystemExit(base.main())
