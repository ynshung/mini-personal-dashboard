# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Server — run from `server/`:

```bash
uv sync                                                       # install dependencies
uv run uvicorn main:app --host 0.0.0.0 --port 7333 --reload  # dev server
```

Firmware — run from project root:

```bash
pio run                  # build firmware
pio run --target upload  # build and flash to device
pio device monitor       # open serial monitor (115200 baud)
```

## Architecture

This is a FastAPI server (`server/`) that exposes JSON endpoints for a NodeMCU microcontroller display. Each feature is a self-contained router in `server/routes/` and registered in `server/main.py` under the `/v1` prefix.

**Adding a new endpoint:** create `server/routes/<feature>.py` with a `router = APIRouter()`, add the route handlers, then register it in `main.py` with `app.include_router(<router>, prefix="/v1")`.

**API key auth:** all endpoints (except `/v1/spotify/auth` and `/v1/spotify/callback`) require an `X-API-Key` header matching the `API_KEY` value in `.env`.

**Spotify auth flow:** `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` come from `.env` (project root). The OAuth refresh token is obtained once by visiting `/v1/spotify/auth` in a browser and is cached in `server/.spotify_tokens.json` (gitignored). The `now-playing` endpoint auto-refreshes the access token when it expires.

**cc-usage auth:** reads the Claude Code OAuth token directly from the macOS Keychain (`Claude Code-credentials`) — no config needed, macOS only.

## Firmware

**Build system:** PlatformIO (`platformio.ini`). Platform: `espressif32`, board: `esp32dev`, framework: Arduino.

Source lives in `src/main.cpp`. Libraries go in `lib/`, shared headers in `include/`.

**Environment variables:** `load_env.py` is a PlatformIO pre-script that reads `.env` from the project root and injects each key-value pair as a C preprocessor macro (`-D KEY=VALUE`), making server config (e.g. Wi-Fi credentials, API keys) available at compile time.

**Libraries:** `TFT_eSPI` (display driver) and `ArduinoJson` (JSON parsing). TFT_eSPI is configured entirely via `build_flags` in `platformio.ini` — do not edit `User_Setup.h` inside the library.

**IDE diagnostics:** The VS Code clang analyzer will show false errors (`Arduino.h not found`, undeclared identifiers) because it doesn't know about the PlatformIO toolchain. These are harmless — use `pio run` to verify real build status.

## Target display

Hardware: GC9A01 240×240 round TFT, driven via SPI.

**Wiring (ESP32 → GC9A01):**

| GC9A01 | ESP32 GPIO |
|--------|-----------|
| MOSI   | 23        |
| SCLK   | 18        |
| CS     | 15        |
| DC/RS  | 2         |
| RST    | 4         |

**Display layout (`src/main.cpp`):**
- Album art placeholder: 96×96 px rounded rect, top-left at (72, 36), accent color per track
- Track name: font 2, centered at y=148
- Artist: font 1, centered at y=168
- Time (elapsed / total): font 1, centered at y=196
- Progress bar: 160×3 px at (40, 208), green when playing, grey when paused

**Polling & rendering:**
- API poll every 5 s (`POLL_INTERVAL_MS`); full redraw only on track/play-state change
- Local tick every 250 ms (`TICK_INTERVAL_MS`) interpolates progress between polls — redraws bar + time only, no full screen clear
