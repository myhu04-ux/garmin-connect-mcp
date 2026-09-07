r"""Build an approved local workout-template library from the athlete's existing Garmin workouts.

This is READ ONLY with respect to Garmin. It reads the local output from
workout_style_probe.py and classifies known, already working workouts into
approved template families. The resulting library is later used by the coach
instead of letting the language model invent workout structure from scratch.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SOURCE = Path(r"C:\GarminCoach\data\workout_styles_raw.json")
OUT = Path(r"C:\GarminCoach\data\approved_workout_templates.json")


def norm(text: str | None) -> str:
    return (text or "").strip().lower()


def classify(title: str | None, sport: str | None) -> str | None:
    t = norm(title)
    s = norm(sport)

    # Strength is deliberately pinned to the known-good existing home workout.
    if "styrke" in t and ("benpower" in t or "hoftemobilitet" in t):
        return "strength_master"

    if s == "running":
        if "back-to-back" in t:
            return "back_to_back"
        if "langtur trail" in t:
            return "long_trail"
        if "trail" in t and ("let løb" in t or "let loeb" in t):
            return "trail_easy"
        if "tempo" in t:
            return "quality_tempo"
        if "interval" in t:
            return "quality_interval"
        if "shakeout" in t:
            return "shakeout"
        if "let løb" in t or "let loeb" in t:
            return "easy_run"

    if s == "cycling" and ("restitution" in t or "let cykling" in t):
        return "recovery_cross_training"

    return None


def numeric_range_from_title(title: str | None) -> tuple[float | None, float | None]:
    t = norm(title).replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*km", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(?:^|\s)(\d+(?:\.\d+)?)\s*km", t)
    if m:
        v = float(m.group(1))
        return v, v
    return None, None


def main() -> int:
    if not SOURCE.exists():
        print(f"ERROR: Mangler {SOURCE}")
        print("Kør workout_style_probe.py først.")
        return 2

    raw = json.loads(SOURCE.read_text(encoding="utf-8"))
    details = raw.get("details") or []

    families: dict[str, list[dict[str, Any]]] = {}
    unclassified: list[dict[str, Any]] = []

    for item in details:
        if item.get("error"):
            continue
        family = classify(item.get("title"), item.get("sport"))
        low_km, high_km = numeric_range_from_title(item.get("title"))
        compact = {
            "workout_id": item.get("workout_id"),
            "title": item.get("title"),
            "sport": item.get("sport"),
            "step_count": len(item.get("steps") or []),
            "distance_hint_km": {"low": low_km, "high": high_km},
            "steps": item.get("steps") or [],
            "raw": item.get("raw"),
        }
        if family:
            families.setdefault(family, []).append(compact)
        else:
            unclassified.append(compact)

    # Sort variable-distance families by their title distance, making nearest-template
    # selection deterministic later.
    for rows in families.values():
        rows.sort(key=lambda r: (
            r["distance_hint_km"].get("low") if r["distance_hint_km"].get("low") is not None else 9999,
            r.get("title") or "",
        ))

    policies = {
        "language": "da",
        "strength": {
            "mode": "reuse_exact_master",
            "family": "strength_master",
            "allow_ai_exercise_changes": False,
            "reason": "Eksisterende hjemmestyrkepas er godkendt og tilpasset brugerens udstyr.",
        },
        "easy_run": {"mode": "nearest_existing_template"},
        "trail_easy": {"mode": "nearest_existing_template"},
        "quality_tempo": {"mode": "nearest_existing_template"},
        "quality_interval": {"mode": "nearest_existing_template"},
        "long_trail": {"mode": "nearest_existing_template"},
        "back_to_back": {"mode": "nearest_existing_template"},
        "shakeout": {"mode": "nearest_existing_template"},
        "recovery_cross_training": {"mode": "reuse_existing_template"},
    }

    result = {
        "read_only_source": True,
        "language": "da",
        "policies": policies,
        "families": families,
        "unclassified": unclassified,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== GODKENDT WORKOUT-BIBLIOTEK ===")
    for name in sorted(families):
        print(f"{name}: {len(families[name])}")
        for item in families[name]:
            print(f"  - {item.get('title')} | workout={item.get('workout_id')} | trin={item.get('step_count')}")
    print(f"Uklassificerede: {len(unclassified)}")
    strength = families.get("strength_master") or []
    if strength:
        print(f"STYRKE MASTER LÅST: {strength[0].get('title')} | workout={strength[0].get('workout_id')}")
    else:
        print("ADVARSEL: Kunne ikke finde styrke-masteren automatisk.")
    print(f"Gemt lokalt: {OUT}")
    print("Intet blev skrevet eller ændret i Garmin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
