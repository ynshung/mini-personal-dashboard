# Project Guide

This file provides guidance to AI agents and developers working with code in this repository.

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

**Libraries:** `TFT_eSPI` (display driver), `TJpg_Decoder` (JPEG decoder), and `ArduinoJson` (JSON parsing). TFT_eSPI is configured entirely via `build_flags` in `platformio.ini` — do not edit `User_Setup.h` inside the library.

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
- Album art: full-screen 240×240 JPEG fetched from server, decoded on-device by TJpg_Decoder; clipped to circle by server-side mask
- Track name + artist: rendered server-side with Pillow (Inter font) in a gradient overlay region at the bottom
- Progress bar: 160×3 px at (40, 210), white fill when playing, dim when paused — drawn locally by ESP32

**Album art pipeline (`server/routes/album_art.py`):**
- Fetches album art JPEG from Spotify, resizes to 240×240, applies gradient overlay (rows 132–240), composites track/artist text, encodes to JPEG (quality 75, optimized); no server-side circular mask — display hardware clips to circle
- Raw base image cached in `server/.album_art_cache/` keyed by Spotify album ID; gradient and text composited per-request on top of cached base

**Button controls (`src/main.cpp`):**
- GPIO 19, active-high, no internal pull-up (OneButton library)
  - Spotify screen: single click → toggle play/pause, double click → next track, long press → previous track
  - RTSP screen: single click → next stream (`rtspIndex++`), double click → previous stream, long press → no-op
  - Clock screen: all gestures → no-op
- GPIO 21, active-high, no internal pull-up
  - Single click → cycle forward: `CLOCK → CC_USAGE → RTSP → SPOTIFY → CLOCK` (via `activateScreen()`)
  - Double click → cycle backward: `CLOCK → SPOTIFY → RTSP → CC_USAGE → CLOCK`
  - Long press → `ESP.restart()`

