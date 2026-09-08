"""Thinking-mode wrapper for the normal rolling 7-day expert planner.

The scheduled adaptive decision is allowed to take longer than ordinary chat. Qwen3's
thinking trace remains private/not persisted; only the final structured JSON proposal
is passed to the deterministic validator and guarded Garmin writer.
"""

from __future__ import annotations

import json
from typing import Any

import requests

import adaptive_preview_expert as base
import model_manager


def call_model(
    context: dict[str, Any],
    repair_issues: list[str] | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ready, info = model_manager.coach_model_ready()
    if not ready:
        raise RuntimeError(info)
    response = requests.post(
        base.OLLAMA_URL,
        json={
            "model": model_manager.COACH_MODEL,
            "stream": False,
            "think": True,
            "format": "json",
            "keep_alive": "45m",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Du er en evidensbaseret personlig løbetræner. Tænk kritisk over multi-uge belastning, "
                        "helbredstrend, løbsspecificitet og sikker progression. Slut kun med valid JSON."
                    ),
                },
                {"role": "user", "content": base.model_prompt(context, repair_issues, previous)},
            ],
            "options": {
                "temperature": 0.08 if not repair_issues else 0.03,
                "num_predict": 3600,
                "num_ctx": 12288,
            },
        },
        timeout=60 * 20,
    )
    response.raise_for_status()
    message = response.json().get("message") or {}
    raw = str(message.get("content") or "").strip()
    if not raw:
        raise RuntimeError("Ekspertmodellen tænkte, men returnerede intet JSON-svar.")
    return json.loads(raw)


base.call_model = call_model

generate = base.generate

if __name__ == "__main__":
    raise SystemExit(base.main())
