r"""Render the local coach into one readable text + HTML dashboard.

No Garmin API calls and no write-back. This is only presentation of already
collected/validated local data.
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
    return html.escape(str(value or ""))


def recovery_label(state: str) -> str:
    return {
        "green": "GRØN – normal/stabil",
        "yellow": "GUL – lidt presset",
        "red": "RØD – tydeligt presset",
        "insufficient_history": "AFVENTER – for lidt personlig historik",
    }.get(state, state or "ukendt")


def percent_change(current: float, previous: float) -> str:
    if not previous:
        return ""
    pct = 100.0 * (current - previous) / previous
    return f" ({pct:+.0f}% mod ugen før)"


def useful_principles(preview: dict[str, Any]) -> list[dict[str, Any]]:
    used_tags = set()
    for action in preview.get("actions", []) or []:
        used_tags.update(str(t) for t in action.get("evidence_tags", []) or [])
    all_principles = preview.get("external_principles_available", []) or []
    used = [p for p in all_principles if p.get("tag") in used_tags]
    return used if used else all_principles[:4]


def source_map(goal: dict[str, Any], knowledge: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in goal.get("sources", []) or []:
        url = str(source.get("url") or "")
        if url and url not in seen:
            seen.add(url)
            rows.append({"kind": "Løbsmål", "title": str(source.get("title") or source.get("domain") or url), "url": url})
    for ref in knowledge.get("references", []) or []:
        for source in ref.get("sources", []) or []:
            url = str(source.get("url") or "")
            if url and url not in seen:
                seen.add(url)
                rows.append({"kind": "Træningsreference", "title": str(source.get("title") or source.get("domain") or url), "url": url})
    return rows[:12]


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
        "             DAGENS LOKALE COACH",
        "==============================================",
        f"Opdateret: {dt.datetime.now().astimezone().strftime('%d-%m-%Y %H:%M')}",
        "",
        "KORT FORTALT",
        f"- Restitution: {recovery_label(str(recovery.get('state') or ''))}",
        f"- Seneste 7 dage: {recent.get('km', 0)} km / {recent.get('runs', 0)} løb{percent_change(float(recent.get('km') or 0), float(previous.get('km') or 0))}",
        f"- Planmatch: {match.get('matched', 0)}/{match.get('planned_workouts', 0)} sikkert matchet; {match.get('uncertain', 0)} usikre.",
        f"- Mål: {event.get('name') or 'ikke sat'} | {event.get('days_to_event')} dage tilbage.",
        "",
        "HELBRED / RESTITUTION",
        str(narrative.get("helbred") or "Ingen samlet vurdering endnu."),
    ]

    for signal in recovery.get("signals", []) or []:
        if signal.get("severity") in {"yellow", "red"}:
            if signal.get("delta_pct") is not None:
                lines.append(f"- {signal.get('metric')}: {signal.get('delta_pct'):+.1f}% mod baseline ({signal.get('severity')})")
            else:
                lines.append(f"- {signal.get('metric')}: {signal.get('delta'):+.1f} {signal.get('unit')} mod baseline ({signal.get('severity')})")

    lines.extend([
        "",
        "TRÆNING PÅ SPORET",
        str(narrative.get("traening") or "Ingen samlet vurdering endnu."),
        f"- Længste tur sidste 7 dage: {recent.get('longest_km', 0)} km.",
        f"- Trailpas sidste 7 dage: {recent.get('trail_runs', 0)}.",
        f"- Styrkepas sidste 14 dage: {training.get('strength_sessions_last_14_days', 0)}.",
        "",
        "LØBET KRÆVER",
    ])
    course = event.get("course") or {}
    if isinstance(course, dict):
        surfaces = course.get("surface") or course.get("terrain") or []
        features = course.get("terrain_features") or []
        environment = course.get("environment") or []
        if surfaces:
            lines.append(f"- Underlag: {', '.join(str(x) for x in surfaces[:8])}")
        if features:
            lines.append(f"- Terræn: {', '.join(str(x) for x in features[:8])}")
        if environment:
            lines.append(f"- Miljø: {', '.join(str(x) for x in environment[:8])}")
    for p in event.get("training_priorities", [])[:6] if isinstance(event.get("training_priorities"), list) else []:
        if isinstance(p, dict):
            lines.append(f"- {p.get('priority')}: {p.get('reason') or ''}")

    lines.extend(["", "NÆSTE 7 DAGE"])
    labels = {"KEEP": "BEHOLD", "MOVE": "FLYT", "ADJUST": "JUSTÉR", "ADD": "TILFØJ", "REMOVE": "FJERN"}
    for action in preview.get("actions", []) or []:
        chosen = action.get("selected_template") or {}
        title = chosen.get("title") or action.get("source_title") or action.get("family") or "pas"
        lines.append(f"- {action.get('date')}: {labels.get(action.get('action'), action.get('action'))} {title}")
        lines.append(f"  Hvorfor: {action.get('reason')}")

    lines.extend(["", "FOKUS NÆSTE 14 DAGE"])
    for item in preview.get("focus_next_14_days", []) or state.get("focus_points", []) or []:
        lines.append(f"- {item}")

    principles = useful_principles(preview)
    if principles:
        lines.extend(["", "EKSTERN INSPIRATION – KUN DET RELEVANTE"])
        for item in principles[:5]:
            lines.append(f"- [{item.get('tag')}] {item.get('principle')} – {item.get('reference')}")

    attention = (narrative.get("opmaerksomhed") or [])
    if attention:
        lines.extend(["", "VÆR OPMÆRKSOM PÅ"])
        for item in attention[:5]:
            lines.append(f"- {item}")

    lines.extend([
        "",
        "SYSTEMSTATUS",
        f"- Aktivt løbsmål research: {'ja' if goal.get('sources') else 'legacy/manuelt'}",
        f"- Eksterne træningsreferencer: {len(knowledge.get('references', []) or [])}",
        f"- Garmin write-back: {'ON' if preview.get('garmin_writeback') else 'OFF'}",
        "- Nye/justerede pas må kun bruge godkendte Garmin-skabeloner.",
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

    action_html = []
    labels = {"KEEP": "Behold", "MOVE": "Flyt", "ADJUST": "Justér", "ADD": "Tilføj", "REMOVE": "Fjern"}
    for action in preview.get("actions", []) or []:
        chosen = action.get("selected_template") or {}
        title = chosen.get("title") or action.get("source_title") or action.get("family") or "pas"
        action_html.append(f"<div class='session'><b>{esc(action.get('date'))} · {esc(labels.get(action.get('action'), action.get('action')))}</b><br>{esc(title)}<small>{esc(action.get('reason'))}</small></div>")

    focus_html = "".join(f"<li>{esc(x)}</li>" for x in (preview.get("focus_next_14_days", []) or state.get("focus_points", []) or []))
    principle_html = "".join(
        f"<li><b>{esc(p.get('tag'))}</b> {esc(p.get('principle'))}<br><small>{esc(p.get('reference'))}</small></li>"
        for p in useful_principles(preview)[:5]
    ) or "<li>Ingen ekstern træningsreference er nødvendig endnu.</li>"
    source_html = "".join(
        f"<li><span class='pill'>{esc(s['kind'])}</span> <a href='{esc(s['url'])}' target='_blank'>{esc(s['title'])}</a></li>"
        for s in source_map(goal, knowledge)
    ) or "<li>Ingen eksterne kilder registreret.</li>"

    return f"""<!doctype html>
