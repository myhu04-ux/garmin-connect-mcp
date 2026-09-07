"""Research an online running plan and extract reusable coaching principles.

The user may provide a URL or a natural-language description such as
"Hal Higdon Intermediate 1 marathon". The tool does NOT copy a full copyrighted
schedule. It extracts high-level structure, progression and coaching principles,
keeps source references, and stores them as optional evidence for the local
coach. Nothing is written to Garmin.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

try:
    from ddgs import DDGS
except Exception:
    DDGS = None  # type: ignore[assignment]

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"
OUT = Path(r"C:\GarminCoach\data\training_knowledge.json")


def is_url(value: str) -> bool:
    return value.startswith("https://") or value.startswith("http://")


def domain(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def clean_html(raw: str, max_chars: int = 14000) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def fetch_page(url: str) -> str:
    try:
        response = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 GarminLocalCoach/1.0"},
            allow_redirects=True,
        )
        response.raise_for_status()
        if "text" not in response.headers.get("content-type", "").lower():
            return ""
        return clean_html(response.text)
    except Exception:
        return ""


def search_sources(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    if DDGS is None:
        raise RuntimeError("Mangler gratis søgemodul 'ddgs'. Kør coach-opdateringen først.")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    queries = [query, f'"{query}" running training plan', f'"{query}" løbeprogram']
    with DDGS() as ddgs:
        for q in queries:
            try:
                results = ddgs.text(q, max_results=max_results)
            except Exception:
                continue
            for item in results or []:
                url = str(item.get("href") or item.get("url") or "")
                if not url.startswith("http") or url in seen:
                    continue
                seen.add(url)
                rows.append({
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "domain": domain(url),
                    "snippet": str(item.get("body") or item.get("snippet") or "")[:1800],
                })
                if len(rows) >= max_results:
                    return rows
    return rows


def build_sources(user_input: str) -> list[dict[str, Any]]:
    if is_url(user_input):
        rows = [{
            "title": "Brugerangivet træningsplan",
            "url": user_input,
            "domain": domain(user_input),
            "snippet": "Direkte link angivet af brugeren.",
        }]
    else:
        rows = search_sources(user_input)

    for row in rows[:6]:
        row["page_excerpt"] = fetch_page(row["url"])
    return [dict(row, source_id=i + 1) for i, row in enumerate(rows)]


def prompt(user_input: str, sources: list[dict[str, Any]]) -> str:
    safe_sources = [
        {
            "source_id": s["source_id"],
            "title": s.get("title"),
            "url": s.get("url"),
            "domain": s.get("domain"),
            "snippet": s.get("snippet"),
            "page_excerpt": s.get("page_excerpt", "")[:9000],
        }
        for s in sources
    ]
    return f"""Du analyserer et løbeprogram som inspirationskilde for en personlig træner.
Brug kun kilderne nedenfor. Gæt ikke. Du må IKKE gengive en hel uge-for-uge-plan,
en hel tabel eller lange tekstpassager. Udtræk kun abstrakte træningsprincipper,
struktur og få korte numeriske fakta, hvis de tydeligt fremgår.

Brugerens reference:
{user_input}

Kilder:
{json.dumps(safe_sources, ensure_ascii=False, indent=2)}

