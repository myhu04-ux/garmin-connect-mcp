"""Migrate athlete_profile.json forward without overwriting user choices."""

from __future__ import annotations

import json
from pathlib import Path

PROFILE = Path(r"C:\GarminCoach\data\athlete_profile.json")
DEFAULTS = {
    "max_running_days_per_week": None,
    "preferred_long_run_days": [],
    "max_weekday_session_minutes": None,
    "notes": "",
    # The athlete stated that the week beginning 2026-09-07 is Thy Trail week 3.
    "plan_code": "ThyTrail",
    "plan_anchor_monday": "2026-09-07",
    "plan_anchor_week": 3,
}


def main() -> int:
    current = {}
    if PROFILE.exists():
        try:
            raw = json.loads(PROFILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                current = raw
        except Exception:
            current = {}
    changed = []
    for key, value in DEFAULTS.items():
        if key not in current:
            current[key] = value
            changed.append(key)
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=== ATHLETE PROFILE ===")
    print("Tilføjet: " + (", ".join(changed) if changed else "ingen nye felter"))
    print(f"Plan: {current.get('plan_code')} | anchor {current.get('plan_anchor_monday')} = uge {current.get('plan_anchor_week')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
