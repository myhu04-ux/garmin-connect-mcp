"""Compatibility entrypoint for the final local Garmin coach agent.

The implementation is split into coach_chat_agent_core (stable Garmin/model
routing) and coach_chat_agent_v2 (natural sourced goal/plan research). This
filename is preserved for existing Windows startup/scheduler configuration.
"""

from __future__ import annotations

import threading

import coach_chat_agent_core as core
import coach_chat_agent_v2 as app
import model_manager

# Compatibility exports used by regression tests and any older local code.
fast = core.fast
conversation_chat = core.conversation_chat
expert_chat = core.expert_chat
wants_catalog = core.wants_catalog
wants_shadow_week = core.wants_shadow_week
wants_deep_coaching = core.wants_deep_coaching
stage = core.stage
answer = app.answer

fast.base.answer = answer

if __name__ == "__main__":
    threading.Thread(target=model_manager.ensure_all_models_background, daemon=True).start()
    raise SystemExit(fast.base.main())
