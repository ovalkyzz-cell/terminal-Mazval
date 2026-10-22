# ⚡ TERMINAL MAZVAL

Web terminal berbasis **Tornado + PTY** dengan UI brutalist, file upload streaming (0% RAM), gallery preview, dan dukungan **Termux**.

## Fitur

- 🖥️ **Web Terminal** — Shell bash/termux interaktif via WebSocket + xterm.js
- 📁 **Storage Engine** — Upload file streaming langsung ke penyimpanan (SDCard/Termux home)
- 🖼️ **Gallery Preview** — Preview gambar & video langsung di browser (Range request / streaming)
- 📱 **Mobile Friendly** — Tombol shortcut (^C, TAB, PY, LS, CLR, ▲▼) untuk HP
- ⚡ **Brutal Design** — Neobrutalist UI dengan shadow tebal & warna bold
- 🔄 **Auto-Installer** — Tornado otomatis terinstal jika belum ada

## Dua Entry Point

| File | Runtime | Fungsi |
|---|---|---|
| `termux_server.py` | Lokal / Termux (Tornado) | Full storage terminal + gallery (port 8080) |
| `app.py` | Vercel (FastAPI) | Probe kemampuan runtime Vercel: PTY, bash, writable FS, WebSocket |

## Cara Menjalankan

### Lokal (storage terminal penuh)

```bash
pip install tornado
python termux_server.py
```

Akses: `http://localhost:8080`

### Vercel (probe runtime)

```bash
pip install -r requirements.txt
uvicorn app:app --port 8080
```

Endpoints: `GET /api/probe` (diagnostik environment), `WS /ws` (uji PTY via WebSocket).

## Struktur Endpoint

| Endpoint | Method | Fungsi |
|---|---|---|
| `/` | GET | UI Terminal + Storage + Gallery |
| `/ws` | WS | Koneksi terminal interaktif (PTY) |
| `/upload` | POST | Upload streaming file |
| `/api/files` | GET | List file + info storage |
| `/api/files/<nama>` | DELETE | Hapus file |
| `/files/<nama>` | GET | Streaming/preview file |

## Catatan Termux

Di Termux, storage otomatis diprioritaskan ke:

```
/storage/emulated/0/NEXUS_CLOUD_STORAGE
```

Jika tidak tersedia, fallback ke:

```
/data/data/com.termux/files/home/NEXUS_CLOUD_STORAGE
```

## Lisensi

MIT
