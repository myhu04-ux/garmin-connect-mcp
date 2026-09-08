"""Small polling helpers for Garmin calendar eventual consistency.

Garmin Connect may acknowledge a schedule/unschedule write before subsequent reads
show the new state. Calendar writes therefore verify with bounded retries instead of
assuming immediate read-after-write consistency.
"""

from __future__ import annotations

import time
from typing import Any, Callable


def wait_for_value(
    reader: Callable[[], Any],
    predicate: Callable[[Any], bool],
    *,
    attempts: int = 7,
    first_delay: float = 0.35,
    max_delay: float = 2.0,
) -> tuple[bool, Any]:
    """Poll reader until predicate(value) is true, returning (ok, last_value)."""
    last: Any = None
    delay = max(0.0, float(first_delay))
    for index in range(max(1, int(attempts))):
        last = reader()
        if predicate(last):
            return True, last
        if index < attempts - 1:
            time.sleep(delay)
            delay = min(max_delay, max(first_delay, delay * 1.7))
    return False, last


def wait_present(reader: Callable[[], Any], **kwargs: Any) -> tuple[bool, Any]:
    return wait_for_value(reader, lambda value: bool(value), **kwargs)


def wait_absent(reader: Callable[[], Any], **kwargs: Any) -> tuple[bool, Any]:
    return wait_for_value(reader, lambda value: not bool(value), **kwargs)
