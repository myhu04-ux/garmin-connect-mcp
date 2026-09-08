"""Manage the local Ollama models used by Garmin Coach.

PARSER_MODEL is internal only. CHAT_MODEL is a dedicated instruct model for ordinary
free-form conversation. COACH_MODEL is reserved for deep synthesis, weekly planning,
goal research and adaptive planning. Downloads are local/free, serialized, and guarded
by a disk-space check so the Acer does not fill its system drive unexpectedly.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

import requests

import core_evidence

OLLAMA_ROOT = "http://127.0.0.1:11434"
PARSER_MODEL = "qwen3:1.7b"
FAST_MODEL = PARSER_MODEL
CHAT_MODEL = "qwen3:4b-instruct"
COACH_MODEL = "qwen3:8b"
MODEL_SIZE_GB = {CHAT_MODEL: 2.5, COACH_MODEL: 5.2}
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


def _disk_probe_path() -> Path:
    configured = os.environ.get("OLLAMA_MODELS")
    if configured:
        path = Path(configured).expanduser()
        while not path.exists() and path.parent != path:
            path = path.parent
        if path.exists():
            return path
    home = Path.home()
    return Path(home.anchor) if home.anchor else home


def free_disk_gb() -> float | None:
    try:
        return round(shutil.disk_usage(_disk_probe_path()).free / (1024 ** 3), 1)
    except Exception:
        return None


def disk_ready_for(model: str) -> tuple[bool, str]:
    free = free_disk_gb()
    if free is None:
        return True, "Diskplads kunne ikke måles; Ollama får lov at afgøre download."
    expected = float(MODEL_SIZE_GB.get(model, 0.0))
    # Keep room for temporary download data and normal Windows operation.
    required = expected + 3.0
    if free < required:
        return False, (
            f"Der er kun ca. {free:g} GB fri plads på Ollamas drev. "
            f"Jeg vil have mindst ca. {required:g} GB fri før {model} downloades."
        )
    return True, f"Disk OK: ca. {free:g} GB fri."


def pull_model(model: str) -> None:
    with _DOWNLOAD_LOCK:
        _save_model(model, {"state": "checking", "progress_pct": 0, "free_disk_gb": free_disk_gb()})
        try:
            if is_installed(model):
                _save_model(model, {"state": "ready", "progress_pct": 100, "free_disk_gb": free_disk_gb()})
                return
            disk_ok, disk_message = disk_ready_for(model)
            if not disk_ok:
                raise RuntimeError(disk_message)
            _save_model(model, {
                "state": "downloading",
                "progress_pct": 0,
                "detail": disk_message,
                "free_disk_gb": free_disk_gb(),
            })
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
                        "free_disk_gb": free_disk_gb(),
                    })
            if not is_installed(model):
                raise RuntimeError("Ollama afsluttede download, men modellen kan ikke findes i /api/tags.")
            _save_model(model, {"state": "ready", "progress_pct": 100, "free_disk_gb": free_disk_gb()})
        except Exception as exc:
            _save_model(model, {"state": "error", "error": str(exc)[:500], "free_disk_gb": free_disk_gb()})


def ensure_model_background(model: str) -> dict[str, Any]:
    try:
        if is_installed(model):
            _save_model(model, {"state": "ready", "progress_pct": 100, "free_disk_gb": free_disk_gb()})
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
            _save_model(model, {"state": "ready", "progress_pct": 100, "free_disk_gb": free_disk_gb()})
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
    """Prepare 4B instruct first, then 8B, in one background worker."""
    if not is_installed(CHAT_MODEL):
        pull_model(CHAT_MODEL)
    else:
        _save_model(CHAT_MODEL, {"state": "ready", "progress_pct": 100, "free_disk_gb": free_disk_gb()})
    if not is_installed(COACH_MODEL):
        pull_model(COACH_MODEL)
    else:
        _save_model(COACH_MODEL, {"state": "ready", "progress_pct": 100, "free_disk_gb": free_disk_gb()})
