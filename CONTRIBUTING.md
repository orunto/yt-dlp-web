# Contributing

Contributions are welcome. This document explains how to get set up, what the codebase expects, and what we look for in a pull request.

## Before you start

- Check open issues before opening a new one. If you are planning a large change, open an issue first to discuss it. This avoids wasted effort.
- Small, focused pull requests are easier to review and more likely to be merged than large ones that touch many things at once.

## Setting up locally

```bash
git clone https://github.com/madebyorunto/ytdlp-web.git
cd ytdlp-web
pip install -r requirements.txt
python start.py
```

Open `http://localhost:8000`. Changes to `main.py` reload automatically when running with `--reload` (which `start.py` sets). Changes to `static/index.html` take effect on browser refresh.

## Project structure

There are two files that matter:

- `main.py` — the FastAPI backend. Handles the info endpoint (`POST /api/info`) and the download stream (`WebSocket /ws/download`). yt-dlp is invoked as a subprocess here.
- `static/index.html` — the entire frontend in one file. No framework, no bundler. The `App` class at the bottom manages state and renders to the DOM.

If you are new to the project, read both files top to bottom before making changes. They are not long.

## What makes a good contribution

- Fix a real bug or add something genuinely useful. Do not add features for the sake of it.
- Keep the frontend dependency-free. No npm, no bundler, no frameworks. The goal is that anyone can open `index.html` and understand it.
- Do not add Python dependencies without a clear reason. The backend is intentionally minimal.
- Write code that a newcomer can read. Prefer clarity over cleverness.
- Test your change manually before submitting. Run through the full flow: paste a URL, fetch info, change options, start a download, check the output in `downloads/`.

## On the use of AI assistance

AI tools (such as GitHub Copilot, Claude, or ChatGPT) may be used to help write or review code, but the following rules apply without exception:

- You are responsible for every line you submit. "An AI wrote it" is not an excuse for broken, insecure, or untested code.
- Read and understand AI-generated code before including it. If you cannot explain what a block of code does, do not submit it.
- Test AI-generated changes the same way you would test anything else — run it, break it, confirm it works.
- Do not submit AI-generated content verbatim without review. Treat it as a first draft, not a finished product.
- Security-sensitive areas (subprocess calls, WebSocket message parsing, file path handling) require extra scrutiny regardless of how the code was produced.

## Pull request checklist

Before submitting:

- The app starts without errors (`python start.py`)
- The fetch, quality selection, and download flow work end to end
- Your change does not break the advanced options panel
- Code is readable and not over-engineered for the change being made
- Commit messages describe what changed and why, not just what the diff shows

## Reporting bugs

Open a GitHub issue with:

1. What you did
2. What you expected to happen
3. What actually happened (include any error output from the terminal or the browser console)
4. Your OS, Python version, and yt-dlp version (`yt-dlp --version`)
