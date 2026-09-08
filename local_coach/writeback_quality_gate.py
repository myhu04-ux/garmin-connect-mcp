"""Quality gate between adaptive planning and automatic Garmin mutation.

Technical validation alone is not enough. Automatic write-back may only execute a
fresh, fingerprinted, expert-generated adaptive plan whose coaching quality review has
passed. Explicit --test-one remains a separate plumbing test and can bypass this gate.
"""

from __future__ import annotations

from typing import Any

import model_manager


def blocking_reasons(preview: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not isinstance(preview, dict) or not preview:
        return ["planen mangler"]

    if preview.get("stale_for_current_input"):
        reasons.append("planen er markeret som stale for de aktuelle data")
    if preview.get("planning_mode") != "adaptive_rolling_7d":
        reasons.append("planen er ikke den adaptive rolling-7d ekspertplan")
    if not preview.get("input_fingerprint"):
        reasons.append("planen mangler input-fingerprint")

    source = str(preview.get("source") or "")
    if source != model_manager.COACH_MODEL:
        reasons.append(f"planen kommer ikke fra ekspertmodellen {model_manager.COACH_MODEL}")

    quality = preview.get("quality_review")
    if not isinstance(quality, dict):
        reasons.append("planen mangler quality_review")
    else:
        if quality.get("passed") is not True:
            remaining = quality.get("remaining_issues") or []
            detail = "; ".join(str(item) for item in remaining[:4])
            reasons.append("quality_review er ikke bestået" + (f": {detail}" if detail else ""))

    if preview.get("preview_only") is not True:
        reasons.append("planner-output er ikke markeret preview_only")

    return reasons


def allowed(preview: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = blocking_reasons(preview)
    return not reasons, reasons
