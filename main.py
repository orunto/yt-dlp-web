#!/usr/bin/env python3
"""yt-dlp Web Downloader — FastAPI backend"""

import asyncio
import io
import json
import re
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

app = FastAPI(title="yt-dlp Web Downloader")


# ── Static / root ────────────────────────────────────────────────────────────

@app.get("/")
async def root() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


# ── Info endpoint ─────────────────────────────────────────────────────────────

class InfoRequest(BaseModel):
    url: str
    mode: str = "single"


@app.post("/api/info")
async def get_info(req: InfoRequest) -> JSONResponse:
    url = req.url.strip()
    if not url:
        return JSONResponse({"error": "URL is required"}, status_code=400)

    args = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json", "--no-warnings", "--quiet",
        "--yes-playlist", "--playlist-items", "1",
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
        raw: dict[str, Any] = json.loads(first_line)
        return JSONResponse(_format_info(raw))

    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Request timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _fmt_size(b: int | float | None) -> str:
    if b is None:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TiB"


def _friendly_heights(formats: list[dict[str, Any]]) -> list[int]:
    """Return video heights present in the format list, descending."""
    by_height: dict[int, int] = {}
    for f in formats:
        if f.get("vcodec", "none") == "none":
            continue
        h: int | None = f.get("height")
        size = int(f.get("filesize") or f.get("filesize_approx") or 0)
        if h and (h not in by_height or size > by_height[h]):
            by_height[h] = size
    return sorted(by_height.keys(), reverse=True)


def _aggregate_sizes(
    all_fmt_lists: list[list[dict[str, Any]]],
    target_heights: list[int],
) -> dict[str, str]:
    """Sum file sizes across all videos per height bucket and audio track."""
    height_totals: dict[int, int] = {h: 0 for h in target_heights}
    audio_total: int = 0

    for fmt_list in all_fmt_lists:
        by_height: dict[int, int] = {}
        best_audio: int = 0
        for f in fmt_list:
            vcodec: str = f.get("vcodec", "none")
            h: int | None = f.get("height")
            size = int(f.get("filesize") or f.get("filesize_approx") or 0)
            if vcodec == "none":
                if size > best_audio:
                    best_audio = size
            elif h:
                if h not in by_height or size > by_height[h]:
                    by_height[h] = size

        for target_h in target_heights:
            best = max((s for h, s in by_height.items() if h <= target_h), default=0)
            height_totals[target_h] += best
        audio_total += best_audio

    result: dict[str, str] = {}
    for h in target_heights:
        t = height_totals[h]
        result[str(h)] = _fmt_size(t) if t > 0 else "?"
    result["audio"] = _fmt_size(audio_total) if audio_total > 0 else "?"
    return result


def _format_info(raw: dict[str, Any]) -> dict[str, Any]:
    def fmt_views(n: int | None) -> str:
        return f"{n:,} views" if n else "?"

    def fmt_date(d: str | None) -> str:
        if not d or len(d) < 8:
            return d or "?"
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"

    def fmt_duration(sec: int | float | None) -> str:
        if not sec:
            return "?"
        s = int(sec)
        h, m, r = s // 3600, (s % 3600) // 60, s % 60
        return f"{h}:{m:02d}:{r:02d}" if h else f"{m}:{r:02d}"

    formats: list[dict[str, Any]] = []
    for f in raw.get("formats", []):
        vcodec: str = f.get("vcodec", "none")
        acodec: str = f.get("acodec", "none")
        h_px: int | None = f.get("height")
        w_px: int | None = f.get("width")

        if vcodec == "none":
            res = "audio only"
        elif h_px and w_px:
            res = f"{w_px}x{h_px}"
        else:
            res = "?"

        parts: list[str] = []
        if vcodec and vcodec != "none":
            parts.append(vcodec)
        if acodec and acodec != "none":
            parts.append(acodec)
        tbr: float | None = f.get("tbr") or f.get("abr") or f.get("vbr")
        if tbr:
            parts.append(f"({int(tbr)}k)")

        codec_str = " + ".join(parts[:2])
        if len(parts) > 2:
            codec_str += f"  {parts[2]}"

        formats.append({
            "code":  f.get("format_id", "?"),
            "ext":   f.get("ext", "?"),
            "res":   res,
            "codec": codec_str,
            "size":  _fmt_size(f.get("filesize") or f.get("filesize_approx")),
        })

    thumb: str = raw.get("thumbnail") or ""
    if not thumb and raw.get("thumbnails"):
        thumb = raw["thumbnails"][-1].get("url", "")

    playlist_count: int | None = raw.get("playlist_count") or raw.get("n_entries")

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


