"""Offline compatibility tests for workout update fallback.

No network/Garmin calls. These tests make sure the coach never assumes an installed
Garmin client exposes update_workout(), and that the replacement strategy preserves
calendar placement even when Garmin read-back is briefly stale.
"""

from __future__ import annotations

import copy

import calendar_consistency
import workout_replace_compat as compat


class OldGarminClient:
    def upload_workout(self, payload):
        return {"workoutId": 1}


class NewGarminClient(OldGarminClient):
    def update_workout(self, workout_id, payload):
        return {"workoutId": workout_id}


class FakeGarmin:
    def __init__(self):
        self.workouts = {
            10: {"workoutId": 10, "workoutName": "CoachTest-vo2max"},
        }
        self.calendar = [{
            "workout_id": 10,
            "scheduled_workout_id": 100,
            "date": "2026-09-10",
            "title": "CoachTest-vo2max",
        }]
        self.deleted = []
        self.next_workout_id = 20
        self.next_scheduled_id = 200
        self.stale_rows: list[dict] = []
        self.stale_reads_remaining = 0

    def get_workout_by_id(self, workout_id):
        return copy.deepcopy(self.workouts.get(int(workout_id)))

    def upload_workout(self, payload):
        wid = self.next_workout_id
        self.next_workout_id += 1
        row = copy.deepcopy(payload)
        row["workoutId"] = wid
        self.workouts[wid] = row
        return {"workoutId": wid}

    def schedule_workout(self, workout_id, date):
        sid = self.next_scheduled_id
        self.next_scheduled_id += 1
        self.calendar.append({
            "workout_id": int(workout_id),
            "scheduled_workout_id": sid,
            "date": date,
            "title": self.workouts[int(workout_id)].get("workoutName"),
        })
        return {"scheduledWorkoutId": sid}

    def unschedule_workout(self, scheduled_id):
        removed = [
            copy.deepcopy(x) for x in self.calendar
            if str(x.get("scheduled_workout_id")) == str(scheduled_id)
        ]
        self.calendar = [
            x for x in self.calendar
            if str(x.get("scheduled_workout_id")) != str(scheduled_id)
        ]
        # Simulate Garmin acknowledging the write while two subsequent reads still
        # return its cached old calendar row.
        if removed and str(scheduled_id) == "100":
            self.stale_rows = removed
            self.stale_reads_remaining = 2

    def month_rows(self):
        rows = copy.deepcopy(self.calendar)
        if self.stale_reads_remaining > 0:
            rows.extend(copy.deepcopy(self.stale_rows))
            self.stale_reads_remaining -= 1
        return rows

    def delete_workout(self, workout_id):
        self.deleted.append(int(workout_id))
        self.workouts.pop(int(workout_id), None)


def test_transactional_replace_with_stale_calendar_reads() -> None:
    fake = FakeGarmin()
    state = {
        "workout_id": 10,
        "name": "CoachTest-vo2max",
        "recipe": {
            "objective": "vo2max",
            "title": "VO2-maks",
            "stimulus": "test",
            "family": "quality_interval",
            "warmup_min": 12.0,
            "repetitions": 4,
            "work_min": 4.0,
            "recovery_min": 3.0,
            "cooldown_min": 10.0,
        },
        "scheduled_date": "2026-09-10",
        "scheduled_workout_id": 100,
    }
    saved = {}

    original_load = compat.workspace.load
    original_save = compat.workspace.save
    original_build = compat.workspace.build_candidate
    original_login = compat.workspace.login
    original_verify = compat.workspace._verify
    original_month_rows = compat._month_rows
    original_sleep = calendar_consistency.time.sleep
    try:
        compat.workspace.load = lambda path, default: copy.deepcopy(state)
        compat.workspace.save = lambda path, payload: saved.update(copy.deepcopy(payload))
        compat.workspace.build_candidate = lambda intent, old, name: (
            {
                "workoutName": name,
                "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
                "estimatedDurationInSecs": 2400,
                "description": "kortere test",
                "workoutSegments": [{"segmentOrder": 1, "workoutSteps": []}],
            },
            {**old, "cooldown_min": 5.0},
        )
        compat.workspace.login = lambda: fake
        compat.workspace._verify = lambda readback, candidate: (True, "OK", {"actual_execution": {"ok": True}})
        compat._month_rows = lambda api, day: fake.month_rows()
        calendar_consistency.time.sleep = lambda seconds: None

        result = compat.replace_update({"operation": "update_test_workout", "relative_minutes": -10})
    finally:
        compat.workspace.load = original_load
        compat.workspace.save = original_save
        compat.workspace.build_candidate = original_build
        compat.workspace.login = original_login
        compat.workspace._verify = original_verify
        compat._month_rows = original_month_rows
        calendar_consistency.time.sleep = original_sleep

    assert result["workout_id"] == 20, result
    assert saved["workout_id"] == 20, saved
    assert 10 in fake.deleted, fake.deleted
    assert all(str(x.get("workout_id")) != "10" for x in fake.calendar), fake.calendar
    assert any(str(x.get("workout_id")) == "20" and x.get("date") == "2026-09-10" for x in fake.calendar), fake.calendar
    assert saved.get("scheduled_workout_id") == 200, saved
    assert fake.stale_reads_remaining == 0, fake.stale_reads_remaining


def main() -> int:
    assert compat.supports_inplace_update(OldGarminClient()) is False
    assert compat.supports_inplace_update(NewGarminClient()) is True
    print("OK: gammel Garmin-klient uden update_workout opdages")
    print("OK: nyere Garmin-klient med update_workout opdages")
    test_transactional_replace_with_stale_calendar_reads()
    print("OK: erstatningsstrategien tåler forsinket Garmin-kalender read-back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
