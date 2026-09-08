"""Compatibility entrypoint for the final local Garmin coach agent.

The implementation is split into coach_chat_agent_core (stable Garmin/model
routing) and coach_chat_agent_v2 (natural sourced goal/plan research). Keeping
this filename preserves all existing Windows startup/scheduler configuration.
"""

from __future__ import annotations

import threading

import coach_chat_agent_v2 as app
import model_manager

answer = app.answer
app.base.fast.base.answer = answer

if __name__ == "__main__":
    threading.Thread(target=model_manager.ensure_all_models_background, daemon=True).start()
    raise SystemExit(app.base.fast.base.main())
