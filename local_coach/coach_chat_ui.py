r'''Direct local chat with Garmin Local Coach.

Runs only on 127.0.0.1:8766. The chat reads the same validated local coach state,
Garmin challenges and upcoming plan as the dashboard. It can save an explicit user
message as a planning note and trigger the normal validated planning pipeline, but
it never writes directly to Garmin or bypasses safety gates.
'''

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests

ROOT = Path(r"C:\GarminCoach")
REPO = ROOT / "garmin-connect-mcp"
DATA = ROOT / "data"
STATE = DATA / "coach_state.json"
PREVIEW = DATA / "coach_preview.json"
GOAL = DATA / "active_goal.json"
PROFILE = DATA / "athlete_profile.json"
CHALLENGES = DATA / "garmin_challenges.json"
NOTES = DATA / "coach_chat_notes.json"
HISTORY = DATA / "coach_chat_history.json"
COACH_PS1 = REPO / "local_coach" / "coach.ps1"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"
PLAN_JOB_LOCK = threading.Lock()
PLAN_JOB = {"running": False, "message": "Klar"}


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


def powershell_exe() -> str:
    p = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    return str(p) if p.exists() else "powershell.exe"


def compact_context() -> dict[str, Any]:
    state = load(STATE, {})
    preview = load(PREVIEW, {})
    goal = load(GOAL, {})
    profile = load(PROFILE, {})
    challenges = load(CHALLENGES, {})
    notes = load(NOTES, {})

    actions = []
    for a in preview.get("actions", []) if isinstance(preview, dict) else []:
        if not isinstance(a, dict):
            continue
        actions.append({
            "date": a.get("date"),
            "plan_name": a.get("plan_name"),
            "focus": a.get("focus"),
            "workout_style": a.get("workout_style"),
            "reason": a.get("reason"),
            "action": a.get("action"),
        })

    return {
        "recovery": state.get("recovery") or {},
        "training": state.get("training") or {},
        "plan_match": state.get("plan_match") or {},
        "event": state.get("event") or goal,
        "upcoming_plan": actions[:12],
        "garmin_challenges": (challenges.get("active") or [])[:12] if isinstance(challenges, dict) else [],
        "athlete_profile": profile,
        "active_planning_notes": [
            x.get("text") for x in (notes.get("notes", []) if isinstance(notes, dict) else [])
            if isinstance(x, dict) and x.get("active") is not False and x.get("text")
        ][-12:],
        "priority": "Recovery and primary race goal outrank Garmin challenges; challenges are secondary and may only use compatible safe training.",
        "garmin_writeback_note": "Chat itself never writes to Garmin. Calendar changes go through the validated coach pipeline and write-back safety gate.",
    }


def history_rows() -> list[dict[str, str]]:
    payload = load(HISTORY, {})
    rows = payload.get("messages", []) if isinstance(payload, dict) else []
    return [x for x in rows if isinstance(x, dict) and x.get("role") in {"user", "assistant"} and x.get("content")][-30:]


def append_history(role: str, content: str) -> None:
    rows = history_rows()
    rows.append({
        "role": role,
        "content": content[:4000],
        "at": dt.datetime.now().astimezone().isoformat(),
    })
    save(HISTORY, {"updated_at": dt.datetime.now().astimezone().isoformat(), "messages": rows[-30:]})


def add_planning_note(text: str) -> None:
    payload = load(NOTES, {})
    rows = payload.get("notes", []) if isinstance(payload, dict) else []
    rows = [x for x in rows if isinstance(x, dict)]
    rows.append({
        "id": dt.datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "created_at": dt.datetime.now().astimezone().isoformat(),
        "text": text[:1000],
        "active": True,
        "source": "direct_coach_chat",
    })
    save(NOTES, {"updated_at": dt.datetime.now().astimezone().isoformat(), "notes": rows[-30:]})


