"""Fast, tool-aware entrypoint for the direct local Garmin coach chat.

Common factual questions are answered deterministically from validated local data.
When the athlete asks about Garmin challenges/badges, the chat refreshes those
read-only Garmin endpoints on demand before answering. Questions about workout
format can inspect approved real Garmin master workouts and build a local dry-run
candidate without writing to Garmin. Free-form coaching questions use local Qwen.

This module never writes to Garmin.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import requests

import challenge_probe
import coach_chat_ui as base
import workout_lab


def pick(mapping: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    return {k: mapping.get(k) for k in keys if mapping.get(k) is not None}


def fast_context() -> dict[str, Any]:
    state = base.load(base.STATE, {})
    preview = base.load(base.PREVIEW, {})
    goal = base.load(base.GOAL, {})
    profile = base.load(base.PROFILE, {})
    challenges = base.load(base.CHALLENGES, {})
    notes = base.load(base.NOTES, {})

    recovery = state.get("recovery") or {}
    training = state.get("training") or {}
    match = state.get("plan_match") or {}
    event = state.get("event") or goal or {}

    upcoming = []
    for action in (preview.get("actions") or [])[:7]:
        if not isinstance(action, dict):
            continue
        upcoming.append({
            "date": action.get("date"),
            "name": action.get("plan_name"),
            "focus": action.get("focus"),
            "style": action.get("workout_style"),
            "reason": str(action.get("reason") or "")[:180],
        })

    active = challenges.get("active") if isinstance(challenges, dict) else []
    challenge_rows = []
    for item in (active or [])[:8]:
        if not isinstance(item, dict):
            continue
        challenge_rows.append(pick(item, (
            "name", "description", "start_date", "end_date", "days_remaining",
            "goal", "progress", "remaining", "completion_pct", "unit", "metric", "status", "source",
        )))

    active_notes = []
    for row in (notes.get("notes") or [])[-8:] if isinstance(notes, dict) else []:
        if isinstance(row, dict) and row.get("active") is not False and row.get("text"):
            active_notes.append(str(row["text"])[:220])

    return {
        "recovery": {
            "state": recovery.get("state"),
            "history_days": recovery.get("history_days"),
            "latest": pick(recovery.get("latest"), ("sleep_hours", "sleep_score", "hrv", "resting_hr", "body_battery", "stress")),
            "signals": (recovery.get("signals") or [])[:4],
        },
        "training": {
            "last_7_days": training.get("last_7_days") or {},
            "previous_7_days": training.get("previous_7_days") or {},
            "strength_sessions_last_14_days": training.get("strength_sessions_last_14_days"),
        },
        "plan_match": pick(match, ("matched", "planned_workouts", "pending", "uncertain", "missed")),
        "event": pick(event, ("name", "event_name", "days_to_event", "event_type", "distance_km", "dates", "course")),
        "upcoming": upcoming,
        "garmin_challenges": challenge_rows,
        "preferences": pick(profile, ("max_running_days_per_week", "preferred_long_run_days", "max_weekday_session_minutes", "notes", "plan_code", "plan_anchor_week")),
        "planning_notes": active_notes[-6:],
    }


def has_any(text: str, words: tuple[str, ...]) -> bool:
    lower = text.casefold()
    return any(word in lower for word in words)


def format_number(value: Any) -> str:
    try:
        f = float(value)
        return str(int(f)) if f.is_integer() else f"{f:g}"
    except Exception:
        return str(value)


def challenge_answer() -> str:
    """Fresh Garmin read; no LLM and no guessing."""
    try:
        rc = challenge_probe.main()
        if rc not in (0, None):
            raise RuntimeError(f"challenge-proben sluttede med kode {rc}")
    except Exception as exc:
        return f"Jeg prøvede at hente udfordringer direkte fra Garmin, men læsningen fejlede: {exc}"

    payload = base.load(base.CHALLENGES, {})
    active = payload.get("active") or [] if isinstance(payload, dict) else []
    errors = payload.get("errors") or [] if isinstance(payload, dict) else []
    methods = payload.get("method_discovery") or [] if isinstance(payload, dict) else []

    if not active:
        tried = ", ".join(
            str(x.get("method")) for x in methods
            if isinstance(x, dict) and x.get("available")
        ) or "de kendte Garmin challenge-kilder"
        tail = ""
        if errors:
            tail = " Der var også endpoint-fejl: " + "; ".join(str(x.get("source")) for x in errors[:3] if isinstance(x, dict)) + "."
        return (
            "Jeg har lige søgt direkte i Garmin via " + tried + ", men parseren fandt endnu ingen aktive udfordringer. "
            "Jeg gætter derfor ikke. Den rå Garmin-struktur er gemt lokalt, så vi kan lære feltstrukturen fra netop din konto." + tail
        )

    lines = ["Jeg har lige læst Garmin direkte. Jeg kan se disse aktive/relevante udfordringer:"]
    for item in active[:8]:
        if not isinstance(item, dict):
            continue
        detail = []
        progress, goal = item.get("progress"), item.get("goal")
        if progress is not None and goal is not None:
            detail.append(f"{format_number(progress)}/{format_number(goal)}")
            if item.get("remaining") is not None:
                detail.append(f"mangler {format_number(item['remaining'])}")
        elif item.get("completion_pct") is not None:
            detail.append(f"{format_number(item['completion_pct'])}%")
        if item.get("days_remaining") is not None:
            detail.append(f"{item['days_remaining']} dage tilbage")
        suffix = " – " + ", ".join(detail) if detail else ""
        lines.append(f"• {item.get('name')}{suffix}")
    lines.append("De bliver kun brugt som sekundære mål; Thy Trail, restitution og sikker progression har højere prioritet.")
    return "\n".join(lines)


def master_query_from_message(message: str) -> str | None:
    lower = message.casefold()
    if has_any(lower, ("styrke", "benpower", "hofte")):
        return "strength_master"
    if has_any(lower, ("lang trail", "langtur", "long trail")):
        return "long_trail"
    if has_any(lower, ("interval", "bakke", "kvalitet")):
        return "quality_interval"
    if has_any(lower, ("tempo", "progressiv")):
        return "quality_tempo"
    if has_any(lower, ("back-to-back", "back to back")):
        return "back_to_back"
    if has_any(lower, ("trail", "terræn")):
        return "trail_easy"
    if has_any(lower, ("let løb", "zone 2", "rolig")):
        return "easy_run"
    return None


def workout_answer(message: str) -> str:
    query = master_query_from_message(message)
    master = workout_lab.find_master(query)
    if not master:
        return "Jeg kan ikke finde en passende godkendt Garmin-master endnu. Kør en fuld template-opdatering først."
    raw = master.get("raw")
    if not isinstance(raw, dict):
        return "Jeg fandt masteren, men den rå Garmin-struktur mangler, så jeg vil ikke gætte på formatet."

    dry_name = "LAB-DryRun"
    candidate = workout_lab.sanitized_copy(raw, dry_name)
    errors = workout_lab.validation_errors(candidate)
    sig = workout_lab.semantic_signature(candidate)
    workout_lab.CANDIDATE.parent.mkdir(parents=True, exist_ok=True)
    workout_lab.CANDIDATE.write_text(json.dumps(candidate, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        f"Jeg har læst den rigtige Garmin-master: {master.get('title')} ({master.get('family')}).",
        f"Format: {sig.get('sport_type_key')}, {sig.get('segment_count')} segment(er), {len(sig.get('steps') or [])} udførelsestrin.",
    ]
    for step in (sig.get("steps") or [])[:12]:
        part = f"trin {step.get('order')}: {step.get('step_type')}"
        if step.get("end_condition"):
            part += f", {step.get('end_condition')}={step.get('end_value')}"
        if step.get("target_type"):
            part += f", mål={step.get('target_type')}"
        if step.get("category") or step.get("exercise_name"):
            part += f", {step.get('category') or ''}/{step.get('exercise_name') or ''}"
        lines.append("• " + part)
    if len(sig.get("steps") or []) > 12:
        lines.append(f"• … plus {len(sig['steps']) - 12} yderligere trin")
    if errors:
        lines.append("Dry-run fandt formatfejl: " + "; ".join(errors[:4]))
    else:
        lines.append("Dry-run er strukturelt OK. Intet blev skrevet til Garmin. En rigtig upload-test skal stadig læses tilbage fra Garmin, før formatet godkendes til automatik.")
    return "\n".join(lines)


def upcoming_answer(context: dict[str, Any]) -> str | None:
    rows = context.get("upcoming") or []
    if not rows:
        return "Jeg kan ikke se nogen validerede kommende pas i den lokale plan endnu."
    lines = ["De nærmeste planlagte pas er:"]
    for row in rows[:4]:
        name = row.get("name") or row.get("style") or "Træning"
        focus = row.get("focus") or ""
        lines.append(f"• {row.get('date')}: {name}" + (f" – {focus}" if focus else ""))
    return "\n".join(lines)


def recovery_answer(context: dict[str, Any]) -> str:
    recovery = context.get("recovery") or {}
    state = recovery.get("state") or "ukendt"
    latest = recovery.get("latest") or {}
    bits = []
    if latest.get("sleep_hours") is not None:
        bits.append(f"søvn {latest['sleep_hours']} t")
    if latest.get("hrv") is not None:
        bits.append(f"HRV {latest['hrv']}")
    if latest.get("resting_hr") is not None:
        bits.append(f"hvilepuls {latest['resting_hr']}")
    measured = ", ".join(bits[:3])
    return f"Restitutionen er vurderet som {state}." + (f" Seneste nøgledata: {measured}." if measured else "")


def direct_tool_answer(message: str) -> str | None:
    if has_any(message, ("udfordring", "udfordringer", "challenge", "challenges", "badge", "badges")):
        return challenge_answer()

    if has_any(message, ("workout", "garmin-format", "garmin format", "struktur", "test-løb", "test løb", "tør test", "dry run")):
        return workout_answer(message)

    context = fast_context()
    lower = message.casefold()
    if ("hvad" in lower or "vis" in lower) and has_any(message, ("træne", "træning", "næste pas", "kommende pas", "de næste")):
        return upcoming_answer(context)
    if has_any(message, ("restitution", "hrv", "søvn", "hvilepuls", "body battery")) and len(message) < 220:
        return recovery_answer(context)
    return None


def model_answer(message: str) -> str:
    context = fast_context()
    history = base.history_rows()[-2:]
    messages = [{
        "role": "system",
        "content": (
            "Du er en erfaren personlig løbetræner. Svar på naturligt dansk, kort og konkret. "
            "Brug kun de givne fakta. Forklar kort hvorfor. Primært løbsmål, restitution og sikker "
            "progression går foran Garmin-udfordringer. Du ændrer ikke Garmin fra chatten."
        ),
    }]
    messages.extend({"role": x["role"], "content": str(x["content"])[:500]} for x in history)
    messages.append({
        "role": "user",
        "content": "DATA=" + json.dumps(context, ensure_ascii=False, separators=(",", ":")) + "\nSPØRGSMÅL=" + message[:900],
    })
    payload = {
        "model": base.MODEL,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "messages": messages,
        "options": {"temperature": 0.18, "num_predict": 150, "num_ctx": 3072},
    }
    response = requests.post(base.OLLAMA_URL, json=payload, timeout=50)
    response.raise_for_status()
    text = str(response.json().get("message", {}).get("content", "")).strip()
    return text or "Jeg kunne ikke formulere et kort svar ud fra de lokale data."


def fast_answer(message: str) -> str:
    direct = direct_tool_answer(message)
    if direct is not None:
        return direct
    return model_answer(message)


def warm_model() -> None:
    try:
        requests.post(
            base.OLLAMA_URL,
            json={
                "model": base.MODEL,
                "stream": False,
                "think": False,
                "keep_alive": "30m",
                "messages": [{"role": "user", "content": "Svar kun OK"}],
                "options": {"num_predict": 1, "num_ctx": 1024},
            },
            timeout=90,
        )
    except Exception:
        pass


base.answer = fast_answer

if __name__ == "__main__":
    threading.Thread(target=warm_model, daemon=True).start()
    raise SystemExit(base.main())
