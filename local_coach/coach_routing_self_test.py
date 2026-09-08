"""Offline routing tests for the three-role local coach architecture."""

from __future__ import annotations

import coach_chat_agent as agent
import model_manager


def main() -> int:
    assert model_manager.PARSER_MODEL == "qwen3:1.7b"
    assert model_manager.CHAT_MODEL == "qwen3:4b"
    assert model_manager.COACH_MODEL == "qwen3:8b"

    original_direct = agent.fast.direct_tool_answer
    original_chat = agent.conversation_chat.answer
    original_expert = agent.expert_chat.answer
    original_catalog = agent.wants_catalog
    try:
        calls = {"direct": 0, "chat4": 0, "expert8": 0}

        def no_direct(_message: str):
            calls["direct"] += 1
            return None

        def chat_answer(_message: str):
            calls["chat4"] += 1
            return "CHAT4"

        def expert_answer(_message: str):
            calls["expert8"] += 1
            return "EXPERT8"

        agent.fast.direct_tool_answer = no_direct
        agent.conversation_chat.answer = chat_answer
        agent.expert_chat.answer = expert_answer
        agent.wants_catalog = lambda _message: False

        # Ordinary free-form conversation uses 4B, never the parser model.
        result = agent.answer("Hvad synes du er det vigtigste fokus for mig som løber lige nu?")
        assert result == "CHAT4"
        assert calls["chat4"] == 1
        assert calls["expert8"] == 0

        # Multi-week synthesis is escalated to the 8B expert.
        result = agent.answer("Analysér de sidste uger og vurder om jeg er på sporet frem mod Thy Trail.")
        assert result == "EXPERT8"
        assert calls["expert8"] == 1

        # A deterministic tool answer wins before either language model.
        agent.fast.direct_tool_answer = lambda _message: "DIRECT"
        result = agent.answer("Hvad er min restitution?")
        assert result == "DIRECT"
        assert calls["chat4"] == 1
        assert calls["expert8"] == 1

        assert agent.wants_shadow_week(
            "Hvordan skal uge 38 se ud på baggrund af de sidste ugers træning og mit helbred?"
        )
        assert agent.wants_deep_coaching(
            "Analysér de sidste uger og vurder om jeg er på sporet frem mod Thy Trail."
        )

        print("OK: routing = tools -> 4B conversation -> 8B deep/week; 1.7B is not athlete-facing")
        return 0
    finally:
        agent.fast.direct_tool_answer = original_direct
        agent.conversation_chat.answer = original_chat
        agent.expert_chat.answer = original_expert
        agent.wants_catalog = original_catalog


if __name__ == "__main__":
    raise SystemExit(main())
