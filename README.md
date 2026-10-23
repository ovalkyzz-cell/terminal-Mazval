# ⚡ TERMINAL MAZVAL

Web terminal berbasis **bash pipe (PTY-free)** dengan UI brutalist, file upload streaming, gallery preview. Berjalan di **Vercel** maupun **Termux/lokal**.

## Fitur

- 🖥️ **Web Terminal** — shell bash interaktif via xterm.js: echo, prompt, history, tab-completion ditangani readline server-side (tanpa TTY)
- 📁 **Storage Engine** — upload streaming **chunked** (bagian 4 MB per request) — **tanpa batas ukuran file**, list, hapus, preview — **tersimpan permanen di cloud** (repo privat GitHub), tidak hilang walau instance mati
- 🖼️ **Gallery Preview** — preview gambar & video dengan Range request / streaming
- 📱 **Mobile Friendly** — tombol shortcut (^C, TAB, PY, LS, CLR, ▲▼)
- ⚡ **Brutal Design** — neobrutalist UI dengan shadow tebal & warna bold
- ☁️ **Vercel Ready** — respons streaming SSE, shell per-instance, storage `/tmp` sebagai cache + durable cloud sebagai sumber kebenaran

## Dua Entry Point

| File | Runtime | Fungsi |
|---|---|---|
| `app.py` | Vercel / lokal (FastAPI) | Full app: UI + shell SSE + storage + gallery |
| `termux_server.py` | Termux saja (Tornado + PTY) | Versi PTY penuh dengan storage permanen SDCard |

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
| `/api/shell` | POST | Buka sesi shell (spawn bash pipe) |
| `/api/shell/<id>` | GET | Ping sesi (404 = sesi hilang) |
| `/api/shell/<id>/stream` | GET | Stream output shell (SSE, max 290s) |
| `/api/shell/<id>/input` | POST | Kirum input keystroke (batch) |
| `/api/shell/<id>/interrupt` | POST | ^C (SIGINT ke process group) |
| `/api/shell/<id>` | DELETE | Tutup sesi shell |
| `/upload` | POST | Upload streaming file (header `X-Filename`, `X-File-Size`, opsional `X-Chunk`/`X-Chunk-Total`/`X-Append` untuk file besar) — otomatis didorong ke storage permanen setelah staging lengkap |
| `/api/files` | GET | List file + info storage |
| `/api/files/<nama>` | DELETE | Hapus file |
| `/files/<nama>` | GET | Streaming/preview file (dukung Range) |
| `/api/probe` | GET | Diagnostik environment |

## Arsitektur Terminal di Vercel

Vercel serverless **tidak menyediakan PTY** (`/dev/ptmx` tidak ada), jadi terminal dibangun dari:

1. **Shell pipe** — `bash --rcfile ... -i` dengan stdin/stdout pipe. Readline tetap menangani echo per-karakter, prompt, history, dan completion.
2. **Output → SSE** — `GET /api/shell/<id>/stream` streaming chunk output secara real-time (Vercel Python mendukung response streaming).
3. **Input → POST batch** — keystroke dikumpulkan 50ms lalu dikirim via `POST .../input`; Enter langsung flush.
4. **^C → SIGINT** — `killpg` ke process group shell (tanpa tty tidak ada mekanisme sinyal terminal).
5. **Sesi per-instance** — state shell di memori instance. Jika instance berganti (cold start), client otomatis membuka shell baru dengan pemberitahuan.

### Catatan Teknis Vercel

- PTY tidak tersedia di runtime serverless → terminal dibangun dari bash pipe + readline (echo, prompt, history, completion tetap jalan). TUI berat (`vim`, `htop`) tidak didesain untuk mode ini.
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

## Lisensi

MIT
