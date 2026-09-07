r'''Local browser UI for Garmin Local Coach.

Athlete-facing UI: conclusion first, details on click. Runs only on 127.0.0.1;
Garmin credentials never reach the browser. The UI owns interactive coach jobs so
status and errors are visible instead of being hidden in a separate PowerShell.
'''

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from plan_identity import position_for, safe_code

ROOT = Path(r"C:\GarminCoach")
REPO = ROOT / "garmin-connect-mcp"
DATA = ROOT / "data"
COACH_PS1 = REPO / "local_coach" / "coach.ps1"
SETTINGS = DATA / "coach_settings.json"
PROFILE = DATA / "athlete_profile.json"
STATE = DATA / "coach_state.json"
PREVIEW = DATA / "coach_preview.json"
GOAL = DATA / "active_goal.json"
KNOWLEDGE = DATA / "training_knowledge.json"
SNAPSHOT = DATA / "snapshot.json"
SYSTEM = DATA / "system_status.json"
DASHBOARD = DATA / "DAGENS_COACH.html"
LOG = DATA / "ui_last_job.log"
AUTOMATION_PS1 = REPO / "local_coach" / "install_automation.ps1"

DEFAULT_SETTINGS = {
    "schedule": {"days": ["MON", "THU", "SUN"], "time": "22:00"},
    "auto_analysis_enabled": True,
    "writeback_test_passed": False,
    "garmin_writeback_enabled": False,
    "allow_auto_remove": False,
    "max_calendar_changes_per_run": 3,
    "open_ui_at_login": True,
}

# Current block, based on the athlete's stated position in the Thy Trail plan.
# These are user settings, not hard-coded race logic; they are editable in the UI.
DEFAULT_PROFILE = {
    "max_running_days_per_week": None,
    "preferred_long_run_days": [],
    "max_weekday_session_minutes": None,
    "notes": "",
    "plan_code": "ThyTrail",
    "plan_anchor_monday": "2026-09-07",
    "plan_anchor_week": 3,
}

JOB_LOCK = threading.Lock()
JOB = {
    "running": False,
    "kind": None,
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "message": "Klar",
}


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def settings() -> dict[str, Any]:
    current = load(SETTINGS, {})
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    if isinstance(current, dict):
        for key, value in current.items():
            if key != "schedule":
                merged[key] = value
        if isinstance(current.get("schedule"), dict):
            merged["schedule"].update(current["schedule"])
    if not SETTINGS.exists():
        save(SETTINGS, merged)
    return merged


def profile() -> dict[str, Any]:
    current = load(PROFILE, {})
    merged = dict(DEFAULT_PROFILE)
    if isinstance(current, dict):
        merged.update(current)
    if not PROFILE.exists():
        save(PROFILE, merged)
    return merged


def powershell_exe() -> str:
    candidate = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    return str(candidate) if candidate.exists() else "powershell.exe"


