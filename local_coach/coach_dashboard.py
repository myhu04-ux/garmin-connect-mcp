r"""Render one readable text + HTML report from already validated local coach data.

No Garmin API calls and no write-back. The report uses the same athlete-facing
language as the browser UI; technical evidence is shown after the conclusions.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path
from typing import Any

STATE = Path(r"C:\GarminCoach\data\coach_state.json")
PREVIEW = Path(r"C:\GarminCoach\data\coach_preview.json")
KNOWLEDGE = Path(r"C:\GarminCoach\data\training_knowledge.json")
GOAL = Path(r"C:\GarminCoach\data\active_goal.json")
TXT = Path(r"C:\GarminCoach\data\DAGENS_COACH.txt")
HTML = Path(r"C:\GarminCoach\data\DAGENS_COACH.html")


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def recovery_label(state: str) -> str:
    return {
        "green": "Grøn – normal/stabil",
        "yellow": "Gul – lidt presset",
        "red": "Rød – tydeligt presset",
        "insufficient_history": "Afventer – for lidt personlig historik",
    }.get(state, state or "ukendt")


def pct(current: float, previous: float) -> str:
    if not previous:
        return ""
    return f"{100.0 * (current - previous) / previous:+.0f}%"


def action_title(action: dict[str, Any]) -> str:
    return str(
        action.get("plan_name")
        or (action.get("selected_template") or {}).get("title")
        or action.get("source_title")
        or action.get("family")
        or "Træning"
    )


def text_dashboard(state: dict[str, Any], preview: dict[str, Any], knowledge: dict[str, Any], goal: dict[str, Any]) -> str:
    recovery = state.get("recovery") or {}
    narrative = state.get("narrative") or {}
    training = state.get("training") or {}
    recent = training.get("last_7_days") or {}
    previous = training.get("previous_7_days") or {}
    event = state.get("event") or {}
    match = state.get("plan_match") or {}

    lines = [
        "==============================================",
        "                 DAGENS COACH",
        "==============================================",
        f"Opdateret: {dt.datetime.now().astimezone().strftime('%d-%m-%Y %H:%M')}",
        "",
        str(narrative.get("headline") or "Her er vurderingen ud fra de data, der er tilgængelige."),
        "",
        "KROPPEN LIGE NU",
        str(narrative.get("kroppen") or narrative.get("helbred") or "Ingen samlet vurdering endnu."),
        "",
        "TRÆNINGEN",
        str(narrative.get("traeningen") or "Ingen samlet vurdering endnu."),
        "",
        "DET GØR VI NU",
        str(narrative.get("naeste_fokus") or narrative.get("fokus") or "Ingen samlet vurdering endnu."),
        "",
        "NÆSTE 7 DAGE",
    ]

    actions = preview.get("actions", []) or []
    if actions:
        for action in actions:
            lines.append(f"- {action.get('date')} · {action_title(action)}")
            lines.append(f"  Fokus: {action.get('focus') or 'Dagens træningsformål'}")
            if action.get("reason"):
                lines.append(f"  Hvorfor: {action.get('reason')}")
    else:
        lines.append("- Ingen coach-plan endnu.")

    lines.extend([
        "",
        "DATA BAG VURDERINGEN",
        f"- Restitution: {recovery_label(str(recovery.get('state') or ''))}",
        f"- Seneste 7 dage: {recent.get('km', 0)} km / {recent.get('runs', 0)} ture; ugen før {previous.get('km', 0)} km ({pct(float(recent.get('km') or 0), float(previous.get('km') or 0)) or 'ingen sammenligning'}).",
        f"- Længste tur: {recent.get('longest_km', 0)} km · trailpas: {recent.get('trail_runs', 0)} · styrkepas 14 dage: {training.get('strength_sessions_last_14_days', 0)}.",
        f"- Plan gennemført: {match.get('matched', 0)}/{match.get('planned_workouts', 0)} afgjorte pas; {match.get('pending', 0)} afventer i dag; {match.get('uncertain', 0)} usikre.",
        f"- Mål: {event.get('name') or 'ikke sat'} · {event.get('days_to_event')} dage tilbage.",
    ])

    if recovery.get("signals"):
        lines.extend(["", "RESTITUTION – DETALJER"])
        for signal in recovery.get("signals", []):
            if signal.get("delta_pct") is not None:
                change = f"{signal.get('delta_pct'):+.1f}%"
            else:
                change = f"{signal.get('delta'):+.1f} {signal.get('unit') or ''}" if signal.get("delta") is not None else "ukendt"
            lines.append(f"- {signal.get('metric')}: seneste {signal.get('recent_median')} mod baseline {signal.get('baseline_median')} · forskel {change} · {signal.get('severity')}")

    lines.extend(["", "PLAN ↔ GENNEMFØRT"])
    for item in match.get("matches", [])[-12:]:
        shift = int(item.get("date_shift_days") or 0)
        timing = "samme dag" if shift == 0 else f"{abs(shift)} dag{'e' if abs(shift) != 1 else ''} {'senere' if shift > 0 else 'tidligere'}"
        lines.append(f"- ✓ {item.get('planned_date')} {item.get('planned_title')} → {item.get('completed_date')} {item.get('completed_name')} ({timing})")
    for item in match.get("pending_today", [])[:4]:
        lines.append(f"- … {item.get('planned_date')} {item.get('planned_title')} → afventer i dag")
    for item in match.get("uncertain_matches", [])[:4]:
        lines.append(f"- ? {item.get('planned_date')} {item.get('planned_title')} → usikkert match")
    for item in match.get("misses", [])[:4]:
        lines.append(f"- – {item.get('planned_date')} {item.get('planned_title')} → intet sikkert match")

    if preview.get("focus_next_14_days"):
        lines.extend(["", "FOKUS NÆSTE 14 DAGE"])
        for item in preview.get("focus_next_14_days", [])[:5]:
            lines.append(f"- {item}")

    lines.extend([
        "",
        "SYSTEM",
        f"- Træningsreferencer: {len(knowledge.get('references', []) or [])}",
        "- Nye/justerede pas må kun bruge godkendte Garmin-skabeloner.",
        "- Garmin write-back styres af den separate sikkerhedsgate i appen.",
    ])
    return "\n".join(lines)


def html_dashboard(state: dict[str, Any], preview: dict[str, Any], knowledge: dict[str, Any], goal: dict[str, Any]) -> str:
    recovery = state.get("recovery") or {}
    narrative = state.get("narrative") or {}
    training = state.get("training") or {}
    recent = training.get("last_7_days") or {}
    previous = training.get("previous_7_days") or {}
    event = state.get("event") or {}
    match = state.get("plan_match") or {}
    state_key = str(recovery.get("state") or "insufficient_history")
    status_class = {"green": "green", "yellow": "yellow", "red": "red"}.get(state_key, "neutral")

    sessions = []
    for action in preview.get("actions", []) or []:
        sessions.append(
            "<div class='session'>"
            f"<b>{esc(action.get('date'))} · {esc(action_title(action))}</b>"
            f"<div class='focus'>{esc(action.get('focus') or 'Dagens træningsformål')}</div>"
            f"<small>{esc(action.get('reason') or '')}</small>"
            "</div>"
        )

    plan_rows = []
    for item in match.get("matches", [])[-12:]:
        shift = int(item.get("date_shift_days") or 0)
        timing = "samme dag" if shift == 0 else f"{abs(shift)} dag{'e' if abs(shift) != 1 else ''} {'senere' if shift > 0 else 'tidligere'}"
        plan_rows.append(f"<li>✓ {esc(item.get('planned_date'))} {esc(item.get('planned_title'))} → {esc(item.get('completed_date'))} {esc(item.get('completed_name'))} <small>{esc(timing)}</small></li>")
    for item in match.get("pending_today", [])[:4]:
        plan_rows.append(f"<li>… {esc(item.get('planned_date'))} {esc(item.get('planned_title'))} <small>afventer i dag</small></li>")
    for item in match.get("uncertain_matches", [])[:4]:
        plan_rows.append(f"<li>? {esc(item.get('planned_date'))} {esc(item.get('planned_title'))} <small>usikkert match</small></li>")
    for item in match.get("misses", [])[:4]:
        plan_rows.append(f"<li>– {esc(item.get('planned_date'))} {esc(item.get('planned_title'))} <small>intet sikkert match</small></li>")

    focus_items = "".join(f"<li>{esc(x)}</li>" for x in (preview.get("focus_next_14_days", []) or state.get("focus_points", []) or []))

    return f"""<!doctype html><html lang='da'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Dagens coach</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0d1013;color:#eef2f5;margin:0;padding:24px;line-height:1.5}}main{{max-width:1050px;margin:auto}}h1{{margin-bottom:2px}}.sub{{color:#9aa5af;margin-bottom:22px}}.hero{{font-size:1.35rem;font-weight:750;line-height:1.35}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}}.card{{background:#171c21;border:1px solid #2b333c;border-radius:15px;padding:18px;margin-bottom:14px}}.metric{{font-size:1.55rem;font-weight:760}}.green{{color:#6ee7b7}}.yellow{{color:#fbbf24}}.red{{color:#fb7185}}.neutral{{color:#aaa}}.session{{padding:12px 0;border-top:1px solid #2b333c}}.session:first-child{{border-top:0}}.focus{{font-weight:650;margin-top:4px}}small{{display:block;color:#9aa5af;margin-top:4px}}li{{margin:7px 0}}
