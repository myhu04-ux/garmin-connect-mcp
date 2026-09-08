"""Compatibility entrypoint for the final local Garmin coach.

V3 adds deterministic operations on real planned Garmin sessions on top of
sourced goal/plan research and the stable 4B/8B coaching core. The filename
stays unchanged so existing Windows startup tasks continue to work.
"""

from __future__ import annotations

import threading

import coach_chat_agent_core as core
import coach_chat_agent_v3 as app
import model_manager

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
