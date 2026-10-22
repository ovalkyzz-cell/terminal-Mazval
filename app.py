import json
import mimetypes
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.parse
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

app = FastAPI(title="Storage Terminal Mazval")

STORAGE_DIR = os.environ.get("MAZVAL_STORAGE", "/tmp/mazval_storage")
os.makedirs(STORAGE_DIR, exist_ok=True)

RC_PATH = "/tmp/mazval_bashrc"
HIST_PATH = "/tmp/mazval_history"
MAX_SHELLS = 8
STREAM_MAX_SECONDS = 290

EPHEMERAL = bool(os.environ.get("VERCEL"))
UPLOAD_LIMIT_MB = 4.5

BANNER_ENV = "VERCEL SANDBOX" if EPHEMERAL else "LOKAL/Termux"
BANNER_MODE = (
    "PIPE SHELL (tanpa TTY) - echo/prompt ditangani readline"
    if EPHEMERAL
    else "PIPE SHELL - echo/prompt ditangani readline"
)
BANNER_STORE = "EPHEMERAL /tmp (hilang saat cold start)" if EPHEMERAL else "PERMANENT STORAGE"

_RC_CONTENT = f"""set +H
HISTSIZE=5000
HISTFILE={HIST_PATH}
shopt -s histappend 2>/dev/null
PS1='\\e[1;35mmazval-term\\e[0m:\\e[1;32m\\w\\e[0m\\$ '
alias ls='ls --color=auto'
command -v clear >/dev/null 2>&1 || alias clear='printf "\\033[H\\033[2J"'
cd "{STORAGE_DIR}" || cd /tmp
export PYTHONUNBUFFERED=1
printf '\\e[1;35m==================================================\\e[0m\\n'
printf '\\e[1;37m        STORAGE TERMINAL KERNEL ACTIVE            \\e[0m\\n'
printf '\\e[1;35m==================================================\\e[0m\\n'
printf '\\e[1;32m[OK] env        : {BANNER_ENV}\\e[0m\\n'
printf '\\e[1;32m[OK] mode       : {BANNER_MODE}\\e[0m\\n'
printf '\\e[1;32m[OK] storage    : {BANNER_STORE}\\e[0m\\n'
printf '\\e[1;32m[OK] workdir    : %s\\e[0m\\n' "$(pwd)"
printf '\\e[1;33m[NOTE] PTY tidak tersedia di Vercel; TUI/REPL interaktif (python -i, less, vim) terbatas.\\e[0m\\n\\n'
"""

with open(RC_PATH, "w") as _rc:
    _rc.write(_RC_CONTENT)

NOISE_PATTERNS = [
    re.compile(rb"bash: cannot set terminal process group[^\n]*\n"),
    re.compile(rb"bash: no job control in this shell\n"),
]

SHELLS = {}
SHELLS_LOCK = threading.Lock()


def fmt_size(n):
    if n >= 1024**4:
        return f"{round(n / 1024**4, 2)} TB"
    if n >= 1024**3:
        return f"{round(n / 1024**3, 2)} GB"
    if n >= 1024**2:
        return f"{round(n / 1024**2, 2)} MB"
    if n >= 1024:
        return f"{round(n / 1024, 2)} KB"
    return f"{n} B"


