# Personal Dashboard — NodeMCU

A local HTTP server that exposes dashboard data for a NodeMCU display.

## Server

### Requirements

- Python 3.14+
- [uv](https://github.com/astral-sh/uv)
- macOS (Keychain access required for Claude Code credentials)

### Setup & Run

```bash
cd server
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 3737
```

### Environment Variables

Create a `.env` file in the project root:

```env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
```

These are available in your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).

### Testing

```bash
cd server
uv sync --group dev
uv run pytest tests/ -v
```

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
2. Ensure `http://127.0.0.1:3737/v1/spotify/callback` is set as a Redirect URI in your Spotify app
3. Start the server and visit `http://127.0.0.1:3737/v1/spotify/auth` in your browser

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
