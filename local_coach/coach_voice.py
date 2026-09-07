"""Turn structured coach_state into useful Danish coach language.

The analysis remains deterministic in coach_brief.py. This layer only changes how
it is communicated: conclusion first, concrete evidence second, next action third.
It never changes workouts, health classification or Garmin data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

STATE = Path(r"C:\GarminCoach\data\coach_state.json")
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"


def load() -> dict[str, Any]:
    return json.loads(STATE.read_text(encoding="utf-8"))


def save(state: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def moved_count(match: dict[str, Any]) -> int:
    return sum(1 for x in match.get("matches", []) if int(x.get("date_shift_days") or 0) != 0)


def deterministic(state: dict[str, Any]) -> dict[str, Any]:
    recovery = state.get("recovery") or {}
    training = state.get("training") or {}
    recent = training.get("last_7_days") or {}
    previous = training.get("previous_7_days") or {}
    match = state.get("plan_match") or {}
    event = state.get("event") or {}
    focus = state.get("focus_points") or []

    rec = recovery.get("state")
    if rec == "red":
        headline = "Kroppen ser presset ud – vi skal skabe plads til restitution, ikke jagte planen."
    elif rec == "yellow":
        headline = "Du er stadig på sporet, men belastningen skal styres lidt mere forsigtigt de næste dage."
    elif rec == "green":
        headline = "Du ser klar ud til at fortsætte planen uden at forcere progressionen."
    else:
        headline = "Træningen kan vurderes, men vi mangler endnu nok helbredshistorik til en sikker restitutionstrend."

    signals = recovery.get("signals") or []
    notable = [s for s in signals if s.get("severity") in {"yellow", "red"}]
    if notable:
        parts = []
        for s in notable[:2]:
            metric = s.get("metric")
            delta = s.get("delta_pct") if s.get("delta_pct") is not None else s.get("delta")
            unit = "%" if s.get("delta_pct") is not None else s.get("unit", "")
            sign = "+" if isinstance(delta, (int, float)) and delta > 0 else ""
            parts.append(f"{metric} ligger {sign}{delta}{unit} fra din baseline")
        body = " og ".join(parts) + "."
        kroppen = f"{body} Derfor bruger vi de næste pas til at styre belastningen frem for at presse ekstra træning ind."
    elif rec == "insufficient_history":
        latest = recovery.get("latest") or {}
        shown = []
        if latest.get("sleep_hours") is not None:
            shown.append(f"seneste søvn {latest['sleep_hours']} timer")
        if latest.get("resting_hr") is not None:
            shown.append(f"hvilepuls {latest['resting_hr']}")
        tail = (" Vi kan se " + " og ".join(shown) + ", men én nat er ikke en trend.") if shown else ""
        kroppen = f"Vi har {recovery.get('history_days', 0)} historikdage lige nu.{tail} Coachen bliver mere sikker, efterhånden som din personlige baseline fyldes op."
    else:
        kroppen = "De seneste restitutionssignaler ligger samlet tæt på din egen normal. Der er ikke noget i data, der i sig selv kræver en markant nedjustering."

    planned = int(match.get("planned_workouts") or 0)
    matched = int(match.get("matched") or 0)
    moved = moved_count(match)
    if planned:
        shift_text = f" {moved} af dem blev flyttet en dag eller to og tæller stadig som gennemført." if moved else ""
        traeningen = (
            f"Du har gennemført {matched} af {planned} planlagte pas i matchvinduet.{shift_text} "
            f"De seneste 7 dage blev det til {recent.get('km', 0)} km mod {previous.get('km', 0)} km ugen før."
        )
    else:
        traeningen = f"Der er endnu ikke nok kalenderdata til at måle plan mod gennemført. De seneste 7 dage viser {recent.get('km', 0)} km fordelt på {recent.get('runs', 0)} løbeture."

    if focus:
        naeste = " ".join(str(x) for x in focus[:2])
    else:
        naeste = f"Næste blok skal fortsat bygge mod {event.get('name') or 'dit aktive mål'} uden at øge både mængde og intensitet samtidig."

    attention = []
    if match.get("uncertain"):
        attention.append("Der er mindst ét planmatch, som coachen ikke er sikker nok på til at bruge som facit.")
    if match.get("missed"):
        attention.append("Et planlagt pas uden sikkert match bliver ikke automatisk presset ind senere.")

    return {
        "headline": headline,
        "kroppen": kroppen,
        "traeningen": traeningen,
        "naeste_fokus": naeste,
        "opmaerksomhed": attention[:3],
        # Compatibility with the first UI/dashboard.
        "helbred": kroppen,
        "fokus": naeste,
    }


def prompt(state: dict[str, Any]) -> str:
    compact = {
        "recovery": state.get("recovery"),
        "training": state.get("training"),
        "plan_match": state.get("plan_match"),
        "event": state.get("event"),
        "focus_points": state.get("focus_points"),
    }
    return f"""Du er en personlig løbetræner, som taler direkte til løberen på naturligt dansk.