def run_job(kind: str, text: str = "") -> bool:
    with JOB_LOCK:
        if JOB["running"]:
            return False
        JOB.update({
            "running": True,
            "kind": kind,
            "started_at": dt.datetime.now().astimezone().isoformat(),
            "finished_at": None,
            "returncode": None,
            "message": "Henter og analyserer Garmin-data…" if kind == "status" else "Coachen arbejder…",
        })

    def worker() -> None:
        command = [
            powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(COACH_PS1), kind,
        ]
        if text.strip():
            command.append(text.strip())
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            with LOG.open("w", encoding="utf-8", errors="replace") as handle:
                proc = subprocess.run(
                    command,
                    cwd=str(REPO),
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=60 * 35,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            if proc.returncode == 0:
                message = "Opdateret"
            elif proc.returncode == 9:
                message = "Coachen kører allerede"
            else:
                message = f"Fejl (kode {proc.returncode})"
            with JOB_LOCK:
                JOB.update({
                    "running": False,
                    "finished_at": dt.datetime.now().astimezone().isoformat(),
                    "returncode": proc.returncode,
                    "message": message,
                })
        except Exception as exc:
            LOG.write_text(str(exc), encoding="utf-8")
            with JOB_LOCK:
                JOB.update({
                    "running": False,
                    "finished_at": dt.datetime.now().astimezone().isoformat(),
                    "returncode": -1,
                    "message": f"Fejl: {exc}",
                })

    threading.Thread(target=worker, daemon=True).start()
    return True


def install_automation(enabled: bool) -> tuple[bool, str]:
    cfg = settings()
    schedule = cfg.get("schedule") or {}
    days = ",".join(schedule.get("days") or ["MON", "THU", "SUN"])
    run_time = str(schedule.get("time") or "22:00")
    command = [
        powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(AUTOMATION_PS1), "-Days", days, "-Time", run_time,
    ]
    if not enabled:
        command.append("-DisableCoach")
    if cfg.get("open_ui_at_login"):
        command.append("-InstallUiStartup")
    try:
        proc = subprocess.run(
            command,
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return proc.returncode == 0, output[-5000:]
    except Exception as exc:
        return False, str(exc)


def iso_age_minutes(value: Any) -> float | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
        return max(0.0, (dt.datetime.now().astimezone() - parsed.astimezone()).total_seconds() / 60.0)
    except Exception:
        return None


def recent_activities(snapshot: dict[str, Any], days: int = 8) -> list[dict[str, Any]]:
    cutoff = dt.date.today() - dt.timedelta(days=days - 1)
    rows = []
    for activity in snapshot.get("all_activities", []) if isinstance(snapshot, dict) else []:
        try:
            day = dt.date.fromisoformat(str(activity.get("start") or "")[:10])
        except Exception:
            continue
        if day < cutoff:
            continue
        distance = None
        minutes = None
        try:
            if activity.get("distance_m") is not None:
                distance = round(float(activity["distance_m"]) / 1000.0, 1)
        except Exception:
            pass
        try:
            if activity.get("duration_s") is not None:
                minutes = round(float(activity["duration_s"]) / 60.0)
        except Exception:
            pass
        rows.append({
            "date": day.isoformat(),
            "name": activity.get("name"),
            "type": activity.get("type"),
            "distance_km": distance,
            "minutes": minutes,
            "training_load": activity.get("training_load"),
            "aerobic_te": activity.get("aerobic_te"),
            "anaerobic_te": activity.get("anaerobic_te"),
        })
    return sorted(rows, key=lambda row: row["date"], reverse=True)


def current_plan_position(prof: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    try:
        return position_for(dt.date.today(), prof, event)
    except Exception:
        return {"name": None, "week": None, "day": None}


def compact_status() -> dict[str, Any]:
    state = load(STATE, {})
    preview = load(PREVIEW, {})
    goal = load(GOAL, {})
    knowledge = load(KNOWLEDGE, {})
    snapshot = load(SNAPSHOT, {})
    system = load(SYSTEM, {})
    prof = profile()
    event = state.get("event") or {}
    generated = state.get("generated_at") if isinstance(state, dict) else None
    age = iso_age_minutes(generated)
    return {
        "has_data": bool(state),
        "data_age_minutes": round(age, 1) if age is not None else None,
        "data_generated_at": generated,
        "job": dict(JOB),
        "settings": settings(),
        "profile": prof,
        "plan_position": current_plan_position(prof, event),
        "system": system,
        "recovery": state.get("recovery") or {},
        "training": state.get("training") or {},
        "recent": (state.get("training") or {}).get("last_7_days") or {},
        "plan_match": state.get("plan_match") or {},
        "event": event,
        "goal": goal,
        "narrative": state.get("narrative") or {},
        "preview": preview,
        "recent_activities": recent_activities(snapshot),
        "knowledge_count": len(knowledge.get("references", []) or []) if isinstance(knowledge, dict) else 0,
        "dashboard_exists": DASHBOARD.exists(),
        "last_log": LOG.read_text(encoding="utf-8", errors="replace")[-9000:] if LOG.exists() else "",
    }


def needs_initial_run() -> bool:
    state = load(STATE, {})
    if not state:
        return True
    age = iso_age_minutes(state.get("generated_at")) if isinstance(state, dict) else None
    return age is None or age > 12 * 60


INDEX = r'''<!doctype html><html lang="da"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Garmin Local Coach</title>
<style>
:root{--bg:#0b0d10;--panel:#151a20;--panel2:#1a2027;--border:#2b333d;--text:#edf2f6;--muted:#94a0ad;--green:#6ee7b7;--blue:#7dd3fc;--yellow:#fbbf24;--red:#fb7185}*{box-sizing:border-box}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;background:radial-gradient(circle at 8% 0,#17232e 0,#0b0d10 36%);color:var(--text);min-height:100vh}header{max-width:1220px;margin:auto;padding:30px 24px 8px;display:flex;justify-content:space-between;align-items:center;gap:18px}.brand h1{margin:0;font-size:28px}.brand p{margin:5px 0;color:var(--muted)}.badge{border:1px solid var(--border);border-radius:999px;padding:7px 11px;color:var(--muted);font-size:13px;background:#101419}.live{color:var(--green)}main{max-width:1220px;margin:auto;padding:14px 24px 60px}.banner{display:none;padding:11px 14px;margin:8px 0 14px;border-radius:12px;border:1px solid var(--border);background:#11161b;color:var(--muted)}.banner.show{display:block}.banner.bad{border-color:#56303a;color:#ffc1cc;background:#1b1115}.banner.good{border-color:#205443;color:#baf2dd;background:#0d1915}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:10px 0 18px}.metric,.card{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--border);border-radius:18px;box-shadow:0 18px 60px rgba(0,0,0,.18)}button.metric{padding:18px;text-align:left;color:inherit;cursor:pointer;transition:.15s transform,.15s border-color}button.metric:hover{transform:translateY(-2px);border-color:#465564}.metric label{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em;pointer-events:none}.metric b{display:block;font-size:25px;margin-top:7px}.metric small{color:var(--muted)}.hint{font-size:11px;color:#667482;margin-top:6px}.layout{display:grid;grid-template-columns:1.45fr .82fr;gap:14px}.card{padding:20px;margin-bottom:14px}.card h2{font-size:17px;margin:0 0 12px}.card h3{font-size:13px;color:var(--muted);margin:15px 0 7px}.coachHeadline{font-size:22px;line-height:1.28;font-weight:760;margin:4px 0 20px}.coachBlock{padding:14px 0;border-top:1px solid var(--border)}.coachBlock:first-of-type{border-top:0}.coachBlock h3{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin:0 0 7px}.coachtext{font-size:16px;line-height:1.58;color:#dce3e9}.session{padding:13px 0;border-top:1px solid var(--border);cursor:pointer}.session:hover{background:#171d23}.session .date{color:var(--blue);font-weight:700}.session .planname{font-weight:800;margin-left:5px}.session .focus{display:block;margin-top:4px;font-size:15px}.session small{display:block;color:var(--muted);margin-top:5px;line-height:1.45}input,textarea,select{width:100%;background:#0e1216;color:var(--text);border:1px solid var(--border);border-radius:11px;padding:11px 12px;font:inherit;outline:none}textarea{min-height:76px;resize:vertical}.row{display:flex;gap:8px}.row>*{flex:1}.days{display:flex;flex-wrap:wrap;gap:7px;margin:9px 0}.days label{background:#0e1216;border:1px solid var(--border);padding:6px 9px;border-radius:10px;font-size:13px}.days input{width:auto;margin-right:4px}button{border:0;border-radius:11px;padding:10px 14px;font-weight:750;cursor:pointer;background:var(--green);color:#062017}button.secondary{background:#202832;color:var(--text);border:1px solid var(--border)}button.warn{background:var(--yellow);color:#231900}button:disabled{opacity:.42;cursor:not-allowed}.actions{display:flex;gap:8px;flex-wrap:wrap}.message{padding:9px 11px;border-radius:10px;background:#101419;color:var(--muted);font-size:13px;margin-top:10px;white-space:pre-line}.notice{border-left:3px solid var(--yellow);padding:9px 12px;background:#18170e;color:#d6cfaa;border-radius:7px;font-size:13px;margin-top:12px}.notice.ok{border-left-color:var(--green);background:#0d1a16;color:#bde7d7}.log{display:none;white-space:pre-wrap;font:12px ui-monospace,SFMono-Regular,Consolas,monospace;background:#080a0c;border-radius:10px;padding:12px;max-height:300px;overflow:auto;color:#aab4bf}.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.68);z-index:20;padding:24px}.modal.open{display:flex;align-items:center;justify-content:center}.modalbox{width:min(760px,100%);max-height:88vh;overflow:auto;background:#141a20;border:1px solid #37414c;border-radius:18px;padding:22px;box-shadow:0 24px 90px rgba(0,0,0,.5)}.modalhead{display:flex;justify-content:space-between;gap:15px;align-items:center}.modalhead h2{margin:0}.close{background:#202832;color:var(--text);padding:7px 10px}.detail{padding:12px 0;border-top:1px solid var(--border)}.detail:first-child{border-top:0}.detail strong{display:block;margin-bottom:4px}.muted{color:var(--muted)}@media(max-width:920px){.metrics{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}}@media(max-width:520px){.metrics{grid-template-columns:1fr}header{align-items:flex-start;flex-direction:column;gap:10px}.row{flex-direction:column}}
</style></head><body>
<header><div class="brand"><h1>Garmin Local Coach</h1><p>Din personlige træner kører lokalt på Acer’en</p></div><div id="jobBadge" class="badge">Klar</div></header><main><div id="banner" class="banner"></div>
<div class="metrics">
<button class="metric" onclick="openDetails('recovery')"><label>Restitution</label><b id="recovery">–</b><small id="recoveryDays">Ingen data endnu</small><div class="hint">Klik for detaljer</div></button>
<button class="metric" onclick="openDetails('training')"><label>Seneste 7 dage</label><b id="weekKm">–</b><small id="weekRuns">Ingen data endnu</small><div class="hint">Klik for ture og udvikling</div></button>
<button class="metric" onclick="openDetails('plan')"><label>Plan gennemført</label><b id="planMatch">–</b><small id="planUncertain">Ingen data endnu</small><div class="hint">Klik for plan ↔ aktivitet</div></button>
<button class="metric" onclick="openDetails('goal')"><label>Aktivt mål</label><b id="daysTo">–</b><small id="eventName">Intet mål analyseret endnu</small><div class="hint">Klik for løbets krav</div></button>
</div>
<div class="layout"><section>
<div class="card"><h2>Dagens coach</h2><div id="headline" class="coachHeadline">Jeg henter først data, før jeg vurderer noget.</div><div class="coachBlock"><h3>Kroppen lige nu</h3><div id="healthText" class="coachtext">Ingen analyse endnu.</div></div><div class="coachBlock"><h3>Træningen</h3><div id="trainingText" class="coachtext">Ingen analyse endnu.</div></div><div class="coachBlock"><h3>Det gør vi nu</h3><div id="focusText" class="coachtext">Ingen analyse endnu.</div></div></div>
<div class="card"><h2>Næste 7 dage</h2><div id="sessions"><div class="message">Ingen plan endnu.</div></div><div id="writeNotice" class="notice">Garmin write-back er stadig låst.</div><div class="actions" style="margin-top:10px"><button id="testWrite" class="warn" onclick="runCommand('test-writeback')">Test én Garmin-kalenderændring</button></div></div>
<div class="card"><h2>Teknisk log</h2><button class="secondary" onclick="toggleLog()">Vis/skjul</button><pre id="log" class="log"></pre></div>
</section><aside>
<div class="card"><h2>Opdatér coach</h2><p class="coachtext" style="font-size:14px">Henter Garmin, analyserer restitution og træning, sammenholder kalenderen og laver et nyt valideret forslag.</p><div class="actions"><button id="runNow" onclick="runCommand('status')">Kør nu</button><button class="secondary" onclick="window.open('/dashboard','_blank')">Fuld rapport</button></div><div id="runMessage" class="message">Klar.</div></div>
<div class="card"><h2>Mit løbsmål</h2><textarea id="goalInput" placeholder="Fx: Etapeløb Thy Trail 31/10-1/11 2026, lang distance"></textarea><button style="margin-top:8px" onclick="submitGoal()">Undersøg og sæt som mål</button><div class="message">Coachen finder løbet, miljøet, underlaget og de særlige krav fra webkilder.</div></div>
<div class="card"><h2>Planens navn</h2><div class="row"><div><label>Kort navn på Garmin</label><input id="planCode" maxlength="18" placeholder="ThyTrail"></div><div><label>Denne uge er uge</label><input id="planWeek" type="number" min="1" max="99" placeholder="3"></div></div><div class="message">D1=mandag … D7=søndag. Fx ThyTrailW3D4. Ugen tæller automatisk op hver mandag.</div></div>
<div class="card"><h2>Træningsinspiration</h2><textarea id="planInput" placeholder="Et link eller fx: Hal Higdon Intermediate Marathon"></textarea><button style="margin-top:8px" class="secondary" onclick="submitPlan()">Analysér program</button><div id="knowledgeText" class="message"></div></div>
<div class="card"><h2>Mine praktiske rammer</h2><div class="row"><div><label>Maks løbedage/uge</label><select id="maxRunDays"><option value="">Automatisk</option><option>3</option><option>4</option><option>5</option><option>6</option></select></div><div><label>Maks hverdagsminutter</label><input id="maxWeekday" type="number" min="20" max="240" placeholder="fx 70"></div></div><h3>Foretrukken langtur</h3><div class="days" id="longDays"></div><label>Andre praktiske noter</label><textarea id="profileNotes" placeholder="Fx: styrke passer bedst tirsdag; søndag er ofte travl"></textarea><button class="secondary" style="margin-top:8px" onclick="saveProfile()">Gem plan og rammer</button><div id="profileMessage" class="message"></div></div>
<div class="card"><h2>Automatik</h2><label>Tidspunkt</label><input id="scheduleTime" type="time" value="22:00"><div class="days" id="days"></div><label style="display:block;margin:10px 0"><input id="autoAnalysis" type="checkbox" style="width:auto"> Automatisk coach</label><label style="display:block;margin:10px 0;color:var(--muted)"><input id="writeback" type="checkbox" style="width:auto" disabled> Opdatér Garmin-kalenderen automatisk</label><button class="secondary" onclick="saveAutomation()">Gem og installér automatik</button><div id="automationMessage" class="message"></div></div>
</aside></div></main>
<div id="modal" class="modal" onclick="if(event.target===this)closeModal()"><div class="modalbox"><div class="modalhead"><h2 id="modalTitle">Detaljer</h2><button class="close" onclick="closeModal()">Luk</button></div><div id="modalBody" style="margin-top:12px"></div></div></div>
<script>
let S={};const dayNames={MON:'Man',TUE:'Tir',WED:'Ons',THU:'Tor',FRI:'Fre',SAT:'Lør',SUN:'Søn'};function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}async function api(path,options={}){const r=await fetch(path,options),j=await r.json();if(!r.ok)throw new Error(j.error||j.message||'Fejl');return j}function recLabel(s){return {green:'Grøn',yellow:'Gul',red:'Rød',insufficient_history:'Afventer'}[s]||'Ukendt'}function dayBoxes(id,selected){document.getElementById(id).innerHTML=Object.entries(dayNames).map(([k,v])=>`<label><input type="checkbox" value="${k}" ${selected.includes(k)?'checked':''}>${v}</label>`).join('')}function fmtDate(x){if(!x)return'–';try{return new Intl.DateTimeFormat('da-DK',{weekday:'short',day:'numeric',month:'short'}).format(new Date(x+'T12:00:00'))}catch{return x}}
function renderSessions(p){const el=document.getElementById('sessions'),a=p.actions||[];el.innerHTML=a.length?a.map((x,i)=>{const name=x.plan_name||((x.selected_template||{}).title)||x.source_title||x.family||'Træning';const focus=x.focus||'Dagens træningsfokus';return `<div class="session" onclick="sessionDetail(${i})"><span class="date">${esc(fmtDate(x.date))}</span><span class="planname">${esc(name)}</span><span class="focus">${esc(focus)}</span><small>${esc(x.reason||'')}</small></div>`}).join(''):'<div class="message">Ingen plan endnu.</div>'}
function statusBanner(s){const b=document.getElementById('banner'),sys=s.system||{},job=s.job||{};b.className='banner';if(job.running){b.className='banner show';b.textContent='Coachen arbejder: '+(job.message||'henter data…');return}if(sys.ok===false){b.className='banner show bad';const bad=(sys.checks||[]).filter(x=>x.status==='fatal').map(x=>x.message).slice(0,2);b.textContent='Data er ikke godkendt endnu: '+(bad.join(' · ')||'se teknisk log');return}if(s.has_data){b.className='banner show good';const age=s.data_age_minutes==null?'':` · opdateret for ${Math.round(s.data_age_minutes)} min siden`;b.textContent='Garmin-data er indlæst og valideret'+age;return}b.className='banner show';b.textContent='Ingen friske Garmin-data endnu. Første opdatering starter automatisk.'}
async function refresh(){try{S=await api('/api/status');const s=S,r=s.recovery||{},recent=s.recent||{},m=s.plan_match||{},e=s.event||{},n=s.narrative||{},p=s.preview||{},cfg=s.settings||{},prof=s.profile||{},pos=s.plan_position||{},has=s.has_data;statusBanner(s);document.getElementById('recovery').textContent=has?recLabel(r.state):'–';document.getElementById('recoveryDays').textContent=has?((r.history_days||0)+' historikdage'):'Ingen data endnu';document.getElementById('weekKm').textContent=has?((recent.km??0)+' km'):'–';document.getElementById('weekRuns').textContent=has?((recent.runs??0)+' ture'):'Ingen data endnu';document.getElementById('planMatch').textContent=has&&m.planned_workouts!=null?((m.matched??0)+' / '+(m.planned_workouts??0)):'–';document.getElementById('planUncertain').textContent=has?((m.uncertain??0)+' usikre match'):'Ingen data endnu';document.getElementById('daysTo').textContent=has&&e.days_to_event!=null?(e.days_to_event+' dage'):'–';document.getElementById('eventName').textContent=e.name||'Intet mål analyseret endnu';document.getElementById('headline').textContent=n.headline||(!has?'Jeg henter først data, før jeg vurderer noget.':'Her er min vurdering ud fra de data, jeg har.');document.getElementById('healthText').textContent=n.kroppen||n.helbred||'Ingen analyse endnu.';document.getElementById('trainingText').textContent=n.traeningen||'Ingen analyse endnu.';document.getElementById('focusText').textContent=n.naeste_fokus||n.fokus||'Ingen analyse endnu.';renderSessions(p);document.getElementById('knowledgeText').textContent=(s.knowledge_count||0)+' træningsreference(r) i vidensbiblioteket.';const jb=s.job||{},badge=document.getElementById('jobBadge');badge.textContent=jb.running?'Coachen arbejder…':(jb.message||'Klar');badge.className='badge '+(jb.running?'live':'');document.getElementById('runNow').disabled=!!jb.running;document.getElementById('testWrite').disabled=!!jb.running||!!cfg.writeback_test_passed||!has;document.getElementById('runMessage').textContent=jb.running?'Arbejder i baggrunden…':(jb.message||'Klar');document.getElementById('log').textContent=s.last_log||'';const test=!!cfg.writeback_test_passed,wb=document.getElementById('writeback');wb.disabled=!test;wb.checked=!!cfg.garmin_writeback_enabled;document.getElementById('writeNotice').className='notice '+(test?'ok':'');document.getElementById('writeNotice').textContent=test?(cfg.garmin_writeback_enabled?'Write-back test er bestået, og automatisk Garmin-opdatering er ON.':'Write-back test er bestået. Du kan nu vælge automatisk Garmin-opdatering.'):'Garmin write-back er låst, indtil én valideret kalenderændring er testet.';if(!window.settingsLoaded){document.getElementById('scheduleTime').value=((cfg.schedule||{}).time)||'22:00';document.getElementById('autoAnalysis').checked=cfg.auto_analysis_enabled!==false;dayBoxes('days',((cfg.schedule||{}).days)||['MON','THU','SUN']);document.getElementById('maxRunDays').value=prof.max_running_days_per_week??'';document.getElementById('maxWeekday').value=prof.max_weekday_session_minutes??'';document.getElementById('profileNotes').value=prof.notes||'';document.getElementById('planCode').value=prof.plan_code||'ThyTrail';document.getElementById('planWeek').value=pos.week??prof.plan_anchor_week??3;dayBoxes('longDays',prof.preferred_long_run_days||[]);window.settingsLoaded=true}}catch(e){document.getElementById('runMessage').textContent='UI-fejl: '+e.message}}
function modal(title,html){document.getElementById('modalTitle').textContent=title;document.getElementById('modalBody').innerHTML=html;document.getElementById('modal').classList.add('open')}function closeModal(){document.getElementById('modal').classList.remove('open')}function detailRow(title,text){return `<div class="detail"><strong>${esc(title)}</strong><div class="muted">${esc(text)}</div></div>`}
function openDetails(kind){const s=S;if(!s.has_data){modal('Ingen data endnu','<div class="message">Coachen er ved at hente de første Garmin-data. Detaljerne kommer her bagefter.</div>');return}if(kind==='plan'){const m=s.plan_match||{};let h='<p class="muted">Et planlagt pas må gerne ligge ±1 dag fra aktiviteten. ±2 dage kræver et meget stærkt match. Usikre match bliver ikke tvunget igennem.</p>';for(const x of m.matches||[]){const shift=Number(x.date_shift_days||0),when=shift===0?'samme dag':`${Math.abs(shift)} dag${Math.abs(shift)===1?'':'e'} ${shift>0?'senere':'tidligere'}`;h+=detailRow(`${fmtDate(x.planned_date)} · ${x.planned_title}`,`${x.completed_name||'Aktivitet'} · ${fmtDate(x.completed_date)} (${when})${x.completed_km!=null?' · '+x.completed_km+' km':''}`)}for(const x of m.uncertain_matches||[]){h+=detailRow('Usikkert: '+x.planned_title,`Bedste kandidat: ${x.best_candidate||'ukendt'} · coachen bruger ikke dette som facit.`)}for(const x of m.misses||[]){h+=detailRow('Intet sikkert match: '+x.planned_title,`Planlagt ${fmtDate(x.planned_date)}. Passet bliver ikke automatisk presset ind senere.`)}if(!(m.matches||[]).length&&!(m.uncertain_matches||[]).length&&!(m.misses||[]).length)h+='<div class="message">Ingen planposter i matchvinduet.</div>';modal('Plan gennemført',h)}else if(kind==='recovery'){const r=s.recovery||{},l=r.latest||{};let h=detailRow('Vurdering',recLabel(r.state)+' · '+(r.history_days||0)+' historikdage');for(const x of r.signals||[]){const d=x.delta_pct!=null?`${x.delta_pct}%`:(`${x.delta??'–'} ${x.unit||''}`);h+=detailRow(x.metric,`Seneste median ${x.recent_median??'–'} · baseline ${x.baseline_median??'–'} · forskel ${d}`)}h+=detailRow('Seneste målinger',`Søvn ${l.sleep_hours??'–'} t · søvnscore ${l.sleep_score??'–'} · HRV ${l.hrv??'–'} · hvilepuls ${l.resting_hr??'–'} · Body Battery ${l.body_battery??'–'} · stress ${l.stress??'–'}`);modal('Restitution – hvad bygger vurderingen på?',h)}else if(kind==='training'){const t=s.training||{},r=t.last_7_days||{},p=t.previous_7_days||{};let h=detailRow('Seneste 7 dage',`${r.runs??0} ture · ${r.km??0} km · ${r.minutes??0} min · længste ${r.longest_km??0} km · ${r.elevation_m??0} hm`)+detailRow('Ugen før',`${p.runs??0} ture · ${p.km??0} km · længste ${p.longest_km??0} km`);for(const a of s.recent_activities||[]){h+=detailRow(`${fmtDate(a.date)} · ${a.name||a.type||'Aktivitet'}`,`${a.distance_km!=null?a.distance_km+' km · ':''}${a.minutes!=null?a.minutes+' min':''}${a.training_load!=null?' · belastning '+a.training_load:''}`)}modal('Seneste træning',h)}else if(kind==='goal'){const g=s.goal&&Object.keys(s.goal).length?s.goal:(s.event||{});let h=detailRow('Løb',g.event_name||g.name||'Aktivt mål');if(g.event_dates)h+=detailRow('Dato',g.event_dates.join(', '));const d=g.distance||{};if(d.total_km!=null)h+=detailRow('Distance',d.total_km+' km'+((d.stages_km||[]).length?' · etaper '+d.stages_km.join(' + ')+' km':''));for(const x of g.training_priorities||[]){h+=detailRow(x.priority||'Træningsfokus',x.reason||'')}for(const x of g.documented_requirements||[]){h+=detailRow('Løbsfakta',x.fact||'')}modal('Aktivt mål – hvad kræver løbet?',h)}}
function sessionDetail(i){const x=(S.preview.actions||[])[i];if(!x)return;const style=x.workout_style||((x.selected_template||{}).title)||x.source_title||x.family||'pas';let h=detailRow('Plan-navn',x.plan_name||'–')+detailRow('Fokus',x.focus||'Dagens træningsformål')+detailRow('Garmin-skabelon',style)+detailRow('Coachens begrundelse',x.reason||'Ingen begrundelse');if(x.source_date)h+=detailRow('Udgangspunkt',`Eksisterende pas ${fmtDate(x.source_date)}${x.source_title?' · '+x.source_title:''}`);modal('Træningspas',h)}
async function runCommand(kind,text=''){try{await api('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,text})});refresh()}catch(e){alert(e.message)}}function submitGoal(){const v=document.getElementById('goalInput').value.trim();if(v)runCommand('goal',v)}function submitPlan(){const v=document.getElementById('planInput').value.trim();if(v)runCommand('plan',v)}async function saveAutomation(){const days=[...document.querySelectorAll('#days input:checked')].map(x=>x.value),payload={schedule:{days,time:document.getElementById('scheduleTime').value||'22:00'},auto_analysis_enabled:document.getElementById('autoAnalysis').checked,garmin_writeback_enabled:document.getElementById('writeback').checked};try{const r=await api('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});document.getElementById('automationMessage').textContent=r.message||'Gemt.';window.settingsLoaded=false;refresh()}catch(e){document.getElementById('automationMessage').textContent='Fejl: '+e.message}}async function saveProfile(){const longDays=[...document.querySelectorAll('#longDays input:checked')].map(x=>x.value),payload={max_running_days_per_week:document.getElementById('maxRunDays').value||null,max_weekday_session_minutes:document.getElementById('maxWeekday').value||null,preferred_long_run_days:longDays,notes:document.getElementById('profileNotes').value.trim(),plan_code:document.getElementById('planCode').value.trim(),plan_current_week:document.getElementById('planWeek').value||null};try{const r=await api('/api/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});document.getElementById('profileMessage').textContent=r.message||'Gemt.';window.settingsLoaded=false;refresh()}catch(e){document.getElementById('profileMessage').textContent='Fejl: '+e.message}}function toggleLog(){const e=document.getElementById('log');e.style.display=e.style.display==='block'?'none':'block'}refresh();setInterval(refresh,3000);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = "GarminLocalCoach/3.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_bytes(self, payload: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def json_response(self, payload: Any, status: int = 200) -> None:
        self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def read_json(self) -> dict[str, Any]:
        try:
            size = min(int(self.headers.get("Content-Length", "0")), 100_000)
            raw = self.rfile.read(size)
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            return self.send_bytes(INDEX.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/status":
            return self.json_response(compact_status())
        if path == "/dashboard":
            if DASHBOARD.exists():
                return self.send_bytes(DASHBOARD.read_bytes(), "text/html; charset=utf-8")
            return self.send_bytes(b"Dashboard not generated yet", "text/plain; charset=utf-8", 404)
        return self.json_response({"error": "Ikke fundet"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        body = self.read_json()

        if path == "/api/run":
            kind = str(body.get("kind") or "status")
            if kind not in {"status", "goal", "plan", "full", "test-writeback"}:
                return self.json_response({"error": "Ukendt coach-kommando"}, 400)
            text = str(body.get("text") or "")[:2000]
            if kind in {"goal", "plan"} and not text.strip():
                return self.json_response({"error": "Der mangler input"}, 400)
            if not run_job(kind, text):
                return self.json_response({"error": "Coachen arbejder allerede"}, 409)
            return self.json_response({"ok": True, "message": "Startet"}, 202)

        if path == "/api/profile":
            prof = profile()
            max_days = body.get("max_running_days_per_week")
            max_minutes = body.get("max_weekday_session_minutes")
            try:
                prof["max_running_days_per_week"] = int(max_days) if max_days not in (None, "") else None
                if prof["max_running_days_per_week"] is not None and not 2 <= prof["max_running_days_per_week"] <= 7:
                    raise ValueError
            except Exception:
                return self.json_response({"error": "Maks løbedage skal være 2-7 eller tom."}, 400)
            try:
                prof["max_weekday_session_minutes"] = int(max_minutes) if max_minutes not in (None, "") else None
                if prof["max_weekday_session_minutes"] is not None and not 20 <= prof["max_weekday_session_minutes"] <= 240:
                    raise ValueError
            except Exception:
                return self.json_response({"error": "Hverdagsminutter skal være 20-240 eller tom."}, 400)

            allowed = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}
            prof["preferred_long_run_days"] = [str(x).upper() for x in body.get("preferred_long_run_days", []) if str(x).upper() in allowed]
            prof["notes"] = str(body.get("notes") or "")[:2000]

            plan_code = safe_code(body.get("plan_code"), fallback=str(prof.get("plan_code") or "RunPlan"))
            prof["plan_code"] = plan_code
            current_week = body.get("plan_current_week")
            if current_week not in (None, ""):
                try:
                    week = int(current_week)
                    if not 1 <= week <= 99:
                        raise ValueError
                except Exception:
                    return self.json_response({"error": "Træningsugen skal være mellem 1 og 99."}, 400)
                today = dt.date.today()
                monday = today - dt.timedelta(days=today.weekday())
                prof["plan_anchor_monday"] = monday.isoformat()
                prof["plan_anchor_week"] = week

            prof["updated_at"] = dt.datetime.now().astimezone().isoformat()
            save(PROFILE, prof)
            return self.json_response({"ok": True, "message": f"Gemt. Denne uge hedder {prof['plan_code']}W{prof.get('plan_anchor_week', '?')}. Ændringen bruges ved næste coach-kørsel."})

        if path == "/api/settings":
            cfg = settings()
            schedule = body.get("schedule") if isinstance(body.get("schedule"), dict) else {}
            allowed = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}
            days = [str(x).upper() for x in schedule.get("days", []) if str(x).upper() in allowed]
            if not days:
                return self.json_response({"error": "Vælg mindst én dag"}, 400)
            run_time = str(schedule.get("time") or "22:00")
            if len(run_time) != 5 or run_time[2] != ":":
                return self.json_response({"error": "Ugyldigt tidspunkt"}, 400)
            auto_enabled = bool(body.get("auto_analysis_enabled", True))
            requested_writeback = bool(body.get("garmin_writeback_enabled", False))
            if requested_writeback and not cfg.get("writeback_test_passed"):
                return self.json_response({"error": "Write-back kan først aktiveres efter én bestået Garmin-kalendertest."}, 409)
            cfg["schedule"] = {"days": days, "time": run_time}
            cfg["auto_analysis_enabled"] = auto_enabled
            cfg["garmin_writeback_enabled"] = requested_writeback if cfg.get("writeback_test_passed") else False
            cfg["last_saved_at"] = dt.datetime.now().astimezone().isoformat()
            save(SETTINGS, cfg)
            ok, output = install_automation(auto_enabled)
            prefix = "Automatik installeret. " if ok else "Kunne ikke installere automatik. "
            return self.json_response({"ok": ok, "message": prefix + output[-1000:]}, 200 if ok else 500)

        return self.json_response({"error": "Ikke fundet"}, 404)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Af sikkerhedshensyn må coach-UI kun bindes til localhost.")
    settings()
    profile()
    url = f"http://127.0.0.1:{args.port}/"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Garmin Local Coach UI: {url}")
    print("Kun tilgængelig lokalt på denne PC. Ctrl+C stopper UI-serveren.")
    if needs_initial_run():
        threading.Timer(1.0, lambda: run_job("status")).start()
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
