"""Offline compatibility tests for workout update fallback.

No network/Garmin calls. These tests make sure the coach never assumes an installed
Garmin client exposes update_workout().
"""

from __future__ import annotations

import workout_replace_compat


class OldGarminClient:
    def upload_workout(self, payload):
        return {"workoutId": 1}


class NewGarminClient(OldGarminClient):
    def update_workout(self, workout_id, payload):
        return {"workoutId": workout_id}


def main() -> int:
    assert workout_replace_compat.supports_inplace_update(OldGarminClient()) is False
    assert workout_replace_compat.supports_inplace_update(NewGarminClient()) is True
    print("OK: gammel Garmin-klient uden update_workout bruger erstatningsstrategi")
    print("OK: nyere Garmin-klient med update_workout kan opdages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
