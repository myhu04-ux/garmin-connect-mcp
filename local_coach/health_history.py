"""Collect and cache personal recovery history from Garmin Connect.

READ ONLY. The cache lets the coach compare the latest days with the athlete's
own baseline instead of generic thresholds. Existing days are reused, so later
runs only fetch missing/recent data. Partial progress is saved if Garmin limits
requests.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any

from garminconnect import Garmin

DEFAULT_OUT = Path(r"C:\GarminCoach\data\health_history.json")


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(path: Path, days: dict[str, dict[str, Any]], start: dt.date, end: dt.date, errors: list[dict[str, str]]) -> None:
    rows = [days[k] for k in sorted(days) if start.isoformat() <= k <= end.isoformat()]
    payload = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "read_only": True,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "days": rows,
        "errors": errors[-20:],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def merge(store: dict[str, dict[str, Any]], date_key: str, values: dict[str, Any]) -> None:
    row = store.setdefault(date_key, {"date": date_key})
    for key, value in values.items():
        if key != "date" and value is not None:
            row[key] = value


def compact_stats(raw: dict[str, Any] | None, date_key: str) -> dict[str, Any]:
    raw = raw or {}
    return {
        "date": raw.get("calendarDate") or date_key,
        "resting_hr": raw.get("restingHeartRate"),
        "avg_stress": raw.get("averageStressLevel"),
        "body_battery_high": raw.get("bodyBatteryHighestValue"),
        "body_battery_low": raw.get("bodyBatteryLowestValue"),
        "body_battery_charged": raw.get("bodyBatteryChargedValue"),
        "body_battery_drained": raw.get("bodyBatteryDrainedValue"),
    }


def compact_sleep(raw: dict[str, Any] | None, date_key: str) -> dict[str, Any]:
    raw = raw or {}
    daily = raw.get("dailySleepDTO") or {}
    overall = ((daily.get("sleepScores") or {}).get("overall") or {})
    seconds = daily.get("sleepTimeSeconds")
    return {
        "date": daily.get("calendarDate") or date_key,
        "sleep_hours": round(float(seconds) / 3600.0, 2) if seconds else None,
        "sleep_score": overall.get("value"),
        "sleep_quality": overall.get("qualifierKey"),
        "sleep_stress": daily.get("avgSleepStress"),
        "sleep_hrv": daily.get("avgSleepHRV"),
    }


def merge_hrv(store: dict[str, dict[str, Any]], raw: Any) -> None:
    if not raw:
        return
    rows: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        if isinstance(raw.get("hrvSummaries"), list):
            rows = [r for r in raw["hrvSummaries"] if isinstance(r, dict)]
        elif isinstance(raw.get("hrvSummary"), dict):
            rows = [raw["hrvSummary"]]
    elif isinstance(raw, list):
        rows = [r for r in raw if isinstance(r, dict)]
    for row in rows:
        key = str(row.get("calendarDate") or "")[:10]
        if not key:
            continue
        merge(store, key, {
            "hrv_last_night": row.get("lastNightAvg"),
            "hrv_weekly_avg": row.get("weeklyAvg"),
            "hrv_status": row.get("status"),
            "hrv_baseline_low": (row.get("baseline") or {}).get("balancedLow") if isinstance(row.get("baseline"), dict) else None,
            "hrv_baseline_high": (row.get("baseline") or {}).get("balancedUpper") if isinstance(row.get("baseline"), dict) else None,
        })


def merge_body_battery(store: dict[str, dict[str, Any]], raw: Any) -> None:
    if not isinstance(raw, list):
        return
    for row in raw:
        if not isinstance(row, dict):
            continue
        key = str(row.get("date") or row.get("calendarDate") or "")[:10]
        if not key:
            continue
        values = row.get("bodyBatteryValuesArray") or []
        numeric = []
        for pair in values:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                try:
                    numeric.append(float(pair[1]))
                except Exception:
                    pass
        merge(store, key, {
            "body_battery_charged": row.get("charged"),
            "body_battery_drained": row.get("drained"),
            "body_battery_high": max(numeric) if numeric else None,
            "body_battery_low": min(numeric) if numeric else None,
        })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-dir", default=os.path.expanduser("~/.garminconnect"))
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--refresh-days", type=int, default=3, help="Always refresh newest N days")
    parser.add_argument("--delay", type=float, default=0.15, help="Small delay between daily calls")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    today = dt.date.today()
    start = today - dt.timedelta(days=max(7, args.days) - 1)
    refresh_start = today - dt.timedelta(days=max(1, args.refresh_days) - 1)

    cached = load(args.output)
    store: dict[str, dict[str, Any]] = {}
    for row in cached.get("days", []) if isinstance(cached, dict) else []:
        if isinstance(row, dict) and row.get("date"):
            store[str(row["date"])[:10]] = dict(row)

    token_dir = os.path.expanduser(args.token_dir)
    if not Path(token_dir).exists():
        print(f"ERROR: Garmin token folder missing: {token_dir}")
        return 2

    garmin = Garmin(retry_attempts=0)
    garmin.login(token_dir)
    errors: list[dict[str, str]] = []

    # Efficient range calls first.
    try:
        merge_hrv(store, garmin.get_hrv_data_range(start.isoformat(), today.isoformat()))
    except Exception as exc:
        errors.append({"call": "hrv_range", "error": str(exc)[:400]})
        print(f"WARNING: HRV range failed: {exc}")
    try:
        merge_body_battery(store, garmin.get_body_battery(start.isoformat(), today.isoformat()))
    except Exception as exc:
        errors.append({"call": "body_battery_range", "error": str(exc)[:400]})
        print(f"WARNING: Body Battery range failed: {exc}")

    cursor = start
    fetched = 0
    while cursor <= today:
        key = cursor.isoformat()
        existing = store.get(key, {})
        must_refresh = cursor >= refresh_start
        need_stats = must_refresh or existing.get("resting_hr") is None or existing.get("avg_stress") is None
        need_sleep = must_refresh or existing.get("sleep_hours") is None or existing.get("sleep_score") is None

        try:
            if need_stats:
                merge(store, key, compact_stats(garmin.get_stats(key), key))
                fetched += 1
                time.sleep(max(0.0, args.delay))
            if need_sleep:
                merge(store, key, compact_sleep(garmin.get_sleep_data(key), key))
                fetched += 1
                time.sleep(max(0.0, args.delay))
        except Exception as exc:
            text = str(exc)
            errors.append({"date": key, "error": text[:400]})
            print(f"WARNING: health backfill stopped at {key}: {text}")
            save(args.output, store, start, today, errors)
            if "429" in text or "Too Many" in text or "rate" in text.lower():
                print("Rate limit detected. Partial history was saved; next run will resume from cache.")
                break
        cursor += dt.timedelta(days=1)

    save(args.output, store, start, today, errors)
    rows = [r for r in store.values() if start.isoformat() <= str(r.get("date")) <= today.isoformat()]
    complete_sleep = sum(1 for r in rows if r.get("sleep_hours") is not None)
    complete_hrv = sum(1 for r in rows if r.get("hrv_last_night") is not None or r.get("sleep_hrv") is not None)
    complete_rhr = sum(1 for r in rows if r.get("resting_hr") is not None)

    print("=== PERSONLIG RESTITUTIONSHISTORIK ===")
    print(f"Dage i cache: {len(rows)}")
    print(f"Søvn-dage: {complete_sleep} | HRV-dage: {complete_hrv} | hvilepuls-dage: {complete_rhr}")
    print(f"Nye/refresh API-kald: {fetched}")
    print(f"Fejl denne kørsel: {len(errors)}")
    print(f"Gemt lokalt: {args.output}")
    print("Intet blev skrevet eller ændret i Garmin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
