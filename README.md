# Mini Personal Dashboard

A local HTTP server that exposes dashboard data for a NodeMCU + GC9A01 display, and ESP32 firmware to drive it.

## Features

- **Spotify Player** — now-playing display with playback controls (play/pause, next, previous)
- **Claude Usage Monitor** — real-time Claude Code plan usage (5-hour session and 7-day windows)
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
DEVELOPMENT_MODE=true
```

- `API_KEY` — used by the ESP32 to authenticate requests (set to any secret string)
- `SERVER_URL` — base URL of the server (e.g. `http://192.168.1.100:7333`), used by the ESP32
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` — from your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
- `WIFI_SSID` / `WIFI_PASSWORD` — for the ESP32 to connect to your network
- `DEVELOPMENT_MODE` — set to `true` to skip API key checks (for local development)

### 2. Install & run the server

```bash
cd server
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 7333
```

Requires Python 3.14+ and [uv](https://github.com/astral-sh/uv).

### 3. Authorize Spotify

1. In your Spotify app settings, add `http://127.0.0.1:7333/v1/spotify/callback` as a Redirect URI
2. Visit `http://127.0.0.1:7333/v1/spotify/auth` in your browser and approve access
3. Tokens are saved to `server/.spotify_tokens.json` and refresh automatically

### 4. Flash the firmware

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

### Display UI

The ESP32 polls `/v1/spotify/now-playing` every 5 seconds and renders:

- **Full-screen album art** — fetched from `/v1/spotify/now-playing/art` as a pre-composited RGB565 image, streamed row-by-row to the display (only on track change)
- **Track name** and **artist** — rendered server-side with Pillow (Inter font) in a gradient overlay at the bottom of the album art
- **Progress bar** — 160×3 px at y=210, white fill when playing; interpolated locally every 250 ms between polls
- **End-of-song detection** — immediately polls when estimated progress reaches song duration

---

## Server

### Requirements

- Python 3.14+
- [uv](https://github.com/astral-sh/uv)
- macOS (Keychain access required for Claude Code credentials)

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
DEVELOPMENT_MODE=true
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

### `GET /v1/cc-usage`

Returns Claude Code plan usage for the current 5-hour session window.

The token is read automatically from the macOS Keychain (`Claude Code-credentials`). If the token is expired, re-login via Claude Code.

**Response**

```json
{
  "five_hour": {
    "utilization": 34.0,
    "resets_at": "1 hr 10 min"
  },
  "seven_day": {
    "utilization": 66.0,
    "resets_at": "45 hr 10 min"
  }
}
```

| Field | Type | Description |
|---|---|---|
| `five_hour.utilization` | `float \| null` | 5-hour session usage as a percentage (0–100). `null` if not applicable to the plan. |
| `five_hour.resets_at` | `string \| null` | Time until the 5-hour window resets, e.g. `"1 hr 10 min"`. `null` if not applicable. |
| `seven_day.utilization` | `float \| null` | 7-day weekly usage as a percentage (0–100). `null` if not applicable to the plan. |
| `seven_day.resets_at` | `string \| null` | Time until the weekly window resets. Under 24 h: `"X hr Y min"`. Over 24 h: `"Sun 6:00 PM"` (local time). `null` if not applicable. |

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

### `POST /v1/spotify/play`

Resumes playback on the active Spotify device.

### `POST /v1/spotify/pause`

Pauses playback on the active Spotify device.

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

### `GET /v1/spotify/now-playing/art`

Returns the current track's album art as a pre-composited 240×240 RGB565 binary image (115,200 bytes). The server fetches the album art from Spotify, resizes it, applies a gradient overlay and circular mask, renders track/artist text, and converts to RGB565. Base images (art + gradient + mask) are cached by album ID in `server/.album_art_cache/`.

Returns `204 No Content` if nothing is playing.

**Response:** raw `application/octet-stream` — 115,200 bytes of big-endian RGB565 pixel data (240 rows × 240 pixels × 2 bytes).

**Error responses**

| Status | Cause |
|---|---|
| `204` | Nothing playing or no album art available |
| `401` | Not authorized — visit `/v1/spotify/auth` |
| `502` | Unexpected response from Spotify API |
