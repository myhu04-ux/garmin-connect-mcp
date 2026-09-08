"""Final automatic Garmin writer entrypoint.

Imports the transactional calendar writer, then adds the expert quality gate. The
explicit one-action plumbing test (`--test-one`) bypasses only the coaching-quality
gate; all Garmin read-back/transaction safety remains active.
"""

from __future__ import annotations

import sys

import calendar_writer_safe as safe
import writeback_quality_gate

_ORIGINAL_ACTIONABLE = safe.base.actionable


def gated_actionable(preview: dict, allow_remove: bool):
    if "--test-one" not in sys.argv:
        ok, reasons = writeback_quality_gate.allowed(preview)
        if not ok:
            print("AUTOMATISK WRITE-BACK BLOKERET: " + " | ".join(reasons))
            return []
    return _ORIGINAL_ACTIONABLE(preview, allow_remove)


safe.base.actionable = gated_actionable

if __name__ == "__main__":
    raise SystemExit(safe.base.main())
