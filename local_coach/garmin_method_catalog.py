"""Discover the installed Garmin client's public read-only API surface.

No Garmin data endpoints are called. We log in only so the catalog reflects the
actual installed client object, then inspect public get_* methods, signatures and
docstrings. This lets the local coach search for likely Garmin tools without being
allowed to invoke arbitrary methods blindly.
"""

from __future__ import annotations

import argparse
import datetime as dt
import inspect
import json
import os
import re
from pathlib import Path
from typing import Any

from garminconnect import Garmin

TOKEN_DIR = os.path.expanduser("~/.garminconnect")
OUT = Path(r"C:\GarminCoach\data\garmin_method_catalog.json")

ALIASES = {
    "udfordring": ["challenge", "badge", "goal"],
    "udfordringer": ["challenge", "badge", "goal"],
    "mærke": ["badge"],
    "badges": ["badge"],
    "træning": ["workout", "training", "activity"],
    "træningspas": ["workout"],
    "kalender": ["scheduled", "calendar", "workout"],
    "søvn": ["sleep"],
    "restitution": ["readiness", "recovery", "hrv", "stress", "body battery"],
    "helbred": ["health", "readiness", "hrv", "stress", "sleep"],
    "udholdenhed": ["endurance"],
    "udstyr": ["gear", "device"],
    "sko": ["gear"],
}


def login() -> Garmin:
    if not Path(TOKEN_DIR).exists():
        raise RuntimeError(f"Garmin tokenmappe mangler: {TOKEN_DIR}")
    api = Garmin(retry_attempts=0)
    api.login(TOKEN_DIR)
    return api


def build_catalog(api: Garmin) -> list[dict[str, Any]]:
    rows = []
    for name in sorted(dir(api)):
        if not name.startswith("get_"):
            continue
        fn = getattr(api, name, None)
        if not callable(fn):
            continue
        try:
            sig = str(inspect.signature(fn))
        except Exception:
            sig = None
        doc = (getattr(fn, "__doc__", None) or "").strip()
        rows.append({
            "method": name,
            "signature": sig,
            "doc": doc.split("\n", 1)[0] if doc else "",
            "read_only_candidate": True,
        })
    return rows


def terms(query: str) -> list[str]:
    words = [x for x in re.findall(r"[\wæøåÆØÅ-]+", query.casefold()) if len(x) > 1]
    expanded = list(words)
    for word in words:
        expanded.extend(ALIASES.get(word, []))
    return list(dict.fromkeys(expanded))


def search_rows(rows: list[dict[str, Any]], query: str, limit: int = 12) -> list[dict[str, Any]]:
    qs = terms(query)
    scored = []
    for row in rows:
        method = str(row.get("method") or "").casefold()
        doc = str(row.get("doc") or "").casefold()
        hay = method.replace("_", " ") + " " + doc
        score = 0
        hits = []
        for term in qs:
            t = term.casefold()
            if t in method:
                score += 5
                hits.append(term)
            elif t in hay:
                score += 2
                hits.append(term)
        if score:
            scored.append((score, row, hits))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("method") or "")))
    return [{**row, "score": score, "hits": hits} for score, row, hits in scored[:limit]]


def save_catalog(rows: list[dict[str, Any]]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "policy": "Catalog only. Unknown methods are not invoked automatically.",
        "methods": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search", default="")
    args = parser.parse_args()

    api = login()
    rows = build_catalog(api)
    save_catalog(rows)
    print(f"Garmin read-only katalog: {len(rows)} get_* metoder")
    if args.search:
        matches = search_rows(rows, args.search)
        print(f"Søgning: {args.search}")
        for row in matches:
            print(f"- {row['method']}{row.get('signature') or ''}: {row.get('doc') or ''}")
    print(f"Gemt: {OUT}")
    print("Ingen Garmin-data-endpoints blev kaldt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
