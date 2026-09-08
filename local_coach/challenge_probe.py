"""Read Garmin badge/challenge progress for the local coach.

READ ONLY. Garmin exposes goal-like items through several families (badge challenges,
adhoc challenges and badges in progress). This collector deliberately discovers the
available methods on the installed garminconnect client, calls only an explicit
read-only allowlist, preserves raw responses, records response shapes, and builds a
compact best-effort summary.

Challenges are always secondary goals: race specificity, recovery and safe
progression outrank badge completion.
"""

from __future__ import annotations

import datetime as dt
import inspect
import json
import os
from pathlib import Path
from typing import Any, Callable

from garminconnect import Garmin

TOKEN_DIR = os.path.expanduser("~/.garminconnect")
OUT = Path(r"C:\GarminCoach\data\garmin_challenges.json")


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def first(d: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = d.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def date_text(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        dt.date.fromisoformat(text)
        return text
    except Exception:
        return None


def response_shape(value: Any, depth: int = 0, max_depth: int = 4) -> Any:
    """Compact schema-like view; values are types, not personal data."""
    if depth >= max_depth:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): response_shape(v, depth + 1, max_depth) for k, v in list(value.items())[:80]}
    if isinstance(value, list):
        if not value:
            return []
        return [response_shape(value[0], depth + 1, max_depth)]
    return type(value).__name__


NAME_KEYS = (
    "challengeName", "badgeName", "name", "title", "displayName", "challengeTitle",
    "badgeTitle", "badgeDisplayName",
)
ID_KEYS = (
    "badgeChallengeId", "challengeId", "badgeId", "id", "uuid", "badgeUuid",
)
START_KEYS = (
    "startDate", "startDateLocal", "challengeStartDate", "badgeStartDate",
    "earnPeriodStartDate", "start",
)
END_KEYS = (
    "endDate", "endDateLocal", "challengeEndDate", "badgeEndDate",
    "earnPeriodEndDate", "expirationDate", "end",
)
GOAL_KEYS = (
    "goalValue", "goal", "targetValue", "target", "requiredValue", "goalQuantity",
    "badgeGoalValue", "badgeGoal", "targetQuantity",
)
PROGRESS_KEYS = (
    "progressValue", "progress", "currentValue", "value", "achievedValue", "userProgress",
    "badgeProgressValue", "currentProgress", "progressQuantity",
)
PCT_KEYS = (
    "percentComplete", "percentageComplete", "completionPercentage", "progressPercent",
    "percentCompleted", "completionPct",
)
UNIT_KEYS = ("unit", "unitKey", "measurementUnit", "goalUnit", "metricUnit", "badgeUnit")
METRIC_KEYS = (
    "metricType", "challengeType", "activityType", "activityTypeKey", "goalType", "type",
    "badgeType", "badgeTypeId",
)
STATUS_KEYS = (
    "status", "challengeStatus", "userStatus", "state", "badgeStatus", "completed", "earned",
    "joined",
)


def source_is_goal_family(source: str) -> bool:
    return source in {
        "in_progress_badges",
        "badge_challenges",
        "adhoc_challenges",
        "available_badge_challenges",
    }


def candidate_score(d: dict[str, Any], source: str) -> int:
    name = first(d, *NAME_KEYS)
    if not name:
        return 0
    score = 2
    if first(d, *ID_KEYS) is not None:
        score += 2
    if first(d, *GOAL_KEYS) is not None:
        score += 2
    if first(d, *PROGRESS_KEYS) is not None or first(d, *PCT_KEYS) is not None:
        score += 2
    if first(d, *START_KEYS) is not None or first(d, *END_KEYS) is not None:
        score += 1
    if first(d, *STATUS_KEYS) is not None:
        score += 1
    # A dict returned from an explicit Garmin goal/badge endpoint is allowed to be
    # less self-describing than a random nested dictionary.
    if source_is_goal_family(source):
        score += 1
    return score


def compact_item(d: dict[str, Any], source: str) -> dict[str, Any] | None:
    if candidate_score(d, source) < 4:
        return None

    name = first(d, *NAME_KEYS)
    challenge_id = first(d, *ID_KEYS)
    start = date_text(first(d, *START_KEYS))
    end = date_text(first(d, *END_KEYS))
    goal = number(first(d, *GOAL_KEYS))
    progress = number(first(d, *PROGRESS_KEYS))
    pct = number(first(d, *PCT_KEYS))
    if pct is None and goal not in (None, 0) and progress is not None:
        pct = 100.0 * progress / goal

    unit = first(d, *UNIT_KEYS)
    metric = first(d, *METRIC_KEYS)
    status = first(d, *STATUS_KEYS)
    description = first(d, "description", "badgeDescription", "challengeDescription")

    today = dt.date.today()
    days_remaining = None
    if end:
        try:
            days_remaining = (dt.date.fromisoformat(end) - today).days
        except Exception:
            pass

    return {
        "source": source,
        "id": challenge_id,
        "name": str(name),
        "description": str(description)[:500] if description is not None else None,
        "start_date": start,
        "end_date": end,
        "days_remaining": days_remaining,
        "goal": goal,
        "progress": progress,
        "remaining": max(0.0, goal - progress) if goal is not None and progress is not None else None,
        "completion_pct": round(max(0.0, min(100.0, pct)), 1) if pct is not None else None,
        "unit": str(unit) if unit is not None else None,
        "metric": str(metric) if metric is not None else None,
        "status": str(status) if status is not None else None,
    }


