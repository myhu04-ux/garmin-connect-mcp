"""Deterministic natural-language operator for real planned Garmin calendar sessions.

This is separate from the historical CoachTest workspace. It lets the athlete say e.g.
'flyt fredagens træning til lørdag', 'fjern torsdagens styrke fra kalenderen', or after
a successful selection 'flyt den til fredag'. Operations are idempotent, use live
Garmin calendar data, and verify read-back. Free-form LLM text never performs writes.

Safety rules:
- never acts on an ambiguous day containing multiple sessions unless the message
  identifies running/strength or an active selection disambiguates it;
- move = ensure exactly one target placement, then remove source/duplicate placements;
- 'fjern fra kalenderen' unschedules only;
- 'slet helt' may delete the workout object only when local coach caches prove that
  the workout is coach-owned. Approved/user masters are never deleted here.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

from garminconnect import Garmin

import calendar_consistency
from calendar_probe import extract_items

ROOT = Path(r"C:\GarminCoach")
DATA = ROOT / "data"
STATE = DATA / "active_calendar_selection.json"
GENERATED = DATA / "generated_plan_workouts.json"
NAMED = DATA / "named_workouts.json"
TEST_STATE = DATA / "active_test_workout.json"

WEEKDAYS = {
    "mandag": 0, "mandagen": 0, "mandags": 0,
    "tirsdag": 1, "tirsdagen": 1, "tirsdags": 1,
    "onsdag": 2, "onsdagen": 2, "onsdags": 2,
    "torsdag": 3, "torsdagen": 3, "torsdags": 3,
    "fredag": 4, "fredagen": 4, "fredags": 4,
    "lørdag": 5, "lørdagen": 5, "lørdags": 5,
    "loerdag": 5, "loerdagen": 5, "loerdags": 5,
    "søndag": 6, "søndagen": 6, "søndags": 6,
    "soendag": 6, "soendagen": 6, "soendags": 6,
}


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def login() -> Garmin:
    token_dir = Path(os.path.expanduser("~/.garminconnect"))
    if not token_dir.exists():
        raise RuntimeError(f"Garmin-tokenmappen mangler: {token_dir}")
    api = Garmin(retry_attempts=0)
    api.login(str(token_dir))
    return api


def normalize(text: str) -> str:
    return " ".join(str(text or "").casefold().strip().split())


def next_weekday(index: int, today: dt.date | None = None, strictly_future: bool = False) -> dt.date:
    today = today or dt.date.today()
    delta = (index - today.weekday()) % 7
    if strictly_future and delta == 0:
        delta = 7
    return today + dt.timedelta(days=delta)


def _weekday_mentions(text: str) -> list[tuple[int, str, int]]:
    lower = normalize(text)
    rows: list[tuple[int, str, int]] = []
    for word, index in WEEKDAYS.items():
        for match in re.finditer(rf"\b{re.escape(word)}\b", lower):
            rows.append((match.start(), word, index))
    rows.sort(key=lambda row: row[0])
    # Variants can match same lexical position; keep the longest/first meaningful one.
    unique: list[tuple[int, str, int]] = []
    seen_positions: set[int] = set()
    for row in rows:
        if row[0] in seen_positions:
            continue
        seen_positions.add(row[0])
        unique.append(row)
    return unique


def _iso_dates(text: str) -> list[tuple[int, dt.date]]:
    rows = []
    for match in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text):
        try:
            rows.append((match.start(), dt.date.fromisoformat(match.group(0))))
        except Exception:
            pass
    return rows


def parse_dates(message: str, today: dt.date | None = None) -> tuple[dt.date | None, dt.date | None]:
    """Return (source, target) for move-like natural Danish.

    Examples:
      'flyt fredagens træning til lørdag' -> Friday, Saturday
      'flyt den fra torsdag til fredag' -> Thursday, Friday
      'læg den på fredag' -> None, Friday
      'slet fredagens træning' -> Friday, None
    """
    today = today or dt.date.today()
    text = normalize(message)
    explicit = _iso_dates(text)
    weekdays = _weekday_mentions(text)

    ordered: list[tuple[int, dt.date]] = list(explicit)
    for pos, word, index in weekdays:
        # 'næste X' means strictly after the upcoming occurrence; otherwise the
        # nearest occurrence on/after today is used.
        before = text[max(0, pos - 8):pos]
        strict = "næste" in before or "naeste" in before
        ordered.append((pos, next_weekday(index, today, strictly_future=strict)))
    ordered.sort(key=lambda row: row[0])

    if not ordered:
        return None, None

    moveish = any(word in text for word in ("flyt", "flytte", "flyttes", "ryk", "rykke", "rykkes"))
    if moveish:
        if len(ordered) >= 2:
            return ordered[0][1], ordered[-1][1]
        # One mentioned date with 'til/på' is the target; source comes from active state.
        only_pos, only_date = ordered[0]
        prefix = text[:only_pos]
        if "til" in prefix or "på" in prefix or "paa" in prefix:
            return None, only_date
        # 'flyt fredagens træning' is incomplete target.
        return only_date, None

    deleteish = any(word in text for word in ("slet", "fjern", "unschedule", "aflys"))
    if deleteish:
        return ordered[0][1], None

    scheduleish = any(word in text for word in ("læg", "laeg", "put", "sæt", "saet", "planlæg", "planlaeg"))
    if scheduleish:
        return None, ordered[-1][1]

    return ordered[0][1], None


def parse_intent(message: str) -> dict[str, Any] | None:
    text = normalize(message)
    if "test" in text and ("træning" in text or "traening" in text or "løb" in text or "loeb" in text):
        return None

    move = any(word in text for word in ("flyt", "flytte", "flyttes", "ryk", "rykke", "rykkes"))
    delete = any(word in text for word in ("slet", "fjern", "aflys", "unschedule"))
    calendar_words = any(word in text for word in ("træning", "traening", "pas", "løb", "loeb", "styrke", "kalender", "den"))
    if not calendar_words or not (move or delete):
        return None

    source_date, target_date = parse_dates(message)
    active = load(STATE, {})
    has_active = isinstance(active, dict) and active.get("workout_id")

    if move:
        if source_date is None and not has_active:
            return None
        if target_date is None:
            return {"operation": "move", "source_date": source_date, "target_date": None, "incomplete": True}
        return {"operation": "move", "source_date": source_date, "target_date": target_date, "incomplete": False}

    complete = any(phrase in text for phrase in (
        "slet helt", "slet træningen helt", "slet traeningen helt", "slet den helt",
        "fjern helt", "slet workout", "slet workoutet",
    ))
    from_calendar = any(phrase in text for phrase in (
        "fra kalender", "af kalender", "i kalenderen", "kalenderen",
    ))
    if source_date is None and not has_active:
        return None
    return {
        "operation": "delete_complete" if complete and not from_calendar else "unschedule",
        "source_date": source_date,
        "target_date": None,
        "incomplete": False,
    }


def _month_span(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    cursor = dt.date(start.year, start.month, 1)
    last = dt.date(end.year, end.month, 1)
    rows = []
    while cursor <= last:
        rows.append((cursor.year, cursor.month))
        if cursor.month == 12:
            cursor = dt.date(cursor.year + 1, 1, 1)
        else:
            cursor = dt.date(cursor.year, cursor.month + 1, 1)
    return rows


def live_calendar(api: Any, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for year, month in _month_span(start, end):
        for item in extract_items(api.get_scheduled_workouts(year, month)):
            day = str(item.get("date") or "")[:10]
            if not (start.isoformat() <= day <= end.isoformat()):
                continue
            sid = str(item.get("scheduled_workout_id") or "")
            key = sid or f"{day}|{item.get('workout_id')}|{item.get('title')}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
    return sorted(rows, key=lambda row: (str(row.get("date") or ""), str(row.get("title") or "")))


def _is_strength(item: dict[str, Any]) -> bool:
    text = f"{item.get('title','')} {item.get('workout_name','')}".casefold()
    return "styrke" in text or "strength" in text or text.endswith("s") and "thytrailw" in text


def _is_running(item: dict[str, Any]) -> bool:
    return not _is_strength(item)


def choose(rows: list[dict[str, Any]], message: str, source_date: dt.date | None, active: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    text = normalize(message)
    candidates = rows
    if source_date:
        candidates = [row for row in candidates if str(row.get("date") or "")[:10] == source_date.isoformat()]
    elif active.get("workout_id"):
        candidates = [row for row in candidates if str(row.get("workout_id") or "") == str(active.get("workout_id"))]
        if active.get("date"):
            same_date = [row for row in candidates if str(row.get("date") or "")[:10] == str(active.get("date"))[:10]]
            if same_date:
                candidates = same_date

    if any(word in text for word in ("styrke", "strength")):
        candidates = [row for row in candidates if _is_strength(row)]
    elif any(word in text for word in ("løb", "loeb", "løbet", "loebet", "løbetræning", "loebetraening")):
        candidates = [row for row in candidates if _is_running(row)]

    if not candidates:
        return None, "Jeg kunne ikke finde en levende Garmin-kalenderpost, der matcher den træning."
    if len(candidates) > 1:
        names = "; ".join(f"{row.get('date')}: {row.get('title') or row.get('workout_id')}" for row in candidates[:5])
        return None, "Der er flere mulige træninger, så jeg ændrer ikke noget endnu: " + names
    return candidates[0], None


def _entry(api: Any, workout_id: Any, day: dt.date) -> list[dict[str, Any]]:
    return [
        row for row in extract_items(api.get_scheduled_workouts(day.year, day.month))
        if str(row.get("workout_id") or "") == str(workout_id or "")
        and str(row.get("date") or "")[:10] == day.isoformat()
    ]


def _sid(api: Any, scheduled_id: Any, day: dt.date) -> dict[str, Any] | None:
    target = str(scheduled_id or "")
    for row in extract_items(api.get_scheduled_workouts(day.year, day.month)):
        if str(row.get("scheduled_workout_id") or "") == target:
            return row
    return None


def save_active(item: dict[str, Any], date: dt.date | None = None) -> None:
    payload = {
        "selected_at": dt.datetime.now().astimezone().isoformat(),
        "workout_id": item.get("workout_id"),
        "scheduled_workout_id": item.get("scheduled_workout_id"),
        "title": item.get("title"),
        "date": (date.isoformat() if date else str(item.get("date") or "")[:10]),
    }
    save(STATE, payload)


def coach_owned(workout_id: Any) -> bool:
    target = str(workout_id or "")
    for path in (GENERATED, NAMED):
        payload = load(path, {})
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if isinstance(entries, dict):
            for row in entries.values():
                if isinstance(row, dict) and str(row.get("workout_id") or "") == target:
                    return True
    test = load(TEST_STATE, {})
    return isinstance(test, dict) and str(test.get("workout_id") or "") == target


def move(api: Any, item: dict[str, Any], target: dt.date) -> str:
    workout_id = item.get("workout_id")
    if not workout_id:
        raise RuntimeError("Kalenderposten mangler workout_id.")
    source = dt.date.fromisoformat(str(item.get("date") or "")[:10])

    # Ensure target exists, then collapse target duplicates to exactly one.
    target_rows = _entry(api, workout_id, target)
    if not target_rows:
        api.schedule_workout(workout_id, target.isoformat())
        ok, value = calendar_consistency.wait_present(lambda: _entry(api, workout_id, target), attempts=8)
        if not ok or not value:
            raise RuntimeError("Garmin bekræftede ikke den nye kalenderplacering ved gentaget read-back.")
        target_rows = list(value)

    keep = target_rows[0]
    for duplicate in target_rows[1:]:
        sid = duplicate.get("scheduled_workout_id")
        if sid:
            api.unschedule_workout(sid)
            ok, _ = calendar_consistency.wait_absent(lambda sid=sid: _sid(api, sid, target), attempts=8)
            if not ok:
                raise RuntimeError("Garmin beholdt en dublet på måldagen efter unschedule.")

    # Remove every source placement for the same workout, unless target == source.
    if source != target:
        for old in _entry(api, workout_id, source):
            sid = old.get("scheduled_workout_id")
            if sid:
                api.unschedule_workout(sid)
                ok, _ = calendar_consistency.wait_absent(lambda sid=sid: _sid(api, sid, source), attempts=8)
                if not ok:
                    raise RuntimeError("Garmin viser stadig den gamle kalenderpost efter gentaget read-back.")

    final_target = _entry(api, workout_id, target)
    final_source = _entry(api, workout_id, source) if source != target else []
    if len(final_target) != 1 or final_source:
        raise RuntimeError(
            f"Flytningen bestod ikke slutkontrollen (mål={len(final_target)}, gammel={len(final_source)})."
        )
    save_active(final_target[0], target)
    return f"Flyttet og verificeret i Garmin: {item.get('title') or 'træningen'} ligger nu kun {target.isoformat()}."


def unschedule(api: Any, item: dict[str, Any]) -> str:
    day = dt.date.fromisoformat(str(item.get("date") or "")[:10])
    workout_id = item.get("workout_id")
    rows = _entry(api, workout_id, day)
    removed = 0
    for row in rows:
        sid = row.get("scheduled_workout_id")
        if not sid:
            continue
        api.unschedule_workout(sid)
        ok, _ = calendar_consistency.wait_absent(lambda sid=sid: _sid(api, sid, day), attempts=8)
        if not ok:
            raise RuntimeError("Garmin viser stadig kalenderposten efter gentaget read-back.")
        removed += 1
    if _entry(api, workout_id, day):
        raise RuntimeError("Kalenderposten er stadig synlig efter slutkontrol.")
    save(STATE, {})
    return f"Fjernet fra Garmin-kalenderen og verificeret ({removed} kalenderpost{'er' if removed != 1 else ''}). Selve workoutet under Træninger er bevaret."


def delete_complete(api: Any, item: dict[str, Any]) -> str:
    workout_id = item.get("workout_id")
    title = item.get("title") or "træningen"
    if not coach_owned(workout_id):
        # Never delete an unproven master/user workout. Calendar removal is still safe.
        detail = unschedule(api, item)
        return detail + " Jeg slettede ikke selve workoutet, fordi det ikke er dokumenteret som coach-ejet."

    selected_day = dt.date.fromisoformat(str(item.get("date") or "")[:10])
    start = selected_day - dt.timedelta(days=62)
    end = selected_day + dt.timedelta(days=124)
    instances = [row for row in live_calendar(api, start, end) if str(row.get("workout_id") or "") == str(workout_id)]
    for row in instances:
        sid = row.get("scheduled_workout_id")
        day = dt.date.fromisoformat(str(row.get("date") or "")[:10])
        if sid:
            api.unschedule_workout(sid)
            ok, _ = calendar_consistency.wait_absent(lambda sid=sid, day=day: _sid(api, sid, day), attempts=8)
            if not ok:
                raise RuntimeError("Jeg stoppede før workout-sletning, fordi en kalenderpost stadig er synlig.")

    # Only after every known placement is gone do we delete the coach-owned workout.
    api.delete_workout(workout_id)
    try:
        api.get_workout_by_id(workout_id)
    except Exception:
        save(STATE, {})
        return f"Slettet helt og verificeret i Garmin: {title} er væk fra kalenderen og fra Træninger."
    raise RuntimeError("Garmin returnerer stadig workoutet efter delete_workout; jeg rapporterer derfor ikke sletningen som gennemført.")


def handle(message: str) -> str | None:
    intent = parse_intent(message)
    if intent is None:
        return None
    if intent.get("incomplete"):
        return "Jeg forstår, hvilken træning du mener, men mangler måldagen. Hvilken dag skal den flyttes til?"

    api = login()
    today = dt.date.today()
    source_date = intent.get("source_date")
    target_date = intent.get("target_date")
    active = load(STATE, {})
    start_candidates = [today - dt.timedelta(days=7)]
    end_candidates = [today + dt.timedelta(days=21)]
    if isinstance(source_date, dt.date):
        start_candidates.append(source_date - dt.timedelta(days=2))
        end_candidates.append(source_date + dt.timedelta(days=2))
    if isinstance(target_date, dt.date):
        start_candidates.append(target_date - dt.timedelta(days=2))
        end_candidates.append(target_date + dt.timedelta(days=2))
    rows = live_calendar(api, min(start_candidates), max(end_candidates))
    item, error = choose(rows, message, source_date if isinstance(source_date, dt.date) else None, active if isinstance(active, dict) else {})
    if error:
        return error
    assert item is not None
    save_active(item)

    operation = intent.get("operation")
    if operation == "move":
        assert isinstance(target_date, dt.date)
        return move(api, item, target_date)
    if operation == "unschedule":
        return unschedule(api, item)
    if operation == "delete_complete":
        return delete_complete(api, item)
    return None
