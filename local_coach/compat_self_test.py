"""Compatibility gate for the simplified native Garmin architecture.

This file intentionally keeps its historical filename because older self-update
scripts already execute compat_self_test.py after pulling new code. It now validates
the native garminconnect API and the idempotent calendar/full-delete state machine.
"""

from __future__ import annotations

import garmin_capability_self_test
import test_workout_calendar_self_test


def main() -> int:
    print("=== NATIVE GARMIN COMPATIBILITY TEST ===")
    rc = garmin_capability_self_test.main()
    if rc not in (0, None):
        return int(rc)
    rc = test_workout_calendar_self_test.main()
    if rc not in (0, None):
        return int(rc)
    print("OK: native Garmin API + idempotent move/delete semantics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
