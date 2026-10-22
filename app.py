import os
import sys
import shutil

from fastapi import FastAPI, WebSocket

app = FastAPI(title="mazval-probe")


@app.get("/api/probe")
def probe():
    info = {
        "python": sys.version.split()[0],
        "bash": shutil.which("bash"),
        "sh": shutil.which("sh"),
        "dev_ptmx": os.path.exists("/dev/ptmx"),
        "tmpdir": os.environ.get("TMPDIR", "/tmp"),
        "vercel_env": os.environ.get("VERCEL"),
        "cwd": os.getcwd(),
        "cwd_writable": os.access(os.getcwd(), os.W_OK),
        "tmp_writable": os.access(os.environ.get("TMPDIR", "/tmp"), os.W_OK),
    }
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


@app.websocket("/ws")
async def ws_probe(websocket: WebSocket):
    await websocket.accept()
    try:
        import pty
        import asyncio

        pid, fd = pty.fork()
        if pid == 0:
            os.execvp("bash", ["bash", "-i"])
        else:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, os.read, fd, 1024)
            await websocket.send_text(data.decode(errors="ignore"))
            await websocket.send_text("\r\n[WS_PTY_OK]\r\n")
            os.write(fd, b"exit\n")
            os.close(fd)
            os.waitpid(pid, 0)
    except Exception as e:
        await websocket.send_text(f"[WS_ERR] {type(e).__name__}: {e}")
    await websocket.close()
