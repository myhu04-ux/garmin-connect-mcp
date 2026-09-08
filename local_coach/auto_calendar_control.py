"""Explicit chat-controlled enable/disable for adaptive Garmin calendar write-back.

Enabling is allowed only after the active CoachTest workout has passed workout
read-back and at least one calendar placement has passed calendar read-back. Automatic
removal remains disabled; the coach may add, adjust and move validated future sessions.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

ROOT = Path(r"C:\GarminCoach")
DATA = ROOT / "data"
TEST_STATE = DATA / "active_test_workout.json"
SETTINGS = DATA / "coach_settings.json"


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def intent(message: str) -> str | None:
    text = message.casefold()
    if not ("automat" in text and ("kalender" in text or "garmin" in text)):
        return None
    if any(x in text for x in ("slå fra", "sla fra", "stop", "deaktiv", "ikke længere", "ikke laengere")):
        return "disable"
    if any(x in text for x in ("slå til", "sla til", "aktiver", "må du", "maa du", "opdater selv", "hold min", "gør det selv", "goer det selv")):
        return "enable"
    return None


def disable() -> str:
    cfg = load(SETTINGS, {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["garmin_writeback_enabled"] = False
    cfg["auto_calendar_disabled_at"] = dt.datetime.now().astimezone().isoformat()
    save(SETTINGS, cfg)
    return "Automatisk Garmin-kalenderwriteback er slået fra. Coachen analyserer fortsat, men ændrer ikke kalenderen automatisk."


def enable() -> str:
    test = load(TEST_STATE, {})
    if not isinstance(test, dict) or not test.get("workout_id") or not test.get("readback_ok"):
        return "Jeg slår ikke automatisk kalenderwriteback til endnu: vi mangler et test-workout, der er læst korrekt tilbage fra Garmin."
    if not test.get("calendar_verified_at") or not test.get("scheduled_date"):
        return "Jeg slår ikke automatikken til endnu: test-workoutet skal først placeres i Garmin-kalenderen og verificeres via read-back."

    cfg = load(SETTINGS, {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.update({
        "writeback_test_passed": True,
        "writeback_test_passed_at": test.get("calendar_verified_at"),
        "garmin_writeback_enabled": True,
        "garmin_writeback_enabled_at": dt.datetime.now().astimezone().isoformat(),
        "allow_auto_remove": False,
        "max_calendar_changes_per_run": min(max(int(cfg.get("max_calendar_changes_per_run", 3) or 3), 1), 3),
        "writeback_verified_by": "conversational_test_workout_and_calendar_readback",
    })
    save(SETTINGS, cfg)
    return (
        "Automatisk Garmin-kalenderwriteback er nu slået til. Ved de planlagte coach-kørsler må træneren tilføje, "
        "justere og flytte validerede fremtidige pas. Automatisk sletning er stadig slået fra, og der udføres højst "
        f"{cfg['max_calendar_changes_per_run']} kalenderændringer pr. kørsel."
    )


def handle(message: str) -> str | None:
    action = intent(message)
    if action == "enable":
        return enable()
    if action == "disable":
        return disable()
    return None