# ── Info WebSocket ────────────────────────────────────────────────────────────

@app.websocket("/ws/info")
async def ws_info(ws: WebSocket) -> None:
    await ws.accept()
    try:
        data = await ws.receive_json()
    except Exception:
        await ws.close()
        return

    url = (data.get("url") or "").strip()
    if not url:
        await ws.send_json({"type": "error", "message": "URL is required"})
        await ws.close()
        return

    args = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json", "--no-warnings", "--quiet",
        "--yes-playlist",
        url,
    ]

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _stream() -> None:
        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(BASE_DIR),
            )
            assert proc.stdout is not None

            first_done = False
            all_fmt_lists: list[list[dict[str, Any]]] = []

            while True:
                raw_bytes = proc.stdout.readline()
                if not raw_bytes:
                    break
                line = raw_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue

                fmt_list: list[dict[str, Any]] = raw.get("formats", [])
                all_fmt_lists.append(fmt_list)

                if not first_done:
                    first_done = True
                    asyncio.run_coroutine_threadsafe(
                        queue.put({"type": "meta", **_format_info(raw)}),
                        loop,
                    )
                else:
                    total = raw.get("playlist_count") or raw.get("n_entries")
                    asyncio.run_coroutine_threadsafe(
                        queue.put({"type": "progress", "n": len(all_fmt_lists), "total": total}),
                        loop,
                    )

            proc.wait()

            if proc.returncode != 0:
                assert proc.stderr is not None
                err = proc.stderr.read().decode("utf-8", errors="replace").strip()
                if not first_done:
                    asyncio.run_coroutine_threadsafe(
                        queue.put({"type": "error", "message": err or "yt-dlp failed"}),
                        loop,
                    )
                asyncio.run_coroutine_threadsafe(queue.put({"type": "done"}), loop)
                return

            if len(all_fmt_lists) > 1:
                target_heights = _friendly_heights(all_fmt_lists[0])
                sizes = _aggregate_sizes(all_fmt_lists, target_heights)
                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "sizes", "sizes": sizes}),
                    loop,
                )

            asyncio.run_coroutine_threadsafe(queue.put({"type": "done"}), loop)
        except Exception as exc:
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "error", "message": str(exc)}),
                loop,
            )

    threading.Thread(target=_stream, daemon=True).start()

    try:
        while True:
            msg = await queue.get()
            await ws.send_json(msg)
            if msg.get("type") in ("done", "error"):
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass

    try:
        await ws.close()
    except Exception:
        pass


# ── Download WebSocket ────────────────────────────────────────────────────────

def _build_args(params: dict[str, Any], session_dir: Path) -> list[str]:
    """Build yt-dlp subprocess args from frontend params. Never uses shell=True."""
    # Use the current Python interpreter so venv installs work on all platforms.
    args: list[str] = [sys.executable, "-m", "yt_dlp", "--newline"]

    fmt_flag = (params.get("fmtFlag") or "bv*+ba/b").strip()
    args += ["-f", fmt_flag]

    pp: dict[str, Any] = params.get("pp", {})
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
    args += ["-o", str(session_dir / out_tpl)]

    url = (params.get("url") or "").strip()
    if url:
        args.append(url)

    return args


