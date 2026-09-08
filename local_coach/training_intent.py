"""Translate natural Danish coach requests into a small, validated training intent.

The athlete should be able to describe the desired outcome in ordinary language.
This module maps that language to a constrained schema. It does NOT write to Garmin.
A deterministic parser handles common cases quickly; local Qwen is only used when
needed to interpret freer wording. Python remains responsible for workout safety and
Garmin formatting.
"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"

OPERATIONS = {
    "create_test_workout",
    "update_test_workout",
    "delete_test_workout",
    "inspect_workout",
    "training_question",
}
OBJECTIVES = {
    "vo2max",
    "threshold",
    "endurance",
    "trail_specificity",
    "recovery",
    "strength",
    "marathon_pace",
    "general",
}
SPORTS = {"running", "strength", "unknown"}


def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text, flags=re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None


def deterministic(message: str) -> dict[str, Any]:
    text = message.casefold().strip()
    explicit_test = any(k in text for k in ("test-løb", "test løb", "testløb", "test-workout", "test workout", "testpas", "test-pas"))
    refers_to_plan = any(k in text for k in ("planen", "ugen", "kalender", "næste uge", "hele planen"))
    followup_delete = (not refers_to_plan) and any(k in text for k in ("slet den", "fjern den", "slet løbet", "fjern løbet", "delete den"))
    followup_update = (not refers_to_plan) and any(k in text for k in (
        "juster den", "justér den", "juster løbet", "ændr den", "ændr løbet", "ret den",
        "gør den", "lav den om", "kort den", "forlæng den", "skift den", "skift til",
    ))

    if (explicit_test and any(k in text for k in ("slet", "fjern", "delete"))) or followup_delete:
        operation = "delete_test_workout"
    elif (explicit_test and any(k in text for k in ("juster", "justér", "ændr", "ret ", "gør den", "lav den om", "opdater"))) or followup_update:
        operation = "update_test_workout"
    elif explicit_test and any(k in text for k in ("lav", "opret", "byg", "create", "kan du")):
        operation = "create_test_workout"
    elif any(k in text for k in ("struktur", "format", "trin", "hvordan er")) and any(k in text for k in ("workout", "træning", "løb", "styrke")):
        operation = "inspect_workout"
    else:
        operation = "training_question"

    if any(k in text for k in ("vo2", "vo₂", "iltoptag", "maksimal ilt")):
        objective = "vo2max"
    elif any(k in text for k in ("tærskel", "threshold", "tempo", "laktat")):
        objective = "threshold"
    elif any(k in text for k in ("maratonpace", "marathon pace", "maratonfart")):
        objective = "marathon_pace"
    elif any(k in text for k in ("trail", "terræn", "bakke", "sand", "teknisk")):
        objective = "trail_specificity"
    elif any(k in text for k in ("restitution", "recovery", "meget let", "aktiv restitution")):
        objective = "recovery"
    elif any(k in text for k in ("styrke", "benpower", "hofte", "mobilitet")):
        objective = "strength"
    elif any(k in text for k in ("udholdenhed", "endurance", "langtur", "tid på benene")):
        objective = "endurance"
    else:
        objective = "general"

    sport = "strength" if objective == "strength" else ("running" if operation != "training_question" or any(k in text for k in ("løb", "run", "vo2", "trail", "tempo")) else "unknown")

    duration = _num(r"(?:i|på|ca\.?|cirka)?\s*(\d+(?:[\.,]\d+)?)\s*min", text)
    distance = _num(r"(\d+(?:[\.,]\d+)?)\s*km", text)
    repetitions = _num(r"(\d+)\s*[x×]\s*\d", text)
    work = _num(r"\d+\s*[x×]\s*(\d+(?:[\.,]\d+)?)\s*min", text)
    recovery = _num(r"(?:pause|pauser|restitution|jog|rolig)\D{0,12}(\d+(?:[\.,]\d+)?)\s*min", text)
    warmup = _num(r"(?:opvarmning|varm op)\D{0,10}(\d+(?:[\.,]\d+)?)\s*min", text)
    cooldown = _num(r"(?:nedjog|afjog|cooldown)\D{0,10}(\d+(?:[\.,]\d+)?)\s*min", text)
    shorter = _num(r"(\d+(?:[\.,]\d+)?)\s*min(?:utter)?\s*kortere", text)
    longer = _num(r"(\d+(?:[\.,]\d+)?)\s*min(?:utter)?\s*længere", text)

    return {
        "operation": operation,
        "sport": sport,
        "objective": objective,
        "duration_min": duration,
        "distance_km": distance,
        "repetitions": int(repetitions) if repetitions is not None else None,
        "work_min": work,
        "recovery_min": recovery,
        "warmup_min": warmup,
        "cooldown_min": cooldown,
        "relative_minutes": (-shorter if shorter is not None else longer),
        "original_message": message,
        "source": "deterministic",
    }


def _clean(candidate: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return fallback
    out = dict(fallback)
    if candidate.get("operation") in OPERATIONS:
        out["operation"] = candidate["operation"]
    if candidate.get("objective") in OBJECTIVES:
        out["objective"] = candidate["objective"]
    if candidate.get("sport") in SPORTS:
        out["sport"] = candidate["sport"]
    for key in ("duration_min", "distance_km", "work_min", "recovery_min", "warmup_min", "cooldown_min", "relative_minutes"):
        value = candidate.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = float(value)
    value = candidate.get("repetitions")
    if isinstance(value, int) and 1 <= value <= 30:
        out["repetitions"] = value
    out["source"] = "qwen+deterministic"
    return out


def interpret(message: str, use_model: bool = True) -> dict[str, Any]:
    base = deterministic(message)
    # Clear action/objective requests do not need an LLM roundtrip.
    if not use_model or (base["operation"] != "training_question" and base["objective"] != "general"):
        return base

    schema = {
        "operation": "training_question|create_test_workout|update_test_workout|delete_test_workout|inspect_workout",
        "sport": "running|strength|unknown",
        "objective": "vo2max|threshold|endurance|trail_specificity|recovery|strength|marathon_pace|general",
        "duration_min": None,
        "distance_km": None,
        "repetitions": None,
        "work_min": None,
        "recovery_min": None,
        "warmup_min": None,
        "cooldown_min": None,
        "relative_minutes": None,
    }
    prompt = (
        "Fortolk brugerens danske besked som træner-intent. Returner KUN ét JSON-objekt, ingen markdown. "
        "Gæt ikke på tal, som brugeren ikke har angivet. 'Jeg vil forbedre VO2-maks' betyder objective=vo2max, "
        "men er normalt training_question medmindre brugeren udtrykkeligt beder om at oprette/ændre et testpas. "
        f"Tilladt schema: {json.dumps(schema, ensure_ascii=False)}\nBESKED={message[:1200]}"
    )
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "stream": False,
                "think": False,
                "keep_alive": "30m",
                "messages": [{"role": "user", "content": prompt}],
                "options": {"temperature": 0.0, "num_predict": 180, "num_ctx": 2048},
            },
            timeout=35,
        )
        response.raise_for_status()
        raw = str(response.json().get("message", {}).get("content", "")).strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:].lstrip()
        return _clean(json.loads(raw), base)
    except Exception:
        return base


def training_recipe(intent: dict[str, Any]) -> dict[str, Any]:
    """Translate a goal into a conservative workout recipe; no Garmin write here."""
    objective = intent.get("objective") or "general"
    recipes: dict[str, dict[str, Any]] = {
        "vo2max": {
            "title": "VO2-maks – kontrollerede intervaller",
            "stimulus": "Gentagne hårde arbejdsperioder med samlet tid ved høj aerob belastning og tilstrækkelig aktiv pause.",
            "warmup_min": 12.0,
            "repetitions": 4,
            "work_min": 4.0,
            "recovery_min": 3.0,
            "cooldown_min": 10.0,
            "family": "quality_interval",
        },
        "threshold": {
            "title": "Tærskel – kontrolleret længere kvalitet",
            "stimulus": "Længere arbejdsperioder omkring kontrolleret høj, men bæredygtig intensitet.",
            "warmup_min": 12.0,
            "repetitions": 3,
            "work_min": 8.0,
            "recovery_min": 2.0,
            "cooldown_min": 10.0,
            "family": "quality_tempo",
        },
        "endurance": {
            "title": "Udholdenhed – rolig tid på benene",
            "stimulus": "Sammenhængende let aerob belastning med fokus på robusthed og varighed.",
            "warmup_min": 8.0,
            "repetitions": 1,
            "work_min": 45.0,
            "recovery_min": 0.0,
            "cooldown_min": 5.0,
            "family": "long_trail",
        },
        "trail_specificity": {
            "title": "Trail – terrænspecifik robusthed",
            "stimulus": "Kontrolleret tid i ujævnt terræn med teknik, bakker og løbeøkonomi frem for ren fart.",
            "warmup_min": 10.0,
            "repetitions": 1,
            "work_min": 40.0,
            "recovery_min": 0.0,
            "cooldown_min": 5.0,
            "family": "trail_easy",
        },
        "recovery": {
            "title": "Restitution – meget let løb",
            "stimulus": "Lav belastning, der understøtter bevægelse uden at skabe ny væsentlig træthed.",
            "warmup_min": 5.0,
            "repetitions": 1,
            "work_min": 25.0,
            "recovery_min": 0.0,
            "cooldown_min": 5.0,
            "family": "easy_run",
        },
        "marathon_pace": {
            "title": "Maratonfart – specifik udholdenhed",
            "stimulus": "Kontrolleret længere arbejde omkring planlagt maratonintensitet efter en rolig opvarmning.",
            "warmup_min": 15.0,
            "repetitions": 2,
            "work_min": 15.0,
            "recovery_min": 3.0,
            "cooldown_min": 10.0,
            "family": "quality_tempo",
        },
        "general": {
            "title": "Roligt løb",
            "stimulus": "Let aerob træning og kontinuitet.",
            "warmup_min": 5.0,
            "repetitions": 1,
            "work_min": 30.0,
            "recovery_min": 0.0,
            "cooldown_min": 5.0,
            "family": "easy_run",
        },
    }
    recipe = dict(recipes.get(objective, recipes["general"]))
    for key in ("warmup_min", "repetitions", "work_min", "recovery_min", "cooldown_min"):
        value = intent.get(key)
        if value is not None:
            recipe[key] = value
    recipe["objective"] = objective
    return recipe
