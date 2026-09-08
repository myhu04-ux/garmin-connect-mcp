"""Offline regression tests for deterministic real-plan Garmin calendar operations."""

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

import plan_calendar_operator as op


class FakeApi:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.next_sid = 100
        self.deleted_workouts: list[int] = []

    def schedule_workout(self, workout_id, date):
        row = {
            "date": str(date),
            "workout_id": workout_id,
            "scheduled_workout_id": self.next_sid,
            "title": "ThyTrailW4D5",
        }
        self.next_sid += 1
        self.rows.append(row)
        return {"scheduledWorkoutId": row["scheduled_workout_id"]}

    def unschedule_workout(self, scheduled_id):
        self.rows = [row for row in self.rows if str(row.get("scheduled_workout_id")) != str(scheduled_id)]

    def delete_workout(self, workout_id):
        self.deleted_workouts.append(int(workout_id))

    def get_workout_by_id(self, workout_id):
        if int(workout_id) in self.deleted_workouts:
            raise RuntimeError("not found")
        return {"workoutId": workout_id, "workoutName": "Coach owned"}


def main() -> int:
    today = dt.date(2026, 9, 8)  # Tuesday
    source, target = op.parse_dates("flyt fredagens træning til lørdag", today=today)
    assert source == dt.date(2026, 9, 11), (source, target)
    assert target == dt.date(2026, 9, 12), (source, target)

    source, target = op.parse_dates("flyt den fra torsdag til fredag", today=today)
    assert source == dt.date(2026, 9, 10)
    assert target == dt.date(2026, 9, 11)

    parsed = op.parse_intent("slet fredagens træning helt")
    assert parsed and parsed["operation"] == "delete_complete"
    assert op.parse_intent("slet test-træningen helt") is None
    assert op.parse_intent("jeg løb fredag") is None

    api = FakeApi()
    workout_id = 55
    source_day = dt.date(2026, 9, 11)
    target_day = dt.date(2026, 9, 12)
    source_item = {
        "date": source_day.isoformat(),
        "workout_id": workout_id,
        "scheduled_workout_id": 1,
        "title": "ThyTrailW4D5",
    }
    api.rows = [dict(source_item)]

    old_entry = op._entry
    old_sid = op._sid
    old_state = op.STATE
    old_owned = op.coach_owned
    old_live = op.live_calendar
    try:
        with tempfile.TemporaryDirectory() as temp:
            op.STATE = Path(temp) / "selection.json"

            def fake_entry(_api, wid, day):
                return [
                    dict(row) for row in api.rows
                    if str(row.get("workout_id")) == str(wid)
                    and str(row.get("date"))[:10] == day.isoformat()
                ]

            def fake_sid(_api, sid, day):
                for row in api.rows:
                    if str(row.get("scheduled_workout_id")) == str(sid) and str(row.get("date"))[:10] == day.isoformat():
                        return dict(row)
                return None

            op._entry = fake_entry
            op._sid = fake_sid

            # First move creates Saturday and removes Friday.
            text = op.move(api, source_item, target_day)
            assert "verificeret" in text
            assert len(fake_entry(api, workout_id, target_day)) == 1
            assert len(fake_entry(api, workout_id, source_day)) == 0

            # Simulate a duplicate target and an old source reappearing; move must
            # reconcile to exactly one target, not add yet another copy.
            keep = fake_entry(api, workout_id, target_day)[0]
            api.rows.append({
                "date": target_day.isoformat(), "workout_id": workout_id,
                "scheduled_workout_id": 222, "title": "ThyTrailW4D5",
            })
            api.rows.append({
                "date": source_day.isoformat(), "workout_id": workout_id,
                "scheduled_workout_id": 223, "title": "ThyTrailW4D5",
            })
            text = op.move(api, {**source_item, "scheduled_workout_id": 223}, target_day)
            assert len(fake_entry(api, workout_id, target_day)) == 1
            assert len(fake_entry(api, workout_id, source_day)) == 0
            assert fake_entry(api, workout_id, target_day)[0]["scheduled_workout_id"] in {keep["scheduled_workout_id"], 222}

            # Complete delete of a coach-owned workout removes placements before
            # deleting the workout object.
            op.coach_owned = lambda wid: str(wid) == str(workout_id)
            op.live_calendar = lambda _api, _start, _end: [dict(row) for row in api.rows]
            selected = fake_entry(api, workout_id, target_day)[0]
            text = op.delete_complete(api, selected)
            assert "Slettet helt" in text
            assert workout_id in api.deleted_workouts
            assert not fake_entry(api, workout_id, target_day)

            # Unowned workout is never deleted; only the selected calendar entry is removed.
            other_id = 77
            other = {
                "date": target_day.isoformat(), "workout_id": other_id,
                "scheduled_workout_id": 333, "title": "My personal master",
            }
            api.rows.append(dict(other))
            op.coach_owned = lambda _wid: False
            text = op.delete_complete(api, other)
            assert "ikke dokumenteret som coach-ejet" in text
            assert other_id not in api.deleted_workouts
            assert not fake_entry(api, other_id, target_day)

        print("OK: real-plan operator parses Danish, moves idempotently, collapses duplicates, and protects unowned masters")
        return 0
    finally:
        op._entry = old_entry
        op._sid = old_sid
        op.STATE = old_state
        op.coach_owned = old_owned
        op.live_calendar = old_live


if __name__ == "__main__":
    raise SystemExit(main())
