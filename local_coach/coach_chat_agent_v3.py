"""Final natural local coach router with real-plan Garmin operations.

Layer order:
1) deterministic move/delete of real planned Garmin sessions (never LLM);
2) sourced natural-language goal/online-plan research;
3) stable core Garmin tools + 4B chat + 8B deep/week coaching.

The historical CoachTest workflow is intentionally left to the core router whenever a
message explicitly says 'test'.
"""

from __future__ import annotations

import threading

import coach_chat_agent_v2 as previous
import model_manager
import plan_calendar_operator


def answer(message: str) -> str:
    intent = plan_calendar_operator.parse_intent(message)
    if intent is not None:
        previous.base.stage("Garmin-operatøren finder den levende kalenderpost og verificerer ændringen…")
        try:
            result = plan_calendar_operator.handle(message)
        except Exception as exc:
            return f"Jeg forstod kalenderhandlingen, men stoppede fordi den ikke kunne verificeres sikkert: {exc}"
        if result is not None:
            return result
    return previous.answer(message)


previous.base.fast.base.answer = answer

if __name__ == "__main__":
    threading.Thread(target=model_manager.ensure_all_models_background, daemon=True).start()
    raise SystemExit(previous.base.fast.base.main())
