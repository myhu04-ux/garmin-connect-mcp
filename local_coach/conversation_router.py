"""Deterministic first-line router for conversational Garmin actions.

Known workout/calendar actions must never fall through to the local LLM. The router
understands common Danish inflections, active-workout references and compound
requests such as "gør den 10 min kortere og flyt den fra torsdag til fredag".
"""

from __future__ import annotations

import re
from typing import Any

import training_intent

MOVE_RE = re.compile(r"\b(?:flyt|flytte|flyttes|flyttet|ryk|rykke|rykkes|rykket|skub|skubbe|skubbes|skubbet)\b", re.I)
ADD_RE = re.compile(r"\b(?:læg|laeg|lægge|laegge|lægges|laegges|sæt|saet|sætte|saette|sættes|saettes|put|putte|puttes|placer|placér|placere|placeres|planlæg|planlaeg|planlægge|planlaegge|schedule)\b", re.I)
REMOVE_CAL_RE = re.compile(r"(?:fjern|fjerne|fjernes|tag|tage|slet|slette)\b.*\bkalender", re.I)
UPDATE_RE = re.compile(r"\b(?:juster|justere|justeres|justér|ændr|ændre|ændres|ret|rette|rettes|forkort|forkorte|forkortes|forlæng|forlænge|forlænges|kortere|længere|skift|skifte|skiftes)\b", re.I)
TEST_NOUN = r"(?:løb(?:et)?|pas(?:set)?|workout(?:et)?|træning(?:en)?|traening(?:en)?)"
TEST_REF_RE = re.compile(rf"\b(?:den|det|test(?:[- ]?{TEST_NOUN})?|coach[- ]?test(?:[- ]?{TEST_NOUN})?|løbet|passet|workoutet|træningen|traeningen)\b", re.I)
EXPLICIT_TEST_DELETE_RE = re.compile(rf"\b(?:slet|slette|fjern|fjerne)\b.*\b(?:test(?:[- ]?{TEST_NOUN})?|coach[- ]?test(?:[- ]?{TEST_NOUN})?)\b", re.I)
ACTIVE_DELETE_RE = re.compile(
    r"\b(?:slet|slette|fjern|fjerne)\b.*\b(?:den|det|træningen|traeningen|passet|løbet|workoutet)\b",
    re.I,
)
FULL_DELETE_HINT_RE = re.compile(r"\b(?:helt|fuldstændig(?:t)?|komplet|fra garmin|fra træninger|fra traeninger)\b", re.I)
CALENDAR_RE = re.compile(r"\bkalender(?:en)?\b", re.I)


def destination_date(text: str) -> str | None:
    """Prefer the destination after 'til' for move phrases with two dates/days."""
    lower = text.casefold()
    parts = re.split(r"\btil\b", lower)
    if len(parts) > 1:
        suffix = parts[-1].strip()
        if suffix:
            parsed = training_intent.parse_target_date(suffix)
            if parsed:
                return parsed
    return training_intent.parse_target_date(lower)


def route(message: str) -> dict[str, Any]:
    text = message.strip()
    target_date = destination_date(text)
    parsed = training_intent.deterministic(text)

    update_fields = (
        "duration_min", "distance_km", "repetitions", "work_min", "recovery_min",
        "warmup_min", "cooldown_min", "relative_minutes",
    )
    has_update_parameters = any(parsed.get(k) is not None for k in update_fields)

    move = bool(MOVE_RE.search(text)) and bool(target_date or CALENDAR_RE.search(text))
    add = bool(ADD_RE.search(text)) and bool(target_date or CALENDAR_RE.search(text))
    remove_calendar = bool(REMOVE_CAL_RE.search(text))
    update = bool(UPDATE_RE.search(text)) and bool(TEST_REF_RE.search(text) or has_update_parameters)

    # "Slet træningen helt" / "fjern den fra Garmin" means the active CoachTest
    # workout itself, not merely its calendar placement. Calendar wording wins.
    delete_workout = False
    if not remove_calendar:
        delete_workout = bool(EXPLICIT_TEST_DELETE_RE.search(text)) or bool(
            ACTIVE_DELETE_RE.search(text) and FULL_DELETE_HINT_RE.search(text)
        )
        # In the guarded test-workout chat, a plain "slet træningen/passset/workoutet"
        # without a calendar reference is also a full delete request.
        if not delete_workout and ACTIVE_DELETE_RE.search(text) and not CALENDAR_RE.search(text):
            delete_workout = True

    calendar_operation = None
    if remove_calendar:
        calendar_operation = "unschedule_test_workout"
    elif move:
        calendar_operation = "move_test_workout"
    elif add:
        calendar_operation = "schedule_test_workout"

    actionish = bool(calendar_operation or update or delete_workout)
    incomplete_calendar = bool(
        (MOVE_RE.search(text) or ADD_RE.search(text))
        and CALENDAR_RE.search(text)
        and not target_date
        and not remove_calendar
    )

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
