import os, sys, subprocess, pty, gc, urllib.parse, shutil, mimetypes

# ==========================================
# 1. AUTO-INSTALLER TORNADO SERVER
# ==========================================
try:
    import tornado.ioloop, tornado.web, tornado.websocket, tornado.httpserver
except ImportError:
    print("⏳ Menginstal Tornado Server...")
    os.system(f"{sys.executable} -m pip install tornado -q")
    import tornado.ioloop, tornado.web, tornado.websocket, tornado.httpserver

# ==========================================
# 2. TERMUX ENVIRONMENT & PORT CLEANER
# ==========================================
PREFIX = "/data/data/com.termux/files/usr"
if os.path.exists(PREFIX):
    os.environ["PATH"] = f"{PREFIX}/bin:" + os.environ.get("PATH", "")
    os.environ["LD_LIBRARY_PATH"] = f"{PREFIX}/lib"
    TERMUX_BASH = f"{PREFIX}/bin/bash"
else:
    TERMUX_BASH = "/bin/bash"

os.system("fuser -k 8080/tcp > /dev/null 2>&1")

# ==========================================
# 3. ABSOLUTE PERMANENT STORAGE ENGINE (400 TB POOL)
# ==========================================
SDCARD_DIR = "/storage/emulated/0/NEXUS_CLOUD_STORAGE"
TERMUX_HOME_DIR = "/data/data/com.termux/files/home/NEXUS_CLOUD_STORAGE"

try:
    os.makedirs(SDCARD_DIR, exist_ok=True)
    test_file = os.path.join(SDCARD_DIR, ".test_access")
    with open(test_file, "w") as f: f.write("OK")
    os.remove(test_file)
    PERMANENT_STORAGE_DIR = SDCARD_DIR
    STORAGE_LABEL = "INTERNAL SDCARD STORAGE (400 TB POOL)"
except Exception:
    os.makedirs(TERMUX_HOME_DIR, exist_ok=True)
    PERMANENT_STORAGE_DIR = TERMUX_HOME_DIR
    STORAGE_LABEL = "TERMUX ROOT STORAGE (400 TB POOL)"

# ==========================================
# 4. PROFESSIONAL BASH INJECTION (AUTO-CD TO STORAGE)
# ==========================================
TMP_DIR = os.environ.get("TMPDIR", "/data/data/com.termux/files/usr/tmp")
if not os.path.exists(TMP_DIR): TMP_DIR = "/tmp"
BASH_RC_PATH = os.path.join(TMP_DIR, "storage_terminal_rc")

with open(BASH_RC_PATH, "w") as f:
    f.write(r'''
[ -f /data/data/com.termux/files/usr/etc/bash.bashrc ] && source /data/data/com.termux/files/usr/etc/bash.bashrc
set +H
cd "''' + PERMANENT_STORAGE_DIR + r'''"
export PS1='\[\e[1;35m\]storage-term\[\e[0m\]:\[\e[1;32m\]\w\[\e[0m\]\$ '
alias ls='ls --color=auto'
clear
echo -e "\e[1;35m==================================================\e[0m"
echo -e "\e[1;37m        STORAGE TERMINAL KERNEL ACTIVE            \e[0m"
echo -e "\e[1;35m==================================================\e[0m"
echo -e "\e[1;32m[✔] Working Directory: $(pwd)\e[0m"
echo -e "\e[1;33m[✔] Pool Kapasitas   : 400.0 TB Virtual\e[0m\n"
''')

