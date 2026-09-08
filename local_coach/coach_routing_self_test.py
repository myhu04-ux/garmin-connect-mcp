"""Offline routing tests: athlete-facing free chat must never fall back to 1.7B."""

from __future__ import annotations

import coach_chat_agent as agent


def main() -> int:
    original_direct = agent.fast.direct_tool_answer
    original_expert = agent.expert_chat.answer
    original_catalog = agent.wants_catalog
    try:
        calls = {"direct": 0, "expert": 0}

        def no_direct(_message: str):
            calls["direct"] += 1
            return None

        def expert_answer(_message: str):
            calls["expert"] += 1
            return "EXPERT"

        agent.fast.direct_tool_answer = no_direct
        agent.expert_chat.answer = expert_answer
        agent.wants_catalog = lambda _message: False

        result = agent.answer("Hvad synes du er det vigtigste fokus for mig som løber lige nu?")
        assert result == "EXPERT"
        assert calls["expert"] == 1

        # A deterministic tool answer wins before the expert model and therefore
        # remains instant for simple facts.
        agent.fast.direct_tool_answer = lambda _message: "DIRECT"
        result = agent.answer("Hvad er min restitution?")
        assert result == "DIRECT"
        assert calls["expert"] == 1

        assert agent.wants_shadow_week(
            "Hvordan skal uge 38 se ud på baggrund af de sidste ugers træning og mit helbred?"
        )
        assert agent.wants_deep_coaching(
            "Analysér de sidste uger og vurder om jeg er på sporet frem mod Thy Trail."
        )

        print("OK: athlete-facing free chat routes to deterministic tools or 8B expert, never 1.7B")
        return 0
    finally:
        agent.fast.direct_tool_answer = original_direct
        agent.expert_chat.answer = original_expert
        agent.wants_catalog = original_catalog


if __name__ == "__main__":
    raise SystemExit(main())