def start_replan() -> bool:
    with PLAN_JOB_LOCK:
        if PLAN_JOB["running"]:
            return False
        PLAN_JOB.update({"running": True, "message": "Planen opdateres…"})

    def worker() -> None:
        try:
            proc = subprocess.run(
                [powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(COACH_PS1), "status"],
                cwd=str(REPO),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60 * 35,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode == 0:
                msg = "Planen er opdateret"
            elif proc.returncode == 9:
                msg = "En coach-opdatering kører allerede; noten bruges ved næste kørsel"
            else:
                msg = f"Planopdatering sluttede med kode {proc.returncode}"
        except Exception as exc:
            msg = f"Planopdatering fejlede: {exc}"
        with PLAN_JOB_LOCK:
            PLAN_JOB.update({"running": False, "message": msg})

    threading.Thread(target=worker, daemon=True).start()
    return True


def answer(message: str) -> str:
    context = compact_context()
    history = history_rows()[-10:]
    messages = [{
        "role": "system",
        "content": (
            "Du er brugerens personlige lokale løbetræner. Tal naturligt dansk, konkret og kort. "
            "Brug KUN den vedlagte coach-kontekst og samtalen til faktuelle påstande om brugeren. "
            "Forklar hvorfor, ikke bare hvad. Garmin-udfordringer er sekundære mål: de må gerne "
            "flettes ind, hvis den samme sikre træning hjælper, men de må aldrig presse ekstra hård "
            "eller uhensigtsmæssig belastning ind. Primært løbsmål, restitution og sikker progression "
            "har højere prioritet. Du må ikke diagnosticere sygdom/skade. Du må ikke påstå, at du har "
            "ændret Garmin; chatten kan kun rådgive og gemme eksplicitte plan-noter."
        ),
    }]
    messages.extend({"role": x["role"], "content": x["content"]} for x in history)
    messages.append({
        "role": "user",
        "content": f"COACH-KONTEKST:\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\nBRUGERENS BESKED:\n{message}",
    })
    payload = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "messages": messages,
        "options": {"temperature": 0.25, "num_predict": 500},
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=120)
    response.raise_for_status()
    text = str(response.json().get("message", {}).get("content", "")).strip()
    return text or "Jeg kunne ikke formulere et svar ud fra de lokale data."