def _display_args(args: list[str]) -> str:
    """Build a human-readable command string from the full arg list."""
    # args[0..2] = python -m yt_dlp — show as 'yt-dlp' instead
    display = ["yt-dlp"] + args[3:]
    parts: list[str] = []
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
async def ws_download(ws: WebSocket) -> None:
    await ws.accept()
    try:
        params: dict[str, Any] = await ws.receive_json()
    except Exception:
        await ws.close()
        return

    session_id = str(uuid.uuid4())
    session_dir = DOWNLOADS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    args = _build_args(params, session_dir)

    await ws.send_json({
        "text":   "$ " + _display_args(args),
        "color":  "var(--ac)",
        "update": False,
    })

    # asyncio.create_subprocess_exec is broken on Windows (NotImplementedError).
    # Instead: run subprocess.Popen in a background thread, push each output
    # line into an asyncio.Queue, and drain the queue from the async handler.
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _stream() -> None:
        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(BASE_DIR),
            )
            assert proc.stdout is not None
            buf = b""
            while True:
                chunk = proc.stdout.read(512)
                if not chunk:
                    break
                buf += chunk
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
                        asyncio.run_coroutine_threadsafe(
                            queue.put({"text": stripped, "color": _line_color(stripped), "update": is_cr}),
                            loop,
                        )
            proc.wait()
            done_msg: dict[str, Any] = {"done": True, "exitCode": proc.returncode}
            if proc.returncode == 0:
                files = sorted(f for f in session_dir.rglob("*") if f.is_file())
                if len(files) == 1:
                    done_msg["downloadUrl"] = f"/files/{session_id}/{files[0].name}"
                elif len(files) > 1:
                    done_msg["downloadUrl"] = f"/files/{session_id}"
            asyncio.run_coroutine_threadsafe(queue.put(done_msg), loop)
        except Exception as exc:
            asyncio.run_coroutine_threadsafe(
                queue.put({"text": f"[stream error] {exc}", "color": "#f85149", "update": False, "done": True, "exitCode": 1}),
                loop,
            )

    threading.Thread(target=_stream, daemon=True).start()

    try:
        while True:
            msg = await queue.get()
            await ws.send_json(msg)
            if msg.get("done"):
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass

    try:
        await ws.close()
    except Exception:
        pass


# ── File serving ─────────────────────────────────────────────────────────────

def _validate_session(session_id: str) -> Path | None:
    """Return the session dir if it's valid and exists, else None."""
    if not _UUID_RE.match(session_id):
        return None
    p = DOWNLOADS_DIR / session_id
    return p if p.exists() else None


@app.get("/files/{session_id}/{filename}", response_model=None)
async def serve_file(session_id: str, filename: str) -> FileResponse | JSONResponse:
    """Serve a single downloaded file then delete its session directory."""
    session_dir = _validate_session(session_id)
    if session_dir is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    if "/" in filename or "\\" in filename or filename.startswith("."):
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    path = session_dir / filename
    if not path.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)
    # Security: confirm path is inside DOWNLOADS_DIR
    try:
        path.resolve().relative_to(DOWNLOADS_DIR.resolve())
    except ValueError:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    return FileResponse(
        str(path),
        filename=path.name,
        background=BackgroundTask(shutil.rmtree, str(session_dir), True),
    )


@app.get("/files/{session_id}", response_model=None)
async def serve_zip(session_id: str) -> StreamingResponse | JSONResponse:
    """Zip all files in a session and serve the archive, then clean up."""
    session_dir = _validate_session(session_id)
    if session_dir is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    files = sorted(f for f in session_dir.rglob("*") if f.is_file())
    if not files:
        return JSONResponse({"error": "No files in session"}, status_code=404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for f in files:
            zf.write(f, f.relative_to(session_dir))
    buf.seek(0)
    shutil.rmtree(str(session_dir), True)  # safe to delete; zip is in memory
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="download.zip"'},
    )


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