# ==========================================
# 5. FRONTEND UI (STORAGE TERMINAL BRANDING)
# ==========================================
HTML_FRONTEND = f"""<!DOCTYPE html>
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
        <button class="tab-btn" id="btn-tab-storage" onclick="switchTab('storage'); updateStorageInfo();">Storage (400TB)</button>
        <button class="tab-btn" id="btn-tab-gallery" onclick="switchTab('gallery'); loadGallery();">Gallery Preview</button>
    </div>

    <!-- TAB 1: TERMINAL -->
    <div id="tab-terminal" class="content-area active">
        <div class="terminal-wrapper">
            <div class="terminal-window">
                <div class="window-header">
                    <div class="dot"></div><div class="dot"></div><div class="dot"></div>
                    <span style="margin-left: 5px; font-size: 0.75rem; font-weight: 900; color: #000;">TERMUX RUNNER KERNEL</span>
                </div>
                <div id="terminal-container"></div>
                <div id="mobile-bar">
                    <button class="brutal-btn" onclick="sendSpecial('\\x03')">^C</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\t')">TAB</button>
                    <button class="brutal-btn btn-primary-color" onclick="sendCommand('python\\r')">PY</button>
                    <button class="brutal-btn" onclick="sendCommand('ls -la\\r')">LS</button>
                    <button class="brutal-btn" onclick="sendCommand('clear\\r')">CLR</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\x1b[A')">▲</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\x1b[B')">▼</button>
                </div>
            </div>
        </div>
    </div>

    <!-- TAB 2: STORAGE & UPLOAD -->
    <div id="tab-storage" class="content-area">
        <div class="box-container">

            <div class="brutal-card">
                <div class="storage-box-label">📁 {STORAGE_LABEL}</div>
                <div class="storage-header">
                    <span>Kapasitas Terpakai (Pool 400 TB)</span>
                    <span id="storagePercentText1" style="font-weight:900;">0%</span>
                </div>
                <div class="storage-meter-bg">
                    <div id="storageMeterFill1" class="storage-meter-fill"></div>
                </div>
                <div class="storage-text-stats">
                    <span id="storageUsedText1">0 MB Terpakai</span>
                    <span id="storageTotalText1">400.0 TB Total</span>
                </div>
                <div class="path-text">PATH: {PERMANENT_STORAGE_DIR}</div>
            </div>

            <div class="brutal-card">
                <div class="card-header">
                    <h3>🚀 File Stream Upload</h3>
                    <span style="font-size:0.7rem; font-weight:900; background:var(--success); padding:3px 6px; border:2px solid var(--border); border-radius:4px;">0% RAM HP</span>
                </div>
                <div class="upload-area">
                    <div style="font-size: 2rem; margin-bottom: 5px;">📥</div>
                    <div style="font-weight: 900; font-size: 0.9rem;" id="fileNameDisplay">Pilih file python/video ke brankas...</div>
                    <input type="file" id="fileInput" multiple onchange="updateFileName()">
                </div>
                <button class="brutal-btn btn-primary-color" style="width: 100%; padding: 12px; font-size: 0.9rem;" onclick="uploadFile()">UNGGAH SEKARANG</button>
                <div class="progress-box" id="progressLog">[READY] Menunggu input data...</div>
            </div>
        </div>
    </div>

    <!-- TAB 3: GALLERY -->
    <div id="tab-gallery" class="content-area">
        <div class="box-container">

            <div class="brutal-card" style="padding: 15px; margin-bottom: 15px;">
                <div class="storage-header" style="font-size:0.75rem;">
                    <span>STORAGE POOL (400 TB)</span>
                    <span id="storagePercentText2">0%</span>
                </div>
                <div class="storage-meter-bg" style="height: 14px;">
                    <div id="storageMeterFill2" class="storage-meter-fill"></div>
                </div>
                <div class="storage-text-stats" style="font-size:0.65rem;">
                    <span id="storageUsedText2">0 MB</span>
                    <span id="storageTotalText2">400.0 TB</span>
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
                display.innerText = "Pilih file python/video ke brankas...";
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

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(protocol + '//' + window.location.host + '/ws');

        ws.onopen = () => {{ statusEl.innerText = "ONLINE"; statusEl.className = "status online"; term.focus(); }};
        ws.onmessage = (e) => term.write(e.data);
        ws.onclose = () => {{ statusEl.innerText = "OFFLINE"; statusEl.className = "status"; }};
        term.onData((data) => {{ if (ws.readyState === WebSocket.OPEN) ws.send(data); }});

        function sendCommand(cmd) {{ if (ws.readyState === WebSocket.OPEN) {{ ws.send(cmd); term.focus(); }} }}
        function sendSpecial(key) {{ if (ws.readyState === WebSocket.OPEN) {{ ws.send(key); term.focus(); }} }}

        function applyStorageUI(storage) {{
            const pct = storage.percent;
            const pctText = pct + "%";

            let color = "var(--success)";
            if(pct >= 90) color = "var(--primary)";
            else if(pct >= 75) color = "var(--accent)";

            ['1', '2'].forEach(id => {{
                const fill = document.getElementById('storageMeterFill' + id);
                const txt = document.getElementById('storagePercentText' + id);
                const used = document.getElementById('storageUsedText' + id);
                const total = document.getElementById('storageTotalText' + id);

                if(fill) {{
                    fill.style.width = pctText;
                    fill.style.backgroundColor = color;
                }}
                if(txt) txt.innerText = pctText;
                if(used) used.innerText = storage.used_formatted;
                if(total) total.innerText = "400.0 TB Total";
            }});
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
            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/upload', true);
            xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));

            xhr.upload.onprogress = (e) => {{
                if (e.lengthComputable) {{
                    const percent = Math.round((e.loaded / e.total) * 100);
                    const mbL = (e.loaded / 1048576).toFixed(2);
                    const mbT = (e.total / 1048576).toFixed(2);
                    log.innerText = `[UPLOADING] ${{percent}}% (${{mbL}} MB / ${{mbT}} MB)\n[RAM BYPASS] Aktif (0% Beban HP)`;
                }}
            }};

            xhr.onload = () => {{
                if (xhr.status === 200) {{
                    log.innerText += `\\n\\n[SUCCESS] File tersimpan di direktori terminal! Ketik 'ls' atau 'python ${{file.name}}' di terminal.`;
                    fileInput.value = "";
                    document.getElementById('fileNameDisplay').innerText = "Pilih file python/video ke brankas...";
                    setTimeout(() => {{ switchTab('gallery'); loadGallery(); }}, 1000);
                }} else {{ log.innerText += `\\n\\n[ERROR] Gagal upload! Kode: ${{xhr.status}}`; }}
            }};
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
    </script>
</body>
</html>
"""

