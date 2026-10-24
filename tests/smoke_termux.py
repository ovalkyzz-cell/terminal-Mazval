#!/usr/bin/env python3
"""Smoke test untuk termux_server.py (Tornado + PTY penuh).

Jalankan servernya dulu:
    python termux_server.py
lalu:
    python3 tests/smoke_termux.py http://127.0.0.1:8080
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080").rstrip("/")
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail and not ok else ""), flush=True)


def http(method, path, data=None, headers=None, timeout=30):
    request = urllib.request.Request(
        BASE + path, data=data, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)
    except Exception as exc:
        return 0, str(exc).encode(), {}


def is_control(msg):
    return isinstance(msg, str) and msg.startswith('{"type"')


async def read_for(conn, seconds):
    out = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            msg = await asyncio.wait_for(
                conn.read_message(), timeout=max(0.05, deadline - time.time())
            )
        except asyncio.TimeoutError:
            break
        if msg is None:
            break
        out.append(msg)
    return out


async def wait_text(conn, needle, timeout=8.0, collected=None):
    buf = collected if collected is not None else []
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = "".join(m for m in buf if not is_control(m))
        if needle in text:
            return True, text
        try:
            msg = await asyncio.wait_for(
                conn.read_message(), timeout=max(0.05, deadline - time.time())
            )
        except asyncio.TimeoutError:
            break
        if msg is None:
            break
        buf.append(msg)
    return False, "".join(m for m in buf if not is_control(m))


async def shell_tests(url):
    from tornado.httpclient import HTTPRequest
    from tornado.websocket import websocket_connect

    conn = await websocket_connect(HTTPRequest(url, connect_timeout=15))
    buf = []

    ok, text = await wait_text(conn, "STORAGE TERMINAL KERNEL ACTIVE", 12, buf)
    check("banner shell tampil", ok, text[-300:])

    conn.write_message("echo TERMUX_$((6*7))\r")
    ok, text = await wait_text(conn, "TERMUX_42", 6, buf)
    check("eksekusi perintah", ok, text[-300:])

    conn.write_message("tty")
    conn.write_message("\r")
    ok, text = await wait_text(conn, "/dev/pts", 6, buf)
    check("PTY: tty -> /dev/pts", ok, text[-300:])

    conn.write_message(json.dumps({"type": "resize", "cols": 150, "rows": 50}))
    await asyncio.sleep(0.4)
    conn.write_message("stty size\r")
    ok, text = await wait_text(conn, "50 150", 6, buf)
    check("resize (SIGWINCH) diterima shell", ok, text[-300:])

    conn.write_message("printf 'UTF-\\U0001F680\\n'\r")
    ok, text = await wait_text(conn, "UTF-\U0001F680", 6, buf)
    check("output UTF-8 utuh", ok, text[-300:])

    conn.write_message("sleep 30\r")
    await asyncio.sleep(1.0)
    conn.write_message("\x03")
    await asyncio.sleep(0.8)
    conn.write_message("echo AFTER_$((21*2))\r")
    ok, text = await wait_text(conn, "AFTER_42", 6, buf)
    check("^C menghentikan proses", ok, text[-300:])

    conn.write_message("ech\t\t")
    await asyncio.sleep(1.5)
    conn.write_message("\x15")
    await asyncio.sleep(0.3)
    tail = "".join(m for m in buf if not is_control(m))
    check("tab-completion readline", "echo" in tail, tail[-300:])

    conn.write_message("exit\r")
    controls = []
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            msg = await asyncio.wait_for(
                conn.read_message(), timeout=max(0.1, deadline - time.time())
            )
        except asyncio.TimeoutError:
            break
        if msg is None:
            break
        if is_control(msg):
            controls.append(msg)
            try:
                if json.loads(msg).get("type") == "exit":
                    break
            except ValueError:
                pass
        else:
            buf.append(msg)
    check(
        "pesan exit terkirim saat shell berakhir",
        any('"exit"' in c for c in controls),
        str(controls),
    )
    conn.close()


def http_tests():
    status, body, _ = http("GET", "/")
    check("GET / -> 200 + UI", status == 200 and b"STORAGE TERMINAL" in body)

    name = "smoke_termux.txt"
    data = b"termux smoke test\n"
    status, body, _ = http(
        "POST",
        "/upload",
        data=data,
        headers={"X-Filename": name, "Content-Type": "application/octet-stream"},
    )
    check("upload file", status == 200 and b"success" in body, body[:200].decode())

    status, body, _ = http("GET", "/api/files")
    try:
        names = [f["name"] for f in json.loads(body).get("files", [])]
    except ValueError:
        names = []
    check("list file", status == 200 and name in names, str(names)[:200])

    status, body, _ = http("GET", f"/files/{name}")
    check("download file utuh", status == 200 and body == data, f"HTTP {status}")

    status, body, headers = http(
        "GET", f"/files/{name}", headers={"Range": "bytes=0-4"}
    )
    check(
        "download range (206)",
        status == 206 and body == data[:5],
        f"HTTP {status} {body!r}",
    )

    status, body, _ = http("DELETE", f"/api/files/{name}")
    check("hapus file", status == 200 and b"success" in body, body[:200].decode())


def main():
    print(f"=== smoke test termux_server: {BASE}", flush=True)
    http_tests()
    ws_url = BASE.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
    asyncio.run(shell_tests(ws_url))

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== {len(RESULTS) - len(failed)}/{len(RESULTS)} pemeriksaan lulus")
    for name, _, detail in failed:
        print(f"  GAGAL: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