def active_like(item: dict[str, Any]) -> bool:
    today = dt.date.today()
    end = item.get("end_date")
    start = item.get("start_date")
    if end:
        try:
            if dt.date.fromisoformat(end) < today:
                return False
        except Exception:
            pass
    if start:
        try:
            if dt.date.fromisoformat(start) > today + dt.timedelta(days=31):
                return False
        except Exception:
            pass
    status = str(item.get("status") or "").lower()
    if any(word in status for word in ("completed", "expired", "ended", "failed", "earned")):
        return False
    pct = item.get("completion_pct")
    if isinstance(pct, (int, float)) and pct >= 100:
        return False
    return True


def collect_items(raw: Any, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for d in walk(raw):
        if not isinstance(d, dict):
            continue
        item = compact_item(d, source)
        if not item:
            continue
        key = (str(item.get("id") or ""), str(item.get("name") or "").casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def build_calls(api: Garmin) -> tuple[list[tuple[str, Callable[[], Any]]], list[dict[str, Any]]]:
    """Discover methods but call only a hard read-only allowlist."""
    specs = [
        ("in_progress_badges", "get_in_progress_badges", ()),
        ("badge_challenges", "get_badge_challenges", (0, 100)),
        ("adhoc_challenges", "get_adhoc_challenges", (0, 100)),
        ("available_badge_challenges", "get_available_badge_challenges", (0, 100)),
    ]
    calls: list[tuple[str, Callable[[], Any]]] = []
    discovered: list[dict[str, Any]] = []
    for source, method_name, args in specs:
        fn = getattr(api, method_name, None)
        present = callable(fn)
        signature = None
        doc = None
        if present:
            try:
                signature = str(inspect.signature(fn))
            except Exception:
                pass
            doc = (getattr(fn, "__doc__", None) or "").strip().split("\n", 1)[0]
            calls.append((source, lambda fn=fn, args=args: fn(*args)))
        discovered.append({
            "source": source,
            "method": method_name,
            "available": present,
            "signature": signature,
            "doc": doc,
            "read_only_allowlisted": True,
        })
    return calls, discovered


def main() -> int:
    token_dir = Path(TOKEN_DIR)
    if not token_dir.exists():
        print(f"ERROR: Garmin token folder missing: {token_dir}")
        return 2

    api = Garmin(retry_attempts=0)
    api.login(str(token_dir))
    calls, discovered = build_calls(api)

    raw: dict[str, Any] = {}
    schemas: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    items: list[dict[str, Any]] = []
    for source, fn in calls:
        try:
            value = fn()
            raw[source] = value
            schemas[source] = response_shape(value)
            items.extend(collect_items(value, source))
        except Exception as exc:
            errors.append({"source": source, "error": str(exc)[:500]})
            print(f"WARNING: {source} failed: {exc}")

    # Deduplicate across endpoints while preferring entries with richer progress.
    merged_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (str(item.get("id") or ""), str(item.get("name") or "").casefold())
        previous = merged_by_key.get(key)
        richness = sum(item.get(k) is not None for k in ("goal", "progress", "completion_pct", "end_date"))
        old_richness = sum(previous.get(k) is not None for k in ("goal", "progress", "completion_pct", "end_date")) if previous else -1
        if previous is None or richness > old_richness:
            merged_by_key[key] = item
    merged = list(merged_by_key.values())

    active = [x for x in merged if active_like(x)]
    active.sort(key=lambda x: (x.get("days_remaining") is None, x.get("days_remaining") or 9999, x.get("name") or ""))

    payload = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "read_only": True,
        "policy": "Challenges are secondary goals; race goal, recovery and safe progression have higher priority.",
        "method_discovery": discovered,
        "schemas": schemas,
        "active": active,
        "all_detected": merged,
        "errors": errors,
        "raw": raw,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== GARMIN UDFORDRINGER / BADGES ===")
    print("Read-only metoder fundet:")
    for method in discovered:
        state = "OK" if method["available"] else "mangler"
        print(f"- {method['method']}: {state}")
    print(f"Aktive/relevante: {len(active)}")
    for item in active[:30]:
        detail = []
        if item.get("progress") is not None and item.get("goal") is not None:
            detail.append(f"{item['progress']:g}/{item['goal']:g}")
        elif item.get("completion_pct") is not None:
            detail.append(f"{item['completion_pct']:.0f}%")
        if item.get("days_remaining") is not None:
            detail.append(f"{item['days_remaining']} dage tilbage")
        suffix = " | " + " | ".join(detail) if detail else ""
        print(f"- {item.get('name')}{suffix}")
    print(f"Endpoint-fejl: {len(errors)}")
    print(f"Gemt lokalt: {OUT}")
    print("Intet blev skrevet eller ændret i Garmin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
