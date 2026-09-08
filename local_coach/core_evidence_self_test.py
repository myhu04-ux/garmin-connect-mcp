"""Offline tests for the curated evidence seed library."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import core_evidence


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "training_knowledge.json"
        path.write_text(json.dumps({
            "references": [{
                "reference_name": "User plan",
                "reference_input": "My favourite online plan",
                "principles": [{"principle": "keep me", "category": "weekly_structure", "confidence": "medium"}],
            }]
        }), encoding="utf-8")

        changed = core_evidence.seed_training_knowledge(path)
        assert changed is True
        payload = json.loads(path.read_text(encoding="utf-8"))
        refs = payload["references"]
        assert len(refs) == 2
        core = next(r for r in refs if r.get("reference_input") == core_evidence.REFERENCE_INPUT)
        user = next(r for r in refs if r.get("reference_input") == "My favourite online plan")
        assert len(core.get("principles") or []) >= 7
        assert user["principles"][0]["principle"] == "keep me"

        before = path.read_text(encoding="utf-8")
        changed_again = core_evidence.seed_training_knowledge(path)
        after = path.read_text(encoding="utf-8")
        assert changed_again is False
        assert before == after

        print("OK: core evidence seed preserves user research and is idempotent")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
