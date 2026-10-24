#!/usr/bin/env python3
"""Smoke test terminal-Mazval.

Cocok untuk mode PTY (lokal/Termux) maupun PIPE (runtime tanpa PTY / Vercel).

    python3 tests/smoke_test.py http://127.0.0.1:8091
    python3 tests/smoke_test.py https://terminal-mazval.vercel.app

Keluar dengan kode 0 bila semua pemeriksaan lulus.
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8091").rstrip("/")
RESULTS = []


def req(method, path, payload=None, timeout=30, raw=False):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        BASE + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read()
            if raw:
                return resp.status, body, dict(resp.headers)
            try:
                return resp.status, json.loads(body or b"{}"), {}
            except ValueError:
                return resp.status, body.decode("utf-8", "replace"), {}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        if raw:
            return exc.code, body, dict(exc.headers)
        try:
            return exc.code, json.loads(body or b"{}"), {}
        except ValueError:
            return exc.code, body.decode("utf-8", "replace"), {}
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}", {}


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail and not ok:
        line += f" -> {detail}"
    print(line, flush=True)
    return bool(ok)


class Stream(threading.Thread):
    def __init__(self, sid):
        super().__init__(daemon=True)
        self.sid = sid
        self.chunks = []
        self.error = ""
        self.ended = False
        self.stop = False

    def run(self):
        try:
            request = urllib.request.Request(f"{BASE}/api/shell/{self.sid}/stream")
            with urllib.request.urlopen(request, timeout=300) as resp:
                for raw in resp:
                    if self.stop:
                        break
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    if not line.startswith("data: "):
                        continue
                    try:
                        msg = json.loads(line[6:])
                    except ValueError:
                        continue
                    if "o" in msg:
                        self.chunks.append(msg["o"])
                    if msg.get("end"):
                        self.ended = True
                        break
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    def text(self):
        return "".join(self.chunks)

    def offset(self):
        return len(self.chunks)


class Session:
    def __init__(self, info):
        self.info = info
        self.sid = info["id"]
        self.mode = info.get("mode", "pipe")
        self.stream = Stream(self.sid)
        self.stream.start()
        time.sleep(1.2)

    def send(self, data):
        status, body, _ = req(
            "POST", f"/api/shell/{self.sid}/input", {"data": data}, timeout=30
        )
        return status, body

    def cmd(self, command, wait=1.2):
        mark = self.stream.offset()
        if not command.endswith(("\r", "\n")):
            command += "\r"
        self.send(command)
        time.sleep(wait)
        return "".join(
            chunk for chunk in self.stream.chunks[mark:] if chunk is not None
        )

    def wait_text(self, needle, timeout=8.0, from_offset=0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            tail = "".join(self.stream.chunks[from_offset:])
            if needle in tail:
                return True, tail
            time.sleep(0.2)
        return False, "".join(self.stream.chunks[from_offset:])

    def close(self):
        self.stream.stop = True
        req("DELETE", f"/api/shell/{self.sid}")


def open_session(cols=132, rows=43):
    status, body, _ = req("POST", "/api/shell", {"cols": cols, "rows": rows})
    if status != 200 or not isinstance(body, dict) or not body.get("id"):
        return None, f"HTTP {status}: {body}"
    return Session(body), ""


def test_static():
    status, body, _ = req("GET", "/")
    html = str(body)
    check("GET / -> 200 + UI", status == 200 and "STORAGE TERMINAL" in html)
    check(
        "UI memuat logika mode PTY + resize",
        "kernelLabel" in html and "/resize" in html,
        "kernelLabel/resize tidak ada di HTML",
    )

    status, probe, _ = req("GET", "/api/probe")
    ok = status == 200 and isinstance(probe, dict)
    check("GET /api/probe -> 200", ok, str(body if not ok else ""))
    if not ok:
        return None
    check(
        "probe: mode shell valid",
        probe.get("shell_mode") in ("pty", "pipe"),
        str(probe.get("shell_mode")),
    )
    check(
        "probe: bash jalan",
        probe.get("bash_pipe") == "BASH_PIPE_OK",
        str(probe.get("bash_pipe")),
    )
    check(
        "probe: term/terminfo",
        bool(probe.get("term")) and probe.get("term_ok") is not None,
        str(probe.get("term")),
    )
    return probe


def test_shell(probe):
    session, err = open_session()
    if not session:
        check("buka sesi shell", False, err)
        return
    mode = session.mode
    check("buka sesi shell", True, f"mode={mode}")

    status, ping, _ = req("GET", f"/api/shell/{session.sid}")
    check("GET /api/shell/{id} (ping)", status == 200 and ping.get("alive") is True)
    status, ping2, _ = req("GET", f"/api/shell/{session.sid}/ping")
    check("GET /api/shell/{id}/ping (alias)", status == 200)

    banner_ok, banner_txt = session.wait_text("STORAGE TERMINAL KERNEL ACTIVE", 10)
    check("banner shell tampil", banner_ok, banner_txt[-200:])

    mark = session.stream.offset()
    out = session.cmd("echo MARK_$((6*7))", wait=1.5)
    check("eksekusi perintah (echo $((6*7)))", "MARK_42" in out, out[-200:])

    mark = session.stream.offset()
    out = session.cmd("tty", wait=1.5)
    if mode == "pty":
        check("mode PTY: tty -> /dev/pts", "/dev/pts" in out, out[-200:])
        mark = session.stream.offset()
        out = session.cmd("stty size", wait=1.5)
        check(
            "mode PTY: stty size sesuai buka sesi",
            out.strip().splitlines()[-1:] == ["43 132"] or "43 132" in out,
            out[-200:],
        )
    else:
        check("mode PIPE: tty -> not a tty", "not a tty" in out, out[-200:])

    status, body, _ = req(
        "POST", f"/api/shell/{session.sid}/resize", {"cols": 100, "rows": 30}
    )
    resized_ok = status == 200 and body.get("ok")
    check("endpoint resize", bool(resized_ok), str(body))
    if mode == "pty":
        time.sleep(0.3)
        out = session.cmd("stty size", wait=1.5)
        check("resize diterima shell (stty size)", "30 100" in out, out[-200:])
        mark = session.stream.offset()
        session.send(
            "python3 -c \"import signal,sys,time;"
            "signal.signal(signal.SIGWINCH, lambda *a: "
            "(print('WINCH_OK', flush=True), sys.exit(0)));"
            "time.sleep(6)\"\r"
        )
        time.sleep(1.5)
        req(
            "POST",
            f"/api/shell/{session.sid}/resize",
            {"cols": 111, "rows": 33},
        )
        ok, tail = session.wait_text("WINCH_OK", 7.0, mark)
        check("SIGWINCH terkirim ke proses depan", ok, tail[-300:])
        req("POST", f"/api/shell/{session.sid}/interrupt")
        time.sleep(0.6)
    else:
        check("resize diabaikan di mode PIPE", body.get("resized") is False, str(body))

    mark = session.stream.offset()
    session.send("sleep 30\r")
    time.sleep(1.0)
    req("POST", f"/api/shell/{session.sid}/interrupt")
    time.sleep(0.8)
    mark = session.stream.offset()
    out = session.cmd("echo AFTER_$((21*2))", wait=1.5)
    check("interrupt menghentikan proses", "AFTER_42" in out, out[-300:])

    mark = session.stream.offset()
    session.send("ech\t\t")
    time.sleep(1.8)
    tail = "".join(session.stream.chunks[mark:])
    check("tab-completion readline menghasilkan 'echo'", "echo" in tail, tail[-200:])
    session.send("\x15")  # Ctrl+U: bersihkan baris yang masih menggantung
    time.sleep(0.4)

    mark = session.stream.offset()
    out = session.cmd("printf 'OK-\\U0001F680\\n'", wait=1.5)
    check("output UTF-8 utuh (emoji)", "OK-\U0001F680" in out, out[-200:])

    if mode == "pty":
        mark = session.stream.offset()
        session.send("bash -c 'while true; do :; done'\r")
        time.sleep(1.0)
        req("POST", f"/api/shell/{session.sid}/interrupt")
        time.sleep(1.0)
        mark = session.stream.offset()
        out = session.cmd("echo FG_$((7*6))", wait=2.0)
        check("interrupt proses CPU-bound", "FG_42" in out, out[-300:])
    else:
        check("skip uji proses CPU-bound (mode PIPE)", True)

    mark = session.stream.offset()
    session.send("exit\r")
    ok, tail = session.wait_text("STORAGE_TERMINAL_SHELL_EXITED", 0.1, mark)
    time.sleep(1.0)
    status, ping, _ = req("GET", f"/api/shell/{session.sid}")
    check(
        "shell exit terdeteksi (alive=false / 404)",
        status == 404 or ping.get("alive") is False,
        f"HTTP {status} {ping}",
    )
    session.close()


def test_edge():
    status, body, _ = req("GET", "/api/shell/tidak-ada")
    check("ping sesi tak dikenal -> 404", status == 404, str(body))
    status, body, _ = req("POST", "/api/shell/tidak-ada/input", {"data": "x"})
    check("input sesi tak dikenal -> 404", status == 404, str(body))
    status, body, _ = req("POST", "/api/shell/tidak-ada/resize", {"cols": 80, "rows": 24})
    check("resize sesi tak dikenal -> 404", status == 404, str(body))
    status, body, _ = req("GET", "/api/shell/tidak-ada/stream")
    check("stream sesi tak dikenal -> 404", status == 404, str(body))
    status, body, _ = req("DELETE", "/api/shell/tidak-ada")
    check("hapus sesi tak dikenal -> 200", status == 200, str(body))
    status, body, _ = req("GET", "/files/tidak-ada.bin")
    check("file tak dikenal -> 404", status == 404, str(body))

    sessions = []
    errs = []
    for _ in range(3):
        s, err = open_session(cols=80, rows=24)
        if s:
            sessions.append(s)
        else:
            errs.append(err)
    check("3 sesi paralel terbuka", len(sessions) == 3, str(errs))
    alive = 0
    for s in sessions:
        status, ping, _ = req("GET", f"/api/shell/{s.sid}")
        if status == 200 and ping.get("alive"):
            alive += 1
    check("3 sesi paralel hidup", alive == 3, f"alive={alive}")
    for s in sessions:
        out = s.cmd("echo PAR_$((100+1))", wait=1.2)
        check(f"sesi {s.sid[:6]} merespons", "PAR_101" in out, out[-200:])
        s.close()


def test_storage():
    name = "smoke_test.txt"
    content = "terminal-mazval smoke test\n"
    data = content.encode("utf-8")
    request = urllib.request.Request(
        BASE + "/upload",
        data=data,
        method="POST",
        headers={
            "X-Filename": name,
            "X-File-Size": str(len(data)),
            "Content-Type": "application/octet-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            status, body = resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        status, body = 0, str(exc)
    check("upload file", status == 200 and body.get("status") == "success", str(body))

    status, listing, _ = req("GET", "/api/files")
    names = [f.get("name") for f in listing.get("files", [])] if status == 200 else []
    check("list file memuat hasil upload", name in names, str(names)[:200])

    status, body, headers = req("GET", f"/files/{name}", raw=True)
    check(
        "download file utuh",
        status == 200 and body == data,
        f"HTTP {status} {len(body)} vs {len(data)}",
    )

    status, body, headers = req(
        "GET", f"/files/{name}", raw=True, timeout=30
    )
    part_request = urllib.request.Request(
        BASE + f"/files/{name}", headers={"Range": "bytes=0-6"}
    )
    try:
        with urllib.request.urlopen(part_request, timeout=30) as resp:
            status, body = resp.status, resp.read()
            content_range = resp.headers.get("Content-Range", "")
    except urllib.error.HTTPError as exc:
        status, body, content_range = exc.code, b"", ""
    check(
        "download range (206)",
        status == 206 and body == data[:7] and content_range.startswith("bytes 0-6/"),
        f"HTTP {status} {content_range} {body!r}",
    )

    status, body, _ = req("DELETE", f"/api/files/{name}")
    check("hapus file", status == 200 and body.get("status") == "success", str(body))
    status, _, _ = req("GET", f"/files/{name}")
    check("file hilang setelah dihapus", status == 404, f"HTTP {status}")


def main():
    print(f"=== smoke test: {BASE}", flush=True)
    probe = test_static()
    if probe:
        test_shell(probe)
    test_edge()
    test_storage()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== {len(RESULTS) - len(failed)}/{len(RESULTS)} pemeriksaan lulus")
    for name, _, detail in failed:
        print(f"  GAGAL: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
