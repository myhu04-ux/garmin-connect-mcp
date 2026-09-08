"""Offline tests for Garmin data freshness gating."""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path

import data_freshness as freshness


def write(path: Path, minutes_ago: int, extra: dict | None = None) -> None:
    now = dt.datetime.now().astimezone()
    payload = {
        "generated_at": (now - dt.timedelta(minutes=minutes_ago)).isoformat(),
    }
    payload.update(extra or {})
    path.write_text(json.dumps(payload), encoding="utf-8")


def main() -> int:
    old_files = freshness.FILES
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        paths = {
            "snapshot": root / "snapshot.json",
            "calendar": root / "calendar.json",
            "coach_state": root / "coach_state.json",
            "health_history": root / "health.json",
        }
        freshness.FILES = paths
        try:
            write(paths["snapshot"], 5, {"source_status": {"activities": "ok"}})
            write(paths["calendar"], 5)
            write(paths["coach_state"], 5)
            write(paths["health_history"], 5)
            result = freshness.status(max_age_minutes=30)
            assert result["fresh"] is True, result

            write(paths["health_history"], 45)
            result = freshness.status(max_age_minutes=30)
            assert result["fresh"] is False
            assert "health_history" in result["stale"]

            write(paths["health_history"], 5)
            write(paths["snapshot"], 1, {"source_status": {"activities": "failed"}})
            result = freshness.status(max_age_minutes=30)
            assert result["fresh"] is False
            assert "snapshot" in result["stale"]
            assert result["files"]["snapshot"].get("reason") == "activity_source_not_ok"

            paths["calendar"].unlink()
            result = freshness.status(max_age_minutes=30)
            assert result["fresh"] is False
            assert "calendar" in result["stale"]

            print("OK: freshness gate rejects stale, failed or missing Garmin core data")
            return 0
        finally:
            freshness.FILES = old_files


if __name__ == "__main__":
    raise SystemExit(main())