**Screens (`src/main.cpp`):**
- `CLOCK` (default/startup): NTP-synced minimal analog clock rendered entirely on-device via `TFT_eSprite` (240×240, 8-bit color depth); 12 grey radial tick marks, white hour/minute hands, red sweeping second hand with counterweight tail, white center dot, and date text ("Mon 26") below center; smooth animation at 25 FPS (`CLOCK_TICK_MS = 40`); sub-second interpolation via `gettimeofday()` microseconds; sprite created on activation, freed on screen switch to reclaim ~57 KB; date string cached in `clockDateBuf`, recomputed on activation or midnight; timezone set via `NTP_OFFSET_HOURS` float define (default `8.0f` = UTC+8; supports fractional offsets); NTP synced in `initWiFi` via `configTime`; also acts as server-unreachable fallback — after `IDLE_TIMEOUT_MS` (2 min) of server errors on any screen, calls `activateScreen(CLOCK)` and stays there permanently
- `CC_USAGE`: polls `/v1/cc-usage` every 10 s; renders Claude logo (`include/claude_logo.h`, RGB565 bitmap stored byte-swapped for TFT_eSPI), 5-HR and 7-DAY utilization blocks, and a "last refreshed" label at the bottom; color thresholds 0–60% white, 61–99% orange, 100% red; `-1` sentinel means null (plan doesn't have that window); server caches upstream response for 2 min and includes `refreshed_ago` string in every response; each usage bar has a small white downward triangle above it marking `time_pct` (percentage of the billing window elapsed, computed server-side from `resets_at`)
- `RTSP`: dual-core pipeline — `rtspNetTask` (Core 0) fetches `/v1/rtsp/frame?index=rtspIndex` continuously using ping-pong double buffers (`rtspBuf[2][32768]`) and `rtspFreeSem`/`rtspReadySem` counting semaphores; `loop()` (Core 1) renders each frame via TJpgDec as soon as it arrives; overlay (label + dots) composited server-side into the JPEG; `rtspStreamCount` tracked from `X-Stream-Count` header; stream index persists across screen switches; task suspended when not on RTSP screen
- `SPOTIFY`: polls `/v1/spotify/now-playing` every 5 s; when `has_lyrics` is true enters lyrics mode (`lyricsMode = true`) — fetches `/v1/spotify/lyrics/frame` on a timer driven by `X-Next-Lyric-Ms` response header; when `has_lyrics` is false renders album art as usual; progress bar always drawn locally; seek detection resets the lyrics timer immediately (drift > 3 s from local estimate triggers re-fetch)
- On screen switch: `activateScreen(s)` clears `serverUnreachableSince`, `pollFailed`, runs per-screen init (fetch + draw); screens poll independently

**Polling & rendering:**
- `/v1/spotify/now-playing` returns lightweight JSON: `track_id`, `is_playing`, `progress_ms`, `duration_ms`, `has_lyrics`; also updates server-side playback cache used by the lyrics endpoint
- `/v1/spotify/now-playing/art/jpeg` returns composited JPEG (7–29 KB) — called only on track change when `has_lyrics` is false; decoded on-device by TJpg_Decoder
- `/v1/spotify/lyrics/frame` returns 240×240 JPEG with blurred+dimmed album art background and 3-line lyrics overlay; called on timer when `has_lyrics` is true; `X-Next-Lyric-Ms` header tells ESP how long until the next lyric line
- API poll every 5 s (`POLL_INTERVAL_MS`); also polls immediately when estimated progress reaches song duration
- Local tick every 1s (`TICK_INTERVAL_MS`) interpolates progress bar only
- `/v1/rtsp/frame?index=N` returns 240×240 JPEG with circular mask; fetched continuously by Core 0 (`rtspNetTask`); `X-Stream-Count` response header updates `rtspStreamCount` for button cycling

**RTSP server pipeline (`server/routes/rtsp.py`):**
- Config loaded from `server/rtsp_config.json` (gitignored; copy from `rtsp_config-example.json`): array of streams with `url`, `label`, `mode` (`"fill"`, `"fit"`, or `"stretch"`), `grab_interval_s`; top-level `idle_timeout_s`; optional `overlay` object (`show_label`, `show_dots`, `label_y`, `dots_y`) — omitting `overlay` disables all overlay rendering
- `url` can be an RTSP URL (`rtsp://...`) or an absolute path to a local video file; local files are detected via `_is_local_file()` (no URL scheme prefix) and loop automatically on EOF by re-opening the container
- `RtspGrabber` per stream: daemon thread, opens source via PyAV (`av.open`; TCP transport options only for RTSP), decodes frames, JPEG-encodes every `grab_interval_s` (0 = every frame); caches latest frame in memory under a lock; logs INFO on start and idle stop
- Lazy start on first poll; self-terminates after `idle_timeout_s` of no `touch()` calls; restarts automatically on next poll
- Image processing: `resize_frame(img, mode)` → `apply_circular_mask(img)` → optional `composite_overlay(img, index, total, label)` → JPEG quality 75; circle radius 124 px
- `composite_overlay`: draws camera-select dots (y=204, r=3, gap=13) and label text (bottom at y=224) using NotoSansCJK-Medium 14 pt; only runs when `show_overlay` is true

**Lyrics server pipeline (`server/routes/lyrics.py`):**
- Synced lyrics fetched from lrclib.net (`GET /api/get?track_name=...&artist_name=...&duration=...`); parsed from LRC format (`[MM:SS.ms] text`) into sorted `(timestamp_ms, text)` tuples; persisted to `server/.lyrics_cache/{track_id}.json` (survives restarts); in-memory dict is L1 cache on top; `None` stored on miss so lrclib is not re-queried per poll
- Playback cache (`_playback_cache`) holds `track_id`, `track_name`, `artist_name`, `duration_ms`, `album_id`, `art_url`, `progress_ms`, `is_playing`, `cached_at`; updated by `spotify.py` on every `/v1/spotify/now-playing` call
- `GET /v1/spotify/lyrics/frame`: extrapolates `effective_progress = cached_progress + elapsed + LYRICS_LATENCY_OFFSET_MS`; selects prev/curr/next lines; opens cached raw album art (no gradient), applies Gaussian blur (radius 10) + 60% black dim overlay; calls `composite_lyrics()`; returns JPEG with `X-Next-Lyric-Ms` header
- Empty lyric lines (instrumental gaps) displayed as `♪` for all three slots; prev/next truncate with `…` at `LYRICS_CTX_MAX_WIDTH` (160 px); current line wraps with `LYRICS_LINE_SPACING` between wrapped rows
- `LYRICS_FONT_SIZE` (default 17 px) controls current line size; context lines scale to `round(size * 0.72)`; `LYRICS_LATENCY_OFFSET_MS` (default 150 ms) compensates for network + render + decode delay
