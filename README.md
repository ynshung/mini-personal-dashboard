# Mini Personal Dashboard

![Spotify screen](docs/assets/spotify.jpeg)![CC Usage screen](docs/assets/cc.jpeg)

A personal dashboard running on an ESP32 with a 240×240 round GC9A01 display. Shows Spotify now-playing with album art and playback controls, Claude Code plan usage, and RTSP camera feeds. Consists of a local FastAPI server (macOS) and ESP32 firmware that polls it over Wi-Fi.

## Disclaimer

This is a personal project which is heavily developed using Claude Code. Please be aware that it may contain bugs or vulnerabilities, and there may be new breaking changes at any time. Use at your own risk, and feel free to review or fork the code to suit it for yourself.

## Features

- **Spotify Player** — now-playing display with playback controls (play/pause, next, previous)
- **Claude Usage Monitor** — real-time Claude Code plan usage (5-hour session and 7-day windows), with reset timers and expected usage indicators
- **RTSP Camera Viewer** — live camera feed display with multi-stream support; server proxies H.264 RTSP streams as JPEG snapshots
- **RevenueCat Dashboard** *(TODO)* — subscription revenue metrics

## Get Started

### 1. Create `.env`

Copy the template below into a `.env` file in the project root:

```env
API_KEY=your_secret_key
SERVER_URL=http://192.168.1.100:7333
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
WIFI_SSID=your_network_name
WIFI_PASSWORD=your_wifi_password
DEVELOPMENT_MODE=false
```

- `API_KEY` — used by the ESP32 to authenticate requests (set to any secret string)
- `SERVER_URL` — base URL of the server (e.g. `http://192.168.1.100:7333`), used by the ESP32
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` — from your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
- `WIFI_SSID` / `WIFI_PASSWORD` — for the ESP32 to connect to your network
- `DEVELOPMENT_MODE` — set to `true` to skip API key checks (for local development only, default `false`)

### 2. Install & run the server

```bash
cd server
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 7333
```

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

### 3. Configure RTSP streams (optional)

Copy the example config and fill in your camera URLs:

```bash
cp server/rtsp_config.json.example server/rtsp_config.json
```

Edit `server/rtsp_config.json`:

```json
{
  "idle_timeout_s": 30,
  "overlay": {
    "show_label": true,
    "show_dots": true,
    "label_y": 16,
    "dots_y": 218
  },
  "streams": [
    {
      "url": "rtsp://user:pass@192.168.1.100:554/stream1",
      "label": "Front Door",
      "mode": "fill",
      "grab_interval_s": 1.0
    }
  ]
}
```

- `mode`: `"fill"` = center-crop to circle; `"fit"` = letterbox
- `grab_interval_s`: server-side frame capture rate in seconds
- `idle_timeout_s`: seconds before the server stops a stream with no active polling (recommended: 30+)
- `overlay`: omit this section to disable all overlay rendering; when present:
  - `show_label`: show the stream label text (default `true`)
  - `show_dots`: show the camera selection dots indicator (default `true`)
  - `label_y`: top edge of the label text in pixels (default `16`)
  - `dots_y`: center y of the dots indicator in pixels (default `218`)

This file is gitignored (may contain credentials in URLs).

### 4. Authorize Spotify

1. In your Spotify app settings, add `http://127.0.0.1:7333/v1/spotify/callback` as a Redirect URI
2. Visit `http://127.0.0.1:7333/v1/spotify/auth` in your browser and approve access
3. Tokens are saved to `server/.spotify_tokens.json` and refresh automatically

### 5. Flash the firmware

```bash
pio run --target upload
```

