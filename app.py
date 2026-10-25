import base64
import codecs
import errno
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
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    import fcntl
    import pty
    import select
    import struct
    import termios

    _PTY_IMPORT_ERROR = ""
except Exception as _pty_exc:  # pragma: no cover - platform non-unix
    fcntl = pty = select = struct = termios = None
    _PTY_IMPORT_ERROR = f"{type(_pty_exc).__name__}: {_pty_exc}"

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

app = FastAPI(title="Storage Terminal Mazval")

STORAGE_DIR = os.environ.get("MAZVAL_STORAGE", "/tmp/mazval_storage")
os.makedirs(STORAGE_DIR, exist_ok=True)

RC_PATH = "/tmp/mazval_bashrc"
HIST_PATH = "/tmp/mazval_history"
MAX_SHELLS = 64
STREAM_MAX_SECONDS = 290
MAX_INPUT = 65536
MAX_BUFFER = 4 * 1048576
SSE_BATCH_BYTES = 65536
SSE_BATCH_CHUNKS = 64
DEFAULT_COLS = 120
DEFAULT_ROWS = 40

EPHEMERAL = bool(os.environ.get("VERCEL"))
INSTANCE_ID = uuid.uuid4().hex[:8]


def _pty_works():
    """Cek nyata apakah runtime menyediakan PTY (/dev/ptmx + devpts)."""
    if _PTY_IMPORT_ERROR:
        return False
    master = slave = None
    try:
        master, slave = pty.openpty()
        return True
    except Exception:
        return False
    finally:
        for fd in (master, slave):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


# Mode shell bisa dipaksa lewat env (untuk uji jalur cadangan):
#   MAZVAL_SHELL_MODE=pipe  -> paksa pipe walau PTY tersedia
#   MAZVAL_SHELL_MODE=pty   -> paksa PTY (gagal jika runtime tanpa PTY)
FORCE_MODE = os.environ.get("MAZVAL_SHELL_MODE", "").strip().lower()
PTY_AVAILABLE = _pty_works() and FORCE_MODE != "pipe"
SHELL_MODE = "pty" if PTY_AVAILABLE else "pipe"


