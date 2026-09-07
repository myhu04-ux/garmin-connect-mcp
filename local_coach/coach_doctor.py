"""Local integrity checks for Garmin Local Coach.

No Garmin API calls here. The doctor verifies that artefacts are present, parseable,
fresh and internally consistent. Ollama is advisory: deterministic fallbacks keep
the coach safe if the model is unavailable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import requests

ROOT = Path(r"C:\GarminCoach")
DATA = ROOT / "data"
TOKEN_DIR = Path(os.path.expanduser("~/.garminconnect"))
SNAPSHOT = DATA / "snapshot.json"
CALENDAR = DATA / "scheduled_workouts.json"
STATE = DATA / "coach_state.json"
PREVIEW = DATA / "coach_preview.json"
TEMPLATES = DATA / "approved_workout_templates.json"
SETTINGS = DATA / "coach_settings.json"
PROFILE = DATA / "athlete_profile.json"
OUT = DATA / "system_status.json"
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
MODEL = "qwen3:1.7b"


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
        return parsed
    except Exception:
        return None


def age_minutes(payload: Any, key: str = "generated_at") -> float | None:
    if not isinstance(payload, dict):
        return None
    stamp = parse_time(payload.get(key))
    if not stamp:
        return None
    return max(0.0, (dt.datetime.now().astimezone() - stamp.astimezone()).total_seconds() / 60.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["preflight", "postflight"], default="postflight")
    parser.add_argument("--max-age-minutes", type=float, default=90.0)
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    fatal = 0
    warnings = 0

    def add(name: str, status: str, message: str, **extra: Any) -> None:
        nonlocal fatal, warnings
        checks.append({"name": name, "status": status, "message": message, **extra})
        if status == "fatal":
            fatal += 1
        elif status == "warning":
            warnings += 1

    if TOKEN_DIR.exists():
        add("garmin_tokens", "ok", f"Tokenmappe findes: {TOKEN_DIR}")
    else:
        add("garmin_tokens", "fatal", f"Garmin tokenmappe mangler: {TOKEN_DIR}")

    try:
        response = requests.get(OLLAMA_TAGS, timeout=2.5)
        response.raise_for_status()
        body = response.json()
        tags = body.get("models", []) if isinstance(body, dict) else []
        names = {str(x.get("name") or x.get("model") or "") for x in tags if isinstance(x, dict)}
        if MODEL in names:
            add("ollama", "ok", f"Ollama svarer, og {MODEL} er tilgængelig.")
        else:
            add("ollama", "warning", f"Ollama svarer, men {MODEL} blev ikke fundet. Konservativ fallback er stadig mulig.")
    except Exception as exc:
        add("ollama", "warning", f"Ollama svarer ikke lige nu: {exc}")

    profile = load(PROFILE) if PROFILE.exists() else None
    if isinstance(profile, dict) and profile.get("plan_code") and profile.get("plan_anchor_monday") and profile.get("plan_anchor_week"):
        add("plan_identity", "ok", f"Planidentitet: {profile.get('plan_code')} med anchor-uge {profile.get('plan_anchor_week')} fra {profile.get('plan_anchor_monday')}.")
    elif args.mode == "postflight":
        add("plan_identity", "warning", "Planidentiteten er ufuldstændig; Garmin-navne kan mangle W/D-kode.")

    if args.mode == "postflight":
        required = [
            ("snapshot", SNAPSHOT),
            ("calendar", CALENDAR),
            ("coach_state", STATE),
            ("coach_preview", PREVIEW),
            ("templates", TEMPLATES),
        ]
        payloads: dict[str, Any] = {}
        for name, path in required:
            if not path.exists():
                add(name, "fatal", f"Mangler {path.name}")
                continue
            payload = load(path)
            payloads[name] = payload
            if payload is None:
                add(name, "fatal", f"{path.name} er ikke gyldig JSON")
                continue
            add(name, "ok", f"{path.name} kan læses.")

        for name in ("snapshot", "calendar", "coach_state", "coach_preview"):
            payload = payloads.get(name)
            age = age_minutes(payload)
            if age is None:
                add(name + "_freshness", "fatal", f"{name} mangler et gyldigt generated_at-tidspunkt.")
            elif age > args.max_age_minutes:
                add(name + "_freshness", "fatal", f"{name} er {age:.0f} min gammel og betragtes som stale.", age_minutes=round(age, 1))
            else:
                add(name + "_freshness", "ok", f"{name} er frisk ({age:.0f} min).", age_minutes=round(age, 1))

        snapshot = payloads.get("snapshot") or {}
        source_status = snapshot.get("source_status") if isinstance(snapshot, dict) else None
        if isinstance(source_status, dict) and source_status.get("activities") != "ok":
            add("garmin_activity_source", "fatal", "Seneste snapshot blev ikke bygget på en vellykket aktivitetshentning fra Garmin.")
        elif isinstance(snapshot, dict):
            add("garmin_activity_source", "ok", f"Snapshot indeholder {len(snapshot.get('all_activities') or [])} aktiviteter, heraf {len(snapshot.get('running_activities') or [])} løb.")

        templates = payloads.get("templates") or {}
        families = templates.get("families") if isinstance(templates, dict) else {}
        strength = (families or {}).get("strength_master") if isinstance(families, dict) else None
        if strength:
            add("strength_master", "ok", "Godkendt styrke-master findes.")
        else:
            add("strength_master", "warning", "Godkendt styrke-master blev ikke fundet i workout-biblioteket.")

        preview = payloads.get("coach_preview") or {}
        integrity = preview.get("integrity") if isinstance(preview, dict) else None
        if not isinstance(integrity, dict) or not integrity.get("ok"):
            add("preview_integrity", "fatal", "Slutpreviewet mangler en bestået integritetskontrol.")
        else:
            restored = int(integrity.get("restored_keep_actions") or 0)
            if restored:
                add("preview_integrity", "warning", f"Integritetsvagten genindsatte {restored} eksisterende pas som KEEP. Sikkerheden virkede, men forslagslogikken bør observeres.")
            else:
                add("preview_integrity", "ok", "Alle eksisterende pas var repræsenteret efter hele validator-kæden.")

        actions = preview.get("actions", []) if isinstance(preview, dict) else []
        missing_names = sum(1 for action in actions if isinstance(action, dict) and not action.get("plan_name"))
        if missing_names:
            add("plan_names", "warning", f"{missing_names} preview-pas mangler W/D-plan-navn.")
        else:
            add("plan_names", "ok", "Alle preview-pas har plan-navn.")

        settings = load(SETTINGS) if SETTINGS.exists() else {}
        if isinstance(settings, dict) and settings.get("garmin_writeback_enabled") and not settings.get("writeback_test_passed"):
            add("writeback_gate", "fatal", "Write-back står ON uden bestået kalender-test.")
        else:
            add("writeback_gate", "ok", "Write-back gate er konsistent.")

    result = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "mode": args.mode,
        "ok": fatal == 0,
        "fatal_count": fatal,
        "warning_count": warnings,
        "checks": checks,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== GARMIN LOCAL COACH DOCTOR ===")
    for item in checks:
        mark = {"ok": "OK", "warning": "ADVARSEL", "fatal": "FEJL"}.get(item["status"], item["status"])
        print(f"[{mark}] {item['name']}: {item['message']}")
    print(f"Resultat: {fatal} fatale fejl, {warnings} advarsler.")
    print(f"Statusfil: {OUT}")
    return 0 if fatal == 0 else 10


if __name__ == "__main__":
    raise SystemExit(main())
