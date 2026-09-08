"""Read active Garmin challenges for the local coach.

READ ONLY. Challenges are secondary goals: race specificity, recovery and safe
progression always outrank badge/challenge completion. The collector preserves raw
responses and builds a compact best-effort summary without inventing missing fields.
"""

from __future__ import annotations

import datetime as dt
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


def looks_like_challenge(d: dict[str, Any]) -> bool:
    keys = " ".join(str(k).lower() for k in d.keys())
    return "challenge" in keys or any(k in d for k in ("badgeChallengeId", "challengeId", "challengeName"))


def compact_item(d: dict[str, Any], source: str) -> dict[str, Any] | None:
    if not looks_like_challenge(d):
        return None

    name = first(
        d,
        "challengeName",
        "name",
        "badgeName",
        "title",
        "displayName",
        "challengeTitle",
    )
    challenge_id = first(d, "badgeChallengeId", "challengeId", "id", "uuid")
    start = date_text(first(d, "startDate", "startDateLocal", "challengeStartDate", "start"))
    end = date_text(first(d, "endDate", "endDateLocal", "challengeEndDate", "end", "expirationDate"))

    goal = number(first(d, "goalValue", "goal", "targetValue", "target", "requiredValue", "goalQuantity"))
    progress = number(first(d, "progressValue", "progress", "currentValue", "value", "achievedValue", "userProgress"))
    pct = number(first(d, "percentComplete", "percentageComplete", "completionPercentage", "progressPercent"))
    if pct is None and goal not in (None, 0) and progress is not None:
        pct = 100.0 * progress / goal

    unit = first(d, "unit", "unitKey", "measurementUnit", "goalUnit", "metricUnit")
    metric = first(d, "metricType", "challengeType", "activityType", "activityTypeKey", "goalType", "type")
    status = first(d, "status", "challengeStatus", "userStatus", "state")

    today = dt.date.today()
    days_remaining = None
    if end:
        try:
            days_remaining = (dt.date.fromisoformat(end) - today).days
        except Exception:
            pass

    if not name and not challenge_id:
        return None

    return {
        "source": source,
        "id": challenge_id,
        "name": str(name or f"Garmin challenge {challenge_id}"),
        "start_date": start,
        "end_date": end,
        "days_remaining": days_remaining,
        "goal": goal,
        "progress": progress,
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
            # Keep near-future joined challenges so the coach can prepare, but not
            # challenges months away.
            if dt.date.fromisoformat(start) > today + dt.timedelta(days=31):
                return False
        except Exception:
            pass
    status = str(item.get("status") or "").lower()
    if any(word in status for word in ("completed", "expired", "ended", "failed")):
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
        key = (str(item.get("id") or ""), str(item.get("name") or "").lower())
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def main() -> int:
    token_dir = Path(TOKEN_DIR)
    if not token_dir.exists():
        print(f"ERROR: Garmin token folder missing: {token_dir}")
        return 2

    api = Garmin(retry_attempts=0)
    api.login(str(token_dir))

    calls: list[tuple[str, Callable[[], Any]]] = []
    for name, method_name in [
        ("badge_challenges", "get_badge_challenges"),
        ("adhoc_challenges", "get_adhoc_challenges"),
    ]:
        fn = getattr(api, method_name, None)
        if callable(fn):
            calls.append((name, lambda fn=fn: fn(0, 100)))

    raw: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    items: list[dict[str, Any]] = []
    for source, fn in calls:
        try:
            value = fn()
            raw[source] = value
            items.extend(collect_items(value, source))
        except Exception as exc:
            errors.append({"source": source, "error": str(exc)[:500]})
            print(f"WARNING: {source} failed: {exc}")

    # Deduplicate across challenge endpoints.
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (str(item.get("id") or ""), str(item.get("name") or "").lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)

    active = [x for x in merged if active_like(x)]
    active.sort(key=lambda x: (x.get("days_remaining") is None, x.get("days_remaining") or 9999, x.get("name") or ""))

    payload = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "read_only": True,
        "policy": "Challenges are secondary goals; race goal, recovery and safe progression have higher priority.",
        "active": active,
        "all_detected": merged,
        "errors": errors,
        "raw": raw,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== GARMIN UDFORDRINGER ===")
    print(f"Aktive/relevante: {len(active)}")
    for item in active[:20]:
        progress = ""
        if item.get("completion_pct") is not None:
            progress = f" | {item['completion_pct']:.0f}%"
        deadline = f" | {item.get('days_remaining')} dage tilbage" if item.get("days_remaining") is not None else ""
        print(f"- {item.get('name')}{progress}{deadline}")
    print(f"Fejl: {len(errors)}")
    print(f"Gemt lokalt: {OUT}")
    print("Intet blev skrevet eller ændret i Garmin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
