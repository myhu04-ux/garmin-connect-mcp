"""Fast athlete-facing wording for scheduled coach refreshes.

The expensive AI reasoning pass is reserved for the actual 7-day planning step and
direct chat. Dashboard wording is generated deterministically from validated facts
and the validated plan, which removes a second Ollama call from every refresh.
"""

from __future__ import annotations

import coach_voice as voice


voice.call_model = lambda state, preview: None

if __name__ == "__main__":
    raise SystemExit(voice.main())
