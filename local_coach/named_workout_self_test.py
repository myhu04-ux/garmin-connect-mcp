"""Offline read-back tests for named copies of approved Garmin masters."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import named_workout


MASTER_ID = 1234
MASTER_RAW = {
    "workoutId": MASTER_ID,
    "ownerId": 99,
    "workoutName": "Styrke - Benpower & Hoftemobilitet",
    "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
    "workoutSegments": [{
        "segmentOrder": 1,
        "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
        "workoutSteps": [{
            "type": "ExecutableStepDTO",
            "stepId": 88,
            "stepOrder": 1,
            "stepType": {"stepTypeKey": "interval"},
            "endCondition": {"conditionTypeKey": "reps"},
            "endConditionValue": 10,
            "targetType": {"workoutTargetTypeKey": "no.target"},
            "category": "squat",
            "exerciseName": "body_weight_squat",
        }],
    }],
}


class FakeGarmin:
    def __init__(self) -> None:
        self.rows: dict[int, dict] = {MASTER_ID: copy.deepcopy(MASTER_RAW)}
        self.next_id = 5000
        self.uploads = 0
        self.updates: list[int] = []
        self.deletes: list[int] = []

    def upload_workout(self, payload: dict):
        wid = self.next_id
        self.next_id += 1
        row = copy.deepcopy(payload)
        row["workoutId"] = wid
        # Simulate harmless Garmin metadata injection.
        row["ownerId"] = 777
        self.rows[wid] = row
        self.uploads += 1
        return {"workoutId": wid}

    def get_workout_by_id(self, workout_id):
        wid = int(workout_id)
        if wid not in self.rows:
            raise RuntimeError("not found")
        return copy.deepcopy(self.rows[wid])

    def update_workout(self, workout_id, payload: dict):
        wid = int(workout_id)
        if wid == MASTER_ID:
            raise AssertionError("Approved master must never be updated")
        row = copy.deepcopy(payload)
        row["workoutId"] = wid
        self.rows[wid] = row
        self.updates.append(wid)
        return copy.deepcopy(row)

    def delete_workout(self, workout_id):
        wid = int(workout_id)
        if wid == MASTER_ID:
            raise AssertionError("Approved master must never be deleted")
        self.rows.pop(wid, None)
        self.deletes.append(wid)


def main() -> int:
    api = FakeGarmin()
    with tempfile.TemporaryDirectory() as temp:
        old_templates = named_workout.TEMPLATES
        old_cache = named_workout.CACHE
        named_workout.TEMPLATES = Path(temp) / "templates.json"
        named_workout.CACHE = Path(temp) / "named.json"
        named_workout.TEMPLATES.write_text(json.dumps({
            "families": {
                "strength_master": [{
                    "workout_id": MASTER_ID,
                    "title": "Styrke - Benpower & Hoftemobilitet",
                    "raw": MASTER_RAW,
                }]
            }
        }), encoding="utf-8")
        try:
            wid, meta = named_workout.ensure_named_workout(api, MASTER_ID, "ThyTrailW4D3S")
            assert meta["created"] is True
            assert meta["readback_ok"] is True
            assert api.uploads == 1
            assert wid != MASTER_ID
            assert api.rows[MASTER_ID] == MASTER_RAW

            same, meta = named_workout.ensure_named_workout(api, MASTER_ID, "ThyTrailW4D3S")
            assert same == wid
            assert meta["cached"] is True
            assert meta["readback_ok"] is True
            assert api.uploads == 1
            assert not api.updates

            # Simulate drift in the coach-owned copy, not in the master.
            api.rows[wid]["workoutSegments"][0]["workoutSteps"][0]["endConditionValue"] = 99
            restored, meta = named_workout.ensure_named_workout(api, MASTER_ID, "ThyTrailW4D3S")
            assert restored == wid
            assert meta["updated"] is True
            assert meta["readback_ok"] is True
            assert api.updates == [wid]
            restored_step = api.rows[wid]["workoutSegments"][0]["workoutSteps"][0]
            assert restored_step["endConditionValue"] == 10
            assert api.rows[MASTER_ID] == MASTER_RAW

            print("OK: named master copies require semantic read-back and never modify the approved master")
            return 0
        finally:
            named_workout.TEMPLATES = old_templates
            named_workout.CACHE = old_cache


if __name__ == "__main__":
    raise SystemExit(main())
