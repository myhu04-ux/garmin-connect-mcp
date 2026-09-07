"""Research a user-selected race/event and create the active coach goal.

The user can type a natural-language target such as:
  "Etapeløb Thy Trail 31/10-1/11 2026, lang distance"
  "H.C. Andersen Marathon 2026"

The script uses free public web search (DDGS), prefers organiser/official pages,
and asks the local Ollama model to turn sourced facts into a structured event
profile. It never writes to Garmin. Unknown facts must remain unknown rather
than being invented.
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
except Exception:  # pragma: no cover - friendly runtime error below
    DDGS = None  # type: ignore[assignment]

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"
OUT = Path(r"C:\GarminCoach\data\active_goal.json")


def clean_html(raw: str, max_chars: int = 9000) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def fetch_excerpt(url: str) -> str:
    try:
        response = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 GarminLocalCoach/1.0"},
            allow_redirects=True,
        )
        response.raise_for_status()
        ctype = response.headers.get("content-type", "").lower()
        if "text/html" not in ctype and "text/plain" not in ctype:
            return ""
        return clean_html(response.text)
    except Exception:
        return ""


def domain(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def search(goal: str, max_results: int = 12) -> list[dict[str, Any]]:
    if DDGS is None:
        raise RuntimeError("Mangler gratis søgemodul 'ddgs'. Kør projektets update_coach.ps1 først.")

    queries = [
        f'"{goal}"',
        f"{goal} official race route distance terrain",
        f"{goal} rute distance terræn arrangør",
    ]
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    with DDGS() as ddgs:
        for query in queries:
            try:
                rows = ddgs.text(query, max_results=max_results)
            except Exception:
                continue
            for row in rows or []:
                url = str(row.get("href") or row.get("url") or "")
                if not url.startswith("http") or url in seen:
                    continue
                seen.add(url)
                found.append({
                    "title": str(row.get("title") or ""),
                    "url": url,
                    "domain": domain(url),
                    "snippet": str(row.get("body") or row.get("snippet") or "")[:1500],
                })
                if len(found) >= max_results:
                    break
            if len(found) >= max_results:
                break

    # Fetch only a handful of pages; search snippets remain available if a site blocks us.
    for row in found[:6]:
        row["page_excerpt"] = fetch_excerpt(row["url"])
    return found


def source_packet(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    packet = []
    for idx, row in enumerate(rows, start=1):
        packet.append({
            "source_id": idx,
            "title": row.get("title"),
            "url": row.get("url"),
            "domain": row.get("domain"),
            "snippet": row.get("snippet"),
            "page_excerpt": row.get("page_excerpt", "")[:6500],
        })
    return packet


def prompt(goal: str, sources: list[dict[str, Any]]) -> str:
    return f"""Du er research-assistent for en løbetræner. Brug KUN brugerens mål og de
vedlagte webkilder. Gæt aldrig på dato, distance, højdemeter eller terræn. Hvis
noget ikke kan dokumenteres, skriv null/[] og sænk confidence.

Brugerens mål:
{goal}

Kilder:
{json.dumps(sources, ensure_ascii=False, indent=2)}

OPGAVE
1. Identificér det konkrete løb og den distance/variant brugeren sandsynligvis mener.
2. Prioritér arrangørens/officielle website højere end løbskalendere og blogs.
3. Beskriv de FAKTISKE krav: antal løbsdage, distance, underlag, terræn, elevation
   hvis dokumenteret, runder, by/trail, sand/strand, tekniske stier, vindeksponering,
   cutoff eller andre relevante forhold.
4. Udled derefter træningsprioriteter. Markér dem som inference; de er trænerfaglige
   konsekvenser af kilderne og ikke direkte citater.
5. Source IDs må KUN være heltal, som findes i kildelisten ovenfor.
6. Alt brugervendt tekst skal være på dansk.