Du får allerede analyserede fakta. Din opgave er KUN at kommunikere dem godt.

SPROGREGLER
- Start med konklusionen: hvad betyder data for løberen lige nu?
- Skriv som en træner, ikke som en laboratorierapport eller AI-assistent.
- Brug korte, konkrete sætninger og højst 1-2 tal i hvert afsnit.
- Forklar betydningen af tallene. Tal må aldrig stå alene som argument.
- Brug hverdagssprog. Undgå jargon som load, readiness, ATL/CTL osv.; hvis et fagord er nødvendigt, forklar det.
- Et pas flyttet +/-1 dag, som er sikkert matchet, er gennemført - ikke misset.
- Vær positiv og konstruktiv, men ikke overdrevent rosende.
- Fortæl tydeligt, hvad der er vigtigst de næste dage.
- Hvis historikken ikke er stærk nok, sig det enkelt og undlad at gætte.
- Ingen medicinske diagnoser.
- Ingen opdigtede data, træninger eller løbsfakta.

Returner KUN gyldig JSON:
{{
  "headline": "én skarp sætning",
  "kroppen": "2-3 korte sætninger: hvad ser du og hvad betyder det?",
  "traeningen": "2-3 korte sætninger: er planen på sporet, inkl. flyttede pas?",
  "naeste_fokus": "2-3 korte sætninger: hvad skal være vigtigst nu og hvorfor?",
  "opmaerksomhed": ["maks 3 korte ting, kun hvis relevant"]
}}

FAKTA:
{json.dumps(compact, ensure_ascii=False, indent=2)}
"""


def call_model(state: dict[str, Any]) -> dict[str, Any] | None:
    payload = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": "Skriv som en konkret dansk løbetræner. Kun valid JSON, ingen nye fakta."},
            {"role": "user", "content": prompt(state)},
        ],
        "options": {"temperature": 0.15, "num_predict": 900},
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        result = json.loads(response.json().get("message", {}).get("content", ""))
        required = {"headline", "kroppen", "traeningen", "naeste_fokus"}
        if not required.issubset(result):
            return None
        result["opmaerksomhed"] = [str(x)[:300] for x in (result.get("opmaerksomhed") or [])[:3]]
        result["helbred"] = result["kroppen"]
        result["fokus"] = result["naeste_fokus"]
        return result
    except Exception:
        return None


def main() -> int:
    if not STATE.exists():
        print("ERROR: coach_state.json mangler. Kør coach_brief.py først.")
        return 2
    state = load()
    narrative = call_model(state) or deterministic(state)
    state["narrative"] = narrative
    save(state)

    print("=== COACHENS VURDERING ===")
    print(narrative.get("headline", ""))
    print("\nKROPPEN")
    print(narrative.get("kroppen", ""))
    print("\nTRÆNINGEN")
    print(narrative.get("traeningen", ""))
    print("\nDET GØR VI NU")
    print(narrative.get("naeste_fokus", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
