"""Keep plan identity aligned with the active goal without clobbering user choices.

First association with an existing goal preserves the athlete's current block
position. A later *different* event resets the plan code to a derived short name
and starts training week 1 on the current Monday. Custom code changes are preserved
while the event itself remains the same.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from plan_identity import derive_code, monday_of

DATA = Path(r"C:\GarminCoach\data")
GOAL = DATA / "active_goal.json"
PROFILE = DATA / "athlete_profile.json"


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def main() -> int:
    if not GOAL.exists():
        print("Ingen research-baseret active_goal.json endnu; planidentiteten bevares.")
        return 0

    goal = load(GOAL)
    profile = load(PROFILE)
    event_name = str(goal.get("event_name") or goal.get("name") or "").strip()
    if not event_name:
        print("Aktivt mål mangler event_name; planidentiteten bevares.")
        return 0

    previous_goal = str(profile.get("plan_goal_name") or "").strip()
    if not previous_goal:
        # First association: preserve the already stated Thy Trail week 3 position.
        profile["plan_goal_name"] = event_name
        changed = "første målkobling – eksisterende uge/kode bevaret"
    elif previous_goal.casefold() != event_name.casefold():
        profile["plan_goal_name"] = event_name
        profile["plan_code"] = derive_code(event_name)
        profile["plan_anchor_monday"] = monday_of(dt.date.today()).isoformat()
        profile["plan_anchor_week"] = 1
        changed = f"nyt mål – reset til {profile['plan_code']}W1"
    else:
        changed = "samme mål – brugerens kode/uge bevaret"

    profile["updated_at"] = dt.datetime.now().astimezone().isoformat()
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Planidentitet: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
