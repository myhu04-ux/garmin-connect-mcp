"""Collect a compact read-only Garmin snapshot for the local training coach.

The snapshot is deliberately broad enough for coaching: it includes all recent
activities (not only running), plus convenient running/strength subsets, current
daily stats, sleep and Training Readiness when the device/account exposes it.
Nothing is written to Garmin.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Callable

from garminconnect import Garmin


def compact_stats(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    keys = {
        "calendarDate": "date",
        "totalSteps": "steps",
        "restingHeartRate": "resting_hr",
        "lastSevenDaysAvgRestingHeartRate": "resting_hr_7d_avg",
        "averageStressLevel": "avg_stress",
        "maxStressLevel": "max_stress",
        "bodyBatteryChargedValue": "body_battery_charged",
        "bodyBatteryDrainedValue": "body_battery_drained",
        "bodyBatteryHighestValue": "body_battery_high",
        "bodyBatteryLowestValue": "body_battery_low",
        "bodyBatteryMostRecentValue": "body_battery_current",
    }
    return {dst: raw.get(src) for src, dst in keys.items() if raw.get(src) is not None}


def compact_sleep(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    daily = raw.get("dailySleepDTO") or {}
    scores = daily.get("sleepScores") or {}
    overall = scores.get("overall") or {}
    sleep_seconds = daily.get("sleepTimeSeconds")
    result = {
        "date": daily.get("calendarDate"),
        "sleep_seconds": sleep_seconds,
        "sleep_hours": round(float(sleep_seconds) / 3600, 2) if sleep_seconds else None,
        "sleep_score": overall.get("value"),
        "sleep_quality": overall.get("qualifierKey"),
        "resting_hr": daily.get("restingHeartRate"),
        "avg_sleep_stress": daily.get("avgSleepStress"),
        "avg_overnight_hrv": raw.get("avgOvernightHrv") or daily.get("avgSleepHRV"),
        "deep_sleep_seconds": daily.get("deepSleepSeconds"),
        "rem_sleep_seconds": daily.get("remSleepSeconds"),
    }
    return {k: v for k, v in result.items() if v is not None}


def compact_readiness(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    rows = raw if isinstance(raw, list) else [raw]
    output: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        result = {
            "date": r.get("calendarDate"),
            "score": r.get("score"),
            "level": r.get("level"),
            "feedback": r.get("feedbackShort"),
            "sleep_score": r.get("sleepScore"),
            "acute_load": r.get("acuteLoad"),
            "hrv_weekly_avg": r.get("hrvWeeklyAverage"),
            "recovery_time_minutes": r.get("recoveryTime"),
        }
        output.append({k: v for k, v in result.items() if v is not None})
    return output


def _subtype(raw: dict[str, Any]) -> str | None:
    value = raw.get("activitySubType") or raw.get("activitySubtype")
    if isinstance(value, dict):
        return value.get("typeKey") or value.get("key") or value.get("name")
    return str(value) if value not in (None, "") else None


def compact_activities(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for a in rows or []:
        activity_type = a.get("activityType") or {}
        result = {
            "id": a.get("activityId"),
            "name": a.get("activityName"),
            "type": activity_type.get("typeKey") if isinstance(activity_type, dict) else activity_type,
            "subtype": _subtype(a),
            "start": a.get("startTimeLocal"),
            "distance_m": a.get("distance"),
            "duration_s": a.get("duration"),
            "avg_hr": a.get("averageHR"),
            "max_hr": a.get("maxHR"),
            "elevation_gain_m": a.get("elevationGain"),
            "training_load": a.get("activityTrainingLoad"),
            "aerobic_te": a.get("aerobicTrainingEffect"),
            "anaerobic_te": a.get("anaerobicTrainingEffect"),
        }
        output.append({k: v for k, v in result.items() if v is not None})
    return output


def running_like(a: dict[str, Any]) -> bool:
    text = f"{a.get('type', '')} {a.get('subtype', '')}".lower()
    return "running" in text or "trail_run" in text


def strength_like(a: dict[str, Any]) -> bool:
    text = f"{a.get('type', '')} {a.get('subtype', '')} {a.get('name', '')}".lower()
    return "strength" in text or "styrke" in text


def safe_call(name: str, fn: Callable[[], Any], errors: list[dict[str, str]]) -> Any:
    try:
        return fn()
    except Exception as exc:
        text = str(exc)
        errors.append({"call": name, "error": text[:500]})
        print(f"WARNING: {name} failed: {text}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-dir", default=os.path.expanduser("~/.garminconnect"))
    parser.add_argument("--days", type=int, default=42)
    parser.add_argument("--output", default=r"C:\GarminCoach\data\snapshot.json")
    args = parser.parse_args()

    today = dt.date.today()
    start = today - dt.timedelta(days=max(1, args.days - 1))
    yesterday = today - dt.timedelta(days=1)

    token_dir = os.path.expanduser(args.token_dir)
    if not Path(token_dir).exists():
        print(f"ERROR: Garmin token directory not found: {token_dir}")
        return 2

    print(f"Using local Garmin tokens from: {token_dir}")
    garmin = Garmin()
    try:
        garmin.login(token_dir)
    except Exception as exc:
        print(f"ERROR: Garmin token login failed: {exc}")
        print("Do not re-enter your password yet. Verify the token or wait if Garmin reports HTTP 429.")
        return 3

    errors: list[dict[str, str]] = []
    profile_name = safe_call("profile", garmin.get_full_name, errors)
    stats = safe_call("stats_today", lambda: garmin.get_stats(today.isoformat()), errors)

    sleep = safe_call("sleep_today", lambda: garmin.get_sleep_data(today.isoformat()), errors)
    if not sleep:
        sleep = safe_call("sleep_yesterday", lambda: garmin.get_sleep_data(yesterday.isoformat()), errors)

    readiness = safe_call(
        "training_readiness_today",
        lambda: garmin.get_training_readiness(today.isoformat()),
        errors,
    )
    if not readiness:
        readiness = safe_call(
            "training_readiness_yesterday",
            lambda: garmin.get_training_readiness(yesterday.isoformat()),
            errors,
        )

    all_raw = safe_call(
        "all_activities",
        lambda: garmin.get_activities_by_date(start.isoformat(), today.isoformat()),
        errors,
    )
    if all_raw is None:
        # Backward-compatible fallback for an older client/API behavior.
        all_raw = safe_call(
            "running_activities_fallback",
            lambda: garmin.get_activities_by_date(start.isoformat(), today.isoformat(), "running"),
            errors,
        ) or []

    all_activities = compact_activities(all_raw)
    running_activities = [a for a in all_activities if running_like(a)]
    strength_activities = [a for a in all_activities if strength_like(a)]

    snapshot = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "read_only": True,
        "profile_name": profile_name,
        "window": {"start": start.isoformat(), "end": today.isoformat()},
        "today": compact_stats(stats),
        "sleep": compact_sleep(sleep),
        "training_readiness": compact_readiness(readiness),
        "all_activities": all_activities,
        "running_activities": running_activities,
        "strength_activities": strength_activities,
        "errors": errors,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== SNAPSHOT RESULT ===")
    print(f"Profile: {profile_name or 'not returned'}")
    print(f"All activities: {len(all_activities)}")
    print(f"Running activities: {len(running_activities)}")
    print(f"Strength activities: {len(strength_activities)}")
    print(f"Training readiness entries: {len(snapshot['training_readiness'])}")
    print(f"Sleep data: {'yes' if snapshot['sleep'] else 'no'}")
    print(f"Errors: {len(errors)}")
    print(f"Saved locally: {output}")
    print("No Garmin data was written or changed.")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
