# yt-dlp web downloader

A browser-based interface for [yt-dlp][https://github.com/yt-dlp/yt-dlp]. Paste a URL, pick a quality, download. No Electron, no build step — just Python and a single HTML file.

## What it does

- Fetches video metadata (title, uploader, duration, available formats) before you commit to a download
- Detects automatically whether a URL is a single video or a playlist and sets the mode accordingly
- Streams yt-dlp's real output to the browser in real time so you can see exactly what is happening
- Pipes single-stream formats directly to the browser so the file lands in your Downloads folder without first buffering to the server
- Shows a toast while the download is being prepared and lets you cancel it at any time
- Keeps advanced options (post-processing, output templates, rate limiting, subtitles) out of the way until you need them

## Requirements

- Python 3.10 or newer
- yt-dlp installed in the same Python environment
- ffmpeg on your PATH if you want audio extraction or format merging

## Installation

```bash
git clone https://github.com/madebyorunto/ytdlp-web.git
cd ytdlp-web
pip install -r requirements.txt
```

## Running

```bash
python start.py
```

Then open `http://localhost:8000` in your browser.

Alternatively, run directly with uvicorn:

```bash
uvicorn main:app --reload
```

## How to use

1. Paste a YouTube (or any yt-dlp-supported) URL into the input and press Enter or click Fetch Info.
2. The app detects whether the URL is a video or playlist and shows the available quality options.
3. Pick a quality. Files are saved to the `downloads/` folder next to `main.py`.
4. If you need post-processing (audio extraction, subtitle embedding, SponsorBlock removal, etc.) open Advanced options.

## Project structure

```
ytdlp-web/
├── main.py            # FastAPI backend — info, download WebSocket, direct stream
├── static/
│   ├── index.html     # Frontend — all HTML, CSS, and JS in one file
│   ├── favicon.ico    # yt-dlp icon
│   └── favicon.png    # yt-dlp icon (PNG variant)
├── start.py           # Convenience launcher
├── requirements.txt
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
└── downloads/         # Created automatically on first run
```

## Downloaded files

Files land in `downloads/` relative to where you run the server. The output template defaults to `%(title)s.%(ext)s`. You can change this in Advanced options.

## Supported sites

Anything yt-dlp supports — YouTube, Vimeo, Twitter/X, SoundCloud, and hundreds more. See the full list at https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md.

## License

MIT