class ShellSession:
    def __init__(self):
        self.id = uuid.uuid4().hex[:16]
        self.created = time.time()
        self.last_active = time.time()
        self.chunks = queue.Queue()
        env = os.environ.copy()
        env.update(
            {
                "TERM": "xterm-256color",
                "PYTHONUNBUFFERED": "1",
                "HOME": "/tmp",
                "HISTFILE": HIST_PATH,
                "PS1": r"\e[1;35mmazval-term\e[0m:\e[1;32m\w\e[0m\$ ",
            }
        )
        self.proc = subprocess.Popen(
            ["/bin/bash", "--rcfile", RC_PATH, "-i"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=STORAGE_DIR,
            env=env,
            start_new_session=True,
            bufsize=0,
        )
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        received = 0
        try:
            while True:
                data = self.proc.stdout.read(4096)
                if not data:
                    break
                if received < 4096:
                    for pat in NOISE_PATTERNS:
                        data = pat.sub(b"", data)
                    received += len(data)
                if data:
                    self.chunks.put(data.decode("utf-8", "replace"))
        finally:
            self.alive = False
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
            self.chunks.put(None)

    def send(self, text):
        if not self.alive or self.proc.stdin is None:
            raise RuntimeError("shell sudah berakhir")
        self.proc.stdin.write(text.encode("utf-8", "replace"))
        self.proc.stdin.flush()
        self.last_active = time.time()

    def interrupt(self):
        if self.alive:
            try:
                os.killpg(self.proc.pid, signal.SIGINT)
            except (ProcessLookupError, PermissionError):
                pass
            self.last_active = time.time()

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


def prune_shells():
    now = time.time()
    with SHELLS_LOCK:
        dead = [
            k
            for k, s in SHELLS.items()
            if (not s.alive and s.chunks.empty()) or now - s.last_active > 900
        ]
        for k in dead:
            SHELLS.pop(k).close()
        while len(SHELLS) >= MAX_SHELLS:
            oldest = min(SHELLS, key=lambda k: SHELLS[k].last_active)
            SHELLS.pop(oldest).close()


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


@app.get("/api/probe")
def probe():
    info = {
        "python": subprocess.run(
            ["python3", "--version"], capture_output=True, text=True
        ).stdout.strip()
        or "unknown",
        "bash": shutil.which("bash"),
        "clear": shutil.which("clear"),
        "python": shutil.which("python"),
        "python3": shutil.which("python3"),
        "dev_ptmx": os.path.exists("/dev/ptmx"),
        "tmpdir": os.environ.get("TMPDIR", "/tmp"),
        "vercel_env": os.environ.get("VERCEL"),
        "cwd": os.getcwd(),
        "cwd_writable": os.access(os.getcwd(), os.W_OK),
        "tmp_writable": os.access(os.environ.get("TMPDIR", "/tmp"), os.W_OK),
        "shells_active": len(SHELLS),
    }
    try:
        p = subprocess.run(
            ["/bin/bash", "-c", "echo BASH_PIPE_OK"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        info["bash_pipe"] = p.stdout.strip()
    except Exception as e:
        info["bash_pipe"] = f"ERR {type(e).__name__}: {e}"
    try:
        import pty
        import struct
        import fcntl
        import termios

        pid, fd = pty.fork()
        if pid == 0:
            os.write(1, b"PTY_OK\n")
            os._exit(0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        buf = os.read(fd, 64)
        info["pty_fork"] = True
        info["pty_read"] = buf.decode(errors="ignore").strip()
        os.close(fd)
        os.waitpid(pid, 0)
    except Exception as e:
        info["pty_fork"] = False
        info["pty_error"] = f"{type(e).__name__}: {e}"
    return info


@app.post("/api/shell")
def shell_open():
    prune_shells()
    with SHELLS_LOCK:
        s = ShellSession()
        SHELLS[s.id] = s
    return {"id": s.id, "ephemeral": EPHEMERAL}


@app.get("/api/shell/{sid}")
def shell_ping(sid: str):
    s = SHELLS.get(sid)
    if s is None:
        return JSONResponse({"error": "session_expired"}, status_code=404)
    return {"alive": s.alive, "age": int(time.time() - s.created)}


@app.get("/api/shell/{sid}/stream")
def shell_stream(sid: str):
    s = SHELLS.get(sid)
    if s is None:
        return JSONResponse({"error": "session_expired"}, status_code=404)

    deadline = time.time() + STREAM_MAX_SECONDS

    def gen():
        ended_reason = None
        while True:
            if time.time() > deadline:
                ended_reason = "timeout"
                break
            try:
                item = s.chunks.get(timeout=15)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            if item is None:
                break
            s.last_active = time.time()
            yield f"data: {json.dumps({'o': item})}\n\n"
        if ended_reason:
            yield f"data: {json.dumps({'end': True, 'reason': ended_reason})}\n\n"
        else:
            yield f"data: {json.dumps({'end': True})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/shell/{sid}/input")
async def shell_input(sid: str, request: Request):
    s = SHELLS.get(sid)
    if s is None:
        return JSONResponse({"error": "session_expired"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    data = body.get("data", "")
    if not isinstance(data, str) or not data:
        return {"ok": True}
    if len(data) > 8192:
        data = data[:8192]
    try:
        s.send(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True}


@app.post("/api/shell/{sid}/interrupt")
def shell_interrupt(sid: str):
    s = SHELLS.get(sid)
    if s is None:
        return JSONResponse({"error": "session_expired"}, status_code=404)
    s.interrupt()
    return {"ok": True}


@app.delete("/api/shell/{sid}")
def shell_close(sid: str):
    s = SHELLS.pop(sid, None)
    if s is not None:
        s.close()
    return {"ok": True}


@app.post("/upload")
async def upload(request: Request):
    raw = request.headers.get("X-Filename", "upload.bin")
    name = os.path.basename(urllib.parse.unquote(raw)).strip() or "upload.bin"
    path = os.path.join(STORAGE_DIR, name)
    size = 0
    try:
        with open(path, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
                size += len(chunk)
    except Exception as e:
        try:
            os.remove(path)
        except Exception:
            pass
        return JSONResponse(
            {"status": "error", "message": str(e)}, status_code=500
        )
    return {"status": "success", "name": name, "size": size, "size_formatted": fmt_size(size)}


@app.get("/api/files")
def api_files():
    items = []
    used = 0
    try:
        entries = sorted(
            os.listdir(STORAGE_DIR),
            key=lambda x: os.path.getctime(os.path.join(STORAGE_DIR, x)),
            reverse=True,
        )
        for fn in entries:
            fp = os.path.join(STORAGE_DIR, fn)
            if not os.path.isfile(fp):
                continue
            sz = os.path.getsize(fp)
            used += sz
            items.append({"name": fn, "size_formatted": fmt_size(sz)})
    except Exception:
        pass
    du = shutil.disk_usage(STORAGE_DIR)
    percent = round(used / du.total * 100, 2) if du.total else 0
    if percent == 0 and used > 0:
        percent = 0.01
    return {
        "files": items,
        "storage": {
            "path": STORAGE_DIR,
            "used_formatted": fmt_size(used),
            "total_formatted": fmt_size(du.total),
            "free_formatted": fmt_size(du.free),
            "percent": percent,
            "ephemeral": EPHEMERAL,
            "upload_limit_mb": UPLOAD_LIMIT_MB if EPHEMERAL else None,
            "label": (
                "EPHEMERAL INSTANCE STORAGE (/tmp)" if EPHEMERAL else "PERMANENT STORAGE"
            ),
        },
    }


@app.delete("/api/files/{name}")
def delete_file(name: str):
    name = os.path.basename(urllib.parse.unquote(name))
    path = os.path.join(STORAGE_DIR, name)
    if os.path.isfile(path):
        os.remove(path)
        return {"status": "success"}
    return JSONResponse({"status": "error", "message": "File not found"}, status_code=404)


@app.get("/files/{name}")
def get_file(name: str, request: Request):
    name = os.path.basename(urllib.parse.unquote(name))
    path = os.path.join(STORAGE_DIR, name)
    if not os.path.isfile(path):
        return JSONResponse({"error": "File not found"}, status_code=404)
    size = os.path.getsize(path)
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    range_header = request.headers.get("range")
    if range_header:
        try:
            spec = range_header.replace("bytes=", "")
            start_s, _, end_s = spec.partition("-")
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
            if end >= size:
                end = size - 1
            if start > end or start >= size:
                return Response(
                    status_code=416, headers={"Content-Range": f"bytes */{size}"}
                )
            length = end - start + 1

            def gen_range():
                with open(path, "rb") as f:
                    f.seek(start)
                    left = length
                    while left > 0:
                        chunk = f.read(min(65536, left))
                        if not chunk:
                            break
                        left -= len(chunk)
                        yield chunk

            return StreamingResponse(
                gen_range(),
                status_code=206,
                media_type=ctype,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Content-Length": str(length),
                    "Accept-Ranges": "bytes",
                },
            )
        except ValueError:
            pass

    def gen_full():
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        gen_full(),
        media_type=ctype,
        headers={"Content-Length": str(size), "Accept-Ranges": "bytes"},
    )


HTML = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>STORAGE TERMINAL</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css" />
    <script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@700;800;900&display=swap');

        :root {{
            --bg: #FFFDF9;
            --box-bg: #FFFFFF;
            --border: #000000;
            --shadow: 5px 5px 0px #000000;
            --shadow-sm: 3px 3px 0px #000000;
            --primary: #FF5964;
            --secondary: #35A7FF;
            --accent: #FFD166;
            --success: #06D6A0;
            --text: #000000;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Plus Jakarta Sans', sans-serif; }}

        body {{
            background: var(--bg); color: var(--text);
            display: flex; flex-direction: column; height: 100vh; overflow: hidden;
        }}

        #header {{
            background: var(--primary); border-bottom: 4px solid var(--border);
            padding: 15px 20px; display: flex; justify-content: space-between; align-items: center;
            box-shadow: 0px 4px 0px var(--border); z-index: 10;
        }}
        .title {{ font-weight: 900; font-size: 1.3rem; text-transform: uppercase; letter-spacing: -0.5px; color: #fff; text-shadow: 2px 2px 0px #000; }}

        .status {{
            font-size: 0.75rem; padding: 6px 12px; border-radius: 8px; font-weight: 900;
            background: #fff; border: 3px solid var(--border); box-shadow: var(--shadow-sm);
            text-transform: uppercase;
        }}
        .status.online {{ background: var(--success); color: #000; }}

        .tab-bar {{ display: flex; background: var(--box-bg); border-bottom: 4px solid var(--border); }}
        .tab-btn {{
            flex: 1; padding: 14px 5px; text-align: center; background: #fff;
            border: none; border-right: 4px solid var(--border);
            color: var(--text); font-weight: 900; cursor: pointer; font-size: 0.8rem; text-transform: uppercase;
            transition: 0.1s ease;
        }}
        .tab-btn:last-child {{ border-right: none; }}
        .tab-btn:hover {{ background: #FFF0F3; }}
        .tab-btn.active {{ background: var(--secondary); color: #fff; text-shadow: 1px 1px 0px #000; box-shadow: inset 0px -4px 0px #000; }}

        .content-area {{ flex: 1; display: none; flex-direction: column; overflow: hidden; }}
        .content-area.active {{ display: flex; animation: popIn 0.3s ease-out forwards; }}

        @keyframes popIn {{
            0% {{ opacity: 0; transform: scale(0.98); }}
            100% {{ opacity: 1; transform: scale(1); }}
        }}

        .terminal-wrapper {{ flex: 1; padding: 15px; background: var(--bg); display: flex; flex-direction: column; }}
        .terminal-window {{
            flex: 1; background: #121212; border: 4px solid var(--border);
            border-radius: 12px; box-shadow: var(--shadow);
            display: flex; flex-direction: column; overflow: hidden;
        }}
        .window-header {{ background: var(--accent); border-bottom: 4px solid var(--border); padding: 8px 15px; display: flex; gap: 8px; align-items: center; }}
        .dot {{ width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--border); background: #fff; }}
        #terminal-container {{ flex: 1; padding: 10px; background: #121212; }}

        #mobile-bar {{
            background: #fff; padding: 10px; display: flex; gap: 8px; overflow-x: auto;
            border-top: 4px solid var(--border);
        }}

        .brutal-btn {{
            background: #fff; color: var(--text); border: 3px solid var(--border);
            padding: 8px 14px; border-radius: 8px; font-weight: 900; font-size: 0.75rem;
            cursor: pointer; white-space: nowrap; flex-shrink: 0;
            box-shadow: var(--shadow-sm); transition: all 0.1s ease;
            text-transform: uppercase;
        }}
        .brutal-btn:hover {{ transform: translate(2px, 2px); box-shadow: 1px 1px 0px var(--border); }}
        .brutal-btn:active {{ transform: translate(3px, 3px); box-shadow: 0px 0px 0px var(--border); background: var(--border); color: #fff; }}
        .btn-accent-color {{ background: var(--accent); }}
        .btn-primary-color {{ background: var(--secondary); color: #fff; text-shadow: 1px 1px 0px #000; }}
        .btn-danger-color {{ background: var(--primary); color: #fff; text-shadow: 1px 1px 0px #000; }}

        .box-container {{ padding: 20px; flex: 1; overflow-y: auto; background: var(--bg); }}

        .brutal-card {{
            background: var(--box-bg); border: 4px solid var(--border); border-radius: 12px;
            padding: 20px; margin-bottom: 20px; box-shadow: var(--shadow);
        }}

        .card-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 3px solid var(--border); padding-bottom: 12px; margin-bottom: 15px; }}
        .brutal-card h3 {{ font-size: 1.1rem; font-weight: 900; text-transform: uppercase; }}

        .storage-box-label {{ font-size: 0.75rem; font-weight: 900; background: var(--accent); padding: 4px 8px; border: 2px solid var(--border); border-radius: 6px; display: inline-block; margin-bottom: 10px; box-shadow: 2px 2px 0px #000; }}
        .storage-header {{ display: flex; justify-content: space-between; font-weight: 900; font-size: 0.85rem; margin-bottom: 8px; text-transform: uppercase; }}
        .storage-meter-bg {{ background: #eee; height: 22px; border-radius: 10px; border: 3px solid var(--border); overflow: hidden; }}
        .storage-meter-fill {{ height: 100%; width: 0%; background: var(--success); border-right: 3px solid var(--border); transition: width 0.8s ease, background-color 0.4s; }}
        .storage-text-stats {{ display: flex; justify-content: space-between; font-size: 0.75rem; margin-top: 8px; font-weight: 900; color: #333; }}
        .path-text {{ font-family: monospace; font-size: 0.65rem; background: #eee; padding: 4px; border: 2px solid var(--border); border-radius: 4px; margin-top: 8px; word-break: break-all; }}
        .storage-note {{ font-size: 0.7rem; font-weight: 900; margin-top: 8px; padding: 6px 8px; border: 2px solid var(--border); border-radius: 6px; background: #FFF0F3; }}

        .upload-area {{
            border: 3px dashed var(--border); border-radius: 10px; padding: 25px;
            text-align: center; background: #FFF0F3; margin-bottom: 15px; position: relative; cursor: pointer;
        }}
        input[type="file"] {{ position: absolute; top:0; left:0; width:100%; height:100%; opacity:0; cursor:pointer; }}

        .progress-box {{
            margin-top: 15px; padding: 12px; background: #121212; color: var(--success); border-radius: 8px;
            border: 3px solid var(--border); font-family: monospace; font-size: 0.8rem; font-weight: bold; white-space: pre-wrap;
            box-shadow: var(--shadow-sm);
        }}

        .gallery-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 20px; }}
        .media-item {{
            background: #fff; border: 3px solid var(--border); border-radius: 10px;
            overflow: hidden; display: flex; flex-direction: column;
            box-shadow: var(--shadow-sm); transition: 0.15s ease;
        }}
        .media-item:hover {{ transform: translate(-2px, -2px); box-shadow: 5px 5px 0px var(--border); }}

        .media-content {{
            height: 140px; background: #000; display: flex; align-items: center; justify-content: center;
            border-bottom: 3px solid var(--border); overflow: hidden; position: relative;
        }}
        .media-content img {{ width: 100%; height: 100%; object-fit: cover; }}
        .media-content video {{ width: 100%; height: 100%; object-fit: contain; background: #000; }}

        .media-info {{ padding: 10px; background: #fff; flex: 1; display: flex; flex-direction: column; justify-content: space-between; }}
        .file-name {{ font-size: 0.75rem; font-weight: 900; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
        .file-size {{ font-size: 0.65rem; font-weight: 800; color: #555; margin-bottom: 8px; }}

        .action-buttons {{ display: flex; gap: 5px; }}
        .btn-action {{
            flex: 1; text-align: center; text-decoration: none; padding: 6px 4px; border-radius: 6px;
            font-weight: 900; font-size: 0.65rem; border: 2px solid var(--border); box-shadow: 2px 2px 0px #000;
            cursor: pointer; transition: 0.1s; display: inline-block; text-transform: uppercase;
        }}
        .btn-action:active {{ transform: translate(1px, 1px); box-shadow: 0px 0px 0px #000; }}
        .btn-download {{ background: var(--accent); color: var(--text); }}
        .btn-delete {{ background: var(--primary); color: #fff; }}
    </style>
</head>
<body>
    <div id="header">
        <div class="title">⚡ STORAGE TERMINAL_</div>
        <div id="status" class="status">SYNC...</div>
    </div>

    <div class="tab-bar">
        <button class="tab-btn active" id="btn-tab-terminal" onclick="switchTab('terminal')">Terminal</button>
        <button class="tab-btn" id="btn-tab-storage" onclick="switchTab('storage'); updateStorageInfo();">Storage</button>
        <button class="tab-btn" id="btn-tab-gallery" onclick="switchTab('gallery'); loadGallery();">Gallery</button>
    </div>

    <div id="tab-terminal" class="content-area active">
        <div class="terminal-wrapper">
            <div class="terminal-window">
                <div class="window-header">
                    <div class="dot"></div><div class="dot"></div><div class="dot"></div>
                    <span style="margin-left: 5px; font-size: 0.75rem; font-weight: 900; color: #000;">PIPE SHELL KERNEL (PTY-FREE)</span>
                </div>
                <div id="terminal-container"></div>
                <div id="mobile-bar">
                    <button class="brutal-btn" onclick="sendSpecial('\\x03')">^C</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\t')">TAB</button>
                    <button class="brutal-btn btn-primary-color" onclick="sendCommand('python3\\r')">PY</button>
                    <button class="brutal-btn" onclick="sendCommand('ls -la\\r')">LS</button>
                    <button class="brutal-btn" onclick="sendCommand('clear\\r')">CLR</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\x1b[A')">▲</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\x1b[B')">▼</button>
                </div>
            </div>
        </div>
    </div>

    <div id="tab-storage" class="content-area">
        <div class="box-container">

            <div class="brutal-card">
                <div class="storage-box-label" id="storageBoxLabel">📁 STORAGE</div>
                <div class="storage-header">
                    <span>Kapasitas Terpakai</span>
                    <span id="storagePercentText1" style="font-weight:900;">0%</span>
                </div>
                <div class="storage-meter-bg">
                    <div id="storageMeterFill1" class="storage-meter-fill"></div>
                </div>
                <div class="storage-text-stats">
                    <span id="storageUsedText1">0 MB Terpakai</span>
                    <span id="storageTotalText1">memuat...</span>
                </div>
                <div class="path-text" id="storagePathText">PATH: ...</div>
                <div class="storage-note" id="storageNote" style="display:none;"></div>
            </div>

            <div class="brutal-card">
                <div class="card-header">
                    <h3>🚀 File Stream Upload</h3>
                    <span style="font-size:0.7rem; font-weight:900; background:var(--success); padding:3px 6px; border:2px solid var(--border); border-radius:4px;">0% RAM HP</span>
                </div>
                <div class="upload-area">
                    <div style="font-size: 2rem; margin-bottom: 5px;">📥</div>
                    <div style="font-weight: 900; font-size: 0.9rem;" id="fileNameDisplay">Pilih file ke brankas...</div>
                    <input type="file" id="fileInput" multiple onchange="updateFileName()">
                </div>
                <button class="brutal-btn btn-primary-color" style="width: 100%; padding: 12px; font-size: 0.9rem;" onclick="uploadFile()">UNGGAH SEKARANG</button>
                <div class="progress-box" id="progressLog">[READY] Menunggu input data...</div>
            </div>
        </div>
    </div>

    <div id="tab-gallery" class="content-area">
        <div class="box-container">

            <div class="brutal-card" style="padding: 15px; margin-bottom: 15px;">
                <div class="storage-header" style="font-size:0.75rem;">
                    <span id="storageMiniLabel">STORAGE</span>
                    <span id="storagePercentText2">0%</span>
                </div>
                <div class="storage-meter-bg" style="height: 14px;">
                    <div id="storageMeterFill2" class="storage-meter-fill"></div>
                </div>
                <div class="storage-text-stats" style="font-size:0.65rem;">
                    <span id="storageUsedText2">0 MB</span>
                    <span id="storageTotalText2">memuat...</span>
                </div>
            </div>

            <div class="brutal-card">
                <div class="card-header">
                    <h3>🖼️ Media Gallery & Live Preview</h3>
                    <button class="brutal-btn btn-accent-color" onclick="loadGallery()">REFRESH</button>
                </div>
                <div class="gallery-grid" id="gallery-grid"></div>
            </div>
        </div>
    </div>

    <script>
        function updateFileName() {{
            const input = document.getElementById('fileInput');
            const display = document.getElementById('fileNameDisplay');
            if (input.files.length > 0) {{
                display.innerText = input.files[0].name;
            }} else {{
                display.innerText = "Pilih file ke brankas...";
            }}
        }}

        function switchTab(tab) {{
            document.querySelectorAll('.content-area').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById('tab-' + tab).classList.add('active');
            document.getElementById('btn-tab-' + tab).classList.add('active');
            if (tab === 'terminal') term.focus();
        }}

        const statusEl = document.getElementById('status');
        const term = new Terminal({{
            cursorBlink: true, theme: {{ background: '#121212', foreground: '#06D6A0', cursor: '#FF5964' }},
            fontFamily: 'monospace', fontSize: 13, padding: 5
        }});
        const fitAddon = new FitAddon.FitAddon();
        term.loadAddon(fitAddon);
        term.open(document.getElementById('terminal-container'));
        fitAddon.fit();
        window.addEventListener('resize', () => fitAddon.fit());

        let shellId = null;
        let evSource = null;
        let inputBuf = '';
        let flushTimer = null;
        let opening = false;

        function setStatus(txt, online) {{
            statusEl.innerText = txt;
            statusEl.className = 'status' + (online ? ' online' : '');
        }}

        async function openShell() {{
            if (opening || shellId) return;
            opening = true;
            try {{
                const res = await fetch('/api/shell', {{ method: 'POST' }});
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                if (!data.id) throw new Error('no session id');
                shellId = data.id;
                connectStream();
                setStatus('ONLINE', true);
            }} catch (e) {{
                setStatus('RETRY...', false);
                setTimeout(openShell, 3000);
            }} finally {{
                opening = false;
            }}
        }}

        function connectStream() {{
            if (evSource) {{ evSource.close(); evSource = null; }}
            evSource = new EventSource('/api/shell/' + shellId + '/stream');
            evSource.onmessage = (ev) => {{
                try {{
                    const d = JSON.parse(ev.data);
                    if (d.o) term.write(d.o);
                    if (d.end) {{
                        evSource.close(); evSource = null;
                        shellId = null;
                        const why = d.reason === 'timeout' ? 'waktu habis (300s)' : 'shell berakhir';
                        term.write('\\r\\n[SESI TUTUP] ' + why + ', membuka shell baru...\\r\\n');
                        setStatus('SYNC...', false);
                        setTimeout(openShell, 800);
                    }}
                }} catch (e) {{}}
            }};
            evSource.onerror = async () => {{
                if (!shellId) return;
                try {{
                    const r = await fetch('/api/shell/' + shellId + '/ping');
                    if (r.status === 404) {{
                        if (evSource) {{ evSource.close(); evSource = null; }}
                        shellId = null;
                        term.write('\\r\\n[SESSION HILANG] instance berganti, membuka shell baru...\\r\\n');
                        setStatus('SYNC...', false);
                        openShell();
                    }}
                }} catch (e) {{}}
            }};
        }}

        async function flushInput() {{
            flushTimer = null;
            if (!shellId) return;
            if (inputBuf) {{
                const payload = inputBuf;
                inputBuf = '';
                try {{
                    const r = await fetch('/api/shell/' + shellId + '/input', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ data: payload }})
                    }});
                    if (r.status === 404) {{
                        if (evSource) {{ evSource.close(); evSource = null; }}
                        shellId = null;
                        term.write('\\r\\n[SESSION HILANG] membuka shell baru...\\r\\n');
                        setStatus('SYNC...', false);
                        openShell();
                        return;
                    }}
                    setStatus('ONLINE', true);
                }} catch (e) {{
                    setStatus('RETRY...', false);
                }}
            }}
        }}

        function queueInput(data) {{
            inputBuf += data;
            if (!flushTimer) flushTimer = setTimeout(flushInput, 50);
        }}

        term.onData((data) => {{
            if (!shellId) {{ openShell(); return; }}
            if (data === '\\x03') {{
                term.write('^C\\r\\n');
                fetch('/api/shell/' + shellId + '/interrupt', {{ method: 'POST' }});
                return;
            }}
            if (data === '\\x0c') {{ term.clear(); return; }}
            queueInput(data);
            if (data.includes('\\r') || data.includes('\\n')) {{
                if (flushTimer) {{ clearTimeout(flushTimer); flushTimer = null; }}
                flushInput();
            }}
        }});

        function sendCommand(cmd) {{ if (shellId) {{ queueInput(cmd); flushInput(); }} term.focus(); }}
        function sendSpecial(key) {{ if (key === '\\x03') {{
            term.write('^C\\r\\n');
            if (shellId) fetch('/api/shell/' + shellId + '/interrupt', {{ method: 'POST' }});
        }} else if (shellId) {{ queueInput(key); flushInput(); }} term.focus(); }}

        function applyStorageUI(storage) {{
            const pct = Math.min(storage.percent, 100);
            const pctText = pct + "%";

            let color = "var(--success)";
            if (pct >= 90) color = "var(--primary)";
            else if (pct >= 75) color = "var(--accent)";

            ['1', '2'].forEach(id => {{
                const fill = document.getElementById('storageMeterFill' + id);
                const txt = document.getElementById('storagePercentText' + id);
                const used = document.getElementById('storageUsedText' + id);
                const total = document.getElementById('storageTotalText' + id);

                if (fill) {{
                    fill.style.width = pctText;
                    fill.style.backgroundColor = color;
                }}
                if (txt) txt.innerText = pctText;
                if (used) used.innerText = storage.used_formatted + " Terpakai";
                if (total) total.innerText = storage.total_formatted + " Total";
            }});

            const label = document.getElementById('storageBoxLabel');
            if (label) label.innerText = "📁 " + storage.label;
            const mini = document.getElementById('storageMiniLabel');
            if (mini) mini.innerText = storage.label;
            const path = document.getElementById('storagePathText');
            if (path) path.innerText = "PATH: " + storage.path;
            const note = document.getElementById('storageNote');
            if (note) {{
                if (storage.ephemeral) {{
                    note.style.display = 'block';
                    note.innerText = "⚠ EPHEMERAL: data di instance /tmp, hilang saat cold start/deploy baru. Upload maks " + (storage.upload_limit_mb || '4.5') + " MB (batas Vercel).";
                }} else {{
                    note.style.display = 'none';
                }}
            }}
        }}

        async function updateStorageInfo() {{
            try {{
                const res = await fetch('/api/files');
                const data = await res.json();
                applyStorageUI(data.storage);
            }} catch (e) {{ console.log("Storage sync error"); }}
        }}

        async function uploadFile() {{
            const fileInput = document.getElementById('fileInput');
            const log = document.getElementById('progressLog');
            if (fileInput.files.length === 0) return alert("Pilih file terlebih dahulu!");

            const file = fileInput.files[0];
            if (file.size > 4.5 * 1048576) {{
                if (!confirm("File > 4.5 MB kemungkinan DITOLAK Vercel (batas request body). Tetap coba?")) return;
            }}
            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/upload', true);
            xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));

            xhr.upload.onprogress = (e) => {{
                if (e.lengthComputable) {{
                    const percent = Math.round((e.loaded / e.total) * 100);
                    const mbL = (e.loaded / 1048576).toFixed(2);
                    const mbT = (e.total / 1048576).toFixed(2);
                    log.innerText = `[UPLOADING] ${{percent}}% (${{mbL}} MB / ${{mbT}} MB)\n[STREAM] Aktif (0% Beban HP)`;
                }}
            }};

            xhr.onload = () => {{
                if (xhr.status === 200) {{
                    log.innerText += `\n\n[SUCCESS] File tersimpan! Ketik 'ls' atau 'python3 ${{file.name}}' di terminal.`;
                    fileInput.value = "";
                    document.getElementById('fileNameDisplay').innerText = "Pilih file ke brankas...";
                    setTimeout(() => {{ switchTab('gallery'); loadGallery(); }}, 1000);
                }} else if (xhr.status === 413) {{
                    log.innerText += `\n\n[ERROR] File terlalu besar untuk Vercel (maks ±4.5 MB).`;
                }} else {{
                    log.innerText += `\n\n[ERROR] Gagal upload! Kode: ${{xhr.status}}`;
                }}
            }};
            xhr.onerror = () => {{ log.innerText += `\n\n[ERROR] Koneksi gagal saat upload.`; }};
            xhr.send(file);
        }}

        async function deleteFile(filename) {{
            if (!confirm(`Hapus file "${{filename}}" secara permanen?`)) return;
            try {{
                const res = await fetch(`/api/files/${{encodeURIComponent(filename)}}`, {{ method: 'DELETE' }});
                if (res.ok) {{
                    loadGallery();
                    updateStorageInfo();
                }} else {{
                    alert("Gagal menghapus file.");
                }}
            }} catch (err) {{
                alert("Kesalahan koneksi saat menghapus file.");
            }}
        }}

        async function loadGallery() {{
            const grid = document.getElementById('gallery-grid');
            grid.innerHTML = '<div style="font-weight:900; grid-column: 1/-1;">Memuat aset file...</div>';
            try {{
                const res = await fetch('/api/files');
                const data = await res.json();

                applyStorageUI(data.storage);

                grid.innerHTML = '';
                if (data.files.length === 0) {{
                    grid.innerHTML = '<div style="color: var(--primary); font-weight:900; grid-column: 1/-1;">Brankas penyimpanan masih kosong.</div>';
                    return;
                }}
                data.files.forEach(f => {{
                    const ext = f.name.split('.').pop().toLowerCase();
                    const fileUrl = `/files/${{encodeURIComponent(f.name)}}`;
                    let previewHtml = '';

                    if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].includes(ext)) {{
                        previewHtml = `<img src="${{fileUrl}}" alt="${{f.name}}" loading="lazy">`;
                    }} else if (['mp4', 'webm', 'ogg', 'mov', 'mkv'].includes(ext)) {{
                        previewHtml = `<video controls preload="metadata" playsinline><source src="${{fileUrl}}"></video>`;
                    }} else {{
                        previewHtml = `<div style="font-size:2rem; font-weight:900;">📄</div>`;
                    }}

                    grid.innerHTML += `
                        <div class="media-item">
                            <div class="media-content">${{previewHtml}}</div>
                            <div class="media-info">
                                <div class="file-name" title="${{f.name}}">${{f.name}}</div>
                                <div class="file-size">${{f.size_formatted}}</div>
                                <div class="action-buttons">
                                    <a href="${{fileUrl}}" download="${{f.name}}" class="btn-action btn-download">DL</a>
                                    <button onclick="deleteFile('${{f.name}}')" class="btn-action btn-delete">HAPUS</button>
                                </div>
                            </div>
                        </div>`;
                }});
            }} catch (err) {{ grid.innerHTML = `<div style="color:var(--primary); font-weight:900; grid-column: 1/-1;">API Error: ${{err}}</div>`; }}
        }}

        updateStorageInfo();
        openShell();
        term.focus();
    </script>
</body>
</html>
"""
