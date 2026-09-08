"""Deterministic first-line router for conversational Garmin actions.

Known workout/calendar actions must never fall through to the local LLM. The router
understands common Danish inflections and can expose compound requests such as
"gør den 10 min kortere og flyt den til fredag".
"""

from __future__ import annotations

import re
from typing import Any

import training_intent

MOVE_RE = re.compile(r"\b(?:flyt|flytte|flyttes|flyttet|ryk|rykke|rykkes|rykket|skub|skubbe|skubbes|skubbet)\b", re.I)
ADD_RE = re.compile(r"\b(?:læg|laeg|lægge|laegge|lægges|laegges|sæt|saet|sætte|saette|sættes|saettes|put|putte|puttes|placer|placér|placere|placeres|planlæg|planlaeg|planlægge|planlaegge|schedule)\b", re.I)
REMOVE_CAL_RE = re.compile(r"(?:fjern|fjerne|fjernes|tag|tage|slet|slette)\b.*\bkalender", re.I)
UPDATE_RE = re.compile(r"\b(?:juster|justere|justeres|justér|ændr|ændre|ændres|ret|rette|rettes|forkort|forkorte|forkortes|forlæng|forlænge|forlænges|kortere|længere|skift|skifte|skiftes)\b", re.I)
DELETE_WORKOUT_RE = re.compile(r"\b(?:slet|slette|fjern|fjerne)\b.*\b(?:test(?:løb|pas|workout)|coach[- ]?test)", re.I)
TEST_REF_RE = re.compile(r"\b(?:den|det|test(?:løb|pas|workout)|coach[- ]?test|løbet|passet|workoutet)\b", re.I)
CALENDAR_RE = re.compile(r"\bkalender(?:en)?\b", re.I)


def route(message: str) -> dict[str, Any]:
    text = message.strip()
    lower = text.casefold()
    target_date = training_intent.parse_target_date(lower)
    parsed = training_intent.deterministic(text)

    move = bool(MOVE_RE.search(text)) and bool(target_date or CALENDAR_RE.search(text))
    add = bool(ADD_RE.search(text)) and bool(target_date or CALENDAR_RE.search(text))
    remove_calendar = bool(REMOVE_CAL_RE.search(text))
    update = bool(UPDATE_RE.search(text)) and bool(TEST_REF_RE.search(text))
    delete_workout = bool(DELETE_WORKOUT_RE.search(text)) and not remove_calendar

    # Explicit numeric/structural edit details that Python can apply without asking.
    update_fields = (
        "duration_min", "distance_km", "repetitions", "work_min", "recovery_min",
        "warmup_min", "cooldown_min", "relative_minutes",
    )
    has_update_parameters = any(parsed.get(k) is not None for k in update_fields)

    calendar_operation = None
    if remove_calendar:
        calendar_operation = "unschedule_test_workout"
    elif move:
        calendar_operation = "move_test_workout"
    elif add:
        calendar_operation = "schedule_test_workout"

    actionish = bool(calendar_operation or update or delete_workout)
    incomplete_calendar = bool((MOVE_RE.search(text) or ADD_RE.search(text)) and CALENDAR_RE.search(text) and not target_date and not remove_calendar)

    return {
        "message": text,
        "actionish": actionish,
        "calendar_operation": calendar_operation,
        "target_date": target_date,
        "update_requested": update,
        "has_update_parameters": has_update_parameters,
        "delete_workout": delete_workout,
        "incomplete_calendar": incomplete_calendar,
        "parsed_intent": parsed,
    }