Returner KUN valid JSON:
{{
  "reference_name": "navn på planen/programmet",
  "target_event": "marathon|half_marathon|10k|trail|general|other|unknown",
  "intended_athlete": "kort beskrivelse eller ukendt",
  "duration_weeks": null,
  "source_quality": "high|medium|low",
  "principles": [
    {{
      "principle": "kort generaliseret pointe",
      "category": "weekly_structure|long_run|quality|easy_running|strength|cross_training|cutback|taper|fueling|specificity|recovery|other",
      "evidence": "kort faktuel begrundelse, ikke langt citat",
      "source_ids": [1],
      "confidence": "high|medium|low"
    }}
  ],
  "numeric_patterns": [
    {{"name": "fx træningsdage pr uge", "value": "kort værdi/range", "source_ids": [1]}}
  ],
  "cautions": ["hvem planen måske ikke passer til / hvad der kræver individuel tilpasning"],
  "copyright_note": "Kun principper er udtrukket; fuld plan er ikke kopieret."
}}
"""


def call_ollama(user_input: str, sources: list[dict[str, Any]], model: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": "Svar kun med valid JSON på dansk. Udtræk principper, ikke en kopieret plan."},
            {"role": "user", "content": prompt(user_input, sources)},
        ],
        "options": {"temperature": 0.05, "num_predict": 1700},
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=240)
    response.raise_for_status()
    return json.loads(response.json().get("message", {}).get("content", ""))


def validate(result: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    valid_ids = {int(s["source_id"]) for s in sources}
    used: set[int] = set()

    def ids(value: Any) -> list[int]:
        out: list[int] = []
        for raw in value if isinstance(value, list) else []:
            try:
                sid = int(raw)
            except Exception:
                continue
            if sid in valid_ids:
                out.append(sid)
                used.add(sid)
        return out

    principles = []
    for item in result.get("principles", []) if isinstance(result.get("principles"), list) else []:
        if not isinstance(item, dict) or not str(item.get("principle") or "").strip():
            continue
        item["source_ids"] = ids(item.get("source_ids"))
        if not item["source_ids"]:
            item["confidence"] = "low"
        principles.append(item)
    result["principles"] = principles[:20]

    patterns = []
    for item in result.get("numeric_patterns", []) if isinstance(result.get("numeric_patterns"), list) else []:
        if isinstance(item, dict):
            item["source_ids"] = ids(item.get("source_ids"))
            patterns.append(item)
    result["numeric_patterns"] = patterns[:12]

    result["sources"] = [
        {"source_id": s["source_id"], "title": s.get("title"), "url": s.get("url"), "domain": s.get("domain")}
        for s in sources if int(s["source_id"]) in used
    ] or [
        {"source_id": s["source_id"], "title": s.get("title"), "url": s.get("url"), "domain": s.get("domain")}
        for s in sources[:3]
    ]
    result["researched_at"] = dt.datetime.now().astimezone().isoformat()
    result["reference_input"] = result.get("reference_input") or None
    result["mode"] = "principles_not_schedule_copy"
    return result


def load_library(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", help="URL eller navn/beskrivelse af et løbeprogram")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()

    user_input = (args.plan or "").strip()
    if not user_input:
        user_input = input("Hvilket løbeprogram vil du bruge som inspiration? (URL eller navn): ").strip()
    if not user_input:
        print("Intet program angivet.")
        return 2

    print(f"Undersøger træningsreference: {user_input}")
    sources = build_sources(user_input)
    if not sources:
        print("ERROR: Fandt ingen brugbare kilder. Det eksisterende vidensbibliotek er uændret.")
        return 3

    try:
        result = validate(call_ollama(user_input, sources, args.model), sources)
    except Exception as exc:
        print(f"ERROR: Kunne ikke analysere programmet sikkert: {exc}")
        return 4
    result["reference_input"] = user_input

    library = load_library(args.output)
    refs = library.get("references") if isinstance(library.get("references"), list) else []
    # Replace same input instead of creating duplicates.
    refs = [r for r in refs if str(r.get("reference_input") or "").lower() != user_input.lower()]
    refs.append(result)
    library = {
        "updated_at": dt.datetime.now().astimezone().isoformat(),
        "purpose": "Eksterne planer bruges som inspirationskilder; Garmin-data, aktuelt mål og restitution har forrang.",
        "references": refs[-10:],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== TRÆNINGSPRINCIPPER FUNDET ===")
    print(f"Reference: {result.get('reference_name')}")
    print(f"Målgruppe: {result.get('intended_athlete')}")
    print(f"Varighed: {result.get('duration_weeks') or 'ukendt'} uger")
    for item in result.get("principles", [])[:10]:
        print(f"- [{item.get('category')}] {item.get('principle')} ({item.get('confidence')})")
    if result.get("cautions"):
        print("Tilpasningsforbehold:")
        for item in result["cautions"][:5]:
            print(f"- {item}")
    print("Kilder:")
    for source in result.get("sources", []):
        print(f"  [{source.get('source_id')}] {source.get('title')} - {source.get('url')}")
    print(f"Gemt lokalt: {args.output}")
    print("Fuld uge-for-uge-plan er ikke kopieret.")
    print("Garmin write-back: OFF")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
