"""Turn structured coach state + validated 7-day plan into useful Danish coach language.

Facts stay deterministic. This layer only communicates them: conclusion first,
meaning second, action third. It never changes workouts or health classification.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

STATE = Path(r"C:\GarminCoach\data\coach_state.json")
PREVIEW = Path(r"C:\GarminCoach\data\coach_preview.json")
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(state: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def moved_count(match: dict[str, Any]) -> int:
    return sum(1 for x in match.get("matches", []) if int(x.get("date_shift_days") or 0) != 0)


def plan_focus(preview: dict[str, Any]) -> str:
    actions = [a for a in preview.get("actions", []) if isinstance(a, dict)]
    meaningful = [a for a in actions if a.get("action") != "REMOVE"]
    parts = []
    for action in meaningful[:3]:
        name = action.get("plan_name") or action.get("source_title") or (action.get("selected_template") or {}).get("title")
        focus = action.get("focus")
        if name and focus:
            parts.append(f"{name}: {focus}")
    return "; ".join(parts)


def deterministic(state: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any]:
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
        headline = "Du er på sporet, men vi styrer belastningen lidt mere forsigtigt de næste dage."
    elif rec == "green":
        headline = "Du ser klar ud til at fortsætte planen uden at forcere progressionen."
    else:
        headline = "Træningen kan vurderes, men vi mangler endnu nok historik til en sikker restitutionstrend."

    signals = recovery.get("signals") or []
    notable = [s for s in signals if s.get("severity") in {"yellow", "red"}]
    if notable:
        parts = []
        for signal in notable[:2]:
            metric = signal.get("metric")
            delta = signal.get("delta_pct") if signal.get("delta_pct") is not None else signal.get("delta")
            unit = "%" if signal.get("delta_pct") is not None else signal.get("unit", "")
            sign = "+" if isinstance(delta, (int, float)) and delta > 0 else ""
            parts.append(f"{metric} ligger {sign}{delta}{unit} fra din normale baseline")
        kroppen = " og ".join(parts) + ". Det betyder, at vi styrer de næste pas efter restitutionen frem for at presse ekstra træning ind."
    elif rec == "insufficient_history":
        latest = recovery.get("latest") or {}
        shown = []
        if latest.get("sleep_hours") is not None:
            shown.append(f"seneste søvn er {latest['sleep_hours']} timer")
        if latest.get("resting_hr") is not None:
            shown.append(f"hvilepulsen er {latest['resting_hr']}")
        tail = (" Vi kan se, at " + " og ".join(shown) + ", men én nat er ikke en trend.") if shown else ""
        kroppen = f"Vi har {recovery.get('history_days', 0)} historikdage lige nu.{tail} Vurderingen bliver mere sikker, efterhånden som din egen baseline fyldes op."
    else:
        kroppen = "De seneste restitutionssignaler ligger samlet tæt på din egen normal. Der er ikke noget i data, der i sig selv kræver en markant nedjustering."

    planned = int(match.get("planned_workouts") or 0)
    matched = int(match.get("matched") or 0)
    moved = moved_count(match)
    pending = int(match.get("pending") or 0)
    if planned:
        shift_text = f" {moved} blev flyttet en dag eller to og tæller stadig som gennemført." if moved else ""
        traeningen = f"Du har gennemført {matched} af {planned} afgjorte planpas.{shift_text} De seneste 7 dage blev det til {recent.get('km', 0)} km mod {previous.get('km', 0)} km ugen før."
        if pending:
            traeningen += f" Dagens {pending} planlagte pas står som afventer – ikke som misset."
    elif pending:
        traeningen = f"Der er ingen tidligere planpas, der mangler at blive afgjort lige nu. Dagens pas afventer stadig, og de seneste 7 dage viser {recent.get('km', 0)} km fordelt på {recent.get('runs', 0)} ture."
    else:
        traeningen = f"Der er endnu ikke nok kalenderdata til at måle plan mod gennemført. De seneste 7 dage viser {recent.get('km', 0)} km fordelt på {recent.get('runs', 0)} ture."

    preview_focus = plan_focus(preview)
    if preview_focus:
        naeste = f"De næste nøglepas er {preview_focus}. Det vigtigste er at ramme formålet med passene – ikke at jagte et bestemt tempo på en dag, hvor kroppen siger noget andet."
    elif focus:
        naeste = " ".join(str(x) for x in focus[:2])
    else:
        naeste = f"Næste blok skal fortsat bygge mod {event.get('name') or 'dit aktive mål'} uden at øge både mængde og intensitet samtidig."

    attention = []
    if match.get("uncertain"):
        attention.append("Der er et planmatch, som coachen ikke er sikker nok på til at bruge som facit.")
    if match.get("missed"):
        attention.append("Et planlagt pas uden sikkert match bliver ikke automatisk presset ind senere.")

    return {
        "headline": headline,
        "kroppen": kroppen,
        "traeningen": traeningen,
        "naeste_fokus": naeste,
        "opmaerksomhed": attention[:3],
        "helbred": kroppen,
        "fokus": naeste,
    }


def prompt(state: dict[str, Any], preview: dict[str, Any]) -> str:
    compact_actions = []
    for action in preview.get("actions", []) if isinstance(preview, dict) else []:
        if not isinstance(action, dict):
            continue
        compact_actions.append({
            "date": action.get("date"),
            "plan_name": action.get("plan_name"),
            "focus": action.get("focus"),
            "reason": action.get("reason"),
            "action": action.get("action"),
            "workout_style": action.get("workout_style"),
        })
    compact = {
        "recovery": state.get("recovery"),
        "training": state.get("training"),
        "plan_match": state.get("plan_match"),
        "event": state.get("event"),
        "focus_points": state.get("focus_points"),
        "validated_next_7_days": compact_actions,
    }
    return f"""Du er en personlig løbetræner, som taler direkte til løberen på naturligt dansk.
