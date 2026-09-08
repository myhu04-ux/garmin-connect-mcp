"""Use the expert local model for sourced race/event research."""

from __future__ import annotations

import json

import requests

import event_research as base
import model_manager


def call_ollama(goal: str, sources: list[dict], model: str) -> dict:
    ready, info = model_manager.coach_model_ready()
    if not ready:
        raise RuntimeError(info)
    response = requests.post(
        base.OLLAMA_URL,
        json={
            "model": model_manager.COACH_MODEL,
            "stream": False,
            "think": False,
            "format": "json",
            "keep_alive": "45m",
            "messages": [
                {
                    "role": "system",
                    "content": "Returner kun valid JSON. Vær kildekritisk. Ukendt er bedre end opdigtet.",
                },
                {"role": "user", "content": base.prompt(goal, sources)},
            ],
            "options": {"temperature": 0.03, "num_predict": 1900, "num_ctx": 12288},
        },
        timeout=60 * 15,
    )
    response.raise_for_status()
    return json.loads(str(response.json().get("message", {}).get("content", "")))


base.MODEL = model_manager.COACH_MODEL
base.call_ollama = call_ollama

if __name__ == "__main__":
    raise SystemExit(base.main())
