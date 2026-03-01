#!/usr/bin/env python3
"""
DRISHTI Debug Server
====================
Minimal black-and-white web dashboard — runs on the laptop.
Opens at http://localhost:5000

Features
--------
• Live Pi service status (SSH health-check every 5 s)
• Live log tail via Server-Sent Events
• Command buttons: B1 Capture, B2 Cycle Mode, B3 Toggle YOLO
• Pi service control: start / restart / stop camera + buttons
• SSH terminal: run arbitrary commands on the Pi
• Latest capture image preview

Run
---
    source .venv/bin/activate
    python3 debug_server.py
"""

import os
import sys
import json
import time
import socket
import threading
import traceback
import glob
import re
import logging
from datetime import datetime
from pathlib import Path

import paramiko
from flask import Flask, Response, jsonify, request, send_file, abort

# ── Config ─────────────────────────────────────────────────────────────
PI_IP            = "10.42.0.50"
PI_USER          = "abhiroop"
PI_PASS          = "12345678"
CMD_HOST         = "127.0.0.1"   # localhost command server (main.py)
CMD_PORT         = 9090
FLASK_PORT       = 5000
LOGS_DIR         = Path(__file__).parent / "logs"
CAPTURES_DIR     = Path(__file__).parent / "captures"
AUDIO_DIR        = Path(__file__).parent / "audio_output"
STATUS_INTERVAL  = 5            # seconds between Pi SSH health-checks

# ── Flask app ──────────────────────────────────────────────────────────
app = Flask(__name__)

# Suppress Flask access-log noise
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

# ── Shared state ───────────────────────────────────────────────────────
_state: dict = {
    "pi_connected":      False,
    "pi_hostname":       "—",
    "pi_uptime":         "—",
    "camera_service":    "unknown",
    "buttons_service":   "unknown",
    "main_app":          "unknown",
    "blip_mode":         "default",
    "last_detection":    "—",
    "last_description":  "—",
    "last_alert":        "—",
    "timestamp":         "—",
}
_state_lock = threading.Lock()

# ── SSH helper ─────────────────────────────────────────────────────────
def _ssh_exec(cmd: str, timeout: int = 8) -> tuple[bool, str]:
    """
    Run *cmd* on the Pi.
    Returns (success, output_or_error_string).
    """
    try:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(PI_IP, username=PI_USER, password=PI_PASS, timeout=timeout)
        _, stdout, stderr = c.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        c.close()
        return True, (out or err)
    except Exception as exc:
        return False, str(exc)


# ── Background status poller ───────────────────────────────────────────
def _poll_loop():
    while True:
        s: dict = {}
        # ---- Pi SSH ping ----
        try:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(PI_IP, username=PI_USER, password=PI_PASS, timeout=5)

            def _run(cmd):
                _, o, _ = c.exec_command(cmd, timeout=5)
                return o.read().decode().strip()

            s["camera_service"]  = _run("systemctl is-active drishti-camera.service")
            s["buttons_service"] = _run("systemctl is-active drishti-buttons.service")
            lines                = _run("hostname ; uptime -p").splitlines()
            s["pi_hostname"]     = lines[0] if lines else "?"
            s["pi_uptime"]       = lines[1] if len(lines) > 1 else "?"
            s["pi_connected"]    = True
            c.close()
        except Exception:
            s["pi_connected"]    = False
            s["camera_service"]  = "unreachable"
            s["buttons_service"] = "unreachable"
            s["pi_hostname"]     = "?"
            s["pi_uptime"]       = "?"

        # ---- main.py (port 9090) ----
        try:
            sock = socket.socket()
            sock.settimeout(1.0)
            sock.connect((CMD_HOST, CMD_PORT))
            sock.close()
            s["main_app"] = "running"
        except Exception:
            s["main_app"] = "stopped"

        # ---- parse latest log file ----
        log_file = _latest_log()
        if log_file:
            tail = _tail(log_file, 200)
            s["blip_mode"]        = _last_match(tail, r"BLIP mode changed to:\s*(\w+)")        or \
                                    _last_match(tail, r"Mode changed to\s+(\w+)")               or "default"
            s["last_detection"]   = _last_match(tail, r"(\d+ (?:person|people) detected.*)")   or "—"
            s["last_description"] = _last_match(tail, r"Description:\s+(.{10,80})")            or "—"
            s["last_alert"]       = _last_match(tail, r"ALERT:\s+(.*)")                        or "—"
        else:
            for k in ("blip_mode", "last_detection", "last_description", "last_alert"):
                s[k] = "—"

        s["timestamp"] = datetime.now().strftime("%H:%M:%S")

        with _state_lock:
            _state.update(s)

        time.sleep(STATUS_INTERVAL)


