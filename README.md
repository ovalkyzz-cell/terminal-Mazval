# ⚡ TERMINAL MAZVAL

Web terminal berbasis **bash PTY penuh** (auto-fallback ke bash pipe di runtime tanpa PTY seperti Vercel) dengan UI brutalist, file upload streaming, gallery preview. Berjalan di **Vercel** maupun **Termux/lokal**.

## Fitur

- 🖥️ **Web Terminal** — shell bash interaktif via xterm.js. Engine **PTY-first** (`pty.openpty()` + controlling terminal): `tty`, job control, `^C` asli via line-discipline, `^Z`, resize → `SIGWINCH`, `vim`/`htop` jalan penuh. Bila PTY tidak tersedia otomatis fallback ke bash pipe (readline tetap menangani echo/prompt/history/completion)
- 🖵 **Resize Terminal** — `term.onResize` (xterm.js) → `POST /api/shell/<id>/resize` → `TIOCSWINSZ` + `SIGWINCH` → shell & program TUI menyesuaikan ukuran
- 📁 **Storage Engine** — upload streaming **chunked** (bagian 4 MB per request) — **tanpa batas ukuran file**, list, hapus, preview — **tersimpan permanen di cloud** (repo privat GitHub), tidak hilang walau instance mati
- 🖼️ **Gallery Preview** — preview gambar & video dengan Range request / streaming
- 📱 **Mobile Friendly** — tombol shortcut (^C, TAB, PY, LS, CLR, ▲▼)
- ⚡ **Brutal Design** — neobrutalist UI dengan shadow tebal & warna bold
- ☁️ **Vercel Ready** — respons streaming SSE, shell per-instance, storage `/tmp` sebagai cache + durable cloud sebagai sumber kebenaran

## Dua Entry Point

| File | Runtime | Fungsi |
|---|---|---|
| `app.py` | Vercel / lokal (FastAPI) | Full app: UI + shell SSE + storage + gallery. Engine: PTY bila `/dev/ptmx` bisa diminta, selain itu pipe |
| `termux_server.py` | Termux saja (Tornado + PTY) | Versi PTY penuh dengan storage permanen SDCard |

### Environment (opsional)

| Variabel | Nilai | Fungsi |
|---|---|---|
| `MAZVAL_SHELL_MODE` | `pty` / `pipe` | Paksa mode engine shell di `app.py` (default: auto-detect, bila PTY spawn gagal otomatis pipe) |

## Cara Menjalankan

### Vercel / lokal (FastAPI)

```bash
pip install -r requirements.txt
uvicorn app:app --port 8080
```

Akses: `http://localhost:8080`

### Termux (Tornado + PTY, storage permanen)

```bash
pip install tornado
python termux_server.py
```

Akses: `http://localhost:8080`

## Struktur Endpoint (`app.py`)

| Endpoint | Method | Fungsi |
|---|---|---|
| `/` | GET | UI Terminal + Storage + Gallery |
| `/api/shell` | POST | Buka sesi shell (body opsional `{cols, rows}` → balas `{id, mode, term, cols, rows}`) |
| `/api/shell/<id>` | GET | Ping sesi (404 = sesi hilang); alias `/api/shell/<id>/ping` |
| `/api/shell/<id>/stream` | GET | Stream output shell (SSE, max 290s, auto-reconnect) |
| `/api/shell/<id>/input` | POST | Kirim input keystroke (batch) — `{data}` atau `{b64}` |
| `/api/shell/<id>/resize` | POST | `TIOCSWINSZ` + `SIGWINCH` ke sesi PTY (`{cols, rows}`) |
| `/api/shell/<id>/interrupt` | POST | ^C (mode pty: tulis `\x03` ke PTY + fallback `killpg`; mode pipe: `killpg`) |
| `/api/shell/<id>` | DELETE | Tutup sesi shell |
| `/upload` | POST | Upload streaming file (header `X-Filename`, `X-File-Size`, opsional `X-Chunk`/`X-Chunk-Total`/`X-Append` untuk file besar) — otomatis didorong ke storage permanen setelah staging lengkap |
| `/api/files` | GET | List file + info storage |
| `/api/files/<nama>` | DELETE | Hapus file |
| `/files/<nama>` | GET | Streaming/preview file (dukung Range) |
| `/api/probe` | GET | Diagnostik environment |

