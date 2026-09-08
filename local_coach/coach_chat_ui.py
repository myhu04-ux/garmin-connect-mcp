r'''Direct local chat UI for Garmin Local Coach.

Runs only on 127.0.0.1:8766. The agent module replaces `answer()` with the real
tool/model router. This base UI keeps history, plan notes, long-running job status and
model-download status visible so a deep local analysis does not look like a dead app.
'''

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import threading
import time
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
MODEL_STATUS = DATA / "model_status.json"
COACH_PS1 = REPO / "local_coach" / "coach.ps1"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"  # internal compatibility only; athlete-facing routing lives in agent

PLAN_JOB_LOCK = threading.Lock()
PLAN_JOB = {"running": False, "message": "Klar", "started_at": None}
CHAT_JOB_LOCK = threading.Lock()
CHAT_REQUEST_LOCK = threading.Lock()
CHAT_JOB = {
    "running": False,
    "stage": "Klar",
    "model": None,
    "started_at": None,
    "message_preview": None,
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


def powershell_exe() -> str:
    path = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    return str(path) if path.exists() else "powershell.exe"


def compact_context() -> dict[str, Any]:
    state = load(STATE, {})
    preview = load(PREVIEW, {})
    goal = load(GOAL, {})
    profile = load(PROFILE, {})
    challenges = load(CHALLENGES, {})
    notes = load(NOTES, {})

    actions: list[dict[str, Any]] = []
    for action in preview.get("actions", []) if isinstance(preview, dict) else []:
        if not isinstance(action, dict):
            continue
        actions.append({
            "date": action.get("date"),
            "plan_name": action.get("plan_name"),
            "focus": action.get("focus"),
            "workout_style": action.get("workout_style"),
            "reason": action.get("reason"),
            "action": action.get("action"),
        })

    active_notes = []
    for row in notes.get("notes", []) if isinstance(notes, dict) else []:
        if isinstance(row, dict) and row.get("active") is not False and row.get("text"):
            active_notes.append(row.get("text"))

    return {
        "recovery": state.get("recovery") or {},
        "training": state.get("training") or {},
        "plan_match": state.get("plan_match") or {},
        "event": state.get("event") or goal,
        "upcoming_plan": actions[:12],
        "garmin_challenges": (challenges.get("active") or [])[:12] if isinstance(challenges, dict) else [],
        "athlete_profile": profile,
        "active_planning_notes": active_notes[-12:],
        "priority": "Recovery and primary race goal outrank Garmin challenges.",
        "garmin_writeback_note": "Chat writes only through guarded Garmin tools; free-form model text cannot mutate Garmin.",
    }


def history_rows() -> list[dict[str, str]]:
    payload = load(HISTORY, {})
    rows = payload.get("messages", []) if isinstance(payload, dict) else []
    return [
        row for row in rows
        if isinstance(row, dict)
        and row.get("role") in {"user", "assistant"}
        and row.get("content")
    ][-30:]


def append_history(role: str, content: str) -> None:
    rows = history_rows()
    rows.append({
        "role": role,
        "content": content[:8000],
        "at": dt.datetime.now().astimezone().isoformat(),
    })
    save(HISTORY, {
        "updated_at": dt.datetime.now().astimezone().isoformat(),
        "messages": rows[-30:],
    })


def add_planning_note(text: str) -> None:
    payload = load(NOTES, {})
    rows = payload.get("notes", []) if isinstance(payload, dict) else []
    rows = [row for row in rows if isinstance(row, dict)]
    rows.append({
        "id": dt.datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "created_at": dt.datetime.now().astimezone().isoformat(),
        "text": text[:1200],
        "active": True,
        "source": "direct_coach_chat",
    })
    save(NOTES, {
        "updated_at": dt.datetime.now().astimezone().isoformat(),
        "notes": rows[-30:],
    })


def _elapsed(started_at: float | None) -> int:
    if not started_at:
        return 0
    return max(0, int(time.time() - started_at))


def set_chat_stage(stage: str, model: str | None = None) -> None:
    with CHAT_JOB_LOCK:
        if CHAT_JOB.get("running"):
            CHAT_JOB["stage"] = str(stage)[:220]
            if model is not None:
                CHAT_JOB["model"] = model


def start_chat_job(message: str) -> None:
    with CHAT_JOB_LOCK:
        CHAT_JOB.update({
            "running": True,
            "stage": "Fortolker din besked…",
            "model": None,
            "started_at": time.time(),
            "message_preview": message[:120],
        })


def finish_chat_job() -> None:
    with CHAT_JOB_LOCK:
        CHAT_JOB.update({
            "running": False,
            "stage": "Klar",
            "model": None,
            "started_at": None,
            "message_preview": None,
        })


def chat_job_status() -> dict[str, Any]:
    with CHAT_JOB_LOCK:
        payload = dict(CHAT_JOB)
    payload["elapsed_seconds"] = _elapsed(payload.get("started_at"))
    return payload


def plan_job_status() -> dict[str, Any]:
    with PLAN_JOB_LOCK:
        payload = dict(PLAN_JOB)
    payload["elapsed_seconds"] = _elapsed(payload.get("started_at"))
    return payload


def model_status() -> dict[str, Any]:
    payload = load(MODEL_STATUS, {})
    return payload if isinstance(payload, dict) else {}


def start_replan() -> bool:
    with PLAN_JOB_LOCK:
        if PLAN_JOB["running"]:
            return False
        PLAN_JOB.update({
            "running": True,
            "message": "Den adaptive plan opdateres i baggrunden…",
            "started_at": time.time(),
        })

    def worker() -> None:
        try:
            proc = subprocess.run(
                [powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(COACH_PS1), "status"],
                cwd=str(REPO),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60 * 45,
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
            PLAN_JOB.update({"running": False, "message": msg, "started_at": None})

    threading.Thread(target=worker, daemon=True).start()
    return True


def answer(message: str) -> str:
    """Fallback only. coach_chat_agent replaces this module function at runtime."""
    context = compact_context()
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "stream": False,
            "think": False,
            "messages": [
                {"role": "system", "content": "Svar kort på dansk og brug kun den givne kontekst."},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False) + "\n" + message},
            ],
            "options": {"temperature": 0.1, "num_predict": 120},
        },
        timeout=90,
    )
    response.raise_for_status()
    return str((response.json().get("message") or {}).get("content") or "").strip()