def _terminfo_ok(name):
    if not name:
        return False
    if shutil.which("infocmp"):
        try:
            p = subprocess.run(
                ["infocmp", "-1", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if p.returncode == 0:
                return True
        except Exception:
            pass
    roots = [
        os.environ.get("TERMINFO"),
        "/usr/share/terminfo",
        "/lib/terminfo",
        "/etc/terminfo",
        "/usr/local/share/terminfo",
        "/opt/homebrew/Cellar",
    ]
    for d in os.environ.get("TERMINFO_DIRS", "").split(":"):
        if d:
            roots.append(d)
    first = name[0]
    for root in roots:
        if not root:
            continue
        for candidate in (
            os.path.join(root, first, name),
            os.path.join(root, name),
        ):
            if os.path.isfile(candidate):
                return True
    return False


_TERM_CANDIDATES = (
    "xterm-256color",
    "xterm",
    "screen-256color",
    "screen",
    "linux",
    "vt100",
    "ansi",
    "dumb",
)


def _pick_term():
    forced = os.environ.get("MAZVAL_TERM")
    if forced and _terminfo_ok(forced):
        return forced
    for name in _TERM_CANDIDATES:
        if _terminfo_ok(name):
            return name
    return "xterm"


TERM_NAME = _pick_term()
TERM_KNOWN = TERM_NAME != "xterm" or _terminfo_ok("xterm")

BANNER_ENV = "VERCEL SANDBOX" if EPHEMERAL else "LOKAL/Termux"
if PTY_AVAILABLE:
    BANNER_MODE = "PTY KERNEL - penuh seperti Termux"
else:
    BANNER_MODE = (
        "PIPE SHELL - fallback runtime tanpa PTY"
        if EPHEMERAL
        else "PIPE SHELL - echo/prompt dari readline"
    )

# Deteksi info sistem untuk banner profesional
try:
    import platform
    _UNAME_SYS = platform.system() or "Linux"
    _UNAME_REL = platform.release() or "unknown"
    _UNAME_ARCH = platform.machine() or os.uname().machine if hasattr(os, 'uname') else "unknown"
except Exception:
    _UNAME_SYS = "Linux"
    _UNAME_REL = "unknown"
    _UNAME_ARCH = "unknown"

try:
    _PYTHON_VER = platform.python_version() if 'platform' in dir() else ".".join(map(str, __import__('sys').version_info[:3]))
except Exception:
    _PYTHON_VER = "unknown"

try:
    _BASH_VER = subprocess.run([BASH_BIN, "--version"], capture_output=True, text=True, timeout=2).stdout.split('\n')[0].replace("GNU bash, version ", "").split("(")[0].strip() or "unknown"
except Exception:
    _BASH_VER = "unknown"

BANNER_TITLE = "MAZVAL TERMINAL PRO"
BANNER_W = 60  # lebar kotak banner (judul diketengahkan presisi)
BANNER_STORE = "PERMANEN - TERSIMPAN DI CLOUD" if EPHEMERAL else "PERMANENT STORAGE"
BANNER_KERNEL = f"{_UNAME_SYS} {_UNAME_ARCH} ({_UNAME_REL})"
BANNER_PYTHON = _PYTHON_VER
BANNER_BASH = _BASH_VER

_RC_RUNFN = r'''
command -v python >/dev/null 2>&1 || { command -v python3 >/dev/null 2>&1 && alias python=python3; }

# Peringatan friendly untuk editor di mode pipe (serverless)
__mazval_shell_mode__="{SHELL_MODE}"
if [ "$__mazval_shell_mode__" = "pipe" ]; then
    nano() {{ echo "⚠️  Editor TUI membutuhkan mode PTY. Coba: sed, awk, atau gunakan copy file ke Termux lokal."; return 1; }}
    vim() {{ echo "⚠️  Vim membutuhkan mode PTY. Jalankan di Termux lokal untuk akses penuh."; return 1; }}
    vi() {{ echo "⚠️  Vi membutuhkan mode PTY. Jalankan di Termux lokal untuk akses penuh."; return 1; }}
fi

run() {{
    if [ -z "$1" ]; then
        echo "usage: run <file.py|file.sh|program> [arg...]"
        return 2
    fi
    if [ ! -e "$1" ]; then
        echo "run: file tidak ditemukan: $1"
        return 127
    fi
    case "$1" in
        *.py)
            if command -v python3 >/dev/null 2>&1; then python3 "$@"; return $?; fi
            if command -v python >/dev/null 2>&1; then python "$@"; return $?; fi
            if [ -x "$1" ]; then "$@"; return $?; fi
            echo "run: interpreter python belum terpasang (pkg install python)"
            return 127
            ;;
        *.sh|*.bash)
            bash "$@"
            return $?
            ;;
        *)
            if [ -x "$1" ] && [ ! -d "$1" ]; then "$@"; return $?; fi
            if command -v python3 >/dev/null 2>&1; then python3 "$@"; return $?; fi
            echo "run: tidak ada interpreter untuk $1 (coba: bash \"$1\")"
            return 126
            ;;
    esac
}}
'''

_RC_CONTENT = f"""set +H
HISTSIZE=5000
HISTFILESIZE=10000
HISTFILE={HIST_PATH}
shopt -s histappend 2>/dev/null
shopt -s checkwinsize 2>/dev/null
PROMPT_COMMAND='history -a'
PS1='\\[\\e[1;35m\\]mazval-term\\[\\e[0m\\]:\\[\\e[1;32m\\]\\w\\[\\e[0m\\]\\$ '
bind 'set completion-ignore-case on' 2>/dev/null
bind 'set show-all-if-ambiguous on' 2>/dev/null
bind 'set completion-map-case on' 2>/dev/null
if ls --color=auto / >/dev/null 2>&1; then
    alias ls='ls --color=auto'
    alias ll='ls -alF --color=auto'
    alias la='ls -A --color=auto'
fi
alias l='ls -CF'
printf x | grep --color=auto x >/dev/null 2>&1 && alias grep='grep --color=auto'
command -v clear >/dev/null 2>&1 || alias clear='printf "\\033[H\\033[2J"'
{_RC_RUNFN}
cd "{STORAGE_DIR}" || cd /tmp
export PYTHONUNBUFFERED=1
printf '\\e[H\\e[2J\\e[3J'
__w={BANNER_W}
[ -n "$COLUMNS" ] && [ "$COLUMNS" -lt {BANNER_W} ] && __w=$COLUMNS
[ "$__w" -lt 32 ] && __w=32
__sep=''; __i=0
while [ $__i -lt $__w ]; do __sep="$__sep="; __i=$((__i+1)); done
__n={len(BANNER_TITLE)}
__l=$(( (__w - __n) / 2 )); [ $__l -lt 0 ] && __l=0
__r=$(( __w - __n - __l )); [ $__r -lt 0 ] && __r=0
__ls=''; __i=0
while [ $__i -lt $__l ]; do __ls="$__ls "; __i=$((__i+1)); done
__rs=''; __i=0
while [ $__i -lt $__r ]; do __rs="$__rs "; __i=$((__i+1)); done
printf '\\e[1;35m%s\\e[0m\\n' "$__sep"
printf '\\e[1;37m%s{BANNER_TITLE}%s\\e[0m\\n' "$__ls" "$__rs"
printf '\\e[1;35m%s\\e[0m\\n' "$__sep"
printf '\\e[1;32m[OK] %-12s : %s\\e[0m\\n' "env" "{BANNER_ENV}"
printf '\\e[1;32m[OK] %-12s : %s\\e[0m\\n' "mode" "{BANNER_MODE}"
printf '\\e[1;32m[OK] %-12s : %s\\e[0m\\n' "kernel" "{BANNER_KERNEL}"
printf '\\e[1;32m[OK] %-12s : %s\\e[0m\\n' "bash" "{BANNER_BASH}"
printf '\\e[1;32m[OK] %-12s : %s\\e[0m\\n' "python" "{BANNER_PYTHON}"
printf '\\e[1;32m[OK] %-12s : %s\\e[0m\\n' "storage" "{BANNER_STORE}"
printf '\\e[1;32m[OK] %-12s : %s\\e[0m\\n' "workdir" "$(pwd)"
printf '\\n'
"""

try:
    with open(RC_PATH, "w") as _rc:
        _rc.write(_RC_CONTENT)
except OSError:
    RC_PATH = "/dev/null"

NOISE_PATTERNS = [
    re.compile(rb"bash: cannot set terminal process group[^\n]*\n"),
    re.compile(rb"bash: no job control in this shell\n"),
]

SHELLS = {}
SHELLS_LOCK = threading.Lock()


def _clamp(value, low, high, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    if value < low:
        return low
    if value > high:
        return high
    return value


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


# ---------------------------------------------------------------------------
# LAPISAN PENYIMPANAN PERMANEN (repo GitHub privat)
# ---------------------------------------------------------------------------
# File di-staging dulu ke /tmp (cepat + pinning instance), lalu didorong
# segera ke repo GitHub sebagai blob per-part (5MB). List/download/delete
# selalu membaca dari GitHub sehingga file TETAP ADA walau instance mati.
GH_API = "https://api.github.com"
ST_OWNER = os.environ.get("ST_GH_OWNER", "ovalkyzz-cell")
ST_REPO = os.environ.get("ST_GH_REPO", "terminal-mazval-storage")
ST_BRANCH = os.environ.get("ST_GH_BRANCH", "main")
ST_TOKEN = os.environ.get("ST_GH_TOKEN", "")
ST_PART = 5 * 1048576
STORE_PREFIX = "_store"
TREE_TTL = 4.0
DURABLE = bool(ST_TOKEN)

_GH_LOCK = threading.Lock()
_BOOT_LOCK = threading.Lock()
_BOOTED = [False]
_TREE_CACHE = {"t": 0.0, "entries": None}
_EMPTY_SHA = [None]
_PART_DIR = "/tmp/ghc"


def _gh(method, path, payload=None, raw=False, timeout=45):
    if not ST_TOKEN:
        return 0, b"" if raw else {"message": "token tidak ada"}
    req = urllib.request.Request(GH_API + path, method=method)
    req.add_header("Authorization", "Bearer " + ST_TOKEN)
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header(
        "Accept", "application/vnd.github.raw" if raw else "application/vnd.github+json"
    )
    if payload is not None:
        req.data = json.dumps(payload).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if raw:
                return resp.status, body
            return resp.status, json.loads(body or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}")
        except Exception:
            detail = {}
        msg = detail.get("message") or str(e)
        return e.code, str(msg).encode() if raw else {"message": msg}
    except Exception as e:
        return 0, str(e).encode() if raw else {"message": str(e)}


def _ensure_repo():
    """Repo kosong menolak git-data API -> buat commit awal via Contents API."""
    if _BOOTED[0]:
        return True
    with _BOOT_LOCK:
        if _BOOTED[0]:
            return True
        st, obj = _gh(
            "PUT",
            f"/repos/{ST_OWNER}/{ST_REPO}/contents/_boot.md",
            {
                "message": "bootstrap: penyimpanan terminal",
                "content": base64.b64encode(b"# terminal-mazval durable storage\n").decode(),
            },
        )
        if st in (200, 201):
            _TREE_CACHE["t"] = 0.0
            _TREE_CACHE["entries"] = None
            _BOOTED[0] = True
            return True
        msg = str(obj.get("message", "")).lower()
        if "already exists" in msg:
            _BOOTED[0] = True
            return True
        return False


def _gh_tree_entries(force=False):
    now = time.time()
    if (
        not force
        and _TREE_CACHE["entries"] is not None
        and now - _TREE_CACHE["t"] < TREE_TTL
    ):
        return _TREE_CACHE["entries"]
    st, obj = _gh(
        "GET", f"/repos/{ST_OWNER}/{ST_REPO}/git/trees/{ST_BRANCH}?recursive=1"
    )
    if st == 409 and _ensure_repo():
        st, obj = _gh(
            "GET", f"/repos/{ST_OWNER}/{ST_REPO}/git/trees/{ST_BRANCH}?recursive=1"
        )
    if st == 409:
        _TREE_CACHE["t"] = now
        _TREE_CACHE["entries"] = []
        return []
    if st != 200:
        raise RuntimeError("gagal membaca tree: " + str(obj.get("message", st)))
    _TREE_CACHE["t"] = now
    _TREE_CACHE["entries"] = obj.get("tree", [])
    return _TREE_CACHE["entries"]


def _enc_name(name):
    return urllib.parse.quote(name, safe="")


def _empty_sha():
    if _EMPTY_SHA[0]:
        return _EMPTY_SHA[0]
    st, obj = _gh(
        "POST",
        f"/repos/{ST_OWNER}/{ST_REPO}/git/blobs",
        {"content": "", "encoding": "base64"},
    )
    if st != 201:
        raise RuntimeError("gagal blob kosong: " + str(obj.get("message", st)))
    _EMPTY_SHA[0] = obj["sha"]
    return obj["sha"]


def durable_scan(force=False):
    """Daftar file permanen dari GitHub: {name: {size, created, parts}}."""
    if not DURABLE:
        return {}
    entries = _gh_tree_entries(force=force)
    marker_re = re.compile(r"^(?P<size>\d+)__(?P<created>\d+)\.meta$")
    out = {}
    for it in entries:
        parts = (it.get("path") or "").split("/")
        if len(parts) != 3 or parts[0] != STORE_PREFIX:
            continue
        enc, fname = parts[1], parts[2]
        m = marker_re.match(fname)
        if m:
            info = out.get(enc)
            created = int(m.group("created"))
            if info is None or created > info["created"]:
                out[enc] = {
                    "name": urllib.parse.unquote(enc),
                    "size": int(m.group("size")),
                    "created": created,
                    "parts": info["parts"] if info else [],
                }
        elif fname.startswith("p") and fname.endswith(".bin"):
            info = out.get(enc)
            if info is None:
                info = {
                    "name": urllib.parse.unquote(enc),
                    "size": -1,
                    "created": 0,
                    "parts": [],
                }
                out[enc] = info
            info["parts"].append(
                {"sha": it.get("sha"), "size": it.get("size") or 0, "path": parts[2]}
            )
    result = {}
    for info in out.values():
        if info["size"] < 0:
            continue
        info["parts"].sort(key=lambda x: x["path"])
        result[info["name"]] = info
    return result


def durable_get(name):
    if not DURABLE:
        return None
    try:
        return durable_scan().get(name)
    except Exception:
        return None


def _commit_tree(message, tree_entries):
    """Commit penuh (tanpa base_tree) + update ref, dengan retry anti-konflik."""
    for _ in range(4):
        st, obj = _gh(
            "POST", f"/repos/{ST_OWNER}/{ST_REPO}/git/trees", {"tree": tree_entries}
        )
        if st != 201:
            time.sleep(0.5)
            continue
        tree_sha = obj["sha"]
        st, cobj = _gh("GET", f"/repos/{ST_OWNER}/{ST_REPO}/commits/{ST_BRANCH}")
        parents = [cobj["sha"]] if st == 200 else []
        st, obj = _gh(
            "POST",
            f"/repos/{ST_OWNER}/{ST_REPO}/git/commits",
            {"message": message, "tree": tree_sha, "parents": parents},
        )
        if st != 201:
            time.sleep(0.5)
            continue
        st2, _ = _gh(
            "PATCH",
            f"/repos/{ST_OWNER}/{ST_REPO}/git/refs/heads/{ST_BRANCH}",
            {"sha": obj["sha"]},
        )
        if st2 == 200:
            _TREE_CACHE["t"] = 0.0
            _TREE_CACHE["entries"] = None
            return True
        if st2 in (404, 409, 422):
            st3, _ = _gh(
                "POST",
                f"/repos/{ST_OWNER}/{ST_REPO}/git/refs",
                {"ref": f"refs/heads/{ST_BRANCH}", "sha": obj["sha"]},
            )
            if st3 == 201:
                _TREE_CACHE["t"] = 0.0
                _TREE_CACHE["entries"] = None
                return True
        time.sleep(0.6)
    return False


def _keep_entries(enc, extra):
    """Semua leaf blob kecuali bagian file ini, plus entri baru.

    Hanya entri blob (path lengkap) yang dipakai -- GitHub membangun
    direktori antara otomatis. Entri subtree (type=tree) sengaja TIDAK
    ikut, kalau tidak subtree lama bisa menempel kembali.
    """
    entries = _gh_tree_entries(force=True)
    drop = f"{STORE_PREFIX}/{enc}/"
    tree = [
        {"path": e["path"], "mode": e["mode"], "type": "blob", "sha": e["sha"]}
        for e in entries
        if e.get("type") == "blob" and not (e.get("path") or "").startswith(drop)
    ]
    tree.extend(extra)
    return tree


def durable_push(name, path, size):
    """Dorong file yang selesai di-staging ke repo GitHub. Idempoten."""
    if not DURABLE:
        return False
    enc = _enc_name(name)
    with _GH_LOCK:
        try:
            cur = durable_scan(force=True).get(name)
        except Exception:
            cur = None
        if cur and cur["size"] == size and (
            size == 0 or sum(p["size"] for p in cur["parts"]) == size
        ):
            return True
        add = []
        if size == 0:
            add.append(
                {
                    "path": f"{STORE_PREFIX}/{enc}/p00000.bin",
                    "mode": "100644",
                    "type": "blob",
                    "sha": _empty_sha(),
                }
            )
        else:
            with open(path, "rb") as f:
                idx = 0
                while True:
                    data = f.read(ST_PART)
                    if not data:
                        break
                    st, obj = _gh(
                        "POST",
                        f"/repos/{ST_OWNER}/{ST_REPO}/git/blobs",
                        {
                            "content": base64.b64encode(data).decode("ascii"),
                            "encoding": "base64",
                        },
                        timeout=120,
                    )
                    if st != 201:
                        return False
                    add.append(
                        {
                            "path": f"{STORE_PREFIX}/{enc}/p{idx:05d}.bin",
                            "mode": "100644",
                            "type": "blob",
                            "sha": obj["sha"],
                        }
                    )
                    idx += 1
        add.append(
            {
                "path": f"{STORE_PREFIX}/{enc}/{size}__{int(time.time())}.meta",
                "mode": "100644",
                "type": "blob",
                "sha": _empty_sha(),
            }
        )
        try:
            tree_entries = _keep_entries(enc, add)
        except Exception:
            return False
        return _commit_tree(f"store: {name} ({fmt_size(size)})", tree_entries)


def durable_delete(name):
    """Hapus file dari repo GitHub (direktori _store/{enc}/)."""
    if not DURABLE:
        return False
    enc = _enc_name(name)
    with _GH_LOCK:
        try:
            entries = _gh_tree_entries(force=True)
            drop = f"{STORE_PREFIX}/{enc}/"
            if not any(
                e.get("type") == "blob" and (e.get("path") or "").startswith(drop)
                for e in entries
            ):
                return False
            tree = [
                {"path": e["path"], "mode": e["mode"], "type": "blob", "sha": e["sha"]}
                for e in entries
                if e.get("type") == "blob"
                and not (e.get("path") or "").startswith(drop)
            ]
            return _commit_tree(f"delete: {name}", tree)
        except Exception:
            return False


def _part_path(sha):
    if not os.path.isdir(_PART_DIR):
        os.makedirs(_PART_DIR, exist_ok=True)
    path = os.path.join(_PART_DIR, sha)
    if os.path.isfile(path):
        return path
    for _ in range(3):
        st, body = _gh(
            "GET", f"/repos/{ST_OWNER}/{ST_REPO}/git/blobs/{sha}", raw=True, timeout=90
        )
        if st == 200:
            tmp = path + "." + uuid.uuid4().hex[:6]
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, path)
            try:
                cached = sorted(
                    (
                        os.path.join(_PART_DIR, x)
                        for x in os.listdir(_PART_DIR)
                        if x != sha and "." not in x
                    ),
                    key=os.path.getmtime,
                )
                for old in cached[: max(0, len(cached) - 30)]:
                    os.remove(old)
            except Exception:
                pass
            return path
        time.sleep(0.4)
    return None


BASH_BIN = shutil.which("bash") or "/bin/bash"


def _reset_child_signals():
    """Paksa sinyal ke default di dalam shell anak.

    Peluncur (nohup / & / pm2 / systemd) sering mewarisi SIGINT-SIGQUIT-SIGHUP
    sebagai SIG_IGN. Atribut "ignored" melewati exec(), sehingga tanpa reset ini
    Ctrl+C ^Z dan SIGHUP di terminal jadi TIDAK BEREFEK sama sekali.
    """
    for sig in (
        signal.SIGHUP,
        signal.SIGINT,
        signal.SIGQUIT,
        signal.SIGTERM,
        signal.SIGPIPE,
        signal.SIGUSR1,
        signal.SIGUSR2,
        signal.SIGTSTP,
        signal.SIGTTIN,
        signal.SIGTTOU,
        signal.SIGWINCH,
        signal.SIGCHLD,
    ):
        try:
            signal.signal(sig, signal.SIG_DFL)
        except Exception:
            pass


def _set_winsize(fd, cols, rows):
    if fd is None or fcntl is None:
        return False
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        return True
    except Exception:
        return False


class ShellSession:
    """Sesi shell interaktif.

    Utama  : PTY penuh (openpty + controlling tty) -> identik Termux:
             echo, prompt, history, tab-completion, job control (^Z/fg/bg),
             stty, tput, vim/htop, SIGWINCH, semua jalan.
    Cadangan: pipe stdin/stdout bila runtime tanpa PTY (mis. sandbox Vercel).
    """

    def __init__(self, cols=DEFAULT_COLS, rows=DEFAULT_ROWS):
        self.id = uuid.uuid4().hex[:16]
        self.created = time.time()
        self.last_active = time.time()
        self.chunks = queue.Queue()
        self._buf_lock = threading.Lock()
        self._buffered = 0
        self._trimmed = False
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.cols = _clamp(cols, 20, 500, DEFAULT_COLS)
        self.rows = _clamp(rows, 5, 200, DEFAULT_ROWS)
        self.mode = "pipe"
        self.master_fd = None
        self.proc = None
        self._closed = False
        self.alive = True
        self.spawn_error = ""
        if PTY_AVAILABLE:
            try:
                if self._spawn_pty():
                    self.mode = "pty"
            except Exception as exc:
                self.spawn_error = f"{type(exc).__name__}: {exc}"
        if self.mode != "pty":
            try:
                self._spawn_pipe()
            except Exception as exc:
                self.alive = False
                self.spawn_error = f"{type(exc).__name__}: {exc}"
                self.chunks.put(None)
                return
        threading.Thread(target=self._reader, daemon=True).start()

    def _env(self):
        env = os.environ.copy()
        # Utamakan xterm-256color untuk dukungan penuh TUI (nano/vim/htop)
        term_val = "xterm-256color" if TERM_KNOWN else (TERM_NAME or "xterm-256color")
        env.update(
            {
                "TERM": term_val,
                "COLORTERM": "truecolor",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONUNBUFFERED": "1",
                "HOME": STORAGE_DIR if os.access(STORAGE_DIR, os.W_OK) else "/tmp",
                "HISTFILE": HIST_PATH,
                "COLUMNS": str(self.cols),
                "LINES": str(self.rows),
                "PS1": r"\[\e[1;35m\]mazval-term\[\e[0m\]:\[\e[1;32m\]\w\[\e[0m\]\$ ",
            }
        )
        return env

    def _spawn_pty(self):
        if pty is None:
            return False
        master = slave = None
        try:
            master, slave = pty.openpty()
            _set_winsize(master, self.cols, self.rows)

            def _child_setup():
                os.setsid()
                # Coba set controlling terminal, tapi jangan fatal jika gagal
                # (misalnya di environment terbatasi seperti container)
                try:
                    os.dup2(slave, 0)  # stdin = slave
                    os.dup2(slave, 1)  # stdout = slave
                    os.dup2(slave, 2)  # stderr = slave
                    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
                except OSError:
                    # Jika gagal, proses tetap jalan (readline masih handle echo/prompt)
                    pass
                _reset_child_signals()

            self.proc = subprocess.Popen(
                [BASH_BIN, "--rcfile", RC_PATH, "-i"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=STORAGE_DIR,
                env=self._env(),
                preexec_fn=_child_setup,
                close_fds=True,
                bufsize=0,
            )
            os.close(slave)
            slave = None
            flags = fcntl.fcntl(master, fcntl.F_GETFL)
            fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self.master_fd = master
            master = None
            _set_winsize(self.master_fd, self.cols, self.rows)
            return True
        except Exception:
            if self.proc is not None:
                try:
                    self.proc.kill()
                except Exception:
                    pass
                self.proc = None
            return False
        finally:
            for fd in (master, slave):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def _spawn_pipe(self):
        self.proc = subprocess.Popen(
            [BASH_BIN, "--rcfile", RC_PATH, "-i"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=STORAGE_DIR,
            env=self._env(),
            start_new_session=True,
            preexec_fn=_reset_child_signals,
            bufsize=0,
        )

    def _reader(self):
        try:
            if self.mode == "pty":
                self._read_pty()
            else:
                self._read_pipe()
            rest = self._decoder.decode(b"", True)
            if rest:
                self._push(rest)
        finally:
            self.alive = False
            if self.proc is not None:
                try:
                    self.proc.wait(timeout=5)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
            self.chunks.put(None)

    def _read_pipe(self):
        received = 0
        stream = self.proc.stdout
        while True:
            data = stream.read(65536)
            if not data:
                break
            if received < 4096:
                for pat in NOISE_PATTERNS:
                    data = pat.sub(b"", data)
                received += len(data)
            if data:
                self._push(self._decoder.decode(data))

    def _read_pty(self):
        fd = self.master_fd
        while True:
            try:
                ready, _, _ = select.select([fd], [], [], 1.0)
            except (ValueError, OSError):
                break
            if not ready:
                continue
            try:
                data = os.read(fd, 8192)
            except BlockingIOError:
                continue
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                break
            if not data:
                break
            self._push(self._decoder.decode(data))

    def _push(self, text):
        if not text:
            return
        q = self.chunks
        with self._buf_lock:
            if self._trimmed:
                text = "\r\n[OUTPUT TRIMMED - buffer penuh]\r\n" + text
                self._trimmed = False
            q.put(text)
            self._buffered += len(text)
            while self._buffered > MAX_BUFFER:
                try:
                    old = q.get_nowait()
                except queue.Empty:
                    break
                if old is None:
                    try:
                        q.put_nowait(None)
                    except queue.Full:
                        pass
                    break
                self._buffered -= len(old)
                self._trimmed = True

    def send(self, text):
        if not self.alive or self._closed:
            raise RuntimeError("shell sudah berakhir")
        data = text.encode("utf-8", "replace")
        self.last_active = time.time()
        if self.mode == "pty":
            self._write_all(data)
            return
        stdin = self.proc.stdin if self.proc else None
        if stdin is None:
            raise RuntimeError("shell sudah berakhir")
        stdin.write(data)
        stdin.flush()

    def _write_all(self, data, timeout=10.0):
        fd = self.master_fd
        if fd is None:
            raise RuntimeError("shell sudah berakhir")
        deadline = time.time() + timeout
        view = memoryview(data)
        while view:
            if time.time() > deadline:
                raise RuntimeError("shell tidak merespons input")
            try:
                written = os.write(fd, view)
            except BlockingIOError:
                try:
                    select.select([], [fd], [], 0.2)
                except (ValueError, OSError):
                    raise RuntimeError("shell sudah berakhir")
                continue
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise RuntimeError("shell sudah berakhir")
            if written <= 0:
                raise RuntimeError("shell sudah berakhir")
            view = view[written:]

    def interrupt(self):
        self.last_active = time.time()
        if not self.alive:
            return
        if self.mode == "pty":
            try:
                # Ketik ^C asli: line-discipline tty yang mengirim SIGINT,
                # sama persis dengan menekan Ctrl+C di Termux.
                self._write_all(b"\x03", timeout=2.0)
                return
            except Exception:
                pass
            try:
                fg = os.tcgetpgrp(self.master_fd)
                if fg and fg != os.getpgrp():
                    os.killpg(fg, signal.SIGINT)
                    return
            except Exception:
                pass
        try:
            os.killpg(self.proc.pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def resize(self, cols, rows):
        self.cols = _clamp(cols, 20, 500, self.cols)
        self.rows = _clamp(rows, 5, 200, self.rows)
        self.last_active = time.time()
        if self.mode != "pty" or self.master_fd is None:
            return False
        return _set_winsize(self.master_fd, self.cols, self.rows)

    def info(self):
        return {
            "id": self.id,
            "mode": self.mode,
            "term": TERM_NAME,
            "cols": self.cols,
            "rows": self.rows,
            "ephemeral": EPHEMERAL,
            "alive": self.alive,
            "age": int(time.time() - self.created),
        }

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.alive = False
        if self.proc is not None:
            try:
                os.killpg(self.proc.pid, signal.SIGHUP)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                self.proc.kill()
            except Exception:
                pass
            stdin = self.proc.stdin
            if stdin is not None:
                try:
                    stdin.close()
                except Exception:
                    pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        try:
            self.chunks.put_nowait(None)
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
    try:
        pyver = (
            subprocess.run(
                ["python3", "--version"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            or subprocess.run(
                ["python3", "--version"], capture_output=True, text=True, timeout=5
            ).stderr.strip()
        )
    except Exception as exc:
        pyver = f"ERR {type(exc).__name__}"
    modes = {"pty": 0, "pipe": 0}
    for s in SHELLS.values():
        modes[s.mode] = modes.get(s.mode, 0) + 1
    info = {
        "python_version": pyver,
        "python": shutil.which("python"),
        "python3": shutil.which("python3"),
        "bash": shutil.which("bash"),
        "bash_bin": BASH_BIN,
        "clear": shutil.which("clear"),
        "shell_mode": SHELL_MODE,
        "pty_available": PTY_AVAILABLE,
        "pty_import_error": _PTY_IMPORT_ERROR or None,
        "pty_live": _pty_works(),
        "dev_ptmx": os.path.exists("/dev/ptmx"),
        "term": TERM_NAME,
        "term_ok": TERM_KNOWN,
        "instance": INSTANCE_ID,
        "ephemeral": EPHEMERAL,
        "durable": DURABLE,
        "tmpdir": os.environ.get("TMPDIR", "/tmp"),
        "vercel_env": os.environ.get("VERCEL"),
        "cwd": os.getcwd(),
        "cwd_writable": os.access(os.getcwd(), os.W_OK),
        "tmp_writable": os.access(os.environ.get("TMPDIR", "/tmp"), os.W_OK),
        "shells_active": len(SHELLS),
        "shells_by_mode": modes,
    }
    try:
        p = subprocess.run(
            [BASH_BIN, "-c", "echo BASH_PIPE_OK"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        info["bash_pipe"] = p.stdout.strip()
    except Exception as e:
        info["bash_pipe"] = f"ERR {type(e).__name__}: {e}"
    return info


async def _body(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


@app.post("/api/shell")
async def shell_open(request: Request):
    body = await _body(request)
    cols = _clamp(body.get("cols"), 20, 500, DEFAULT_COLS)
    rows = _clamp(body.get("rows"), 5, 200, DEFAULT_ROWS)
    prune_shells()
    with SHELLS_LOCK:
        s = ShellSession(cols, rows)
        SHELLS[s.id] = s
    out = s.info()
    out["spawn_error"] = s.spawn_error or None
    return out


@app.get("/api/shell/{sid}")
def shell_ping(sid: str):
    s = SHELLS.get(sid)
    if s is None:
        return JSONResponse({"error": "session_expired"}, status_code=404)
    return s.info()


@app.get("/api/shell/{sid}/ping")
def shell_ping_alias(sid: str):
    return shell_ping(sid)


@app.post("/api/shell/{sid}/resize")
async def shell_resize(sid: str, request: Request):
    s = SHELLS.get(sid)
    if s is None:
        return JSONResponse({"error": "session_expired"}, status_code=404)
    body = await _body(request)
    resized = s.resize(body.get("cols", s.cols), body.get("rows", s.rows))
    return {
        "ok": True,
        "resized": resized,
        "mode": s.mode,
        "cols": s.cols,
        "rows": s.rows,
    }


@app.get("/api/shell/{sid}/stream")
def shell_stream(sid: str):
    s = SHELLS.get(sid)
    if s is None:
        return JSONResponse({"error": "session_expired"}, status_code=404)

    deadline = time.time() + STREAM_MAX_SECONDS

    def gen():
        ended_reason = None
        while True:
            s.last_active = time.time()
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
            # Kumpulkan burst output dalam 1 pesan SSE, supaya output ratusan
            # ribu baris tidak berubah jadi ribuan request kecil (lambat/lag).
            buf = [item]
            total = len(item)
            got_end = False
            while total < SSE_BATCH_BYTES and len(buf) < SSE_BATCH_CHUNKS:
                try:
                    nxt = s.chunks.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    got_end = True
                    break
                buf.append(nxt)
                total += len(nxt)
            s.last_active = time.time()
            yield f"data: {json.dumps({'o': ''.join(buf)})}\n\n"
            if got_end:
                break
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
    body = await _body(request)
    data = body.get("data", "")
    if not isinstance(data, str):
        data = "" if data is None else str(data)
    encoded = body.get("b64")
    if isinstance(encoded, str) and encoded:
        try:
            pad = "=" * (-len(encoded) % 4)
            data = base64.b64decode(encoded + pad).decode("utf-8", "replace")
        except Exception:
            return JSONResponse({"error": "b64 tidak valid"}, status_code=400)
    if not data:
        return {"ok": True, "mode": s.mode}
    if len(data) > MAX_INPUT:
        data = data[:MAX_INPUT]
    if not s.alive:
        return JSONResponse({"error": "session_expired"}, status_code=404)
    try:
        # Offload: menulis ke PTY/pipe bisa memblokir saat buffer input penuh,
        # jangan pernah memblokir event loop (semua request lain ikut nge-lag).
        await run_in_threadpool(s.send, data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True, "mode": s.mode}


@app.post("/api/shell/{sid}/interrupt")
def shell_interrupt(sid: str):
    s = SHELLS.get(sid)
    if s is None:
        return JSONResponse({"error": "session_expired"}, status_code=404)
    if not s.alive:
        return JSONResponse({"error": "session_expired"}, status_code=404)
    s.interrupt()
    return {"ok": True, "mode": s.mode}


@app.delete("/api/shell/{sid}")
def shell_close(sid: str):
    s = SHELLS.pop(sid, None)
    if s is not None:
        s.close()
    return {"ok": True}


def _make_runnable(path):
    """Buat file .py/.sh bisa langsung dieksekusi: auto shebang + exec bit."""
    tmp_path = None
    try:
        if not os.path.isfile(path):
            return False
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            has_shebang = f.read(2) == b"#!"
        if not has_shebang and ext in (".py", ".sh", ".bash"):
            if ext == ".py":
                prog = shutil.which("python3") or shutil.which("python")
            else:
                prog = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
            if prog:
                tmp_path = path + ".run_tmp"
                with open(path, "rb") as src_f, open(tmp_path, "wb") as dst:
                    dst.write(b"#!" + prog.encode("utf-8") + b"\n")
                    shutil.copyfileobj(src_f, dst, 1048576)
                os.replace(tmp_path, path)
                tmp_path = None
                has_shebang = True
        if has_shebang:
            os.chmod(path, os.stat(path).st_mode | 0o111)
            return True
        return False
    except Exception:
        return False
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@app.post("/upload")
async def upload(request: Request):
    raw = request.headers.get("X-Filename", "upload.bin")
    name = os.path.basename(urllib.parse.unquote(raw)).strip() or "upload.bin"
    path = os.path.join(STORAGE_DIR, name)
    append = request.headers.get("X-Append") == "1"
    if append and not os.path.exists(path):
        async for _ in request.stream():
            pass
        return {"status": "retry", "instance": INSTANCE_ID, "reason": "chunk_awal_di_instance_lain"}
    mode = "ab" if append else "wb"
    size = 0
    try:
        with open(path, mode) as f:
            async for chunk in request.stream():
                f.write(chunk)
                size += len(chunk)
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": str(e)}, status_code=500
        )
    total = os.path.getsize(path)
    try:
        expect = int(request.headers.get("X-File-Size", "-1"))
    except ValueError:
        expect = -1
    final = (not append) or (expect >= 0 and total >= expect)
    if final:
        await run_in_threadpool(_make_runnable, path)
    pushed = None
    if DURABLE:
        if expect >= 0 and total == expect:
            try:
                pushed = await run_in_threadpool(durable_push, name, path, total)
            except Exception:
                pushed = False
    resp = {
        "status": "success",
        "name": name,
        "size": size,
        "total_size": total,
        "instance": INSTANCE_ID,
    }
    if pushed is not None:
        resp["durable"] = bool(pushed)
    return resp


@app.get("/api/files")
def api_files():
    items = []
    used = 0
    durable = {}
    if DURABLE:
        try:
            durable = durable_scan()
        except Exception:
            durable = {}
    for name, info in sorted(durable.items(), key=lambda kv: kv[1]["created"], reverse=True):
        items.append(
            {
                "name": name,
                "size_formatted": fmt_size(info["size"]),
                "size_bytes": info["size"],
                "durable": True,
            }
        )
        used += info["size"]
    try:
        entries = sorted(
            os.listdir(STORAGE_DIR),
            key=lambda x: os.path.getctime(os.path.join(STORAGE_DIR, x)),
            reverse=True,
        )
        for fn in entries:
            if fn in durable:
                continue
            fp = os.path.join(STORAGE_DIR, fn)
            if not os.path.isfile(fp):
                continue
            sz = os.path.getsize(fp)
            used += sz
            items.append(
                {
                    "name": fn,
                    "size_formatted": fmt_size(sz),
                    "size_bytes": sz,
                    "durable": False,
                }
            )
    except Exception:
        pass
    du = shutil.disk_usage(STORAGE_DIR)
    virtual_total = 400.0 * (1024**4)
    percent = round((used / virtual_total) * 100, 4)
    if percent == 0 and used > 0:
        percent = 0.01
    return {
        "files": items,
        "storage": {
            "path": STORAGE_DIR,
            "used_formatted": fmt_size(used),
            "total_formatted": "400.0 TB",
            "free_formatted": fmt_size(du.free),
            "percent": percent,
            "ephemeral": EPHEMERAL,
            "persistent": DURABLE,
            "label": (
                "NEXUS CLOUD STORAGE (400 TB POOL)"
                if EPHEMERAL
                else "INTERNAL SDCARD STORAGE (400 TB POOL)"
            ),
        },
    }


@app.delete("/api/files/{name}")
def delete_file(name: str):
    name = os.path.basename(urllib.parse.unquote(name))
    path = os.path.join(STORAGE_DIR, name)
    removed = False
    if os.path.isfile(path):
        os.remove(path)
        removed = True
    if DURABLE and durable_delete(name):
        removed = True
    if removed:
        return {"status": "success"}
    return JSONResponse({"status": "error", "message": "File not found"}, status_code=404)

@app.get("/files/{name}")
def get_file(name: str, request: Request):
    name = os.path.basename(urllib.parse.unquote(name))
    path = os.path.join(STORAGE_DIR, name)

    if os.path.isfile(path):
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"

        def read_window(start, end):
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                    yield chunk

    else:
        info = durable_get(name)
        if not info:
            return JSONResponse({"error": "File not found"}, status_code=404)
        size = info["size"]
        parts = info["parts"]
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"

        def read_window(start, end):
            if size == 0 or start > end:
                return
            off = 0
            for part in parts:
                psize = part["size"]
                lo, hi = off, off + psize - 1
                off += psize
                if hi < start or lo > end:
                    continue
                pfile = _part_path(part["sha"])
                if not pfile:
                    raise RuntimeError("gagal mengambil bagian file dari penyimpanan")
                s = max(start, lo) - lo
                e = min(end, hi) - lo
                with open(pfile, "rb") as f:
                    f.seek(s)
                    left = e - s + 1
                    while left > 0:
                        chunk = f.read(min(65536, left))
                        if not chunk:
                            break
                        left -= len(chunk)
                        yield chunk

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
                yield from read_window(start, end)

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
        yield from read_window(0, size - 1)

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
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-web-links@0.8.0/lib/xterm-addon-web-links.min.js"></script>
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
        <button class="tab-btn" id="btn-tab-storage" onclick="switchTab('storage'); updateStorageInfo();">Storage</button>
        <button class="tab-btn" id="btn-tab-gallery" onclick="switchTab('gallery'); loadGallery();">Gallery</button>
    </div>

    <div id="tab-terminal" class="content-area active">
        <div class="terminal-wrapper">
            <div class="terminal-window">
                <div class="window-header">
                    <div class="dot"></div><div class="dot"></div><div class="dot"></div>
                    <span id="kernelLabel" style="margin-left: 5px; font-size: 0.75rem; font-weight: 900; color: #000;">KERNEL SYNC...</span>
                </div>
                <div id="terminal-container"></div>
                <div id="mobile-bar">
                    <button class="brutal-btn" onclick="sendSpecial('\\x1b')">ESC</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\x03')">^C</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\t')">TAB</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\x01')">^A</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\x05')">^E</button>
                    <button class="brutal-btn" onclick="sendSpecial('|')">|</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\x1b[A')">▲</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\x1b[B')">▼</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\x1b[D')">◀</button>
                    <button class="brutal-btn" onclick="sendSpecial('\\x1b[C')">▶</button>
                    <button class="brutal-btn btn-primary-color" onclick="sendCommand('python3\\r')">PY</button>
                    <button class="brutal-btn" onclick="sendCommand('ls -la\\r')">LS</button>
                    <button class="brutal-btn btn-danger-color" onclick="sendCommand('clear\\r')">CLR</button>
                </div>
            </div>
        </div>
    </div>

    <div id="tab-storage" class="content-area">
        <div class="box-container">

            <div class="brutal-card">
                <div class="storage-box-label" id="storageBoxLabel">📁 STORAGE</div>
                <div class="storage-header">
                    <span>Kapasitas Terpakai (Pool 400 TB)</span>
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
                    <span id="storageMiniLabel">STORAGE POOL (400 TB)</span>
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
            cursorBlink: true,
            cursorStyle: 'block',
            theme: {{
                background: '#000000',
                foreground: '#00ff66',
                cursor: '#ff0055',
                cursorAccent: '#000000',
                selectionBackground: 'rgba(0, 255, 102, 0.3)',
                black: '#000000',
                red: '#ff5555',
                green: '#55ff55',
                yellow: '#ffff55',
                blue: '#5555ff',
                magenta: '#ff55ff',
                cyan: '#55ffff',
                white: '#bbbbbb',
                brightBlack: '#555555',
                brightRed: '#ff6b6b',
                brightGreen: '#6bff6b',
                brightYellow: '#ffff6b',
                brightBlue: '#6b6bff',
                brightMagenta: '#ff6bff',
                brightCyan: '#6bffff',
                brightWhite: '#ffffff'
            }},
            fontFamily: 'Menlo, Monaco, "DejaVu Sans Mono", "Courier New", monospace',
            fontSize: 13,
            lineHeight: 1.15,
            scrollback: 10000
        }});
        const fitAddon = new FitAddon.FitAddon();
        term.loadAddon(fitAddon);
        if (typeof WebLinksAddon !== 'undefined') {{
            term.loadAddon(new WebLinksAddon.WebLinksAddon());
        }}
        const termContainer = document.getElementById('terminal-container');
        term.open(termContainer);
        fitAddon.fit();

        let fitRaf = 0;
        function scheduleFit() {{
            if (fitRaf) return;
            fitRaf = requestAnimationFrame(() => {{ fitRaf = 0; try {{ fitAddon.fit(); }} catch (e) {{}} }});
        }}
        window.addEventListener('resize', scheduleFit);
        if (window.visualViewport) {{
            const vv = window.visualViewport;
            const syncViewport = () => {{
                const h = Math.round(vv.height);
                // Keyboard HP terbuka: viewport mengecil -> kunci tinggi body
                // ke viewport terlihat supaya terminal ikut rapi (bukan tertutup).
                document.body.style.height = (h > 200 && Math.abs(h - window.innerHeight) > 80) ? (h + 'px') : '';
                scheduleFit();
            }};
            vv.addEventListener('resize', syncViewport);
            vv.addEventListener('scroll', syncViewport);
        }}

        // Render output lewat antrean requestAnimationFrame: burst output
        // ratusan ribu baris ditelan per frame + ada batas, UI tidak macet.
        let outBuf = '', outLen = 0, outRaf = 0;
        const OUT_MAX = 2 * 1048576;
        function writeOut(s) {{
            if (!s) return;
            outBuf += s; outLen += s.length;
            if (outLen > OUT_MAX) {{
                outBuf = '\\r\\n[OUTPUT DILEWAT - buffer penuh]\\r\\n';
                outLen = outBuf.length;
            }}
            if (!outRaf) outRaf = requestAnimationFrame(flushOut);
        }}
        function flushOut() {{
            outRaf = 0;
            const chunk = outBuf;
            outBuf = ''; outLen = 0;
            if (chunk) term.write(chunk);
        }}

        let shellId = null;
        let shellMode = 'pipe';
        let evSource = null;
        let inputBuf = '';
        let flushTimer = null;
        let opening = false;
        let resizeTimer = null;

        function setStatus(txt, online) {{
            statusEl.innerText = txt;
            statusEl.className = 'status' + (online ? ' online' : '');
        }}

        function setKernel(mode) {{
            const el = document.getElementById('kernelLabel');
            if (!el) return;
            el.innerText = (mode === 'pty')
                ? 'PTY KERNEL - TERMUX MODE PENUH (tty / jobs / stty / vim)'
                : 'PIPE SHELL KERNEL (runtime tanpa PTY)';
        }}

        function sendResize() {{
            if (!shellId) return;
            fetch('/api/shell/' + shellId + '/resize', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ cols: term.cols || 120, rows: term.rows || 40 }})
            }}).catch(() => {{}});
        }}

        term.onResize(() => {{
            if (!shellId) return;
            if (resizeTimer) clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {{ resizeTimer = null; sendResize(); }}, 150);
        }});

        async function openShell() {{
            if (opening || shellId) return;
            opening = true;
            try {{
                const res = await fetch('/api/shell', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ cols: term.cols || 120, rows: term.rows || 40 }})
                }});
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                if (!data.id) throw new Error('no session id');
                shellId = data.id;
                shellMode = data.mode || 'pipe';
                setKernel(shellMode);
                connectStream();
                setStatus('ONLINE', true);
                flushInput();
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
                    if (d.o) writeOut(d.o);
                    if (d.end) {{
                        evSource.close(); evSource = null;
                        const keepSid = shellId;
                        shellId = null;
                        const restart = () => {{
                            writeOut('\\r\\n[SESI TUTUP] membuka shell baru...\\r\\n');
                            setStatus('SYNC...', false);
                            setTimeout(openShell, 500);
                        }};
                        if (!keepSid) {{ restart(); return; }}
                        fetch('/api/shell/' + keepSid)
                            .then(r => r.status === 200 ? r.json() : null)
                            .then(j => {{
                                if (j && j.alive) {{
                                    shellId = keepSid;
                                    shellMode = j.mode || shellMode;
                                    setKernel(shellMode);
                                    writeOut('\\r\\n[STREAM] menyambung ulang...\\r\\n');
                                    connectStream();
                                    sendResize();
                                }} else {{
                                    restart();
                                }}
                            }})
                            .catch(restart);
                    }}
                }} catch (e) {{}}
            }};
            evSource.onerror = async () => {{
                if (!shellId) return;
                try {{
                    const r = await fetch('/api/shell/' + shellId);
                    if (r.status === 404) {{
                        if (evSource) {{ evSource.close(); evSource = null; }}
                        shellId = null;
                        writeOut('\\r\\n[SESSION HILANG] instance berganti, membuka shell baru...\\r\\n');
                        setStatus('SYNC...', false);
                        openShell();
                    }}
                }} catch (e) {{}}
            }};
        }}

        function sleep(ms) {{ return new Promise(res => setTimeout(res, ms)); }}

        async function postInput(payload, attempt) {{
            if (!shellId) return;
            try {{
                const r = await fetch('/api/shell/' + shellId + '/input', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ data: payload }})
                }});
                if (r.status === 404) {{
                    if (attempt === 0) {{
                        await sleep(600);
                        return await postInput(payload, 1);
                    }}
                    if (evSource) {{ evSource.close(); evSource = null; }}
                    shellId = null;
                    writeOut('\\r\\n[SESSION HILANG] membuka shell baru...\\r\\n');
                    setStatus('SYNC...', false);
                    openShell();
                    return;
                }}
                setStatus('ONLINE', true);
            }} catch (e) {{
                setStatus('RETRY...', false);
            }}
        }}

        // Pengiriman input di-serial lewat rantai promise: urutan ketikan
        // tidak pernah tertukar walau ada dua flush hampir bersamaan.
        let sendChain = Promise.resolve();

        function postAll(payload) {{
            const SLICE = 4000;
            return (async () => {{
                for (let i = 0; i < payload.length; i += SLICE) {{
                    await postInput(payload.slice(i, i + SLICE), 0);
                    if (!shellId) break;
                }}
            }})();
        }}

        function flushInput() {{
            if (flushTimer) {{ clearTimeout(flushTimer); flushTimer = null; }}
            if (!shellId || !inputBuf) return;
            const payload = inputBuf;
            inputBuf = '';
            sendChain = sendChain.then(() => postAll(payload), () => postAll(payload));
        }}

        function queueInput(data) {{
            inputBuf += data;
            if (inputBuf.length > 262144) inputBuf = inputBuf.slice(-262144);
            // 0ms = kumpulkan event yang datang di frame yang sama saja,
            // tanpa jeda 50ms yang membuat ketikan terasa lambat.
            if (!flushTimer) flushTimer = setTimeout(flushInput, 0);
        }}

        function sendInterrupt() {{
            if (!shellId) {{ openShell(); return; }}
            if (shellMode === 'pty') {{
                queueInput('\\x03');
                flushInput();
                return;
            }}
            writeOut('^C\\r\\n');
            fetch('/api/shell/' + shellId + '/interrupt', {{ method: 'POST' }}).catch(() => {{}});
        }}

        term.onData((data) => {{
            if (data === '\\x03') {{ sendInterrupt(); return; }}
            if (data === '\\x0c') {{
                // Ctrl+L: diteruskan ke shell (seperti terminal asli) + bersihkan
                // layar lokal supaya prompt ikut digambar ulang.
                queueInput(data);
                flushInput();
                flushOut(); term.clear();
                return;
            }}
            queueInput(data);
            if (data.includes('\\r') || data.includes('\\n')) flushInput();
            if (!shellId) openShell();
        }});

        function sendCommand(cmd) {{ queueInput(cmd); flushInput(); if (!shellId) openShell(); term.focus(); }}
        function sendSpecial(key) {{
            if (key === '\\x03') {{ sendInterrupt(); term.focus(); return; }}
            queueInput(key); flushInput();
            if (!shellId) openShell();
            term.focus();
        }}

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
            if (mini) mini.innerText = "STORAGE POOL (400 TB)" + (storage.persistent ? " • PERMANEN" : "");
            const path = document.getElementById('storagePathText');
            if (path) path.innerText = "PATH: " + storage.path + (storage.persistent ? "  •  TERSIMPAN PERMANEN DI CLOUD" : "");
        }}

        async function updateStorageInfo() {{
            try {{
                const res = await fetch('/api/files');
                const data = await res.json();
                applyStorageUI(data.storage);
            }} catch (e) {{ console.log("Storage sync error"); }}
        }}

        const CHUNK_SIZE = 4 * 1048576;
        const MAX_FILE_ATTEMPTS = 3;
        const MAX_CHUNK_ATTEMPTS = 8;

        function sendChunk(file, index, totalChunks, pinned, onProgress) {{
            return new Promise((resolve, reject) => {{
                const start = index * CHUNK_SIZE;
                const blob = file.slice(start, Math.min(file.size, start + CHUNK_SIZE));
                const xhr = new XMLHttpRequest();
                xhr.open('POST', '/upload', true);
                xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
                xhr.setRequestHeader('X-Chunk', String(index));
                xhr.setRequestHeader('X-Chunk-Total', String(totalChunks));
                xhr.setRequestHeader('X-File-Size', String(file.size));
                if (index > 0) xhr.setRequestHeader('X-Append', '1');
                xhr.upload.onprogress = (e) => {{
                    if (e.lengthComputable && onProgress) onProgress(start + e.loaded);
                }};
                xhr.onload = () => {{
                    if (xhr.status === 200) {{
                        try {{ resolve(JSON.parse(xhr.responseText) || {{}}); }}
                        catch (err) {{ resolve({{}}); }}
                    }} else reject(new Error('HTTP ' + xhr.status));
                }};
                xhr.onerror = () => reject(new Error('koneksi terputus'));
                xhr.send(blob);
            }});
        }}

        async function uploadOneFile(file, fileLabel, log) {{
            const totalChunks = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
            let lastError = null;
            for (let fileAttempt = 1; fileAttempt <= MAX_FILE_ATTEMPTS; fileAttempt++) {{
                try {{
                    let pinned = null;
                    for (let i = 0; i < totalChunks; i++) {{
                        let chunkAttempt = 0;
                        let advance = false;
                        while (!advance && chunkAttempt < MAX_CHUNK_ATTEMPTS) {{
                            chunkAttempt++;
                            const res = await sendChunk(file, i, totalChunks, pinned, (sent) => {{
                                const s = Math.min(file.size, sent);
                                const pct = Math.round((s / file.size) * 100);
                                log.innerText = `[UPLOADING] ${{fileLabel}} ${{pct}}% (${{(s / 1048576).toFixed(2)}} MB / ${{(file.size / 1048576).toFixed(2)}} MB) - bagian ${{i + 1}}/${{totalChunks}}\n[STREAM] 0% RAM HP`;
                            }});
                            if (!res || res.status === 'error') {{
                                throw new Error((res && res.message) || 'gagal mengirim bagian');
                            }}
                            if (pinned === null) {{
                                pinned = res.instance || null;
                                advance = true;
                            }} else if (res.status === 'retry' || (res.instance && pinned && res.instance !== pinned)) {{
                                await sleep(300);
                            }} else {{
                                advance = true;
                            }}
                        }}
                        if (!advance) throw new Error('storage instance tidak sinkron');
                    }}
                    let verified = false;
                    for (let v = 0; v < 3 && !verified; v++) {{
                        if (v > 0) await sleep(1500);
                        const check = await fetch('/api/files').then(r => r.json()).catch(() => null);
                        const entry = check && check.files ? check.files.find(f => f.name === file.name) : null;
                        const persistent = !!(check && check.storage && check.storage.persistent);
                        verified = !!(entry && entry.size_bytes === file.size
                            && (!persistent || entry.durable === true));
                    }}
                    if (verified) return true;
                    throw new Error('verifikasi penyimpanan permanen gagal');
                }} catch (e) {{
                    lastError = e;
                    if (fileAttempt < MAX_FILE_ATTEMPTS) {{
                        log.innerText = `[RETRY] ${{fileLabel}} percobaan ${{fileAttempt + 1}}/${{MAX_FILE_ATTEMPTS}}...`;
                        await sleep(700);
                    }}
                }}
            }}
            throw lastError || new Error('gagal');
        }}

        async function uploadFile() {{
            const fileInput = document.getElementById('fileInput');
            const log = document.getElementById('progressLog');
            if (fileInput.files.length === 0) return alert("Pilih file terlebih dahulu!");

            const files = Array.from(fileInput.files);
            log.innerText = `[PREP] Menyiapkan ${{files.length}} file...`;
            const failed = [];

            for (let fi = 0; fi < files.length; fi++) {{
                const file = files[fi];
                try {{
                    await uploadOneFile(file, `[${{fi + 1}}/${{files.length}}] ${{file.name}}`, log);
                }} catch (e) {{
                    failed.push(file.name + ' (' + ((e && e.message) || e) + ')');
                }}
            }}

            if (failed.length === 0) {{
                const mb = (files.reduce((a, f) => a + f.size, 0) / 1048576).toFixed(2);
                log.innerText = `[UPLOADING] 100% (${{mb}} MB total)\n[SUCCESS] File TERSIMPAN PERMANEN! Jalankan: 'run file.py' / 'python file.py' / './file.py' di terminal, atau buka tab Gallery.`;
                fileInput.value = "";
                document.getElementById('fileNameDisplay').innerText = "Pilih file ke brankas...";
                setTimeout(() => {{ switchTab('gallery'); loadGallery(); }}, 800);
            }} else {{
                log.innerText += `\n\n[ERROR] Gagal: ${{failed.join(', ')}}`;
            }}
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