Returner KUN gyldig JSON med denne struktur:
{{
  "event_name": "...",
  "user_goal_text": "...",
  "event_type": "road_marathon|road_half_marathon|trail_race|trail_stage_race|ultra|other",
  "event_dates": ["YYYY-MM-DD"],
  "location": "...",
  "distance": {{"total_km": null, "stages_km": []}},
  "course": {{
    "surface": ["..."],
    "terrain_features": ["..."],
    "elevation": "... eller ukendt",
    "laps": "... eller ukendt",
    "environment": ["..."]
  }},
  "documented_requirements": [
    {{"fact": "...", "source_ids": [1]}}
  ],
  "training_priorities": [
    {{"priority": "...", "reason": "...", "basis": "inference", "source_ids": [1]}}
  ],
  "official_source_id": null,
  "confidence": "high|medium|low",
  "open_questions": ["..."]
}}
"""


def call_ollama(goal: str, sources: list[dict[str, Any]], model: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": "Returner kun valid JSON. Vær kildekritisk. Ukendt er bedre end opdigtet."},
            {"role": "user", "content": prompt(goal, sources)},
        ],
        "options": {"temperature": 0.05, "num_predict": 1800},
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=240)
    response.raise_for_status()
    content = response.json().get("message", {}).get("content", "")
    return json.loads(content)


def validate(profile: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    valid_ids = {int(s["source_id"]) for s in sources}
    used: set[int] = set()

    def valid_list(value: Any) -> list[int]:
        out = []
        for raw in value if isinstance(value, list) else []:
            try:
                sid = int(raw)
            except Exception:
                continue
            if sid in valid_ids:
                out.append(sid)
                used.add(sid)
        return out

    for item in profile.get("documented_requirements", []) or []:
        if isinstance(item, dict):
            item["source_ids"] = valid_list(item.get("source_ids"))
    for item in profile.get("training_priorities", []) or []:
        if isinstance(item, dict):
            item["source_ids"] = valid_list(item.get("source_ids"))
            item["basis"] = "inference"

    official = profile.get("official_source_id")
    try:
        official = int(official) if official is not None else None
    except Exception:
        official = None
    profile["official_source_id"] = official if official in valid_ids else None
    if profile["official_source_id"]:
        used.add(profile["official_source_id"])

    # Preserve only real source records; the model cannot invent URLs.
    profile["sources"] = [
        {"source_id": s["source_id"], "title": s["title"], "url": s["url"], "domain": s["domain"]}
        for s in sources if int(s["source_id"]) in used
    ] or [
        {"source_id": s["source_id"], "title": s["title"], "url": s["url"], "domain": s["domain"]}
        for s in sources[:3]
    ]
    profile["researched_at"] = dt.datetime.now().astimezone().isoformat()
    profile["status"] = "active"
    profile["garmin_writeback_enabled"] = False
    return profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal", help="Frit tekstmål. Hvis udeladt spørger programmet.")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()

    goal = (args.goal or "").strip()
    if not goal:
        goal = input("Hvilket løb eller mål vil du træne mod? ").strip()
    if not goal:
        print("Intet mål angivet.")
        return 2

    print(f"Researcher: {goal}")
    rows = search(goal)
    if not rows:
        print("ERROR: Fandt ingen brugbare webkilder. Det eksisterende aktive mål er ikke ændret.")
        return 3
    sources = source_packet(rows)
    print(f"Fandt {len(sources)} kilder. Analyserer lokalt med {args.model}...")

    try:
        profile = validate(call_ollama(goal, sources, args.model), sources)
    except Exception as exc:
        print(f"ERROR: Kunne ikke bygge en sikker løbsprofil: {exc}")
        print("Det eksisterende aktive mål er ikke ændret.")
        return 4

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== AKTIVT TRÆNINGSMÅL ===")
    print(f"Løb: {profile.get('event_name')}")
    print(f"Dato: {', '.join(profile.get('event_dates') or []) or 'ukendt'}")
    distance = profile.get("distance") or {}
    print(f"Distance: {distance.get('total_km') if isinstance(distance, dict) else '?'} km")
    print(f"Type: {profile.get('event_type')}")
    print(f"Confidence: {profile.get('confidence')}")
    print("Særligt fokus:")
    for item in (profile.get("training_priorities") or [])[:8]:
        if isinstance(item, dict):
            print(f"  - {item.get('priority')}: {item.get('reason')}")
    if profile.get("open_questions"):
        print("Ikke afklaret endnu:")
        for q in profile["open_questions"][:5]:
            print(f"  - {q}")
    print("Kilder:")
    for s in profile.get("sources", []):
        print(f"  [{s.get('source_id')}] {s.get('title')} - {s.get('url')}")
    print(f"Gemt lokalt: {args.output}")
    print("Garmin write-back: OFF")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