PAGE = r'''<!doctype html><html lang="da"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Garmin Coach</title><style>
:root{--bg:#0b0d10;--panel:#171c22;--line:#2c353f;--text:#eef3f7;--muted:#93a0ad;--green:#6ee7b7;--blue:#7dd3fc;--amber:#fbbf24}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#182630,#0b0d10 38%);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:940px;margin:auto;padding:26px 18px}.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.top h1{margin:0;font-size:27px}.top a{color:var(--blue);text-decoration:none}.sub{color:var(--muted);margin-top:5px}.statusCard{margin:16px 0;padding:12px 14px;border:1px solid var(--line);border-radius:13px;background:#11171c;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}.statusMain{font-weight:700}.statusSub{color:var(--muted);font-size:13px;margin-top:3px}.models{color:var(--muted);font-size:12px;text-align:right}.chat{background:linear-gradient(180deg,#1a2027,var(--panel));border:1px solid var(--line);border-radius:18px;padding:18px;min-height:430px;max-height:62vh;overflow:auto}.msg{max-width:84%;padding:12px 14px;border-radius:14px;margin:10px 0;line-height:1.5;white-space:pre-wrap}.user{margin-left:auto;background:#183629}.assistant{background:#202832}.meta{color:var(--muted);font-size:12px;margin-top:5px}.composer{margin-top:14px;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px}textarea{width:100%;min-height:86px;background:#0f1317;color:var(--text);border:1px solid var(--line);border-radius:11px;padding:12px;font:inherit;resize:vertical}.buttons{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}button{border:0;border-radius:10px;padding:10px 14px;font-weight:750;cursor:pointer;background:var(--green);color:#062017}button.secondary{background:#26303a;color:var(--text);border:1px solid var(--line)}button:disabled{opacity:.45}.hint{color:var(--muted);font-size:13px;margin:0 0 13px}.working{color:var(--amber)}@media(max-width:650px){.msg{max-width:96%}.models{text-align:left}}
</style></head><body><main><div class="top"><div><h1>Tal med din Garmin Coach</h1><div class="sub">Lokalt på Acer · Garmin-data · 4B samtale · 8B ekspert</div></div><a href="http://127.0.0.1:8765/">← Dashboard</a></div><div class="statusCard"><div><div id="statusMain" class="statusMain">Klar</div><div id="statusSub" class="statusSub">Ingen analyse kører.</div></div><div id="models" class="models"></div></div><p class="hint">Du kan skrive naturligt. Ugeplaner og flerugers analyse bruger ekspertcoachen. “Send + brug i planen” gemmer også beskeden som en reel planramme.</p><div id="chat" class="chat"></div><div class="composer"><textarea id="input" placeholder="Skriv til coachen…"></textarea><div class="buttons"><button id="send" onclick="send(false)">Send</button><button class="secondary" id="use" onclick="send(true)">Send + brug i planen</button></div></div></main><script>
const chat=document.getElementById('chat'),inp=document.getElementById('input'),main=document.getElementById('statusMain'),sub=document.getElementById('statusSub'),models=document.getElementById('models');
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function add(role,text,meta=''){const d=document.createElement('div');d.className='msg '+role;d.innerHTML=esc(text)+(meta?`<div class="meta">${esc(meta)}</div>`:'');chat.appendChild(d);chat.scrollTop=chat.scrollHeight}
function dur(sec){sec=Number(sec||0);const m=Math.floor(sec/60),s=sec%60;return m?`${m} min ${s}s`:`${s}s`}
function modelText(payload){const ms=payload?.models||{};const rows=[];for(const [name,v] of Object.entries(ms)){let t=name.replace('qwen3:','');if(v.state==='ready')t+=' ✓';else if(v.state==='downloading')t+=` ${v.progress_pct??0}%`;else if(v.state==='error')t+=' fejl';else t+=' …';rows.push(t)}return rows.join(' · ')}
async function load(){try{const r=await fetch('/api/history'),j=await r.json();chat.innerHTML='';for(const x of j.messages||[])add(x.role==='user'?'user':'assistant',x.content)}catch(e){main.textContent='Kunne ikke hente historik'}}
async function refreshStatus(){try{const r=await fetch('/api/status'),j=await r.json();models.textContent=modelText(j.model_status);if(j.chat_job?.running){main.textContent=j.chat_job.stage||'Coachen arbejder…';main.classList.add('working');sub.textContent=(j.chat_job.model?`${j.chat_job.model} · `:'')+`arbejdet i ${dur(j.chat_job.elapsed_seconds)}`;return}main.classList.remove('working');if(j.plan_job?.running){main.textContent=j.plan_job.message||'Planen opdateres…';sub.textContent=`Baggrundsjob · ${dur(j.plan_job.elapsed_seconds)}`;return}main.textContent='Klar';sub.textContent=(j.plan_job?.message&&j.plan_job.message!=='Klar')?j.plan_job.message:'Ingen analyse kører.'}catch(e){}}
async function send(use){const m=inp.value.trim();if(!m)return;inp.value='';add('user',m,use?'Gemmes også som planramme':'');document.getElementById('send').disabled=true;document.getElementById('use').disabled=true;main.textContent='Coachen starter…';main.classList.add('working');try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m,use_in_plan:use})});const j=await r.json();if(!r.ok)throw new Error(j.error||'Fejl');add('assistant',j.answer,j.plan_message||'');}catch(e){add('assistant','Jeg kunne ikke svare: '+e.message)}finally{document.getElementById('send').disabled=false;document.getElementById('use').disabled=false;refreshStatus()}}
inp.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send(false)}});load();refreshStatus();setInterval(refreshStatus,1000);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
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
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.path == "/api/history":
            self.send_json({"messages": history_rows()})
            return
        if self.path == "/api/status":
            self.send_json({
                "chat_job": chat_job_status(),
                "plan_job": plan_job_status(),
                "model_status": model_status(),
            })
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
        if len(message) > 4000:
            self.send_json({"error": "Beskeden er for lang"}, 400)
            return
        if not CHAT_REQUEST_LOCK.acquire(blocking=False):
            self.send_json({"error": "Coachen arbejder allerede på en anden besked. Vent til den er færdig."}, 409)
            return

        try:
            append_history("user", message)
            if use_in_plan:
                add_planning_note(message)

            start_chat_job(message)
            try:
                text = answer(message)
            except Exception as exc:
                text = f"Coachen kunne ikke afslutte svaret sikkert: {exc}"
            finally:
                finish_chat_job()

            append_history("assistant", text)

            # Deliberately start the heavy replan AFTER the conversational answer.
            # This avoids competing local model jobs while the athlete is waiting.
            plan_message = ""
            if use_in_plan:
                started = start_replan()
                plan_message = (
                    "Gemt som planramme. Den adaptive plan opdateres nu i baggrunden."
                    if started else
                    "Gemt som planramme. En planopdatering kører allerede."
                )
            self.send_json({"answer": text, "plan_message": plan_message})
        finally:
            CHAT_REQUEST_LOCK.release()


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