Du får allerede analyserede fakta og en VALIDERET 7-dages plan. Din opgave er KUN
at kommunikere, hvad det betyder. Du må ikke ændre planen eller opfinde nye fakta.

SPROGREGLER
- Start med konklusionen: hvad betyder data for løberen lige nu?
- Skriv som en erfaren løbetræner, ikke som en laboratorierapport eller AI-assistent.
- Kort og konkret. Højst 1-2 tal i hvert afsnit.
- Forklar betydningen af tallene; rems dem ikke bare op.
- Brug hverdagssprog. Undgå jargon som load/readiness/ATL/CTL.
- Et sikkert matchet pas flyttet +/-1 dag er gennemført, ikke misset.
- Et endnu ikke gennemført pas i dag er AFVENTER, ikke misset.
- Vær konstruktiv og rolig, ikke overdrevent rosende eller kontrollerende.
- Giv en kort begrundelse for fokus: hvorfor hjælper det mod det aktuelle løb?
- Brug gerne de korte plan-navne (fx ThyTrailW3D4), når du omtaler konkrete kommende pas.
- Hvis historikken ikke er stærk nok, sig det enkelt og undlad at gætte.
- Ingen medicinske diagnoser.
- Ingen opdigtede data, træninger eller løbsfakta.

Returner KUN gyldig JSON:
{{
  "headline": "én skarp sætning",
  "kroppen": "2-3 korte sætninger: hvad ser du og hvad betyder det?",
  "traeningen": "2-3 korte sætninger: er planen på sporet, inkl. flyttede/afventende pas?",
  "naeste_fokus": "2-3 korte sætninger: hvilke konkrete pas/fokus er vigtigst nu og hvorfor?",
  "opmaerksomhed": ["maks 3 korte ting, kun hvis relevant"]
}}

FAKTA OG VALIDERET PLAN:
{json.dumps(compact, ensure_ascii=False, indent=2)}
"""


def call_model(state: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any] | None:
    payload = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": "Skriv som en konkret dansk løbetræner. Kun valid JSON, ingen nye fakta eller ændringer af planen."},
            {"role": "user", "content": prompt(state, preview)},
        ],
        "options": {"temperature": 0.15, "num_predict": 850},
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
        print("ERROR: coach_state.json mangler. Kør coach_brief_v2.py først.")
        return 2
    state = read_json(STATE, {})
    preview = read_json(PREVIEW, {})
    narrative = call_model(state, preview) or deterministic(state, preview)
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