## Arsitektur Terminal

### Mode PTY (Termux / lokal / VPS — "seperti Termux penuh")

`app.py` dan `termux_server.py` sama-sama membangun PTY sungguhan:

1. **PTY** — `pty.openpty()`; child dibuat via `preexec_fn` = `os.setsid()` + `ioctl(TIOCSCTTY)` → shell menjadi pemilik controlling terminal → job control, `tty`, `vim`, `htop` jalan normal. Winsize awal di-set **sebelum** spawn.
2. **^C asli** — `\x03` ditulis ke master PTY → line-discipline (`VINTR`) mengirim SIGINT ke foreground process group, persis seperti terminal asli. Fallback bila tetap macet: `killpg(os.tcgetpgrp(fd), SIGINT)`.
3. **Resize** — `TIOCSWINSZ` di master fd → kernel otomatis mengirim `SIGWINCH` ke shell & program TUI.
4. **Output** — read non-blocking + `select`, di-decode incremental UTF-8 (`replace`) agar byte emoji multibyte yang terpecah antar-chunk tidak rusak; buffer cap 4 MB dengan penanda trim.
5. **Kebersihan** — `close()` mengirim `SIGHUP`, lalu SIGKILL + reap, dan melepas handler fd dari IOLoop (tidak busy-loop, tidak bocor child).

### Mode Pipe (fallback — Vercel serverless)

Vercel serverless **tidak menyediakan PTY** (`/dev/ptmx` tidak ada, `mknod` "Operation not permitted", `CapEff=0`), jadi engine otomatis jatuh ke mode pipe (atau dipaksa dengan `MAZVAL_SHELL_MODE=pipe`):

1. **Shell pipe** — `bash --rcfile ... -i` dengan stdin/stdout pipe. Readline tetap menangani echo per-karakter, prompt, history, dan completion.
2. **Output → SSE** — `GET /api/shell/<id>/stream` streaming chunk output secara real-time (Vercel Python mendukung response streaming).
3. **Input → POST batch** — keystroke dikumpulkan 50ms lalu dikirim via `POST .../input`; Enter langsung flush.
4. **^C → SIGINT** — `killpg` ke process group shell (tanpa tty tidak ada mekanisme sinyal terminal).
5. **Sesi per-instance** — state shell di memori instance. Jika instance berganti (cold start), client otomatis membuka shell baru dengan pemberitahuan.

### Hardening sinyal

Shell anak **selalu me-reset disposition sinyal ke `SIG_DFL`** di `preexec_fn` (`SIGHUP`, `SIGINT`, `SIGQUIT`, `SIGTERM`, `SIGPIPE`, `SIGUSR1/2`, `SIGTSTP`, `SIGTTIN`, `SIGTTOU`, `SIGWINCH`, `SIGCHLD`). Tanpa reset ini, server yang diluncur lewat `nohup`/`&`/pm2/systemd mewarisi `SIGINT=SIG_IGN` — atribut *ignored* melewati `exec()` — sehingga Ctrl+C, `^Z`, dan SIGHUP **tidak berefek sama sekali** (terdeteksi lewat smoke test: `sleep 60` tetap hidup setelah `^C`). Ini menjamin tingkah laku terminal sama seperti Termux apa pun cara server dijalankan.

### Catatan Teknis Vercel

