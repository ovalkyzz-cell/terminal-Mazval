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
    # Server mengirim pesan kontrol sebagai frame BINER (bytes),
    # output shell selalu frame teks (str).
    return isinstance(msg, (bytes, bytearray))


def ctrl_get(msg, key):
    try:
        obj = json.loads(msg)
    except (ValueError, TypeError):
        return None
    return obj.get(key) if isinstance(obj, dict) else None


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

    conn.write_message(
        json.dumps({"type": "resize", "cols": 150, "rows": 50}), binary=True
    )
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

    # --- output besar, tabrakan pesan kontrol, urutan input ---------------
    conn.write_message("seq 1 200000\r")
    ok, text = await wait_text(conn, "200000", 20, buf)
    check("output besar terkirim utuh (drain PTY)", ok, text[-300:])

    mark = len(buf)
    conn.write_message("echo '{\"type\":\"exit\"}'\r")
    await asyncio.sleep(1.5)
    fake = [m for m in buf[mark:] if is_control(m)]
    check(
        "teks mirip pesan kontrol tidak memutus sesi",
        not any(ctrl_get(m, "type") == "exit" for m in fake),
        str(fake),
    )
    conn.write_message("echo HIDUP_$((2*3))\r")
    ok, text = await wait_text(conn, "HIDUP_6", 6, buf)
    check("sesi tetap hidup setelah teks kontrol palsu", ok, text[-300:])

    # input berbentuk JSON harus jadi ketikan user, bukan pesan kontrol
    conn.write_message('{"type":"resize","cols":80,"rows":24}\r')
    await asyncio.sleep(1.0)
    conn.write_message("echo TERSALIN_$((5*8))\r")
    ok, text = await wait_text(conn, "TERSALIN_40", 6, buf)
    check("input mirip JSON tetap dieksekusi sebagai teks", ok, text[-300:])

    conn.write_message(
        json.dumps({"type": "resize", "cols": 90, "rows": 29}), binary=True
    )
    await asyncio.sleep(0.5)
    conn.write_message("stty size\r")
    ok, text = await wait_text(conn, "29 90", 6, buf)
    check("resize biner tetap diterima shell setelah input JSON", ok, text[-300:])

    mark = len(buf)
    conn.write_message("echo URUT_PERTAMA\r")
    conn.write_message("echo URUT_KEDUA\r")
    ok, text = await wait_text(conn, "URUT_KEDUA", 6, buf)
    joined = "".join(m for m in buf[mark:] if not is_control(m))
    check(
        "urutan input cepat tidak tertukar",
        ok and 0 <= joined.find("URUT_PERTAMA") < joined.find("URUT_KEDUA"),
        joined[-300:],
    )

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
            if ctrl_get(msg, "type") == "exit":
                break
        else:
            buf.append(msg)
    check(
        "pesan exit terkirim saat shell berakhir",
        any(ctrl_get(c, "type") == "exit" for c in controls),
        str(controls),
    )
    conn.close()


async def run_file_tests(url):
    from tornado.httpclient import HTTPRequest
    from tornado.websocket import websocket_connect

    name = "smoke_run_demo.py"
    code = b'print("RUN_DEMO_OK")\n'
    status, body, _ = http(
        "POST",
        "/upload",
        data=code,
        headers={"X-Filename": name, "Content-Type": "application/octet-stream"},
    )
    check("upload file .py", status == 200 and b"success" in body, body[:200].decode())

    conn = await websocket_connect(HTTPRequest(url, connect_timeout=15))
    buf = []

    async def step(command, needle, timeout=8.0):
        start = len(buf)
        conn.write_message(command if command.endswith("\r") else command + "\r")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(
                    conn.read_message(), timeout=max(0.1, deadline - time.time())
                )
            except asyncio.TimeoutError:
                break
            if msg is None:
                break
            if not is_control(msg):
                buf.append(msg)
            if needle in "".join(buf[start:]):
                return True, "".join(buf[start:])
        return False, "".join(buf[start:])

    await wait_text(conn, "STORAGE TERMINAL KERNEL ACTIVE", 12, buf)

    ok, text = await step("head -c 2 smoke_run_demo.py", "#!", 6)
    check("file .py diberi shebang #!", ok, text[-250:])
    ok, text = await step("./smoke_run_demo.py", "RUN_DEMO_OK", 8)
    clean = text.replace("\r\n", "")
    env_noexec = ("Permission denied" in clean) and ("interpreter" not in clean) and ("required file" not in clean)
    check(
        "./smoke_run_demo.py jalan (atau media noexec yang wajar)",
        ok or env_noexec,
        "media noexec: " + clean[-160:] if env_noexec else clean[-250:],
    )
    ok, text = await step(
        'cp smoke_run_demo.py /tmp/rx_$$.py; chmod +x /tmp/rx_$$.py; /tmp/rx_$$.py; rm -f /tmp/rx_$$.py',
        "RUN_DEMO_OK",
        8,
    )
    check("shebang hasil auto-dieksekusi kernel (media exec)", ok, text[-250:])
    ok, text = await step("run smoke_run_demo.py", "RUN_DEMO_OK", 8)
    check("perintah run file.py jalan", ok, text[-250:])
    ok, text = await step("python3 smoke_run_demo.py", "RUN_DEMO_OK", 8)
    check("python3 file.py jalan", ok, text[-250:])

    conn.write_message("exit\r")
    await asyncio.sleep(1)
    conn.close()

    status, body, _ = http("DELETE", f"/api/files/{name}")
    check("bersih-bersih file uji run", status == 200 and b"success" in body, body[:200].decode())


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
    asyncio.run(run_file_tests(ws_url))

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== {len(RESULTS) - len(failed)}/{len(RESULTS)} pemeriksaan lulus")
    for name, _, detail in failed:
        print(f"  GAGAL: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
