"""Offline tests for idempotent conversational test-workout calendar operations."""

from __future__ import annotations

import copy
import datetime as dt
import json
import tempfile
from pathlib import Path

import test_workout_calendar as calendar


class FakeGarmin:
    def __init__(self, rows: list[dict], workout_id: int):
        self.rows = [copy.deepcopy(x) for x in rows]
        self.workout_id = workout_id
        self.next_sid = 500
        self.deleted_workouts: set[int] = set()

    def get_scheduled_workouts(self, year, month):
        return [
            copy.deepcopy(x)
            for x in self.rows
            if str(x.get("calendarDate", ""))[:7] == f"{year:04d}-{month:02d}"
        ]

    def get_scheduled_workout_by_id(self, scheduled_id):
        for row in self.rows:
            if str(row.get("scheduledWorkoutId")) == str(scheduled_id):
                return copy.deepcopy(row)
        raise calendar.GarminConnectNotFoundError("scheduled workout not found")

    def schedule_workout(self, workout_id, date_str):
        sid = self.next_sid
        self.next_sid += 1
        row = {
            "calendarDate": date_str,
            "workoutName": "CoachTest-vo2max",
            "workoutId": int(workout_id),
            "scheduledWorkoutId": sid,
            "itemType": "WORKOUT",
        }
        self.rows.append(row)
        return {"scheduledWorkoutId": sid}

    def unschedule_workout(self, scheduled_id):
        before = len(self.rows)
        self.rows = [x for x in self.rows if str(x.get("scheduledWorkoutId")) != str(scheduled_id)]
        if len(self.rows) == before:
            raise calendar.GarminConnectNotFoundError("scheduled workout not found")
        return None

    def get_workout_by_id(self, workout_id):
        if int(workout_id) in self.deleted_workouts:
            raise calendar.GarminConnectNotFoundError("workout not found")
        return {"workoutId": int(workout_id), "workoutName": "CoachTest-vo2max"}

    def delete_workout(self, workout_id):
        self.deleted_workouts.add(int(workout_id))
        return None


def main() -> int:
    today = dt.date.today()
    thursday = today + dt.timedelta(days=2)
    friday = today + dt.timedelta(days=3)
    wid = 77
    rows = [
        {"calendarDate": thursday.isoformat(), "workoutName": "CoachTest-vo2max", "workoutId": wid, "scheduledWorkoutId": 100},
        {"calendarDate": friday.isoformat(), "workoutName": "CoachTest-vo2max", "workoutId": wid, "scheduledWorkoutId": 101},
        {"calendarDate": friday.isoformat(), "workoutName": "CoachTest-vo2max", "workoutId": wid, "scheduledWorkoutId": 102},
    ]
    fake = FakeGarmin(rows, wid)

    original_login = calendar.login
    original_sleep = calendar.time.sleep
    original_state = calendar.STATE
    original_audit = calendar.AUDIT

    with tempfile.TemporaryDirectory() as tmp:
        calendar.STATE = Path(tmp) / "active_test_workout.json"
        calendar.AUDIT = Path(tmp) / "audit.jsonl"
        calendar.STATE.write_text(json.dumps({
            "workout_id": wid,
            "name": "CoachTest-vo2max",
            "scheduled_date": thursday.isoformat(),
            "scheduled_workout_id": 100,
        }), encoding="utf-8")
        calendar.login = lambda: fake
        calendar.time.sleep = lambda seconds: None

        try:
            moved = calendar.schedule_or_move(friday.isoformat())
            live = [x for x in fake.rows if int(x.get("workoutId")) == wid]
            assert len(live) == 1, live
            assert live[0]["calendarDate"] == friday.isoformat(), live
            assert moved["action"] == "moved", moved
            assert moved["removed_count"] == 2, moved
            print("OK: move reconciles Thursday + duplicate Friday to exactly one Friday instance")

            deleted = calendar.delete_completely()
            assert not [x for x in fake.rows if int(x.get("workoutId")) == wid], fake.rows
            assert wid in fake.deleted_workouts, fake.deleted_workouts
            assert not calendar.STATE.exists(), calendar.STATE
            assert deleted["action"] == "deleted_completely", deleted
            print("OK: full delete removes all calendar instances and the workout template")
        finally:
            calendar.login = original_login
            calendar.time.sleep = original_sleep
            calendar.STATE = original_state
            calendar.AUDIT = original_audit

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
