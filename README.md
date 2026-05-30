# Mini Personal Dashboard

![Spotify screen](docs/assets/spotify.jpeg)![CC Usage screen](docs/assets/cc.jpeg)

A personal dashboard running on an ESP32 with a 240×240 round GC9A01 display. Shows a live clock, Spotify now-playing synced lyrics with playback controls, Claude Code plan usage, RTSP camera feeds and video playback. Consists of a local FastAPI server (macOS) and ESP32 firmware that polls it over Wi-Fi.

## Disclaimer

> [!CAUTION]
> This is a personal project which is heavily developed using Claude Code. Please be aware that it may contain bugs or vulnerabilities, and there may be new breaking changes at any time. Use at your own risk, and feel free to review or fork the code to suit it for yourself.

## Features

- **Clock** — always-on NTP-synced digital clock (weekday, date, time); initial startup screen; falls back to clock when server is unreachable and auto-restores when it comes back
- **Spotify Player** — now-playing time-synced lyrics, album art and playback controls (play/pause, next, previous), 
- **Claude Usage Monitor** — real-time Claude Code plan usage (5-hour session and 7-day windows), with reset timers and expected usage indicators
- **RTSP Camera Viewer** — live camera feed display with multi-stream support; server proxies RTSP streams
- **Video Player** — local video file playback (e.g. for ambient videos)
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

# Optional — lyrics tuning
LYRICS_FONT_SIZE=17
LYRICS_LATENCY_OFFSET_MS=150
LYRICS_ROMAJI=false
```

- `API_KEY` — used by the ESP32 to authenticate requests (set to any secret string)
- `SERVER_URL` — base URL of the server (e.g. `http://192.168.1.100:7333`), used by the ESP32
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` — from your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
- `WIFI_SSID` / `WIFI_PASSWORD` — for the ESP32 to connect to your network
- `DEVELOPMENT_MODE` — set to `true` to skip API key checks (default `false`)
- `LYRICS_FONT_SIZE` — current lyric line font size in px (default `17`); context lines scale proportionally
- `LYRICS_LATENCY_OFFSET_MS` — ms added to playback position before lyric lookup to compensate for network + render delay (default `150`); increase if lyrics lag, decrease if they appear too early
- `LYRICS_ROMAJI` — set to `true` to convert Japanese lyrics to romaji (default `false`); detects Japanese by the presence of hiragana/katakana, English lines pass through unchanged

### 2. Install & run the server

```bash
cd server
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 7333
```

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

### 3. Configure RTSP streams / video files (optional)

Copy the example config and fill in your camera URLs or video file paths:

```bash
cp server/rtsp_config-example.json server/rtsp_config.json
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
      "grab_interval_s": 0.1
    },
    {
      "url": "/absolute/path/to/ambient.mjpeg",
      "label": "Ambient",
      "mode": "stretch",
      "grab_interval_s": 0.1
    }
  ]
}
```

- `url`: RTSP stream URL (`rtsp://...`) or an absolute path to a local video file — local files loop automatically
- `mode`: `"fill"` = center-crop to circle; `"fit"` = letterbox; `"stretch"` = stretch to fill (ignores aspect ratio); use `"stretch"` for pre-sized 240×240 files
- `show_overlay`: render the label and dots indicator onto the frame (default `true`); set to `false` to skip all overlay compositing for a stream
- `apply_resize`: resize/crop the frame to 240×240 (default `true`); set to `false` for pre-converted 240×240 videos to skip the resize step entirely
- `apply_mask`: apply a circular black mask to the frame (default `true`); set to `false` for pre-converted videos or when the display's physical round clip is sufficient
- `grab_interval_s`: server-side frame encode rate in seconds (`0` = encode every decoded frame, `0.1` = ~10 fps cap)
- `idle_timeout_s`: seconds before the server stops a stream with no active polling (recommended: 30+)
- `overlay`: omit this section to disable all overlay rendering; when present:
  - `show_label`: show the stream label text (default `true`)
  - `show_dots`: show the camera selection dots indicator (default `true`)
  - `label_y`: top edge of the label text in pixels (default `16`)
  - `dots_y`: center y of the dots indicator in pixels (default `218`)

> [!NOTE]
> This file is gitignored as it may contain credentials in RTSP URLs.

> [!TIP]
> For looping ambient video, pre-convert to 240×240 MJPEG for best performance:
> ```bash
> ffmpeg -i input.mp4 \
>   -vf "scale=240:240:force_original_aspect_ratio=increase:flags=lanczos,crop=240:240" \
>   -c:v mjpeg -q:v 5 -an \
>   ambient.mjpeg
> ```

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
| 19 | Single click | Toggle play/pause (Spotify) / Next stream (RTSP) / No-op (Clock) |
| 19 | Double click | Next track (Spotify) / Previous stream (RTSP) / No-op (Clock) |
| 19 | Long press | Previous track (Spotify) / No-op (RTSP, Clock) |
| 21 | Single click | Cycle screens forward: Clock → CC Usage → RTSP → Spotify → … |
| 21 | Double click | Cycle screens backward: Clock → Spotify → RTSP → CC Usage → … |
| 21 | Long press | Restart device |

### Display UI

The display has four screens cycled by GPIO 21.

**Clock screen** (default startup screen) — NTP-synced, updates every second:

- **Weekday** — e.g. "Monday" (grey)
- **Date** — e.g. "26 May 2026" (grey)
- **Time** — `HH:MM:SS` (white)
- **Server fallback** — automatically switches to this screen after 2 minutes of server unreachability and stays there permanently
- Timezone configured via `NTP_OFFSET_HOURS` in `src/main.cpp` (default `8.0f` = UTC+8; supports fractional offsets e.g. `-5.5`)

**Spotify screen** — polls `/v1/spotify/now-playing` every 5 seconds:

- **Full-screen album art** — fetched from `/v1/spotify/now-playing/art/jpeg` as a composited JPEG (7–29 KB), decoded on-device by TJpg_Decoder (only on track change)
- **Track name** and **artist** — rendered server-side with Pillow in a gradient overlay at the bottom of the album art
- **Synced lyrics** — when the current track has lyrics on [lrclib.net](https://lrclib.net), the album art view is automatically replaced with a 3-line lyrics display (previous / **current** / next); the current line is rendered bold and white, context lines are dimmer and truncated; background is a blurred + dimmed version of the album art; fetched per lyric line change rather than continuously
- **Progress bar** — 160×3 px at y=210, white fill when playing; interpolated locally between polls; redrawn within the same SPI transaction as each lyrics frame to prevent flicker
- **End-of-song detection** — immediately polls when estimated progress reaches song duration
- **Seek detection** — lyrics display re-syncs immediately when playback position jumps by more than 3 s

**RTSP Camera screen** — renders frames as fast as they arrive (dual-core: Core 0 fetches, Core 1 renders):

- **Live camera frame** — server decodes RTSP stream or local video file, resizes to 240×240 with circular mask, returns as JPEG; ESP32 decodes and blits each frame immediately
- **Local video looping** — local file paths (e.g. `/path/to/ambient.mjpeg`) are supported alongside RTSP URLs; files loop automatically on end-of-file
- **Stream label and dots indicator** — composited server-side onto the JPEG (controlled by `overlay` in config); label shown near top, dots near bottom indicate selected stream out of total
- **Multi-stream navigation** — single/double click GPIO 19 to cycle next/previous stream
- Configure streams in `server/rtsp_config.json` (copy from `server/rtsp_config-example.json`)

**CC Usage screen** — polls `/v1/cc-usage` every 10 seconds:

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

> [!IMPORTANT]
> The CC Usage feature requires macOS — it reads the Claude Code OAuth token from the macOS Keychain. All other features should work on Linux.

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
| `LYRICS_FONT_SIZE` | Current lyric line font size in px (default `17`); context lines scale to `round(size × 0.7)` |
| `LYRICS_LATENCY_OFFSET_MS` | ms added to playback position before lyric lookup to compensate for network + render + decode delay (default `150`) |
| `LYRICS_ROMAJI` | Set to `true` to convert Japanese lyrics to romaji; detects Japanese by hiragana/katakana presence, English lines pass through unchanged (default `false`) |

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

The token is read automatically from the macOS Keychain (`Claude Code-credentials`).

> [!NOTE]
> If the token is expired, re-login via Claude Code to refresh it.

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

Returns lightweight playback state for polling. Also updates the server-side playback cache used by the lyrics endpoint, and checks lrclib.net for synced lyrics on track change (synchronous, ~3 s timeout).

**Response (playing)**

```json
{
  "track_id": "6rqhFgbbKwnb9MLmUQDhG6",
  "is_playing": true,
  "progress_ms": 83000,
  "duration_ms": 354000,
  "has_lyrics": true
}
```

**Response (nothing playing)**

```json
{"is_playing": false, "has_lyrics": false}
```

| Field | Type | Description |
|---|---|---|
| `track_id` | `string` | Spotify track ID |
| `is_playing` | `bool` | Whether a track is currently playing |
| `progress_ms` | `int` | Playback position in milliseconds |
| `duration_ms` | `int` | Total track duration in milliseconds |
| `has_lyrics` | `bool` | Whether synced lyrics are available on lrclib.net for this track |

**Error responses**

| Status | Cause |
|---|---|
| `401` | Not authorized — visit `/v1/spotify/auth` |
| `500` | Missing `SPOTIFY_CLIENT_ID` or `SPOTIFY_CLIENT_SECRET` in `.env` |
| `502` | Unexpected response from Spotify API |

---

### `GET /v1/spotify/lyrics/frame`

Returns the current lyric frame as a 240×240 JPEG. The server blurs and dims the cached album art (no gradient — uniform background), then composites a 3-line lyrics overlay (previous / current / next lyric line). Lyric position is extrapolated from the cached playback state plus `LYRICS_LATENCY_OFFSET_MS`.

Returns `204 No Content` if nothing is playing. Returns `404` if no synced lyrics are available (ESP falls back to album art mode).

**Response:** `image/jpeg` — 240×240 px

**Response headers**

| Header | Description |
|---|---|
| `X-Next-Lyric-Ms` | Milliseconds until the next lyric line timestamp. ESP waits this long before fetching the next frame. |

---

### `GET /v1/spotify/now-playing/art/jpeg`

Returns the current track's album art as a composited JPEG image. The server fetches album art from Spotify, resizes to 240×240, applies a gradient overlay, renders track/artist text, and encodes to JPEG (quality 75). Raw base images are cached by album ID in `server/.album_art_cache/`; gradient and text are composited per-request.

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