PAGE = r'''<!doctype html><html lang="da"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Tal med Garmin Coach</title><style>
:root{--bg:#0b0d10;--panel:#171c22;--line:#2c353f;--text:#eef3f7;--muted:#93a0ad;--green:#6ee7b7;--blue:#7dd3fc}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#182630,#0b0d10 38%);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:900px;margin:auto;padding:28px 20px}.top{display:flex;justify-content:space-between;align-items:center;gap:16px}.top h1{margin:0;font-size:27px}.top a{color:var(--blue);text-decoration:none}.sub{color:var(--muted);margin:5px 0 20px}.chat{background:linear-gradient(180deg,#1a2027,var(--panel));border:1px solid var(--line);border-radius:18px;padding:18px;min-height:430px}.msg{max-width:82%;padding:12px 14px;border-radius:14px;margin:10px 0;line-height:1.5;white-space:pre-wrap}.user{margin-left:auto;background:#183629}.assistant{background:#202832}.meta{color:var(--muted);font-size:12px;margin-top:5px}.composer{margin-top:14px;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px}textarea{width:100%;min-height:82px;background:#0f1317;color:var(--text);border:1px solid var(--line);border-radius:11px;padding:12px;font:inherit;resize:vertical}.buttons{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}button{border:0;border-radius:10px;padding:10px 14px;font-weight:750;cursor:pointer;background:var(--green);color:#062017}button.secondary{background:#26303a;color:var(--text);border:1px solid var(--line)}button:disabled{opacity:.45}.status{color:var(--muted);font-size:13px;margin-top:9px}.note{padding:10px 12px;border-left:3px solid var(--green);background:#101713;border-radius:8px;color:#cde9dd;margin-bottom:13px}@media(max-width:650px){.msg{max-width:95%}}
</style></head><body><main><div class="top"><div><h1>Tal med din Garmin Coach</h1><div class="sub">Lokal AI · dine Garmin-data · ingen cloud-API</div></div><a href="http://127.0.0.1:8765/">← Dashboard</a></div><div class="note">Spørg fx: “Hvorfor har jeg lang trail lørdag?”, “Hvordan ligger jeg til i mine Garmin-udfordringer?” eller “Hvad er vigtigst denne uge?”. Brug <b>Send + brug i planen</b>, når du giver en reel ramme som “Jeg kan ikke træne fredag”.</div><div id="chat" class="chat"></div><div class="composer"><textarea id="input" placeholder="Skriv til coachen…"></textarea><div class="buttons"><button id="send" onclick="send(false)">Send</button><button class="secondary" id="use" onclick="send(true)">Send + brug i planen</button></div><div id="status" class="status">Klar</div></div></main><script>
const chat=document.getElementById('chat'),inp=document.getElementById('input'),statusEl=document.getElementById('status');function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function add(role,text,meta=''){const d=document.createElement('div');d.className='msg '+role;d.innerHTML=esc(text)+(meta?`<div class="meta">${esc(meta)}</div>`:'');chat.appendChild(d);chat.scrollTop=chat.scrollHeight}async function load(){try{const r=await fetch('/api/history'),j=await r.json();chat.innerHTML='';for(const x of j.messages||[])add(x.role==='user'?'user':'assistant',x.content)}catch(e){statusEl.textContent='Kunne ikke hente historik'}}async function send(use){const m=inp.value.trim();if(!m)return;inp.value='';add('user',m,use?'Gemmes som plan-note':'');document.getElementById('send').disabled=true;document.getElementById('use').disabled=true;statusEl.textContent=use?'Coachen svarer og gemmer dette til planen…':'Coachen tænker…';try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m,use_in_plan:use})}),j=await r.json();if(!r.ok)throw new Error(j.error||'Fejl');add('assistant',j.answer,j.plan_message||'');statusEl.textContent=j.plan_message||'Klar'}catch(e){add('assistant','Jeg kunne ikke svare: '+e.message);statusEl.textContent='Fejl'}finally{document.getElementById('send').disabled=false;document.getElementById('use').disabled=false}}inp.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send(false)}});load();setInterval(async()=>{try{const r=await fetch('/api/status'),j=await r.json();if(j.plan_job&&j.plan_job.running)statusEl.textContent=j.plan_job.message;else if(j.plan_job&&j.plan_job.message!=='Klar')statusEl.textContent=j.plan_job.message}catch{}},3000);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/" or self.path.startswith("/?"):
            raw = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.path == "/api/history":
            self.send_json({"messages": history_rows()})
            return
        if self.path == "/api/status":
            self.send_json({"plan_job": dict(PLAN_JOB)})
            return
        self.send_json({"error": "Ikke fundet"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/chat":
            self.send_json({"error": "Ikke fundet"}, 404)
            return
        body = self.read_json()
        message = str(body.get("message") or "").strip()
        use_in_plan = bool(body.get("use_in_plan"))
        if not message:
            self.send_json({"error": "Tom besked"}, 400)
            return
        if len(message) > 3000:
            self.send_json({"error": "Beskeden er for lang"}, 400)
            return

        append_history("user", message)
        plan_message = ""
        if use_in_plan:
            add_planning_note(message)
            started = start_replan()
            plan_message = "Gemt som plan-note. Planen opdateres i baggrunden." if started else "Gemt som plan-note. En planopdatering kører allerede."
        try:
            text = answer(message)
        except Exception as exc:
            text = f"Den lokale model kunne ikke svare lige nu: {exc}"
        append_history("assistant", text)
        self.send_json({"answer": text, "plan_message": plan_message})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Garmin Coach Chat: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
