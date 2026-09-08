"""Thinking-mode wrapper around the expert exact-week planner.

Qwen3 supports a separate thinking field in Ollama's chat API. Weekly planning is the
place where extra reasoning time is worth the latency, so this wrapper enables it while
keeping the final answer constrained to JSON. The hidden thinking trace is never stored
or shown to the athlete.
"""

from __future__ import annotations

import json
from typing import Any

import requests

import model_manager
import shadow_week_expert as base


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
                        "Du er en evidensbaseret personlig løbetræner. Tænk kritisk over belastning, "
                        "restitution, løbsmål og progression. Det endelige svar skal kun være valid JSON."
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

handle = base.handle
generate = base.generate
quality_issues = base.quality_issues
personal_response_examples = base.personal_response_examples
sanitize_session_spec = base.sanitize_session_spec

if __name__ == "__main__":
    import sys
    print(base.handle(" ".join(sys.argv[1:])))