- PTY tidak tersedia di runtime serverless → terminal dibangun dari bash pipe + readline (echo, prompt, history, completion tetap jalan). TUI berat (`vim`, `htop`) tidak didesain untuk mode ini; di Termux/lokal mode PTY yang dipakai sehingga TUI jalan penuh.
- **Storage permanen dua lapis**: file di-staging dulu ke `/tmp` instance (cepat, pinning instance), lalu didorong ke **repo GitHub privat** (`_store/...` per-part 5MB + marker meta) begitu lengkap. List/download/delete selalu membaca dari GitHub → file **tetap ada** setelah cold start/deploy baru. `/tmp` hanya cache baca (download yang belum ada di cache diambil dari GitHub, dengan dukungan Range).
- Upload file besar dipecah client menjadi bagian 4 MB (melewati batas request body per-request), diverifikasi ukurannya setelah selesai, dengan retry per-bagian + pinning instance + auto-restart file bila gagal.
- Stream sesi berakhir otomatis pada batas durasi function, lalu **otomatis menyambung ulang ke sesi yang sama** — shell, cwd, dan proses tetap hidup.
- Di Termux (Tornado + PTY) semua alur di atas berjalan penuh dengan storage permanen.

## Catatan Termux (`termux_server.py`)

Storage diprioritaskan ke:

```
/storage/emulated/0/NEXUS_CLOUD_STORAGE
```

Fallback:

```
/data/data/com.termux/files/home/NEXUS_CLOUD_STORAGE
```

## Pengujian

Dua smoke-test suite (stdlib-only untuk `app.py`, butuh `tornado` untuk `termux_server.py`):

```bash
# engine mode PTY (harusnya 43/43) — jalankan server app.py dulu
python3 tests/smoke_test.py http://127.0.0.1:8080

# engine mode pipe / fallback Vercel (41/41)
MAZVAL_SHELL_MODE=pipe uvicorn app:app --port 8081 &
python3 tests/smoke_test.py http://127.0.0.1:8081

# termux_server.py (Tornado + PTY) — 20/20
python tests/smoke_termux.py http://127.0.0.1:8080
```

Cakupan: buka/tutup sesi, eksekusi perintah, `tty` (`/dev/pts` di mode pty), `stty size` + resize + bukti `SIGWINCH`, interrupt proses & proses CPU-bound, tab-completion, UTF-8 multibyte, deteksi exit, **output besar (batching)**, **teks mirip pesan kontrol tidak memutus sesi / input mirip JSON tetap jadi ketikan**, **urutan input cepat tidak tertukar**, **Ctrl+L**, 404/edge case, sesi paralel, upload → list → download (utuh & Range 206) → delete.

## Kinerja (anti-lag) & Ketiadaan Bug

| Lapisan | Mekanisme |
|---|---|
| Input | Micro-batch `0ms` (event dalam satu frame digabung, tanpa jeda 50ms) + **rantai promise FIFO** → urutan ketikan tidak pernah tertukar; latensi ketikan→echo terukur **median ~3,5 ms** (PTY lokal) |
| Output server | Batch burst dalam 1 pesan SSE (maks 64 KB/64 chunk) — output ratusan ribu baris bukan ribuan request kecil; di WS PTY seluruh data yang tersedia disedot dalam satu event (maks 256 KB/event) |
| Render klien | Antrean `requestAnimationFrame` + batas buffer 2 MB (di atas itu ditandai `[OUTPUT DILEWAT]`) → `seq`/`yes`/`cat` raksasa tidak membekukan UI |
| Backpressure WS | Penghitung frame tertunda; klien tertinggal → output ditahan + penanda, bukan antrean tak terbatas |
| IOLoop | Input PTY ditulis lewat antrean + thread penulis per koneksi, `send` di app.py di-offload ke threadpool → satu paste besar tidak memblokir request lain |
| Kanal kontrol | Pesan kontrol WS = **frame biner** (`binary=True`), input user = frame teks → teks user tidak pernah bisa menyamar jadi kontrol (`echo '{"type":"exit"}'` aman), begitu pula sebaliknya |
| Mobile | `visualViewport` (keyboard HP) → body ikut mengecil + `fit()` → terminal rapi, bukan tertutup keyboard; ketikan selama koneksi putus ditahan lalu dikirim begitu tersambung |

## Lisensi

MIT
