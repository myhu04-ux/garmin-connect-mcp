"""Match scheduled Garmin running workouts to completed activities.

READ ONLY. A planned workout may be completed one day early or late and still
count as completed when type/load also fit. The matcher deliberately prefers
uncertainty over a false match.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata
from typing import Any


KEYWORDS = {
    "trail": {"trail", "terræn", "terrain", "singletrack"},
    "back_to_back": {"back-to-back", "back to back", "b2b"},
    "interval": {"interval", "intervaller", "repetition", "hill", "bakke"},
    "tempo": {"tempo", "progressiv", "threshold", "tærskel"},
    "easy": {"let", "rolig", "easy", "zone 2", "z2", "restitution"},
    "long": {"langtur", "long run", "lang trail", "long trail"},
}


def clean(text: Any) -> str:
    value = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", value.lower()).strip()


def activity_date(a: dict[str, Any]) -> dt.date | None:
    raw = str(a.get("start") or "")[:10]
    try:
        return dt.date.fromisoformat(raw)
    except Exception:
        return None


def planned_date(item: dict[str, Any]) -> dt.date | None:
    raw = str(item.get("date") or "")[:10]
    try:
        return dt.date.fromisoformat(raw)
    except Exception:
        return None


def extract_km_target(title: str) -> tuple[float | None, float | None]:
    text = clean(title).replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*km", text)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*km", text)
    if m:
        x = float(m.group(1))
        return x * 0.90, x * 1.10
    return None, None


def kind(text: Any) -> str:
    t = clean(text)
    if any(k in t for k in KEYWORDS["back_to_back"]):
        return "back_to_back"
    if any(k in t for k in KEYWORDS["long"]) and any(k in t for k in KEYWORDS["trail"]):
        return "long_trail"
    if any(k in t for k in KEYWORDS["interval"]):
        return "quality_interval"
    if any(k in t for k in KEYWORDS["tempo"]):
        return "quality_tempo"
    if any(k in t for k in KEYWORDS["trail"]) and any(k in t for k in KEYWORDS["easy"]):
        return "trail_easy"
    if any(k in t for k in KEYWORDS["trail"]):
        return "trail"
    if any(k in t for k in KEYWORDS["easy"]):
        return "easy"
    return "unknown"


def compatible(planned_kind: str, activity_kind: str) -> bool:
    if planned_kind == activity_kind:
        return True
    groups = [
        {"long_trail", "trail"},
        {"trail_easy", "trail", "easy"},
        {"quality_interval", "quality_tempo"},
    ]
    return any(planned_kind in g and activity_kind in g for g in groups)


def km_of_activity(a: dict[str, Any]) -> float | None:
    try:
        return float(a.get("distance_m")) / 1000.0
    except Exception:
        return None


def candidate_score(item: dict[str, Any], activity: dict[str, Any]) -> tuple[float, list[str]]:
    pd = planned_date(item)
    ad = activity_date(activity)
    if not pd or not ad:
        return -999, []
    day_delta = abs((ad - pd).days)
    if day_delta > 1:
        return -999, []

    score = 4.0 if day_delta == 0 else 3.0
    reasons = ["samme dag" if day_delta == 0 else "±1 dag"]

    pk = kind(item.get("title"))
    ak = kind(activity.get("name"))
    if pk != "unknown" and ak != "unknown":
        if pk == ak:
            score += 3.0
            reasons.append("samme træningstype")
        elif compatible(pk, ak):
            score += 1.5
            reasons.append("kompatibel træningstype")
        else:
            score -= 1.5

    low, high = extract_km_target(str(item.get("title") or ""))
    actual_km = km_of_activity(activity)
    if low is not None and high is not None and actual_km is not None:
        if low <= actual_km <= high:
            score += 4.0
            reasons.append("distance matcher")
        else:
            midpoint = (low + high) / 2
            rel = abs(actual_km - midpoint) / max(midpoint, 0.1)
            if rel <= 0.20:
                score += 2.0
                reasons.append("distance tæt på")
            elif rel <= 0.35:
                score += 0.5
            else:
                score -= 2.0

    pwords = {w for w in clean(item.get("title")).split() if len(w) >= 4}
    awords = {w for w in clean(activity.get("name")).split() if len(w) >= 4}
    overlap = pwords & awords
    if overlap:
        score += min(1.5, 0.5 * len(overlap))
        reasons.append("navn overlapper")

    return score, reasons


def match_recent_plan(
    snapshot: dict[str, Any],
    calendar: dict[str, Any],
    days_back: int = 8,
    min_score: float = 5.0,
) -> dict[str, Any]:
    today = dt.date.today()
    start = today - dt.timedelta(days=days_back)

    planned: list[dict[str, Any]] = []
    for item in calendar.get("items", []):
        d = planned_date(item)
        title = clean(item.get("title"))
        if not d or not (start <= d <= today):
            continue
        if "styrke" in title or "strength" in title:
            # Current snapshot contains running activities only. Do not create a false miss.
            continue
        planned.append(item)

    activities = [
        a for a in snapshot.get("running_activities", [])
        if activity_date(a) and start - dt.timedelta(days=1) <= activity_date(a) <= today
    ]

    used_activity_ids: set[str] = set()
    matches: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []

    for item in sorted(planned, key=lambda x: x.get("date") or ""):
        candidates: list[tuple[float, dict[str, Any], list[str]]] = []
        for activity in activities:
            aid = str(activity.get("id") or "")
            if aid and aid in used_activity_ids:
                continue
            score, reasons = candidate_score(item, activity)
            if score > -900:
                candidates.append((score, activity, reasons))
        candidates.sort(key=lambda x: x[0], reverse=True)

        if not candidates or candidates[0][0] < min_score:
            misses.append({
                "planned_date": item.get("date"),
                "planned_title": item.get("title"),
            })
            continue

        best_score, activity, reasons = candidates[0]
        aid = str(activity.get("id") or "")
        if aid:
            used_activity_ids.add(aid)
        pd = planned_date(item)
        ad = activity_date(activity)
        delta = (ad - pd).days if pd and ad else 0
        matches.append({
            "planned_date": item.get("date"),
            "planned_title": item.get("title"),
            "completed_date": ad.isoformat() if ad else None,
            "completed_name": activity.get("name"),
            "completed_km": round(km_of_activity(activity) or 0, 1),
            "date_shift_days": delta,
            "score": round(best_score, 1),
            "reasons": reasons,
        })

    return {
        "planned_running_workouts": len(planned),
        "matched": len(matches),
        "missed": len(misses),
        "matches": matches,
        "misses": misses,
    }