</style></head><body><main>
<h1>Dagens coach</h1><div class='sub'>Opdateret {esc(dt.datetime.now().astimezone().strftime('%d-%m-%Y %H:%M'))}</div>
<div class='card hero'>{esc(narrative.get('headline') or 'Her er vurderingen ud fra de tilgængelige data.')}</div>
<div class='grid'><div class='card'><div>Restitution</div><div class='metric {status_class}'>{esc(recovery_label(state_key))}</div></div><div class='card'><div>Seneste 7 dage</div><div class='metric'>{esc(recent.get('km',0))} km</div><small>{esc(recent.get('runs',0))} ture · ugen før {esc(previous.get('km',0))} km</small></div><div class='card'><div>Plan gennemført</div><div class='metric'>{esc(match.get('matched',0))}/{esc(match.get('planned_workouts',0))}</div><small>{esc(match.get('pending',0))} afventer i dag · {esc(match.get('uncertain',0))} usikre</small></div><div class='card'><div>Aktivt mål</div><div class='metric'>{esc(event.get('days_to_event'))} dage</div><small>{esc(event.get('name'))}</small></div></div>
<div class='card'><h2>Kroppen lige nu</h2><p>{esc(narrative.get('kroppen') or narrative.get('helbred') or '')}</p></div>
<div class='card'><h2>Træningen</h2><p>{esc(narrative.get('traeningen') or '')}</p></div>
<div class='card'><h2>Det gør vi nu</h2><p>{esc(narrative.get('naeste_fokus') or narrative.get('fokus') or '')}</p></div>
<div class='card'><h2>Næste 7 dage</h2>{''.join(sessions) or '<p>Ingen plan endnu.</p>'}</div>
<div class='card'><h2>Plan ↔ gennemført</h2><ul>{''.join(plan_rows) or '<li>Ingen afgjorte planposter endnu.</li>'}</ul></div>
<div class='card'><h2>Fokus næste 14 dage</h2><ul>{focus_items or '<li>Ingen ekstra fokuspunkt endnu.</li>'}</ul></div>
</main></body></html>"""


def main() -> int:
    state = load(STATE, {})
    preview = load(PREVIEW, {})
    knowledge = load(KNOWLEDGE, {})
    goal = load(GOAL, {})
    if not state:
        print("ERROR: Mangler coach_state.json")
        return 2
    TXT.parent.mkdir(parents=True, exist_ok=True)
    text = text_dashboard(state, preview, knowledge, goal)
    TXT.write_text(text, encoding="utf-8")
    HTML.write_text(html_dashboard(state, preview, knowledge, goal), encoding="utf-8")
    print(text)
    print(f"\nTekst: {TXT}")
    print(f"HTML: {HTML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
