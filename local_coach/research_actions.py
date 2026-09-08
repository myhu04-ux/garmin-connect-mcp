"""Execute sourced goal/plan research requested in ordinary chat.

The language understanding that triggers this module is deterministic (`research_intent`).
Actual web research is delegated to the existing sourced DDGS + 8B expert scripts.
Successful research only changes local goal/knowledge files; Garmin is never written.
A later adaptive replan may use the new information through the normal safety pipeline.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(r"C:\GarminCoach")
REPO = ROOT / "garmin-connect-mcp"
COACH = REPO / "local_coach"
DATA = ROOT / "data"
ACTIVE_GOAL = DATA / "active_goal.json"
KNOWLEDGE = DATA / "training_knowledge.json"


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def run_script(name: str, args: list[str], timeout: int = 20 * 60) -> None:
    script = COACH / name
    if not script.exists():
        raise RuntimeError(f"Research-script mangler: {script}")
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout or "").splitlines()[-8:]).strip()
        raise RuntimeError(
            f"{name} sluttede med kode {proc.returncode}" + (f": {tail}" if tail else "")
        )


def _event_summary(goal: dict[str, Any]) -> str:
    name = goal.get("event_name") or goal.get("name") or "det nye løbsmål"
    dates = goal.get("event_dates") or goal.get("dates") or []
    distance = goal.get("distance") if isinstance(goal.get("distance"), dict) else {}
    total = distance.get("total_km") if isinstance(distance, dict) else None
    if total is None:
        total = goal.get("total_distance_km") or goal.get("distance_km")
    course = goal.get("course") if isinstance(goal.get("course"), dict) else {}
    priorities = goal.get("training_priorities") or []
    focus: list[str] = []
    for row in priorities[:5] if isinstance(priorities, list) else []:
        if isinstance(row, dict):
            value = row.get("priority") or row.get("reason")
        else:
            value = row
        if value:
            focus.append(str(value))
    bits = [f"Mål: {name}."]
    if dates:
        bits.append("Dato: " + ", ".join(str(x) for x in dates[:3]) + ".")
    if total is not None:
        bits.append(f"Distance: {total} km.")
    terrain = course.get("terrain") or course.get("surface")
    if terrain:
        bits.append(f"Terræn: {terrain}.")
    if focus:
        bits.append("Vigtigste træningskrav: " + "; ".join(focus) + ".")
    confidence = goal.get("confidence")
    if confidence:
        bits.append(f"Kildeconfidence: {confidence}.")
    return " ".join(bits)


def set_goal(query: str) -> str:
    run_script("event_research_expert.py", ["--goal", query])
    # Keep plan identity/profile in sync immediately, but do not generate/write a plan here.
    run_script("sync_goal_profile.py", [], timeout=120)
    goal = load(ACTIVE_GOAL, {})
    if not isinstance(goal, dict) or not goal:
        raise RuntimeError("Event-research sluttede, men active_goal.json mangler.")
    return _event_summary(goal)


def _find_reference(library: dict[str, Any], query: str) -> dict[str, Any] | None:
    refs = library.get("references") if isinstance(library, dict) else []
    if not isinstance(refs, list):
        return None
    q = query.casefold().strip()
    exact = [
        row for row in refs
        if isinstance(row, dict) and str(row.get("reference_input") or "").casefold().strip() == q
    ]
    if exact:
        return exact[-1]
    return refs[-1] if refs and isinstance(refs[-1], dict) else None


def research_plan(query: str) -> str:
    run_script("plan_research_expert.py", ["--plan", query])
    library = load(KNOWLEDGE, {})
    ref = _find_reference(library, query)
    if not isinstance(ref, dict):
        raise RuntimeError("Plan-research sluttede, men den nye reference kunne ikke findes i vidensbiblioteket.")
    name = ref.get("reference_name") or query
    quality = ref.get("source_quality") or "ukendt"
    principles = []
    for row in ref.get("principles", []) if isinstance(ref.get("principles"), list) else []:
        if isinstance(row, dict) and row.get("principle"):
            principles.append(str(row["principle"]))
    lines = [f"Jeg har analyseret {name} som inspirationskilde (kildekvalitet: {quality})."]
    if principles:
        lines.append("Mest relevante principper: " + "; ".join(principles[:5]))
    cautions = ref.get("cautions") if isinstance(ref.get("cautions"), list) else []
    if cautions:
        lines.append("Forbehold: " + "; ".join(str(x) for x in cautions[:3]))
    lines.append("Jeg har gemt principperne – ikke kopieret hele ugeplanen – så de kan indgå som sekundær evidens i din adaptive træning.")
    return "\n".join(lines)


def handle(intent: dict[str, Any]) -> str:
    operation = str(intent.get("operation") or "")
    query = str(intent.get("query") or "").strip()
    if not query:
        raise RuntimeError("Research-intent mangler query.")
    if operation == "set_goal":
        return set_goal(query)
    if operation == "research_plan":
        return research_plan(query)
    raise RuntimeError(f"Ukendt research-operation: {operation}")
