"""Match scheduled Garmin workouts to completed activities.

READ ONLY. The matcher behaves like a coach rather than a strict calendar:
- same day is ideal
- +/-1 day is normally acceptable when session type/load fit
- +/-2 days can count only with strong evidence
- strength is matched against strength activities, not marked as a false miss
- ambiguous matches remain uncertain instead of being forced
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Any

KEYWORDS = {
    "trail": {"trail", "terraen", "terrain", "singletrack", "mtb"},
    "back_to_back": {"back-to-back", "back to back", "b2b"},
    "interval": {"interval", "intervaller", "repetition", "bakke", "hill"},
    "tempo": {"tempo", "progressiv", "threshold", "taerskel"},
    "easy": {"let", "rolig", "easy", "zone 2", "z2", "restitution"},
    "long": {"langtur", "long run", "lang trail", "long trail"},
    "strength": {"styrke", "strength", "benpower", "hoftemobilitet"},
    "cycling": {"cykling", "cycling", "bike"},
}


def clean(text: Any) -> str:
    value = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", value.lower()).strip()


def date_of(raw: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(raw or "")[:10])
    except Exception:
        return None


def activity_date(a: dict[str, Any]) -> dt.date | None:
    return date_of(a.get("start"))


def planned_date(item: dict[str, Any]) -> dt.date | None:
    return date_of(item.get("date"))


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


def kind_from_text(text: Any) -> str:
    t = clean(text)
    if any(k in t for k in KEYWORDS["strength"]):
        return "strength"
    if any(k in t for k in KEYWORDS["cycling"]):
        return "recovery_cross_training" if any(k in t for k in KEYWORDS["easy"]) else "cycling"
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


def activity_kind(a: dict[str, Any]) -> str:
    text = " ".join(str(a.get(k) or "") for k in ("name", "type", "subtype"))
    t = clean(text)
    if "strength" in t or "styrke" in t:
        return "strength"
    if "cycling" in t or "bike" in t or "cyk" in t:
        return "recovery_cross_training" if any(k in t for k in KEYWORDS["easy"]) else "cycling"
    return kind_from_text(text)


def compatible(planned_kind: str, actual_kind: str) -> bool:
    if planned_kind == actual_kind:
        return True
    groups = [
        {"long_trail", "trail", "trail_easy"},
        {"trail_easy", "trail", "easy"},
        {"quality_interval", "quality_tempo"},
        {"easy", "trail_easy"},
    ]
    return any(planned_kind in g and actual_kind in g for g in groups)


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
    delta = (ad - pd).days
    day_delta = abs(delta)
    if day_delta > 2:
        return -999, []

    score = {0: 5.0, 1: 3.5, 2: 1.5}[day_delta]
    reasons = ["samme dag" if day_delta == 0 else f"{delta:+d} dag" if day_delta == 1 else f"{delta:+d} dage"]

    planned_workout = str(item.get("workout_id") or "")
    actual_workout = str(activity.get("workout_id") or "")
    if planned_workout and actual_workout and planned_workout == actual_workout:
        score += 10.0
        reasons.append("samme Garmin-workout")

    pk = kind_from_text(item.get("title"))
    ak = activity_kind(activity)
    if pk != "unknown" and ak != "unknown":
        if pk == ak:
            score += 4.0
            reasons.append("samme træningstype")
        elif compatible(pk, ak):
            score += 2.0
            reasons.append("kompatibel træningstype")
        else:
            score -= 4.0
            reasons.append("anden træningstype")

    # Distance is a strong clue for running, but deliberately ignored for strength.
    if pk != "strength":
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
                    score -= 2.5
                    reasons.append("distance afviger")

    pwords = {w for w in clean(item.get("title")).split() if len(w) >= 4}
    awords = {w for w in clean(activity.get("name")).split() if len(w) >= 4}
    overlap = pwords & awords
    if overlap:
        score += min(2.0, 0.5 * len(overlap))
        reasons.append("navn overlapper")

    return score, reasons


def match_recent_plan(
    snapshot: dict[str, Any],
    calendar: dict[str, Any],
    days_back: int = 14,
    min_score: float = 6.0,
) -> dict[str, Any]:
    today = dt.date.today()
    start = today - dt.timedelta(days=days_back - 1)

    planned = [
        item for item in calendar.get("items", [])
        if planned_date(item) and start <= planned_date(item) <= today
    ]
    activities = [
        a for a in (snapshot.get("all_activities") or snapshot.get("running_activities") or [])
        if activity_date(a) and start - dt.timedelta(days=2) <= activity_date(a) <= today
    ]

    used: set[str] = set()
    matches: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []

    for item in sorted(planned, key=lambda x: x.get("date") or ""):
        candidates: list[tuple[float, dict[str, Any], list[str]]] = []
        for activity in activities:
            aid = str(activity.get("id") or "")
            if aid and aid in used:
                continue
            score, reasons = candidate_score(item, activity)
            if score > -900:
                candidates.append((score, activity, reasons))
        candidates.sort(key=lambda x: x[0], reverse=True)

        if not candidates:
            misses.append({"planned_date": item.get("date"), "planned_title": item.get("title")})
            continue

        best_score, activity, reasons = candidates[0]
        pd = planned_date(item)
        ad = activity_date(activity)
        delta = (ad - pd).days if pd and ad else 0
        required = 8.0 if abs(delta) == 2 else min_score
        second = candidates[1][0] if len(candidates) > 1 else -999

        if best_score < required:
            misses.append({"planned_date": item.get("date"), "planned_title": item.get("title")})
            continue
        if second > -900 and best_score - second < 1.5 and str(activity.get("workout_id") or "") != str(item.get("workout_id") or ""):
            uncertain.append({
                "planned_date": item.get("date"),
                "planned_title": item.get("title"),
                "best_candidate": activity.get("name"),
                "best_score": round(best_score, 1),
                "second_score": round(second, 1),
            })
            continue

        aid = str(activity.get("id") or "")
        if aid:
            used.add(aid)
        matches.append({
            "planned_date": item.get("date"),
            "planned_title": item.get("title"),
            "planned_kind": kind_from_text(item.get("title")),
            "completed_date": ad.isoformat() if ad else None,
            "completed_name": activity.get("name"),
            "completed_kind": activity_kind(activity),
            "completed_km": round(km_of_activity(activity), 1) if km_of_activity(activity) is not None else None,
            "date_shift_days": delta,
            "score": round(best_score, 1),
            "reasons": reasons,
        })

    return {
        "planned_workouts": len(planned),
        "matched": len(matches),
        "missed": len(misses),
        "uncertain": len(uncertain),
        "matches": matches,
        "misses": misses,
        "uncertain_matches": uncertain,
    }
