"""Deterministic natural-language routing for race goals and online plan research.

The athlete should be able to speak as naturally as in a strong cloud assistant.
Only explicit intent to CHANGE/SET the primary goal or to ANALYSE an external training
plan triggers local research. Casual mentions of a race/program never mutate profile
state. The research itself remains sourced and is executed by the 8B expert modules.
"""

from __future__ import annotations

import re
from typing import Any

URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


def normalize(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _strip_prefix(message: str, patterns: tuple[str, ...]) -> str:
    value = normalize(message)
    lower = value.casefold()
    for pattern in patterns:
        index = lower.find(pattern)
        if index >= 0:
            return value[index + len(pattern):].strip(" .,:;-\t")
    return ""


def goal_intent(message: str) -> dict[str, Any] | None:
    text = normalize(message)
    lower = text.casefold()
    # Explicit state-changing language only. 'Jeg træner mod Thy Trail' is a fact,
    # not permission to replace the goal.
    phrases = (
        "jeg vil træne mod ", "jeg vil traene mod ",
        "jeg vil gerne træne mod ", "jeg vil gerne traene mod ",
        "mit nye mål er ", "mit nye maal er ",
        "skift mit mål til ", "skift mit maal til ",
        "sæt mit mål til ", "saet mit maal til ",
        "sæt mål til ", "saet maal til ",
        "nyt løbsmål: ", "nyt loebsmaal: ",
        "gør mit mål til ", "goer mit maal til ",
    )
    target = _strip_prefix(text, phrases)
    if not target:
        return None
    # Remove common trailing action clauses while keeping useful date/location words.
    target = re.split(
        r"\s+(?:og\s+)?(?:lav|tilpas|opdater|planlæg|planlaeg)\s+(?:min|mit|en|træningen|traeningen|planen)\b",
        target,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .,:;-")
    if len(target) < 3:
        return None
    return {"operation": "set_goal", "query": target[:500], "explicit": True}


def plan_intent(message: str) -> dict[str, Any] | None:
    text = normalize(message)
    lower = text.casefold()
    urls = URL_RE.findall(text)
    plan_words = (
        "løbeprogram", "loebeprogram", "løbeplan", "loebeplan", "træningsprogram", "traeningsprogram",
        "træningsplan", "traeningsplan", "running plan", "marathon plan", "trail plan",
    )
    action_words = (
        "analyser", "analysér", "undersøg", "undersoeg", "brug", "find", "læs", "laes",
        "tag inspiration", "brug som inspiration", "lær af", "laer af",
    )
    has_plan_word = any(word in lower for word in plan_words)
    has_action = any(word in lower for word in action_words)

    if urls and (has_plan_word or has_action):
        return {"operation": "research_plan", "query": urls[0].rstrip(".,;)")[:1000], "explicit": True}

    phrases = (
        "analyser løbeprogrammet ", "analysér løbeprogrammet ", "analyser loebeprogrammet ",
        "undersøg løbeprogrammet ", "undersoeg loebeprogrammet ",
        "brug løbeprogrammet ", "brug loebeprogrammet ",
        "brug træningsprogrammet ", "brug traeningsprogrammet ",
        "brug træningsplanen ", "brug traeningsplanen ",
        "find og analyser ", "find og analysér ",
        "brug som inspiration ",
    )
    query = _strip_prefix(text, phrases)
    if query and (has_plan_word or len(query) >= 5):
        return {"operation": "research_plan", "query": query[:1000], "explicit": True}
    return None


def parse(message: str) -> dict[str, Any] | None:
    # Goal state change has priority over plan research if both are somehow present.
    return goal_intent(message) or plan_intent(message)
