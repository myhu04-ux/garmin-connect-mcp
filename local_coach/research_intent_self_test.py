"""Offline regression tests for natural-language goal / plan research routing."""

from __future__ import annotations

import coach_chat_agent_v2 as agent
import research_intent


def main() -> int:
    goal = research_intent.parse("Jeg vil gerne træne mod H.C. Andersen Marathon 2027")
    assert goal is not None
    assert goal["operation"] == "set_goal"
    assert "H.C. Andersen Marathon" in goal["query"]

    goal2 = research_intent.parse("Skift mit mål til Berlin Marathon 2027 og opdater planen")
    assert goal2 is not None
    assert goal2["operation"] == "set_goal"
    assert goal2["query"].casefold().startswith("berlin marathon")

    # Casual factual mention must never silently mutate the primary goal.
    assert research_intent.parse("Jeg træner mod Thy Trail og løb 10 km i går") is None
    assert research_intent.parse("Hvad kræver Thy Trail af mig?") is None

    plan = research_intent.parse(
        "Analyser dette løbeprogram og brug det som inspiration: https://example.com/marathon-plan"
    )
    assert plan is not None
    assert plan["operation"] == "research_plan"
    assert plan["query"] == "https://example.com/marathon-plan"

    named = research_intent.parse("Brug løbeprogrammet Hal Higdon Intermediate 1 som inspiration")
    assert named is not None
    assert named["operation"] == "research_plan"
    assert "Hal Higdon" in named["query"]

    # Prove the V2 router chooses research before the generic coach path without
    # touching web, Ollama, Garmin or the filesystem.
    original_handle = agent.research_actions.handle
    original_ready = agent.model_manager.coach_model_ready
    original_replan = getattr(agent.base.fast.base, "start_replan", None)
    original_base_answer = agent.base.answer
    try:
        calls = {"research": 0, "fallback": 0}

        agent.model_manager.coach_model_ready = lambda: (True, agent.model_manager.COACH_MODEL)

        def fake_handle(intent):
            calls["research"] += 1
            return f"RESEARCH:{intent['operation']}:{intent['query']}"

        def fake_fallback(_message):
            calls["fallback"] += 1
            return "FALLBACK"

        agent.research_actions.handle = fake_handle
        agent.base.answer = fake_fallback
        agent.base.fast.base.start_replan = lambda: True

        result = agent.answer("Jeg vil træne mod H.C. Andersen Marathon 2027")
        assert result.startswith("RESEARCH:set_goal:")
        assert "adaptive plan opdateres" in result.casefold()
        assert calls == {"research": 1, "fallback": 0}

        result = agent.answer("Hvad er det vigtigste fokus for mig lige nu?")
        assert result == "FALLBACK"
        assert calls["fallback"] == 1

        print("OK: explicit goal/plan research routes before generic coach; casual mentions do not mutate state")
        return 0
    finally:
        agent.research_actions.handle = original_handle
        agent.model_manager.coach_model_ready = original_ready
        agent.base.answer = original_base_answer
        if original_replan is not None:
            agent.base.fast.base.start_replan = original_replan


if __name__ == "__main__":
    raise SystemExit(main())
