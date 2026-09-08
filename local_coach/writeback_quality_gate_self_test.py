"""Offline tests for the expert-plan automatic write-back quality gate."""

from __future__ import annotations

import model_manager
import writeback_quality_gate as gate


def good_plan() -> dict:
    return {
        "planning_mode": "adaptive_rolling_7d",
        "input_fingerprint": "abc123",
        "source": model_manager.COACH_MODEL,
        "stale_for_current_input": False,
        "preview_only": True,
        "quality_review": {
            "passed": True,
            "remaining_issues": [],
            "repair_used": False,
        },
    }


def main() -> int:
    plan = good_plan()
    ok, reasons = gate.allowed(plan)
    assert ok is True, reasons

    stale = good_plan()
    stale["stale_for_current_input"] = True
    assert gate.allowed(stale)[0] is False

    weak = good_plan()
    weak["quality_review"] = {"passed": False, "remaining_issues": ["for mange hårde pas"]}
    ok, reasons = gate.allowed(weak)
    assert ok is False
    assert any("for mange hårde pas" in reason for reason in reasons)

    fallback = good_plan()
    fallback["source"] = "deterministic_fallback"
    assert gate.allowed(fallback)[0] is False

    no_fingerprint = good_plan()
    no_fingerprint.pop("input_fingerprint")
    assert gate.allowed(no_fingerprint)[0] is False

    print("OK: automatic write-back requires fresh fingerprinted expert plan with passed quality review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
