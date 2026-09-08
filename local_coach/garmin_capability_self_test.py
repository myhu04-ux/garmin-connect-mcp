"""Fail fast unless the installed Garmin client has the native APIs the coach uses."""

from __future__ import annotations

from importlib.metadata import version

from garminconnect import Garmin

REQUIRED_VERSION = "0.3.12"
REQUIRED_METHODS = (
    "get_workout_by_id",
    "upload_workout",
    "update_workout",
    "delete_workout",
    "get_scheduled_workouts",
    "get_scheduled_workout_by_id",
    "schedule_workout",
    "unschedule_workout",
)


def version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for chunk in text.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def main() -> int:
    installed = version("garminconnect")
    if version_tuple(installed) < version_tuple(REQUIRED_VERSION):
        raise RuntimeError(
            f"garminconnect {installed} er for gammel; coachen kræver mindst {REQUIRED_VERSION}."
        )
    missing = [name for name in REQUIRED_METHODS if not callable(getattr(Garmin, name, None))]
    if missing:
        raise RuntimeError("Garmin-klienten mangler native metoder: " + ", ".join(missing))
    print(f"OK: garminconnect {installed}")
    print("OK: native workout update + scheduled-workout read-back er tilgængelig")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
