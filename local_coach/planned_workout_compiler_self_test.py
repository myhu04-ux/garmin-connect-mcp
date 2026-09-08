"""Offline transaction tests for expert-generated Garmin workout compilation."""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import planned_workout_compiler as compiler


class FakeGarmin:
    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self.next_id = 9001
        self.uploads = 0
        self.updates = 0

    def upload_workout(self, payload: dict):
        wid = self.next_id
        self.next_id += 1
        row = copy.deepcopy(payload)
        row["workoutId"] = wid
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
        if wid not in self.rows:
            raise RuntimeError("not found")
        row = copy.deepcopy(payload)
        row["workoutId"] = wid
        self.rows[wid] = row
        self.updates += 1
        return row

    def delete_workout(self, workout_id):
        self.rows.pop(int(workout_id), None)


def interval_action(repetitions: int, work_min: float, recovery_min: float) -> dict:
    return {
        "action": "ADD",
        "date": "2026-09-15",
        "plan_name": "ThyTrailW4D2",
        "family": "quality_interval",
        "focus": "VO2-maks og løbeøkonomi",
        "selected_template": {"workout_id": 123, "title": "Interval master"},
        "session_spec": {
            "goal": "vo2max",
            "total_minutes": 50,
            "warmup_min": 12,
            "repetitions": repetitions,
            "work_min": work_min,
            "recovery_min": recovery_min,
            "cooldown_min": 10,
            "effort": "kontrolleret hårdt",
        },
    }


def main() -> int:
    api = FakeGarmin()
    with tempfile.TemporaryDirectory() as tmp:
        old_cache = compiler.CACHE
        old_named_cache = compiler.named_workout.CACHE
        compiler.CACHE = Path(tmp) / "generated.json"
        compiler.named_workout.CACHE = Path(tmp) / "named.json"
        try:
            first = interval_action(5, 3, 2)
            candidate = compiler.build_candidate(first)
            assert candidate["workoutName"] == "ThyTrailW4D2"
            ok, detail = compiler.verify(candidate, candidate)
            assert ok, detail

            wid, meta = compiler.ensure_generated_workout(api, first)
            assert meta["created"] is True
            assert api.uploads == 1
            assert api.updates == 0

            same_wid, meta = compiler.ensure_generated_workout(api, first)
            assert same_wid == wid
            assert meta["cached"] is True
            assert api.uploads == 1
            assert api.updates == 0

            changed = interval_action(4, 4, 3)
            changed_wid, meta = compiler.ensure_generated_workout(api, changed)
            assert changed_wid == wid
            assert meta["updated"] is True
            assert api.uploads == 1
            assert api.updates == 1

            renamed_wid, meta = compiler.rename_generated_workout(api, wid, "ThyTrailW4D5")
            assert renamed_wid == wid
            assert meta["renamed"] is True
            assert api.get_workout_by_id(wid)["workoutName"] == "ThyTrailW4D5"

            distance_action = {
                "action": "ADD",
                "date": "2026-09-20",
                "plan_name": "ThyTrailW4D7",
                "family": "long_trail",
                "focus": "Tid på benene",
                "selected_template": {"workout_id": 456, "title": "Long trail master"},
                "session_spec": {
                    "goal": "endurance",
                    "target_km": 18,
                    "effort": "rolig aerob indsats",
                    "terrain": "trail og ujævnt underlag",
                },
            }
            distance_candidate = compiler.build_candidate(distance_action)
            step = distance_candidate["workoutSegments"][0]["workoutSteps"][0]
            assert step["endCondition"]["conditionTypeKey"] == "distance"
            assert step["endConditionValue"] == 18000.0

            print("OK: generated workout compiler create/cache/update/rename/distance tests bestået")
            return 0
        finally:
            compiler.CACHE = old_cache
            compiler.named_workout.CACHE = old_named_cache


if __name__ == "__main__":
    raise SystemExit(main())
