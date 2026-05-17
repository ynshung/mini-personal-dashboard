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
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
WIFI_SSID=your_network_name
WIFI_PASSWORD=your_wifi_password
```

- `API_KEY` — used by the ESP32 to authenticate requests (set to any secret string)
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` — from your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
- `WIFI_SSID` / `WIFI_PASSWORD` — for the ESP32 to connect to your network

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
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
WIFI_SSID=your_network_name
WIFI_PASSWORD=your_wifi_password
```

| Variable | Description |
|---|---|
| `API_KEY` | Required. All endpoints (except Spotify OAuth) require `X-API-Key` header matching this value. |
| `SPOTIFY_CLIENT_ID` | From your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) |
| `SPOTIFY_CLIENT_SECRET` | From your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) |
| `WIFI_SSID` | Wi-Fi network name for the ESP32 |
| `WIFI_PASSWORD` | Wi-Fi password for the ESP32 |

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

Returns the currently playing Spotify track.

**Response (playing)**

```json
{
  "is_playing": true,
  "track": "Bohemian Rhapsody",
  "artist": "Queen",
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
| `is_playing` | `bool` | Whether a track is currently playing |
| `track` | `string \| null` | Track name |
| `artist` | `string \| null` | Artist name(s), comma-separated |
| `progress_ms` | `int \| null` | Playback position in milliseconds |
| `duration_ms` | `int \| null` | Total track duration in milliseconds |
| `album_art_url` | `string \| null` | Album art URL (smallest image ≥ 240px wide, typically 300×300) |

**Error responses**

| Status | Cause |
|---|---|
| `401` | Not authorized — visit `/v1/spotify/auth` |
| `500` | Missing `SPOTIFY_CLIENT_ID` or `SPOTIFY_CLIENT_SECRET` in `.env` |
| `502` | Unexpected response from Spotify API |
