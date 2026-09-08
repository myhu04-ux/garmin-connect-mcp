"""Ensure athlete-facing analysis is based on recent local Garmin data.

The local coach is allowed to keep cached Garmin data for normal UI responsiveness,
but deep/week coaching should not silently reason over an old snapshot. This helper
runs the dedicated read-only refresh only when core files are older than the requested
age. It never invokes the heavy planning model and never writes to Garmin.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(r"C:\GarminCoach")
REPO = ROOT / "garmin-connect-mcp"
DATA = ROOT / "data"
REFRESH_SCRIPT = REPO / "local_coach" / "refresh_readonly.ps1"
FILES = {
    "snapshot": DATA / "snapshot.json",
    "calendar": DATA / "scheduled_workouts.json",
    "coach_state": DATA / "coach_state.json",
    "health_history": DATA / "health_history.json",
}


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _generated_at(path: Path) -> dt.datetime | None:
    payload = _read(path)
    raw = payload.get("generated_at") or payload.get("updated_at")
    if raw:
        try:
            value = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.astimezone()
            return value
        except Exception:
            pass
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except Exception:
        return None


def age_minutes(path: Path, now: dt.datetime | None = None) -> float | None:
    stamp = _generated_at(path)
    if stamp is None:
        return None
    now = now or dt.datetime.now().astimezone()
    try:
        return max(0.0, (now - stamp.astimezone()).total_seconds() / 60.0)
    except Exception:
        return None


def status(max_age_minutes: int = 30) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    stale: list[str] = []
    for name, path in FILES.items():
        age = age_minutes(path)
        exists = path.exists()
        fresh = bool(exists and age is not None and age <= max_age_minutes)
        rows[name] = {
            "exists": exists,
            "age_minutes": round(age, 1) if age is not None else None,
            "fresh": fresh,
            "path": str(path),
        }
        if not fresh:
            stale.append(name)

    snapshot = _read(FILES["snapshot"])
    source_status = snapshot.get("source_status") if isinstance(snapshot, dict) else {}
    activities_ok = isinstance(source_status, dict) and source_status.get("activities") == "ok"
    if not activities_ok and "snapshot" not in stale:
        stale.append("snapshot")
        rows["snapshot"]["fresh"] = False
        rows["snapshot"]["reason"] = "activity_source_not_ok"

    return {
        "fresh": not stale,
        "max_age_minutes": max_age_minutes,
        "stale": stale,
        "files": rows,
    }


def _powershell() -> str:
    candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.exists() else "powershell.exe"


def _wait_for_external_refresh(max_age_minutes: int, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.time() + max(5, timeout_seconds)
    last = status(max_age_minutes)
    while time.time() < deadline:
        if last.get("fresh"):
            return last
        time.sleep(3)
        last = status(max_age_minutes)
    return last


def ensure_fresh(max_age_minutes: int = 30, timeout_seconds: int = 12 * 60) -> dict[str, Any]:
    current = status(max_age_minutes)
    if current.get("fresh"):
        return {"refreshed": False, "waited_for_other_process": False, **current}
    if not REFRESH_SCRIPT.exists():
        raise RuntimeError(f"Read-only refresh-script mangler: {REFRESH_SCRIPT}")

    proc = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(REFRESH_SCRIPT),
        ],
        cwd=str(REPO),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=max(60, timeout_seconds),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    if proc.returncode == 9:
        after = _wait_for_external_refresh(max_age_minutes, min(timeout_seconds, 10 * 60))
        if not after.get("fresh"):
            raise RuntimeError(
                "En anden coach-opdatering kører, men friske Garmin-data blev ikke klar inden ventetiden udløb."
            )
        return {"refreshed": False, "waited_for_other_process": True, **after}
    if proc.returncode != 0:
        raise RuntimeError(f"Read-only Garmin-refresh sluttede med kode {proc.returncode}.")

    after = status(max_age_minutes)
    if not after.get("fresh"):
        raise RuntimeError(
            "Read-only Garmin-refresh sluttede, men en eller flere kernefiler er stadig for gamle eller ugyldige: "
            + ", ".join(after.get("stale") or [])
        )
    return {"refreshed": True, "waited_for_other_process": False, **after}