def _latest_log() -> Path | None:
    files = sorted(LOGS_DIR.glob("drishti_*.log"), reverse=True)
    return files[0] if files else None


def _tail(path: Path, lines: int = 200) -> str:
    try:
        with open(path, "r", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except Exception:
        return ""


def _last_match(text: str, pattern: str) -> str | None:
    m = None
    for m in re.finditer(pattern, text, re.IGNORECASE):
        pass
    return m.group(1).strip() if m else None


# ── Command forwarder ──────────────────────────────────────────────────
def _send_cmd(command: str) -> dict:
    """Forward command to main.py command server."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((CMD_HOST, CMD_PORT))
        msg = json.dumps({"command": command, "timestamp": time.time()})
        sock.sendall(msg.encode())
        resp = sock.recv(4096).decode()
        sock.close()
        return json.loads(resp)
    except ConnectionRefusedError:
        return {"status": "error", "detail": "main.py is not running (port 9090 closed)"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


# ── Log SSE generator ──────────────────────────────────────────────────
def _log_sse_generator():
    """Yields Server-Sent Events from the current log file."""
    while True:
        log_file = _latest_log()
        if not log_file:
            yield "data: [waiting for log file...]\n\n"
            time.sleep(2)
            continue

        try:
            with open(log_file, "r", errors="replace") as f:
                f.seek(0, 2)             # seek to end
                yield f"data: [tailing {log_file.name}]\n\n"
                while True:
                    line = f.readline()
                    if line:
                        # Strip ANSI codes and trailing whitespace
                        clean = re.sub(r"\x1b\[[0-9;]*m", "", line).rstrip()
                        if clean:
                            yield f"data: {clean}\n\n"
                    else:
                        # Check if a newer log file appeared
                        new = _latest_log()
                        if new and new != log_file:
                            break
                        time.sleep(0.4)
        except Exception as exc:
            yield f"data: [log error: {exc}]\n\n"
            time.sleep(2)


# ═════════════════════════════════════════════════════════════
#  Routes
# ═════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return _HTML


@app.route("/api/status")
def api_status():
    with _state_lock:
        return jsonify(dict(_state))


@app.route("/api/logs/stream")
def api_logs_stream():
    return Response(
        _log_sse_generator(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/command", methods=["POST"])
def api_command():
    cmd = request.json.get("command", "")
    if cmd not in ("capture", "cycle_mode", "toggle_yolo"):
        return jsonify({"status": "error", "detail": "Unknown command"}), 400
    result = _send_cmd(cmd)
    return jsonify(result)


@app.route("/api/pi/service", methods=["POST"])
def api_pi_service():
    data    = request.json or {}
    svc     = data.get("service", "")
    action  = data.get("action", "")

    valid_svcs    = {"camera": "drishti-camera", "buttons": "drishti-buttons"}
    valid_actions = {"start", "stop", "restart", "status"}

    if svc not in valid_svcs or action not in valid_actions:
        return jsonify({"ok": False, "output": "Invalid service/action"}), 400

    full_svc = f"{valid_svcs[svc]}.service"
    cmd      = f"sudo systemctl {action} {full_svc} 2>&1 ; " \
               f"systemctl is-active {full_svc}"

    ok, out = _ssh_exec(cmd)
    return jsonify({"ok": ok, "output": out})


@app.route("/api/pi/ssh", methods=["POST"])
def api_pi_ssh():
    cmd = (request.json or {}).get("cmd", "").strip()
    if not cmd:
        return jsonify({"ok": False, "output": "Empty command"}), 400
    ok, out = _ssh_exec(cmd, timeout=15)
    return jsonify({"ok": ok, "output": out or "(no output)"})


@app.route("/api/capture/latest")
def api_capture_latest():
    files = sorted(CAPTURES_DIR.glob("capture_*.jpg"), reverse=True)
    if not files:
        # Return a 1×1 black pixel PNG as placeholder
        import base64, io
        try:
            from PIL import Image
            img = Image.new("RGB", (320, 240), color=(0, 0, 0))
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            buf.seek(0)
            return send_file(buf, mimetype="image/jpeg")
        except Exception:
            abort(404)
    return send_file(files[0], mimetype="image/jpeg")


@app.route("/api/captures")
def api_captures():
    files = sorted(CAPTURES_DIR.glob("capture_*.jpg"), reverse=True)[:10]
    return jsonify([f.name for f in files])


# ═════════════════════════════════════════════════════════════
#  Embedded HTML (single-file, no external deps)
# ═════════════════════════════════════════════════════════════

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>DRISHTI :: DEBUG</title>
<style>
/* ── Reset ── */
*{box-sizing:border-box;margin:0;padding:0;}
:root{
  --bg:   #000;
  --bg1:  #0a0a0a;
  --bg2:  #111;
  --bg3:  #181818;
  --line: #2a2a2a;
  --dim:  #444;
  --mid:  #888;
  --txt:  #ddd;
  --hi:   #fff;
  --ok:   #fff;
  --err:  #777;
  --font: 'Courier New', 'Lucida Console', monospace;
}

html,body{
  height:100%;background:var(--bg);color:var(--txt);
  font-family:var(--font);font-size:12px;overflow:hidden;
}

/* ── Layout ── */
#app{display:grid;grid-template-rows:36px 1fr 240px;height:100vh;}

/* ── Header ── */
#hdr{
  display:flex;align-items:center;justify-content:space-between;
  padding:0 12px;border-bottom:1px solid var(--line);background:var(--bg2);
  font-size:13px;letter-spacing:2px;text-transform:uppercase;
}
#hdr .title{color:var(--hi);font-weight:bold;}
#hdr .meta{display:flex;gap:16px;color:var(--mid);}
#hdr .meta span{display:flex;align-items:center;gap:5px;}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block;background:var(--dim);}
.dot.ok{background:var(--hi);}
.dot.err{background:var(--dim);}

/* ── Middle grid ── */
#mid{display:grid;grid-template-columns:1fr 1fr;overflow:hidden;}

/* ── Panels ── */
.col{overflow-y:auto;border-right:1px solid var(--line);}
.col:last-child{border-right:none;}
.panel{border-bottom:1px solid var(--line);padding:10px 14px;}
.panel-title{
  color:var(--mid);text-transform:uppercase;letter-spacing:1px;
  font-size:10px;margin-bottom:8px;
}

/* ── Status table ── */
.stat-row{display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid var(--line);}
.stat-row:last-child{border-bottom:none;}
.stat-label{color:var(--mid);}
.stat-val{color:var(--hi);text-align:right;max-width:70%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.stat-val.ok{color:var(--ok);}
.stat-val.err{color:var(--err);}
.stat-val.warn{color:var(--txt);}

/* ── Capture image ── */
#capture-img{
  width:100%;border:1px solid var(--line);display:block;
  background:var(--bg2);min-height:80px;object-fit:contain;
}

/* ── Buttons ── */
.btn-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:6px;}
.btn-grid.three{grid-template-columns:1fr 1fr 1fr;}
button{
  background:var(--bg2);color:var(--txt);border:1px solid var(--line);
  font-family:var(--font);font-size:11px;padding:7px 4px;cursor:pointer;
  text-transform:uppercase;letter-spacing:1px;transition:background 0.1s,color 0.1s;
}
button:hover{background:var(--bg3);border-color:var(--dim);color:var(--hi);}
button:active{background:var(--hi);color:var(--bg);}
button.danger{border-color:#444;}
button.danger:hover{background:#222;color:var(--hi);}

/* ── SSH terminal ── */
.ssh-row{display:flex;gap:6px;margin-bottom:6px;}
.ssh-row input{
  flex:1;background:var(--bg);color:var(--hi);border:1px solid var(--line);
  font-family:var(--font);font-size:12px;padding:5px 8px;outline:none;
}
.ssh-row input:focus{border-color:var(--mid);}
#ssh-out{
  background:var(--bg);border:1px solid var(--line);padding:8px;
  font-size:11px;color:var(--txt);white-space:pre-wrap;word-break:break-all;
  max-height:120px;overflow-y:auto;min-height:30px;
}

/* ── Log panel ── */
#log-panel{
  border-top:1px solid var(--line);display:flex;flex-direction:column;
  overflow:hidden;
}
#log-hdr{
  display:flex;align-items:center;justify-content:space-between;
  padding:4px 14px;background:var(--bg2);border-bottom:1px solid var(--line);
  flex-shrink:0;
}
#log-hdr .panel-title{margin:0;}
#log-status{font-size:10px;color:var(--mid);}
#log-content{
  flex:1;overflow-y:auto;padding:6px 14px;font-size:11px;line-height:1.6;
}
#log-content .ll{white-space:pre-wrap;word-break:break-all;}
#log-content .ll.err{color:var(--hi);font-weight:bold;}
#log-content .ll.warn{color:var(--txt);}
#log-content .ll.alert{background:var(--hi);color:var(--bg);padding:0 3px;}
#log-content .ll.dim{color:var(--dim);}

/* ── Flash ── */
#flash{
  position:fixed;bottom:16px;right:16px;background:var(--hi);color:var(--bg);
  font-family:var(--font);font-size:11px;padding:8px 14px;
  opacity:0;transition:opacity 0.3s;pointer-events:none;z-index:100;
}
#flash.show{opacity:1;}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:4px;height:4px;}
::-webkit-scrollbar-track{background:var(--bg);}
::-webkit-scrollbar-thumb{background:var(--line);}
</style>
</head>
<body>
<div id="app">

  <!-- HEADER -->
  <div id="hdr">
    <span class="title">DRISHTI :: DEBUG CONSOLE</span>
    <div class="meta">
      <span><span class="dot" id="dot-pi"></span><span id="lbl-pi">Pi</span></span>
      <span><span class="dot" id="dot-app"></span><span id="lbl-app">App</span></span>
      <span><span class="dot" id="dot-cam"></span><span id="lbl-cam">Camera</span></span>
      <span><span class="dot" id="dot-btn"></span><span id="lbl-btn">Buttons</span></span>
      <span id="clock" style="color:var(--hi)">--:--:--</span>
    </div>
  </div>

  <!-- MIDDLE -->
  <div id="mid">

    <!-- LEFT COLUMN -->
    <div class="col">

      <!-- Pi Status -->
      <div class="panel">
        <div class="panel-title">[ Pi Status ]</div>
        <div id="status-rows"></div>
      </div>

      <!-- Latest Capture -->
      <div class="panel">
        <div class="panel-title">[ Latest Capture ]
          <span style="float:right;cursor:pointer;color:var(--mid)" onclick="refreshCapture()">↻ refresh</span>
        </div>
        <img id="capture-img" src="/api/capture/latest" alt="no capture"/>
      </div>

    </div><!-- /left -->

    <!-- RIGHT COLUMN -->
    <div class="col">

      <!-- Commands -->
      <div class="panel">
        <div class="panel-title">[ Button Commands → main.py ]</div>
        <div class="btn-grid three">
          <button onclick="sendCmd('capture')">B1 CAPTURE</button>
          <button onclick="sendCmd('cycle_mode')">B2 CYCLE MODE</button>
          <button onclick="sendCmd('toggle_yolo')">B3 YOLO TOGGLE</button>
        </div>
        <div id="cmd-resp" style="color:var(--mid);font-size:10px;min-height:14px;"></div>
      </div>

      <!-- Pi Service Control -->
      <div class="panel">
        <div class="panel-title">[ Pi Service Control ]</div>
        <div style="display:grid;grid-template-columns:auto 1fr 1fr 1fr;gap:4px 6px;align-items:center;margin-bottom:4px;">
          <span style="color:var(--mid)">camera</span>
          <button onclick="piSvc('camera','start')">START</button>
          <button onclick="piSvc('camera','restart')">RESTART</button>
          <button class="danger" onclick="piSvc('camera','stop')">STOP</button>

          <span style="color:var(--mid)">buttons</span>
          <button onclick="piSvc('buttons','start')">START</button>
          <button onclick="piSvc('buttons','restart')">RESTART</button>
          <button class="danger" onclick="piSvc('buttons','stop')">STOP</button>
        </div>
        <div id="svc-resp" style="color:var(--mid);font-size:10px;min-height:14px;"></div>
      </div>

      <!-- SSH Terminal -->
      <div class="panel">
        <div class="panel-title">[ SSH Terminal → Pi ]</div>
        <div class="ssh-row">
          <input id="ssh-cmd" type="text" placeholder="e.g.  journalctl -n 20 -u drishti-camera"
                 onkeydown="if(event.key==='Enter')runSsh()"/>
          <button onclick="runSsh()">RUN</button>
        </div>
        <div id="ssh-out">(output appears here)</div>
      </div>

      <!-- Quick SSH Macros -->
      <div class="panel">
        <div class="panel-title">[ Quick SSH Macros ]</div>
        <div class="btn-grid">
          <button onclick="macro('journalctl -n 30 --no-pager -u drishti-camera')">camera log</button>
          <button onclick="macro('journalctl -n 30 --no-pager -u drishti-buttons')">buttons log</button>
          <button onclick="macro('vcgencmd measure_temp && free -h && df -h /')">pi health</button>
          <button onclick="macro('ls -lh /tmp/drishti_audio/')">audio files</button>
          <button onclick="macro('amixer cset numid=3 1 && amixer sset Master 80%')">fix audio</button>
          <button onclick="macro('rpicam-hello --list-cameras')">list cameras</button>
        </div>
      </div>

    </div><!-- /right -->
  </div><!-- /mid -->

  <!-- LOG PANEL -->
  <div id="log-panel">
    <div id="log-hdr">
      <span class="panel-title">[ LIVE LOG ]</span>
      <span id="log-status">connecting…</span>
      <span style="cursor:pointer;color:var(--mid);font-size:10px" onclick="clearLog()">CLR</span>
    </div>
    <div id="log-content"></div>
  </div>

</div><!-- /app -->

<div id="flash"></div>

<script>
// ── Clock ──────────────────────────────────────────────────────────
function tick(){
  const d = new Date();
  document.getElementById('clock').textContent =
    String(d.getHours()).padStart(2,'0') + ':' +
    String(d.getMinutes()).padStart(2,'0') + ':' +
    String(d.getSeconds()).padStart(2,'0');
}
setInterval(tick, 1000); tick();

// ── Flash notification ─────────────────────────────────────────────
let _ft; 
function flash(msg){
  const el = document.getElementById('flash');
  el.textContent = msg; el.classList.add('show');
  clearTimeout(_ft);
  _ft = setTimeout(()=>el.classList.remove('show'), 2500);
}

// ── Status polling ─────────────────────────────────────────────────
const STATUS_ROWS = [
  ['pi_connected',     'Pi SSH'],
  ['pi_hostname',      'Hostname'],
  ['pi_uptime',        'Uptime'],
  ['camera_service',   'Camera svc'],
  ['buttons_service',  'Buttons svc'],
  ['main_app',         'main.py'],
  ['blip_mode',        'BLIP mode'],
  ['last_detection',   'Last detect'],
  ['last_description', 'Last desc'],
  ['last_alert',       'Last alert'],
  ['timestamp',        'Polled at'],
];

function statusClass(key, val){
  if(key === 'pi_connected')    return val ? 'ok' : 'err';
  if(key === 'main_app')        return val === 'running' ? 'ok' : 'err';
  if(key === 'camera_service')  return val === 'active'  ? 'ok' : 'err';
  if(key === 'buttons_service') return val === 'active'  ? 'ok' : 'err';
  return '';
}
function dotClass(val){return val ? 'dot ok' : 'dot err';}

function updateStatus(d){
  // Header dots
  const pi = d.pi_connected;
  document.getElementById('dot-pi').className  = dotClass(pi);
  document.getElementById('dot-app').className = dotClass(d.main_app === 'running');
  document.getElementById('dot-cam').className = dotClass(d.camera_service  === 'active');
  document.getElementById('dot-btn').className = dotClass(d.buttons_service === 'active');

  // Status panel rows
  const container = document.getElementById('status-rows');
  container.innerHTML = STATUS_ROWS.map(([key, label]) => {
    const raw = d[key];
    const val = raw === true ? 'connected' : raw === false ? 'offline' : (raw || '—');
    const cls = statusClass(key, raw);
    return `<div class="stat-row">
      <span class="stat-label">${label}</span>
      <span class="stat-val ${cls}">${val}</span>
    </div>`;
  }).join('');
}

async function pollStatus(){
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    updateStatus(d);
  } catch(e){ /* ignore */ }
}
setInterval(pollStatus, 5000);
pollStatus();

// ── Commands ───────────────────────────────────────────────────────
async function sendCmd(cmd){
  document.getElementById('cmd-resp').textContent = `→ sending ${cmd}…`;
  try {
    const r = await fetch('/api/command', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({command: cmd}),
    });
    const d = await r.json();
    const msg = d.status || d.detail || JSON.stringify(d);
    document.getElementById('cmd-resp').textContent = `↳ ${msg}`;
    flash(msg);
  } catch(e){
    document.getElementById('cmd-resp').textContent = `✗ ${e}`;
  }
}

// ── Pi service control ─────────────────────────────────────────────
async function piSvc(svc, action){
  document.getElementById('svc-resp').textContent = `→ ${action} ${svc}…`;
  try {
    const r = await fetch('/api/pi/service', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({service: svc, action: action}),
    });
    const d = await r.json();
    document.getElementById('svc-resp').textContent = `↳ ${d.output}`;
    flash(`${svc}: ${d.output}`);
    setTimeout(pollStatus, 2000);
  } catch(e){
    document.getElementById('svc-resp').textContent = `✗ ${e}`;
  }
}

// ── SSH terminal ───────────────────────────────────────────────────
async function runSsh(){
  const inp = document.getElementById('ssh-cmd');
  const out = document.getElementById('ssh-out');
  const cmd = inp.value.trim();
  if(!cmd) return;
  out.textContent = `→ ${cmd}\\n(running…)`;
  try {
    const r = await fetch('/api/pi/ssh', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({cmd}),
    });
    const d = await r.json();
    out.textContent = d.output;
    out.scrollTop = out.scrollHeight;
  } catch(e){
    out.textContent = `Error: ${e}`;
  }
}

function macro(cmd){
  document.getElementById('ssh-cmd').value = cmd;
  runSsh();
}

// ── Capture refresh ────────────────────────────────────────────────
function refreshCapture(){
  const img = document.getElementById('capture-img');
  img.src = '/api/capture/latest?t=' + Date.now();
}

// ── Log SSE ────────────────────────────────────────────────────────
const logContent = document.getElementById('log-content');
const LOG_MAX = 300;

function classifyLine(line){
  const l = line.toLowerCase();
  if(l.includes('error') || l.includes('failed') || l.includes('exception')) return 'err';
  if(l.includes('warning') || l.includes('warn')) return 'warn';
  if(l.includes('alert:') || l.includes('⚠')) return 'alert';
  if(l.startsWith('[tailing') || l.startsWith('[waiting')) return 'dim';
  return '';
}

function appendLog(line){
  const div = document.createElement('div');
  div.className = 'll ' + classifyLine(line);
  div.textContent = line;
  logContent.appendChild(div);

  // Keep log trim
  while(logContent.children.length > LOG_MAX){
    logContent.removeChild(logContent.firstChild);
  }
  logContent.scrollTop = logContent.scrollHeight;
}

function clearLog(){
  logContent.innerHTML = '';
}

function connectSSE(){
  document.getElementById('log-status').textContent = 'connecting…';
  const es = new EventSource('/api/logs/stream');

  es.onopen = () => {
    document.getElementById('log-status').textContent = '● live';
  };
  es.onmessage = (e) => {
    appendLog(e.data);
  };
  es.onerror = () => {
    document.getElementById('log-status').textContent = '○ reconnecting';
    es.close();
    setTimeout(connectSSE, 3000);
  };
}
connectSSE();
</script>
</body>
</html>
"""

# ═════════════════════════════════════════════════════════════
#  Entry point
# ═════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Ensure dirs exist
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # Free port if a stale instance is holding it
    import subprocess as _sp
    try:
        _sp.run(["fuser", "-k", f"{FLASK_PORT}/tcp"],
                capture_output=True, timeout=3)
        time.sleep(0.3)
    except Exception:
        pass

    # Start background status poller
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()

    print("=" * 50)
    print("  DRISHTI Debug Server")
    print(f"  Dashboard → http://localhost:{FLASK_PORT}")
    print(f"  Watching Pi at {PI_IP}")
    print("=" * 50)
    print()

    # Open browser automatically
    try:
        _sp.Popen(["xdg-open", f"http://localhost:{FLASK_PORT}"],
                  stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
    except Exception:
        pass

    app.run(
        host="0.0.0.0",
        port=FLASK_PORT,
        debug=False,
        threaded=True,
        use_reloader=False,
    )
