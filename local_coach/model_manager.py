"""Manage the local Ollama models used by Garmin Coach.

PARSER_MODEL is internal only. CHAT_MODEL handles ordinary free-form conversation when
no deterministic tool can answer. COACH_MODEL is reserved for deep synthesis, weekly
planning, goal research and adaptive planning. Downloads are local/free and serialized
so the Acer does not try to download two multi-GB models concurrently.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path
from typing import Any

import requests

import core_evidence

OLLAMA_ROOT = "http://127.0.0.1:11434"
PARSER_MODEL = "qwen3:1.7b"
FAST_MODEL = PARSER_MODEL
CHAT_MODEL = "qwen3:4b"
COACH_MODEL = "qwen3:8b"
STATUS = Path(r"C:\GarminCoach\data\model_status.json")
_THREADS: dict[str, threading.Thread] = {}
_THREADS_LOCK = threading.Lock()
_DOWNLOAD_LOCK = threading.Lock()

try:
    core_evidence.seed_training_knowledge()
except Exception:
    pass


def _load_status() -> dict[str, Any]:
    if not STATUS.exists():
        return {"models": {}}
    try:
        value = json.loads(STATUS.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"models": {}}
    except Exception:
        return {"models": {}}


def _save_model(model: str, payload: dict[str, Any]) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    current = _load_status()
    models = current.get("models") if isinstance(current.get("models"), dict) else {}
    row = dict(payload)
    row["model"] = model
    row["updated_at"] = dt.datetime.now().astimezone().isoformat()
    models[model] = row
    current["models"] = models
    current["updated_at"] = row["updated_at"]
    STATUS.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def status(model: str | None = None) -> dict[str, Any]:
    payload = _load_status()
    if model is None:
        return payload
    models = payload.get("models") if isinstance(payload.get("models"), dict) else {}
    row = models.get(model)
    return row if isinstance(row, dict) else {"state": "unknown", "model": model}


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


def is_installed(model: str) -> bool:
    try:
        names = installed_models()
    except Exception:
        return False
    return model in names or f"{model}:latest" in names


def pull_model(model: str) -> None:
    with _DOWNLOAD_LOCK:
        _save_model(model, {"state": "checking", "progress_pct": 0})
        try:
            if is_installed(model):
                _save_model(model, {"state": "ready", "progress_pct": 100})
                return
            _save_model(model, {"state": "downloading", "progress_pct": 0})
            with requests.post(
                f"{OLLAMA_ROOT}/api/pull",
                json={"model": model, "stream": True},
                stream=True,
                timeout=(10, 60 * 60),
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
                    _save_model(model, {
                        "state": "downloading",
                        "progress_pct": last_pct,
                        "detail": last_status,
                    })
            if not is_installed(model):
                raise RuntimeError("Ollama afsluttede download, men modellen kan ikke findes i /api/tags.")
            _save_model(model, {"state": "ready", "progress_pct": 100})
        except Exception as exc:
            _save_model(model, {"state": "error", "error": str(exc)[:500]})


def ensure_model_background(model: str) -> dict[str, Any]:
    try:
        if is_installed(model):
            _save_model(model, {"state": "ready", "progress_pct": 100})
            return status(model)
    except Exception:
        pass
    with _THREADS_LOCK:
        thread = _THREADS.get(model)
        if thread is None or not thread.is_alive():
            thread = threading.Thread(target=pull_model, args=(model,), daemon=True)
            _THREADS[model] = thread
            thread.start()
    return status(model)


def _ready(model: str, label: str) -> tuple[bool, str]:
    if is_installed(model):
        current = status(model)
        if current.get("state") != "ready":
            _save_model(model, {"state": "ready", "progress_pct": 100})
        return True, model
    current = ensure_model_background(model)
    state = current.get("state") or "downloading"
    pct = current.get("progress_pct")
    if state == "error":
        return False, f"{label} kunne ikke installeres: {current.get('error') or 'ukendt fejl'}"
    suffix = f" ({pct}%)" if pct is not None else ""
    return False, f"{label} {model} installeres lokalt{suffix}. Prøv igen, når den er klar."


def chat_model_ready() -> tuple[bool, str]:
    return _ready(CHAT_MODEL, "Samtalemodellen")


def coach_model_ready() -> tuple[bool, str]:
    return _ready(COACH_MODEL, "Ekspertmodellen")


def ensure_chat_model_background() -> dict[str, Any]:
    return ensure_model_background(CHAT_MODEL)


def ensure_coach_model_background() -> dict[str, Any]:
    return ensure_model_background(COACH_MODEL)


def ensure_all_models_background() -> None:
    """Prepare 4B first, then 8B, in one background worker.

    This function itself may block while downloading, so callers should run it in a
    daemon/background thread (the chat agent does). A user request can still trigger a
    missing model independently; _DOWNLOAD_LOCK keeps actual downloads serialized.
    """
    if not is_installed(CHAT_MODEL):
        pull_model(CHAT_MODEL)
    else:
        _save_model(CHAT_MODEL, {"state": "ready", "progress_pct": 100})
    if not is_installed(COACH_MODEL):
        pull_model(COACH_MODEL)
    else:
        _save_model(COACH_MODEL, {"state": "ready", "progress_pct": 100})
