"""Ordinary athlete-facing conversation using the local 4B model.

Simple factual questions should normally be answered by deterministic tools before
this module is called. This is the natural-language middle layer: more capable than
the 1.7B parser, but deliberately lighter than the 8B deep/weekly coach.
"""

from __future__ import annotations

import json

import requests

import coach_chat_fast as fast
import model_manager

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"


def answer(message: str) -> str:
    ready, info = model_manager.chat_model_ready()
    if not ready:
        return info
    context = fast.fast_context()
    prompt = f"""Du er brugerens personlige løbetræner. Svar naturligt dansk, konkret og hjælpsomt.

REGLER:
- Brug kun de vedlagte fakta til påstande om brugerens træning/helbred.
- Primært løbsmål og restitution går foran Garmin-udfordringer.
- Forklar kort hvorfor; undgå robotord som KEEP/ADJUST.
- Hvis spørgsmålet kræver egentlig analyse over flere uger, og konteksten ikke er nok, sig at den dybe coach-analyse bør bruges i stedet for at gætte.
- Diagnosticér ikke sygdom/skade.
- Påstå aldrig at Garmin er ændret; Garmin-handlinger udføres af særskilte værktøjer.

SPØRGSMÅL:
{message[:1200]}

KOMPAKT COACH-KONTEKST:
{json.dumps(context, ensure_ascii=False, separators=(",", ":"))}
"""
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model_manager.CHAT_MODEL,
            "stream": False,
            "think": False,
            "keep_alive": "45m",
            "messages": [
                {"role": "system", "content": "Du er en konkret personlig løbetræner. Svar kort på dansk og brug kun givne fakta."},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.15, "num_predict": 320, "num_ctx": 4096},
        },
        timeout=60 * 4,
    )
    response.raise_for_status()
    text = str(response.json().get("message", {}).get("content", "")).strip()
    if not text:
        raise RuntimeError("Samtalemodellen returnerede et tomt svar.")
    return text
