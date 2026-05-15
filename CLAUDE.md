# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from `server/`:

```bash
uv sync                                                       # install dependencies
uv run uvicorn main:app --host 0.0.0.0 --port 7333 --reload  # dev server
```

## Architecture

This is a FastAPI server (`server/`) that exposes JSON endpoints for a NodeMCU microcontroller display. Each feature is a self-contained router in `server/routes/` and registered in `server/main.py` under the `/v1` prefix.

**Adding a new endpoint:** create `server/routes/<feature>.py` with a `router = APIRouter()`, add the route handlers, then register it in `main.py` with `app.include_router(<router>, prefix="/v1")`.

**Spotify auth flow:** `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` come from `.env` (project root). The OAuth refresh token is obtained once by visiting `/v1/spotify/auth` in a browser and is cached in `server/.spotify_tokens.json` (gitignored). The `now-playing` endpoint auto-refreshes the access token when it expires.

**cc-usage auth:** reads the Claude Code OAuth token directly from the macOS Keychain (`Claude Code-credentials`) — no config needed, macOS only.

## Target display

The NodeMCU polls these endpoints to drive a physical display. Current hardware: potentially a GC9A01 240×240 TFT. Endpoints return structured data; formatting is handled on the device side.
