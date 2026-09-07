"""Naming and position helpers for an active training block.

The plan position is independent of ISO calendar week numbers. An athlete can say
"this Monday starts training week 3"; from then on the week number increments
automatically every Monday. D1=Monday ... D7=Sunday.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any


def monday_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def safe_code(value: Any, fallback: str = "RunPlan") -> str:
    raw = re.sub(r"[^A-Za-z0-9]", "", str(value or ""))
    return raw[:18] or fallback


def derive_code(event_name: Any) -> str:
    text = str(event_name or "").lower()
    if "thy" in text and "trail" in text:
        return "ThyTrail"
    if "anders" in text and "marathon" in text:
        return "HCAMarathon"
    words = re.findall(r"[A-Za-z0-9]+", str(event_name or ""))
    if not words:
        return "RunPlan"
    joined = "".join(w[:1].upper() + w[1:] for w in words[:3])
    return safe_code(joined)


def tracking(profile: dict[str, Any], event: dict[str, Any], today: dt.date | None = None) -> dict[str, Any]:
    today = today or dt.date.today()
    code = safe_code(profile.get("plan_code") or derive_code(event.get("name") or event.get("event_name")))
    raw_anchor = profile.get("plan_anchor_monday")
    try:
        anchor = dt.date.fromisoformat(str(raw_anchor))
        anchor = monday_of(anchor)
    except Exception:
        anchor = monday_of(today)
    try:
        anchor_week = max(1, int(profile.get("plan_anchor_week") or 1))
    except Exception:
        anchor_week = 1
    return {"code": code, "anchor_monday": anchor.isoformat(), "anchor_week": anchor_week}


def position_for(date_value: str | dt.date, profile: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    day = date_value if isinstance(date_value, dt.date) else dt.date.fromisoformat(str(date_value)[:10])
    cfg = tracking(profile, event, today=day)
    anchor = dt.date.fromisoformat(cfg["anchor_monday"])
    week_offset = (monday_of(day) - anchor).days // 7
    week = max(1, int(cfg["anchor_week"]) + week_offset)
    day_no = day.weekday() + 1
    return {
        "code": cfg["code"],
        "week": week,
        "day": day_no,
        "name": f"{cfg['code']}W{week}D{day_no}",
        "date": day.isoformat(),
    }
