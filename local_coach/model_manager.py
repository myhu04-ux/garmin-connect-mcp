"""Manage the two local Ollama brains used by Garmin Coach.

FAST_MODEL is for short conversational turns. COACH_MODEL is deliberately larger and
is reserved for weekly planning / deeper coaching. The larger model is downloaded in
the background so the web UI stays responsive. Status is persisted for the UI/chat.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path
from typing import Any

import requests

OLLAMA_ROOT = "http://127.0.0.1:11434"
FAST_MODEL = "qwen3:1.7b"
# Official Ollama library tag. 8B is intentionally chosen for weekly/deep coaching
# quality; the Acer has ample system RAM even if the whole model cannot stay in VRAM.
COACH_MODEL = "qwen3:8b"
STATUS = Path(r"C:\GarminCoach\data\model_status.json")
_LOCK = threading.Lock()
_PULL_THREAD: threading.Thread | None = None


def _save(payload: dict[str, Any]) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["updated_at"] = dt.datetime.now().astimezone().isoformat()
    STATUS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def status() -> dict[str, Any]:
    if not STATUS.exists():
        return {"state": "unknown", "model": COACH_MODEL}
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {"state": "unknown", "model": COACH_MODEL}


def installed_models(timeout: float = 4.0) -> set[str]:
    response = requests.get(f"{OLLAMA_ROOT}/api/tags", timeout=timeout)
    response.raise_for_status()
    rows = response.json().get("models") or []
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("name", "model"):
            value = str(row.get(key) or "").strip()
            if value:
                names.add(value)
                if value.endswith(":latest"):
                    names.add(value[:-7])
    return names


def is_installed(model: str = COACH_MODEL) -> bool:
    try:
        names = installed_models()
    except Exception:
        return False
    return model in names or f"{model}:latest" in names


def pull_model(model: str = COACH_MODEL) -> None:
    _save({"state": "checking", "model": model, "progress_pct": 0})
    try:
        if is_installed(model):
            _save({"state": "ready", "model": model, "progress_pct": 100})
            return
        _save({"state": "downloading", "model": model, "progress_pct": 0})
        with requests.post(
            f"{OLLAMA_ROOT}/api/pull",
            json={"model": model, "stream": True},
            stream=True,
            timeout=(10, 60 * 45),
        ) as response:
            response.raise_for_status()
            last_pct = 0
            last_status = "downloading"
            for raw in response.iter_lines():
                if not raw:
                    continue
                try:
                    row = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                last_status = str(row.get("status") or last_status)
                total = row.get("total")
                completed = row.get("completed")
                if total and completed is not None:
                    try:
                        last_pct = max(0, min(100, int(100 * float(completed) / float(total))))
                    except Exception:
                        pass
                _save({
                    "state": "downloading",
                    "model": model,
                    "progress_pct": last_pct,
                    "detail": last_status,
                })
        if not is_installed(model):
            raise RuntimeError("Ollama afsluttede download, men modellen kan ikke findes i /api/tags.")
        _save({"state": "ready", "model": model, "progress_pct": 100})
    except Exception as exc:
        _save({"state": "error", "model": model, "error": str(exc)[:500]})


def ensure_coach_model_background() -> dict[str, Any]:
    global _PULL_THREAD
    try:
        if is_installed(COACH_MODEL):
            _save({"state": "ready", "model": COACH_MODEL, "progress_pct": 100})
            return status()
    except Exception:
        pass
    with _LOCK:
        if _PULL_THREAD is None or not _PULL_THREAD.is_alive():
            _PULL_THREAD = threading.Thread(target=pull_model, args=(COACH_MODEL,), daemon=True)
            _PULL_THREAD.start()
    return status()


def coach_model_ready() -> tuple[bool, str]:
    if is_installed(COACH_MODEL):
        current = status()
        if current.get("state") != "ready":
            _save({"state": "ready", "model": COACH_MODEL, "progress_pct": 100})
        return True, COACH_MODEL
    current = ensure_coach_model_background()
    state = current.get("state") or "downloading"
    pct = current.get("progress_pct")
    if state == "error":
        return False, f"Den større coach-model kunne ikke installeres: {current.get('error') or 'ukendt fejl'}"
    suffix = f" ({pct}%)" if pct is not None else ""
    return False, f"Den større coach-model {COACH_MODEL} installeres lokalt{suffix}. Prøv ugeplanen igen, når den er klar."
