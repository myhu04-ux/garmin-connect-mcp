"""Faster local planning entrypoint.

Loads coach_preview_v2 safety/context patches, then replaces only the Ollama call
with a shorter generation budget. Planning quality comes primarily from structured
context + deterministic validation; this avoids spending minutes generating verbose
JSON that is discarded by the validator.
"""

from __future__ import annotations

import json
from typing import Any

import requests

import coach_preview as base
import coach_preview_v2  # noqa: F401  applies build_context/validate patches


def call_model(context: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "model": base.MODEL,
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": "Svar kun med kompakt valid JSON på dansk. Prioritér restitution, løbsmål, faktisk træning og sikker progression. Garmin-udfordringer er sekundære mål.",
            },
            {"role": "user", "content": base.prompt(context)},
        ],
        "options": {"temperature": 0.08, "num_predict": 950},
    }
    response = requests.post(base.OLLAMA_URL, json=payload, timeout=150)
    response.raise_for_status()
    return json.loads(response.json().get("message", {}).get("content", ""))


base.call_model = call_model

if __name__ == "__main__":
    raise SystemExit(base.main())
