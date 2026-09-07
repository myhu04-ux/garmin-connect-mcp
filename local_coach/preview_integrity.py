"""Final integrity pass for the validated 7-day preview.

No model calls and no Garmin writes. Every existing calendar workout in the preview
horizon must be represented by a final KEEP/MOVE/ADJUST/REMOVE decision. After any
restoration, plan names are recalculated for the whole week so double-session R/S
suffixes remain unique.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from coach_preview_v2 import decorate

DATA = Path(r"C:\GarminCoach\data")
PREVIEW = DATA / "coach_preview.json"
CALENDAR = DATA / "scheduled_workouts.json"
PROFILE = DATA / "athlete_profile.json"
STATE = DATA / "coach_state.json"


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def key(workout_id: Any, date: Any) -> tuple[str, str]:
    return str(workout_id or ""), str(date or "")[:10]


def main() -> int:
    preview = load(PREVIEW, {})
    calendar = load(CALENDAR, {})
    profile = load(PROFILE, {})
    state = load(STATE, {})
    if not preview or not calendar:
        print("ERROR: preview eller kalender mangler.")
        return 2

    today = dt.date.today()
    end = today + dt.timedelta(days=6)
    existing = []
    for item in calendar.get("items", []):
        try:
            day = dt.date.fromisoformat(str(item.get("date") or "")[:10])
        except Exception:
            continue
        if today <= day <= end:
            existing.append(item)

    actions = [a for a in preview.get("actions", []) if isinstance(a, dict)]
    represented = set()
    for action in actions:
        source_id = action.get("source_workout_id")
        source_date = action.get("source_date")
        if source_id is not None and source_date:
            represented.add(key(source_id, source_date))

    restored = []
    for item in existing:
        k = key(item.get("workout_id"), item.get("date"))
        if k in represented:
            continue
        restored.append({
            "action": "KEEP",
            "source_date": item.get("date"),
            "source_title": item.get("title"),
            "source_workout_id": item.get("workout_id"),
            "date": item.get("date"),
            "family": None,
            "target_km": None,
            "intensity": "moderate",
            "reason": "Sikkerhedsvagten beholdt dette eksisterende Garmin-pas, fordi en foreslået ændring ikke overlevede alle validatorer.",
            "evidence_tags": [],
        })

    actions.extend(restored)

    # Re-decorate the complete final set. This matters if a restored strength
    # session shares a day with a run; both then receive unique R/S plan names.
    context = {
        "athlete_preferences": profile,
        "event": state.get("event") or {},
    }
    decorate(actions, context)
    actions.sort(key=lambda a: (str(a.get("date") or ""), str(a.get("plan_name") or "")))

    preview["actions"] = actions
    preview["integrity"] = {
        "checked_at": dt.datetime.now().astimezone().isoformat(),
        "existing_in_horizon": len(existing),
        "restored_keep_actions": len(restored),
        "ok": True,
    }
    PREVIEW.write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== PREVIEW INTEGRITY ===")
    print(f"Eksisterende Garmin-pas i horisont: {len(existing)}")
    print(f"Genindsat som KEEP: {len(restored)}")
    print("Alle eksisterende pas kan spores, og plan-navne er genberegnet samlet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