Requires [PlatformIO](https://platformio.org/). The build reads `.env` automatically for Wi-Fi and API key config.

## Firmware

### Requirements

- [PlatformIO](https://platformio.org/) CLI or IDE extension

### Build & Flash

```bash
pio run                  # build firmware
pio run --target upload  # build and flash to device
pio device monitor       # open serial monitor (115200 baud)
```

Board: ESP32 (`esp32dev`), framework: Arduino. Source in `src/main.cpp`.

### Display Wiring (GC9A01 → ESP32)

| GC9A01 pin | ESP32 GPIO |
|------------|-----------|
| MOSI / SDA | 23        |
| SCLK / SCL | 18        |
| CS         | 15        |
| DC / RS    | 2         |
| RST        | 4         |
| VCC        | 3.3 V     |
| GND        | GND       |

### Button Controls

| GPIO | Gesture | Action |
|---|---|---|
| 19 | Single click | Toggle play/pause (Spotify) / Next stream (RTSP) |
| 19 | Double click | Next track (Spotify) / Previous stream (RTSP) |
| 19 | Long press | Previous track (Spotify) / No-op (RTSP) |
| 21 | Single click | Cycle screens forward: Spotify → RTSP → CC Usage → … |
| 21 | Double click | Cycle screens backward: Spotify → CC Usage → RTSP → … |
| 21 | Long press | Restart device |

### Display UI

The display has three screens cycled by GPIO 21.

**Spotify screen** — polls `/v1/spotify/now-playing` every 5 seconds:

- **Full-screen album art** — fetched from `/v1/spotify/now-playing/art/jpeg` as a composited JPEG (7–29 KB), decoded on-device by TJpg_Decoder (only on track change)
- **Track name** and **artist** — rendered server-side with Pillow (Inter font) in a gradient overlay at the bottom of the album art
- **Progress bar** — 160×3 px at y=210, white fill when playing; interpolated locally every 250 ms between polls
- **End-of-song detection** — immediately polls when estimated progress reaches song duration

**RTSP Camera screen** — polls `/v1/rtsp/frame?index=N` every 1 second:

- **Live camera frame** — server decodes H.264 RTSP stream, resizes to 240×240 with circular mask, returns as JPEG
- **Stream label and dots indicator** — composited server-side onto the JPEG (controlled by `show_overlay` in config); label shown near the bottom, dots above it indicate selected stream out of total
- **Multi-stream navigation** — single/double click GPIO 19 to cycle next/previous stream
- Configure streams in `server/rtsp_config.json` (copy from `server/rtsp_config.json.example`)

**CC Usage screen** (default) — polls `/v1/cc-usage` every 10 seconds:

- **Claude logo** — shown at the top in orange (#d6755a)
- **5-hour** and **7-day** Claude Code plan utilization, each showing percentage, a color-coded progress bar, and time until reset
- **Last refreshed** label at the bottom (e.g. "Just now", "A moment ago", "A minute ago")
- Bar/text color: white (0–60%), orange (61–99%), red (100%)
- Shows `--` when a usage window is not applicable to the current plan
- Server cached for 2 minutes to avoid hitting 429 rate limits

---

## Server

### Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- macOS — required for the CC Usage feature (reads Claude Code OAuth token from the macOS Keychain) (Pretty sure can be used in Linux with from .claude dir)

### Setup & Run

```bash
cd server
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 7333
```

### Environment Variables

Create a `.env` file in the project root:

```env
API_KEY=your_secret_key
SERVER_URL=http://192.168.1.100:7333
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
WIFI_SSID=your_network_name
WIFI_PASSWORD=your_wifi_password
DEVELOPMENT_MODE=false
```

| Variable | Description |
|---|---|
| `API_KEY` | Required (unless `DEVELOPMENT_MODE` is set). All endpoints (except Spotify OAuth) require `X-API-Key` header matching this value. |
| `SERVER_URL` | Base URL of the dashboard server (used by ESP32 firmware) |
| `SPOTIFY_CLIENT_ID` | From your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) |
| `SPOTIFY_CLIENT_SECRET` | From your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) |
| `WIFI_SSID` | Wi-Fi network name for the ESP32 |
| `WIFI_PASSWORD` | Wi-Fi password for the ESP32 |
| `DEVELOPMENT_MODE` | Set to `true` to disable API key authentication (for local development only) |

---

## Endpoints

### `GET /v1/rtsp/frame?index=N`

Returns the latest JPEG frame from the RTSP stream at position `N` in `rtsp_config.json`. Lazily starts a background grabber thread on first request; shuts it down after `idle_timeout_s` seconds of no polling.

**Query parameters**

| Parameter | Type | Description |
|---|---|---|
| `index` | `int` (default `0`) | Zero-based stream index |

**Response:** `image/jpeg` — 240×240 px with circular mask applied

**Response headers**

| Header | Description |
|---|---|
| `X-Stream-Count` | Total number of configured streams |

Returns a black circular placeholder JPEG while the grabber is starting up (before the first frame is available). If `show_overlay` is enabled, the label and dots indicator are composited into the image before encoding.

**Error responses**

| Status | Cause |
|---|---|
| `400` | `index` out of range |
| `503` | No streams configured (missing or empty `rtsp_config.json`) |

---

### `GET /v1/cc-usage`

Returns Claude Code plan usage for the current 5-hour session window.

The token is read automatically from the macOS Keychain (`Claude Code-credentials`). If the token is expired, re-login via Claude Code.

**Response**

```json
{
  "five_hour": {
    "utilization": 34.0,
    "resets_at": "1 hr 10 min",
    "time_pct": 80.0
  },
  "seven_day": {
    "utilization": 66.0,
    "resets_at": "Tue 6:00 PM",
    "time_pct": 42.3
  },
  "refreshed_ago": "A moment ago"
}
```

| Field | Type | Description |
|---|---|---|
| `five_hour.utilization` | `float \| null` | 5-hour session usage as a percentage (0–100). `null` if not applicable to the plan. |
| `five_hour.resets_at` | `string \| null` | Time until the 5-hour window resets, e.g. `"1 hr 10 min"`. `null` if not applicable. |
| `five_hour.time_pct` | `float \| null` | Percentage of the 5-hour window elapsed (0–100), e.g. `80.0` means 1 hr remaining. `null` if not applicable. |
| `seven_day.utilization` | `float \| null` | 7-day weekly usage as a percentage (0–100). `null` if not applicable to the plan. |
| `seven_day.resets_at` | `string \| null` | Time until the weekly window resets. Under 24 h: `"X hr Y min"`. Over 24 h: day and local time e.g. `"Tue 6:00 PM"`. `null` if not applicable. |
| `seven_day.time_pct` | `float \| null` | Percentage of the 7-day window elapsed (0–100). `null` if not applicable. |
| `refreshed_ago` | `string` | How long ago the upstream data was fetched: `"Just now"` (<30 s), `"A moment ago"` (<60 s), `"A minute ago"` (<2 min), `"2 minutes ago"` (<3 min), `">3 minutes ago"`. |

**Error responses**

| Status | Cause |
|---|---|
| `401` | Keychain lookup failed or token expired |
| `502` | Unexpected response from Anthropic API |

---

### `GET /v1/spotify/auth`

Redirects to Spotify's authorization page. Visit this once in a browser to authorize the server. After approval, Spotify redirects to `/v1/spotify/callback` and tokens are saved automatically to `server/.spotify_tokens.json`.

**Setup (one-time):**
1. Add `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` to `.env`
2. Ensure `http://127.0.0.1:7333/v1/spotify/callback` is set as a Redirect URI in your Spotify app
3. Start the server and visit `http://127.0.0.1:7333/v1/spotify/auth` in your browser

---

### `POST /v1/spotify/toggle`

Toggles play/pause on the active Spotify device. Queries the current playback state and issues the appropriate play or pause command.

### `POST /v1/spotify/next`

Skips to the next track.

### `POST /v1/spotify/previous`

Skips to the previous track.

---

### `GET /v1/spotify/now-playing`

Returns lightweight playback state for polling.

**Response (playing)**

```json
{
  "track_id": "6rqhFgbbKwnb9MLmUQDhG6",
  "is_playing": true,
  "progress_ms": 83000,
  "duration_ms": 354000
}
```

**Response (nothing playing)**

```json
{"is_playing": false}
```

| Field | Type | Description |
|---|---|---|
| `track_id` | `string` | Spotify track ID |
| `is_playing` | `bool` | Whether a track is currently playing |
| `progress_ms` | `int` | Playback position in milliseconds |
| `duration_ms` | `int` | Total track duration in milliseconds |

**Error responses**

| Status | Cause |
|---|---|
| `401` | Not authorized — visit `/v1/spotify/auth` |
| `500` | Missing `SPOTIFY_CLIENT_ID` or `SPOTIFY_CLIENT_SECRET` in `.env` |
| `502` | Unexpected response from Spotify API |

---

### `GET /v1/spotify/now-playing/art/jpeg`

Returns the current track's album art as a composited JPEG image. The server fetches album art from Spotify, resizes to 240×240, applies a gradient overlay and circular mask, renders track/artist text, and encodes to JPEG (quality 75). Base images (art + gradient + mask) are cached by album ID in `server/.album_art_cache/`; text is composited per-request.

Returns `204 No Content` if nothing is playing.

**Response:** `image/jpeg` — typically 7–29 KB

**Error responses**

| Status | Cause |
|---|---|
| `204` | Nothing playing or no album art available |
| `401` | Not authorized — visit `/v1/spotify/auth` |
| `502` | Unexpected response from Spotify API |

---

### `GET /v1/spotify/now-playing/art`

Returns the same composited image as `/art/jpeg` but as a raw 240×240 RGB565 binary (115,200 bytes, big-endian). Legacy endpoint — the ESP32 firmware now uses `/art/jpeg`.

Returns `204 No Content` if nothing is playing.

**Response:** raw `application/octet-stream` — 115,200 bytes