<html lang='da'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Dagens lokale coach</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#111;color:#eee;margin:0;padding:24px;line-height:1.45}}
main{{max-width:1050px;margin:auto}} h1{{margin:0 0 4px}} .sub{{color:#aaa;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:18px}}
.card{{background:#1c1c1c;border:1px solid #333;border-radius:14px;padding:18px;margin-bottom:14px}}
.metric{{font-size:1.6rem;font-weight:700}} .green{{color:#75d37b}} .yellow{{color:#ffd166}} .red{{color:#ff6b6b}} .neutral{{color:#bbb}}
.session{{border-left:3px solid #777;padding:8px 12px;margin:9px 0;background:#181818;border-radius:4px}} small{{display:block;color:#aaa;margin-top:4px}}
ul{{padding-left:20px}} a{{color:#8ecaff}} .pill{{font-size:.75rem;padding:2px 6px;border:1px solid #555;border-radius:10px;color:#bbb}}
</style></head><body><main>
<h1>Dagens lokale coach</h1><div class='sub'>Opdateret {esc(dt.datetime.now().astimezone().strftime('%d-%m-%Y %H:%M'))} · Garmin write-back er OFF</div>
<div class='grid'>
<div class='card'><div>Restitution</div><div class='metric {status_class}'>{esc(recovery_label(state_key))}</div></div>
<div class='card'><div>Seneste 7 dage</div><div class='metric'>{esc(recent.get('km',0))} km</div><small>{esc(recent.get('runs',0))} ture{esc(percent_change(float(recent.get('km') or 0), float(previous.get('km') or 0)))}</small></div>
<div class='card'><div>Plan vs. gennemført</div><div class='metric'>{esc(match.get('matched',0))}/{esc(match.get('planned_workouts',0))}</div><small>{esc(match.get('uncertain',0))} usikre match</small></div>
<div class='card'><div>Aktivt mål</div><div class='metric'>{esc(event.get('days_to_event'))} dage</div><small>{esc(event.get('name'))}</small></div>
</div>
<div class='card'><h2>Helbred / restitution</h2><p>{esc(narrative.get('helbred') or 'Ingen samlet vurdering endnu.')}</p></div>
<div class='card'><h2>Træningen på sporet</h2><p>{esc(narrative.get('traening') or 'Ingen samlet vurdering endnu.')}</p><p>Længste tur: <b>{esc(recent.get('longest_km',0))} km</b> · Trailpas: <b>{esc(recent.get('trail_runs',0))}</b> · Styrke 14 dage: <b>{esc(training.get('strength_sessions_last_14_days',0))}</b></p></div>
<div class='card'><h2>Næste 7 dage</h2>{''.join(action_html) or '<p>Ingen planlagte ændringer.</p>'}</div>
<div class='card'><h2>Fokus næste 14 dage</h2><ul>{focus_html}</ul></div>
<div class='card'><h2>Ekstern inspiration – filtreret</h2><p>Disse principper er inspiration. Dine Garmin-data og det aktuelle løb har altid højere prioritet.</p><ul>{principle_html}</ul></div>
<div class='card'><h2>Kilder</h2><ul>{source_html}</ul></div>
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
    print(f"Dashboard: {HTML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
