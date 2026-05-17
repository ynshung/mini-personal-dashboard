# Button Playback Control Design

## Overview

Add a single-button hardware control to the ESP32 dashboard for Spotify playback. One three-pin button module (VCC/GND/SIG) wired to GPIO 19 provides Play/Pause, Next, and Back via gesture detection.

## Hardware Wiring

| Button pin | ESP32 |
|------------|-------|
| VCC | 3.3V |
| GND | GND |
| SIG | GPIO 19 |

The module has an onboard pull-up resistor. Signal is HIGH at rest, LOW on press.

## Gesture Mapping

| Gesture | Action |
|---------|--------|
| Single press | Play/Pause (toggle) |
| Double press | Next track |
| Long press | Previous track |

## Firmware Changes (`src/main.cpp`)

1. Add `OneButtonTiny` to `platformio.ini` library dependencies.
2. Declare `OneButtonTiny btn(19, true)` (active-low).
3. In `setup()`, register callbacks:
   - `btn.attachClick` → `sendCommand("/v1/spotify/toggle")`
   - `btn.attachDoubleClick` → `sendCommand("/v1/spotify/next")`
   - `btn.attachLongPressStart` → `sendCommand("/v1/spotify/previous")`
4. Call `btn.tick()` at the top of `loop()` on every iteration.
5. `sendCommand(path)` helper: fires an HTTP POST to `serverUrl + path` with the `X-API-Key` header. On success, waits 200ms then sets `lastPoll = 0` to trigger an immediate `fetchNowPlaying()` on the next loop iteration.

## Server Changes (`server/routes/spotify.py`)

- **Remove** `POST /spotify/play` and `POST /spotify/pause` (unused).
- **Add** `POST /spotify/toggle`:
  1. Gets access token via `_get_access_token()`.
  2. Fetches current playback state from `GET https://api.spotify.com/v1/me/player`.
  3. If `is_playing` → calls `PUT .../player/pause`; otherwise → calls `PUT .../player/play`.
  4. Returns `204 No Content`.

No changes to `main.py` — the spotify router is already registered.
