"""Use the expert local model to extract sourced principles from an online running plan."""

from __future__ import annotations

import json

import requests

import model_manager
import plan_research as base


def call_ollama(user_input: str, sources: list[dict], model: str) -> dict:
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
                    "content": "Svar kun med valid JSON på dansk. Udtræk kildeunderstøttede træningsprincipper, ikke en kopieret ugeplan.",
                },
                {"role": "user", "content": base.prompt(user_input, sources)},
            ],
            "options": {"temperature": 0.03, "num_predict": 1800, "num_ctx": 12288},
        },
        timeout=60 * 15,
    )
    response.raise_for_status()
    return json.loads(str(response.json().get("message", {}).get("content", "")))


base.MODEL = model_manager.COACH_MODEL
base.call_ollama = call_ollama

if __name__ == "__main__":
    raise SystemExit(base.main())
