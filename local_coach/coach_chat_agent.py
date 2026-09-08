"""Tool-aware local coach chat entrypoint.

Extends coach_chat_fast with a read-only Garmin method catalog. The coach can search
what the installed Garmin client exposes without invoking unknown endpoints. Known
domains (challenges and workout format) are handled by explicit safe tools in
coach_chat_fast.
"""

from __future__ import annotations

import threading

import coach_chat_fast as fast
import garmin_method_catalog as catalog


def wants_catalog(message: str) -> bool:
    text = message.casefold()
    phrases = (
        "søg i garmin", "soge i garmin", "find i garmin", "finde i garmin",
        "hvor ligger", "hvor finder", "garmin-funktion", "garmin funktion",
        "garmin struktur", "garmins struktur", "hvilke funktioner",
    )
    return any(p in text for p in phrases)


def catalog_answer(message: str) -> str:
    try:
        api = catalog.login()
        rows = catalog.build_catalog(api)
        catalog.save_catalog(rows)
        matches = catalog.search_rows(rows, message, limit=10)
    except Exception as exc:
        return f"Jeg kunne ikke inspicere Garmin-klienten lige nu: {exc}"

    if not matches:
        return (
            "Jeg har gennemsøgt den installerede Garmin-klients read-only get_* metoder, "
            "men fandt ingen tydelig kandidat til det, du beskrev. Jeg vil ikke gætte på et endpoint."
        )

    lines = ["Jeg har søgt i den Garmin-klient, der faktisk er installeret. Mest relevante read-only kandidater:"]
    for row in matches[:7]:
        sig = row.get("signature") or ""
        doc = row.get("doc") or ""
        lines.append(f"• {row.get('method')}{sig}" + (f" – {doc}" if doc else ""))
    lines.append(
        "Det her er kun discovery. Et nyt endpoint bliver først koblet på som et eksplicit read-only værktøj, "
        "så coachen ikke eksperimenterer blindt mod din Garmin-konto."
    )
    return "\n".join(lines)


def answer(message: str) -> str:
    if wants_catalog(message):
        return catalog_answer(message)
    return fast.fast_answer(message)


fast.base.answer = answer

if __name__ == "__main__":
    threading.Thread(target=fast.warm_model, daemon=True).start()
    raise SystemExit(fast.base.main())
