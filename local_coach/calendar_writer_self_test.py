"""Offline tests for the automatic calendar writer transaction semantics."""

from __future__ import annotations

import copy
import datetime as dt

import calendar_consistency
import calendar_writer_safe as safe


class FakeGarmin:
    def __init__(self, old_date: str):
        self.rows = [{
            "calendarDate": old_date,
            "workoutName": "Old workout",
            "workoutId": 10,
            "scheduledWorkoutId": 100,
            "itemType": "WORKOUT",
        }]
        self.next_sid = 200
        self.stale_rows: list[dict] = []
        self.stale_reads = 0

    def schedule_workout(self, workout_id, date):
        sid = self.next_sid
        self.next_sid += 1
        self.rows.append({
            "calendarDate": date,
            "workoutName": "New workout",
            "workoutId": int(workout_id),
            "scheduledWorkoutId": sid,
            "itemType": "WORKOUT",
        })
        return {"scheduledWorkoutId": sid}

    def unschedule_workout(self, scheduled_id):
        removed = [copy.deepcopy(x) for x in self.rows if str(x.get("scheduledWorkoutId")) == str(scheduled_id)]
        self.rows = [x for x in self.rows if str(x.get("scheduledWorkoutId")) != str(scheduled_id)]
        if removed:
            self.stale_rows = removed
            self.stale_reads = 2

    def get_scheduled_workouts(self, year, month):
        visible = [
            copy.deepcopy(x) for x in self.rows
            if str(x.get("calendarDate", ""))[:7] == f"{year:04d}-{month:02d}"
        ]
        if self.stale_reads > 0:
            visible.extend([
                copy.deepcopy(x) for x in self.stale_rows
                if str(x.get("calendarDate", ""))[:7] == f"{year:04d}-{month:02d}"
            ])
            self.stale_reads -= 1
        return visible


def main() -> int:
    today = dt.date.today()
    old_day = today + dt.timedelta(days=2)
    target_day = today + dt.timedelta(days=3)
    fake = FakeGarmin(old_day.isoformat())
    normalized_calendar = {"items": [{
        "date": old_day.isoformat(),
        "title": "Old workout",
        "workout_id": 10,
        "scheduled_workout_id": 100,
    }]}
    old_item = normalized_calendar["items"][0]

    original_sleep = calendar_consistency.time.sleep
    try:
        calendar_consistency.time.sleep = lambda seconds: None
        result = safe.transactional_schedule_then_unschedule(
            fake,
            normalized_calendar,
            20,
            target_day.isoformat(),
            old_item,
        )
    finally:
        calendar_consistency.time.sleep = original_sleep

    assert result["scheduled"] is True, result
    assert result["old_unscheduled"] is True, result
    assert result["scheduled_workout_id"] == 200, result
    assert all(str(x.get("scheduledWorkoutId")) != "100" for x in fake.rows), fake.rows
    assert any(str(x.get("workoutId")) == "20" and x.get("calendarDate") == target_day.isoformat() for x in fake.rows), fake.rows
    assert fake.stale_reads == 0, fake.stale_reads
    print("OK: automatisk kalendertransaktion venter på ny placering før gammel fjernes")
    print("OK: forsinket Garmin read-back giver ikke falsk rollback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