# ==========================================
# 6. BACKEND HANDLERS (AUTO-CD PTY & RUNNER)
# ==========================================
class MainHandler(tornado.web.RequestHandler):
    def get(self): self.write(HTML_FRONTEND)

class TerminalSocketHandler(tornado.websocket.WebSocketHandler):
    def check_origin(self, origin): return True
    def open(self):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["PYTHONUNBUFFERED"] = "1"  # Real-time python output without buffering
            os.execve(TERMUX_BASH, ["bash", "--rcfile", BASH_RC_PATH, "-i"], env)
        else:
            tornado.ioloop.IOLoop.current().add_handler(self.fd, self.on_pty_read, tornado.ioloop.IOLoop.READ)

    def on_pty_read(self, fd, events):
        try:
            data = os.read(fd, 1024)
            if data: self.write_message(data.decode("utf-8", errors="ignore"))
        except Exception: pass

    def on_message(self, message):
        if hasattr(self, 'fd'): os.write(self.fd, message.encode("utf-8"))

    def on_close(self):
        if hasattr(self, 'fd'):
            tornado.ioloop.IOLoop.current().remove_handler(self.fd)
            os.close(self.fd)

@tornado.web.stream_request_body
class AntiRamUploadHandler(tornado.web.RequestHandler):
    def prepare(self):
        try:
            raw = self.request.headers.get("X-Filename", "unknown.bin")
            safe_filename = os.path.basename(urllib.parse.unquote(raw))
            self.filepath = os.path.join(PERMANENT_STORAGE_DIR, safe_filename)
            self.file = open(self.filepath, "wb")
        except Exception as e:
            print(f"Error preparing file upload: {e}")
            self.set_status(500)
            self.finish({"status": "error", "message": str(e)})

    def data_received(self, chunk):
        try:
            if hasattr(self, 'file') and self.file:
                self.file.write(chunk)
            del chunk
            gc.collect()
        except Exception as e:
            print(f"Error writing chunk: {e}")

    def post(self):
        try:
            if hasattr(self, 'file') and self.file:
                self.file.close()
            self.write({"status": "success"})
        except Exception as e:
            self.set_status(500)
            self.write({"status": "error", "message": str(e)})

class FileDeleteAPIHandler(tornado.web.RequestHandler):
    def delete(self, filename):
        try:
            filename = urllib.parse.unquote(filename)
            filepath = os.path.join(PERMANENT_STORAGE_DIR, filename)
            if os.path.exists(filepath) and os.path.isfile(filepath):
                os.remove(filepath)
                self.write({"status": "success"})
            else:
                self.set_status(404)
                self.write({"status": "error", "message": "File not found"})
        except Exception as e:
            self.set_status(500)
            self.write({"status": "error", "message": str(e)})

