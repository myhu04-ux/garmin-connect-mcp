"""Offline rollback regression tests for guarded workout resolution."""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import named_workout
import planned_workout_compiler as compiler
import writeback_transaction as tx


class FakeGarmin:
    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self.next_id = 7001
        self.uploads = 0
        self.updates = 0
        self.deletes = 0

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
        return copy.deepcopy(row)

    def delete_workout(self, workout_id):
        wid = int(workout_id)
        self.rows.pop(wid, None)
        self.deletes += 1


def action(repetitions: int = 5, work_min: float = 3.0, name: str = "ThyTrailW4D2") -> dict:
    return {
        "action": "ADD",
        "date": "2026-09-15",
        "plan_name": name,
        "family": "quality_interval",
        "focus": "VO2-maks",
        "selected_template": {"workout_id": 123, "title": "Interval master"},
        "session_spec": {
            "goal": "vo2max",
            "total_minutes": 50,
            "warmup_min": 12,
            "repetitions": repetitions,
            "work_min": work_min,
            "recovery_min": 2,
            "cooldown_min": 10,
            "effort": "kontrolleret hårdt",
        },
    }


def main() -> int:
    api = FakeGarmin()
    with tempfile.TemporaryDirectory() as temp:
        old_compiler_cache = compiler.CACHE
        old_named_cache = named_workout.CACHE
        compiler.CACHE = Path(temp) / "generated.json"
        named_workout.CACHE = Path(temp) / "named.json"
        try:
            # 1) A newly-created target must disappear completely on rollback.
            wid, meta = tx.resolve(api, action())
            assert meta.get("created") is True
            assert tx.pending(wid)
            assert int(wid) in api.rows
            ok, detail = tx.rollback(api, wid)
            assert ok, detail
            assert int(wid) not in api.rows
            assert not tx.pending(wid)
            assert compiler.load(compiler.CACHE, {}) == {}

            # 2) An existing coach workout changed for a later action must restore
            #    its exact previous execution/name and previous cache on rollback.
            base_wid, _ = compiler.ensure_generated_workout(api, action())
            original_raw = api.get_workout_by_id(base_wid)
            original_cache = copy.deepcopy(compiler.load(compiler.CACHE, {}))
            changed = action(repetitions=4, work_min=4.0)
            changed_wid, meta = tx.resolve(api, changed)
            assert changed_wid == base_wid
            assert meta.get("updated") is True
            assert api.get_workout_by_id(base_wid) != original_raw
            ok, detail = tx.rollback(api, base_wid)
            assert ok, detail
            assert api.get_workout_by_id(base_wid) == original_raw
            assert compiler.load(compiler.CACHE, {}) == original_cache

            # 3) A plain MOVE reusing an unmodified workout/master must never call
            #    update_workout during rollback.
            master_id = 8888
            api.rows[master_id] = {
                "workoutId": master_id,
                "workoutName": "Approved master",
                "sportType": {"sportTypeKey": "running"},
                "workoutSegments": [{
                    "segmentOrder": 1,
                    "sportType": {"sportTypeKey": "running"},
                    "workoutSteps": [{
                        "type": "ExecutableStepDTO",
                        "stepOrder": 1,
                        "stepType": {"stepTypeKey": "interval"},
                        "endCondition": {"conditionTypeKey": "time"},
                        "endConditionValue": 1800,
                        "targetType": {"workoutTargetTypeKey": "no.target"},
                    }],
                }],
            }
            before_updates = api.updates
            move = {
                "action": "MOVE",
                "source_date": "2026-09-17",
                "source_workout_id": master_id,
                "date": "2026-09-18",
                "plan_name": "ThyTrailW4D5",
            }
            resolved, _meta = tx.resolve(api, move)
            # With no approved local clone mapping the compiler may simply reuse
            # the source id. Whatever it resolves, rollback must not rewrite the
            # source when metadata says it was not updated/renamed.
            ok, detail = tx.rollback(api, resolved)
            assert ok, detail
            assert api.updates == before_updates
            assert api.get_workout_by_id(master_id)["workoutName"] == "Approved master"

            print("OK: workout transaction rollback create/update/master-safety tests bestået")
            return 0
        finally:
            compiler.CACHE = old_compiler_cache
            named_workout.CACHE = old_named_cache


if __name__ == "__main__":
    raise SystemExit(main())
