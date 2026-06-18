#!/usr/bin/env python3
"""yt-dlp Web Downloader — FastAPI backend"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="yt-dlp Web Downloader")


# ── Static / root ────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(BASE_DIR / "static" / "index.html")


# ── Info endpoint ─────────────────────────────────────────────────────────────

class InfoRequest(BaseModel):
    url: str
    mode: str = "single"


@app.post("/api/info")
async def get_info(req: InfoRequest):
    url = req.url.strip()
    if not url:
        return JSONResponse({"error": "URL is required"}, status_code=400)

    args = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json", "--no-warnings", "--quiet",
        "--yes-playlist" if req.mode == "playlist" else "--no-playlist",
        url,
    ]

    try:
        # subprocess.run in a thread — avoids asyncio subprocess pipe issues on Windows.
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(args, capture_output=True, timeout=120),
        )

        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace").strip()
            for prefix in ("ERROR: ", "[download] "):
                if err.startswith(prefix):
                    err = err[len(prefix):]
            return JSONResponse({"error": err or "Unknown error"}, status_code=400)

        # --dump-json writes one JSON object per line; grab the first.
        first_line = result.stdout.split(b"\n")[0].strip()
        raw = json.loads(first_line)
        return JSONResponse(_format_info(raw))

    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Request timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _format_info(raw: dict) -> dict:
    def fmt_size(b):
        if b is None:
            return "?"
        for unit in ("B", "KiB", "MiB", "GiB"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TiB"

    def fmt_views(n):
        return f"{n:,} views" if n else "?"

    def fmt_date(d):
        if not d or len(d) < 8:
            return d or "?"
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"

    def fmt_duration(sec):
        if not sec:
            return "?"
        sec = int(sec)
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    formats = []
    for f in raw.get("formats", []):
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        h_px, w_px = f.get("height"), f.get("width")

        if vcodec == "none":
            res = "audio only"
        elif h_px and w_px:
            res = f"{w_px}x{h_px}"
        else:
            res = "?"

        parts = []
        if vcodec and vcodec != "none":
            parts.append(vcodec)
        if acodec and acodec != "none":
            parts.append(acodec)
        tbr = f.get("tbr") or f.get("abr") or f.get("vbr")
        if tbr:
            parts.append(f"({int(tbr)}k)")

        size = f.get("filesize") or f.get("filesize_approx")
        codec_str = " + ".join(parts[:2])
        if len(parts) > 2:
            codec_str += f"  {parts[2]}"

        formats.append({
            "code": f.get("format_id", "?"),
            "ext":  f.get("ext", "?"),
            "res":  res,
            "codec": codec_str,
            "size":  fmt_size(size),
        })

    thumb = raw.get("thumbnail") or ""
    if not thumb and raw.get("thumbnails"):
        thumb = raw["thumbnails"][-1].get("url", "")

    playlist_count = raw.get("playlist_count") or raw.get("n_entries")

    return {
        "id":            raw.get("id", ""),
        "title":         raw.get("title", ""),
        "uploader":      raw.get("uploader") or raw.get("channel") or "?",
        "duration":      fmt_duration(raw.get("duration")),
        "views":         fmt_views(raw.get("view_count")),
        "uploadDate":    fmt_date(raw.get("upload_date")),
        "thumbnail":     thumb,
        "formats":       formats,
        "isPlaylist":    bool(raw.get("playlist_id") and playlist_count and playlist_count > 1),
        "playlistCount": playlist_count,
        "playlistTitle": raw.get("playlist_title") or raw.get("playlist") or "",
    }


# ── Download WebSocket ────────────────────────────────────────────────────────

def _build_args(params: dict) -> list:
    """Build yt-dlp subprocess args from frontend params. Never uses shell=True."""
    # Use the current Python interpreter so venv installs work on all platforms.
    args = [sys.executable, "-m", "yt_dlp", "--newline"]

    fmt_flag = (params.get("fmtFlag") or "bv*+ba/b").strip()
    args += ["-f", fmt_flag]

    pp = params.get("pp", {})
    if pp.get("extractAudio"):
        args.append("-x")
    if pp.get("mp3"):
        args += ["--audio-format", "mp3"]
    if pp.get("remux"):
        args += ["--remux-video", "mp4"]
    if pp.get("thumb"):
        args.append("--embed-thumbnail")
    if pp.get("subs"):
        args.append("--embed-subs")
        sub_lang = (params.get("subLang") or "en").strip()
        if sub_lang:
            args += ["--sub-langs", sub_lang]
    if pp.get("metadata"):
        args.append("--embed-metadata")
    if pp.get("chapters"):
        args.append("--split-chapters")
    if pp.get("sponsor"):
        args += ["--sponsorblock-remove", "all"]

    rate = (params.get("rate") or "").strip()
    if rate:
        args += ["-r", rate]

    if params.get("skip"):
        args += ["--download-archive", str(DOWNLOADS_DIR / "archive.txt")]

    args.append("--yes-playlist" if params.get("mode") == "playlist" else "--no-playlist")

    out_tpl = (params.get("outTpl") or "%(title)s.%(ext)s").strip()
    args += ["-o", str(DOWNLOADS_DIR / out_tpl)]

    url = (params.get("url") or "").strip()
    if url:
        args.append(url)

    return args


def _display_args(args: list) -> str:
    """Build a human-readable command string from the full arg list."""
    # args[0..2] = python -m yt_dlp — show as 'yt-dlp' instead
    display = ["yt-dlp"] + args[3:]
    parts = []
    for a in display:
        if " " in a or any(c in a for c in "%()*&|<>!"):
            parts.append(f'"{a}"')
        else:
            parts.append(a)
    return " ".join(parts)


def _line_color(line: str) -> str:
    lo = line.lower()
    if "[error]" in lo or lo.startswith("error:"):
        return "#f85149"
    if "[warning]" in lo or "[sponsorblock]" in lo:
        return "#e0a93c"
    if "[download]" in lo and "finished" in lo:
        return "var(--ac)"
    if (any(lo.startswith(p) for p in ("[debug]", "[info]"))
            or any(p in lo for p in ("[youtube]", "[generic]", "[extractor]"))):
        return "#7d8590"
    return "#c9d1d9"


@app.websocket("/ws/download")
async def ws_download(ws: WebSocket):
    await ws.accept()
    try:
        params = await ws.receive_json()
    except Exception:
        await ws.close()
        return

    args = _build_args(params)

    await ws.send_json({
        "text":   "$ " + _display_args(args),
        "color":  "var(--ac)",
        "update": False,
    })

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(BASE_DIR),
    )

    buf = b""
    try:
        while True:
            chunk = await proc.stdout.read(512)
            if not chunk:
                break
            buf += chunk
            # Split on both \r and \n; \r-only lines are in-place progress updates.
            while True:
                n_pos = buf.find(b"\n")
                r_pos = buf.find(b"\r")
                if n_pos == -1 and r_pos == -1:
                    break
                if n_pos == -1 or (r_pos != -1 and r_pos < n_pos):
                    pos, is_cr = r_pos, True
                else:
                    pos, is_cr = n_pos, False
                line = buf[:pos].decode("utf-8", errors="replace")
                buf = buf[pos + 1:]
                stripped = line.strip()
                if stripped:
                    try:
                        await ws.send_json({
                            "text":   stripped,
                            "color":  _line_color(stripped),
                            "update": is_cr,
                        })
                    except Exception:
                        proc.kill()
                        return
    except WebSocketDisconnect:
        proc.kill()
        return
    except Exception as exc:
        try:
            await ws.send_json({"text": f"[stream error] {exc}", "color": "#f85149", "update": False})
        except Exception:
            pass

    await proc.wait()
    try:
        await ws.send_json({"done": True, "exitCode": proc.returncode})
        await ws.close()
    except Exception:
        pass


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