class DirectMediaHandler(tornado.web.RequestHandler):
    def get(self, filename):
        try:
            filename = urllib.parse.unquote(filename)
            filepath = os.path.join(PERMANENT_STORAGE_DIR, filename)
            if not os.path.exists(filepath) or not os.path.isfile(filepath):
                raise tornado.web.HTTPError(404, "File not found")

            content_type, _ = mimetypes.guess_type(filepath)
            if not content_type:
                content_type = "application/octet-stream"
            self.set_header("Content-Type", content_type)

            file_size = os.path.getsize(filepath)
            self.set_header("Accept-Ranges", "bytes")

            range_header = self.request.headers.get("Range")
            if range_header:
                try:
                    range_val = range_header.replace("bytes=", "")
                    parts = range_val.split("-")
                    start = int(parts[0]) if parts[0] else 0
                    end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
                    if end >= file_size:
                        end = file_size - 1
                    length = end - start + 1

                    self.set_status(206)
                    self.set_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                    self.set_header("Content-Length", length)

                    with open(filepath, "rb") as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk_size = min(65536, remaining)
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            self.write(chunk)
                            remaining -= len(chunk)
                            try:
                                self.flush()
                            except:
                                break
                    return
                except Exception as e:
                    print(f"Range request error: {e}")

            self.set_header("Content-Length", file_size)
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.write(chunk)
                    try:
                        self.flush()
                    except:
                        break
            gc.collect()
        except Exception as e:
            raise tornado.web.HTTPError(500, str(e))

class FileListAPIHandler(tornado.web.RequestHandler):
    def get(self):
        out = []
        used_bytes = 0
        try:
            if os.path.exists(PERMANENT_STORAGE_DIR):
                files = sorted(
                    [f for f in os.listdir(PERMANENT_STORAGE_DIR) if os.path.isfile(os.path.join(PERMANENT_STORAGE_DIR, f))],
                    key=lambda x: os.path.getctime(os.path.join(PERMANENT_STORAGE_DIR, x)), reverse=True
                )
                for f in files:
                    fp = os.path.join(PERMANENT_STORAGE_DIR, f)
                    sz_bytes = os.path.getsize(fp)
                    used_bytes += sz_bytes

                    if sz_bytes >= 1073741824:
                        sz_fmt = f"{round(sz_bytes / 1073741824, 2)} GB"
                    else:
                        sz_fmt = f"{round(sz_bytes / 1048576, 2)} MB"

                    out.append({"name": f, "size_formatted": sz_fmt})
        except Exception as e:
            print(f"Error reading file list: {e}")

        total_bytes = 400.0 * (1024**4) # 400 TB
        percent = round((used_bytes / total_bytes) * 100, 4) if total_bytes > 0 else 0
        if percent == 0 and used_bytes > 0:
            percent = 0.01

        if used_bytes >= 1099511627776:
            used_fmt = f"{round(used_bytes / 1099511627776, 2)} TB"
        elif used_bytes >= 1073741824:
            used_fmt = f"{round(used_bytes / 1073741824, 2)} GB"
        else:
            used_fmt = f"{round(used_bytes / 1048576, 2)} MB"

        storage_info = {
            "total_gb": 400.0,
            "used_gb": round(used_bytes / (1024**3), 2),
            "used_formatted": used_fmt,
            "percent": percent
        }

        self.write({"files": out, "storage": storage_info})

if __name__ == "__main__":
    PORT = 8080
    app = tornado.web.Application([
        (r"/", MainHandler),
        (r"/ws", TerminalSocketHandler),
        (r"/upload", AntiRamUploadHandler),
        (r"/api/files", FileListAPIHandler),
        (r"/api/files/(.*)", FileDeleteAPIHandler),  # Endpoint hapus file
        (r"/files/(.*)", DirectMediaHandler)         # Endpoint preview & streaming video/gambar
    ])

    server = tornado.httpserver.HTTPServer(app, max_buffer_size=1048576000000)
    server.listen(PORT)

    print(f"\n[+] STORAGE TERMINAL PTY: ACTIVE")
    print(f"[+] AUTO-CD WORKING DIRECTORY: {PERMANENT_STORAGE_DIR}")
    print(f"[+] 0% RAM BYPASS: ACTIVE (UNLIMITED STREAM)")
    print(f"[+] STORAGE POOL: 400 TB VIRTUAL MOUNT")
    print(f"\n👉 AKSES BROWSER: http://localhost:{PORT}\n")
    tornado.ioloop.IOLoop.current().start()
