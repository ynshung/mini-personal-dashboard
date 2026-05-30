# Synced Lyrics Feature Design

**Date:** 2026-05-30
**Status:** Approved

## Overview

Display time-synced lyrics on the Spotify screen of the 240×240 round GC9A01 display. When lyrics are available for the current track, the album art view is automatically replaced with a 3-line lyrics overlay rendered server-side as a JPEG. When no lyrics are available, the existing album art + track/artist text view is unchanged.

Lyrics are sourced from [lrclib.net](https://lrclib.net/docs) (public API, no key required, synced LRC format).

---

## Server

### New route: `server/routes/lyrics.py`

Registered in `main.py` under `/v1`.

#### `GET /v1/spotify/lyrics/frame`

Returns a 240×240 JPEG of the current lyric frame.

**No query parameters.** The server uses its internally cached playback state (updated on each `/v1/spotify/now-playing` call) to extrapolate the current `progress_ms`:

```
effective_progress = cached_progress_ms + (now - cached_at)   # if is_playing
effective_progress += LYRICS_LATENCY_OFFSET_MS
```

**Response:**
- Body: JPEG image, 240×240, quality 75
- Header: `X-Next-Lyric-Ms: <ms>` — milliseconds until the next lyric line timestamp. ESP waits this long before fetching the next frame.

**Fallback behaviour:**
- No synced lyrics found on lrclib.net → 404 (ESP falls back to album art mode)
- No active track → 204

#### Lyrics cache

Parsed lyrics (list of `(timestamp_ms, text)` tuples) are cached **in memory** per `track_id`. No disk cache. Cache is cleared on server restart; lrclib is re-queried on cache miss.

#### lrclib.net lookup

Query by `artist_name`, `track_name`, and `duration` (from Spotify metadata cached on last now-playing poll). Use synced lyrics only (`syncedLyrics` field). If only plain lyrics are returned, treat as not found.

### Playback state cache

A module-level dict defined and updated in `spotify.py`, imported by `lyrics.py`. Updated on each `/v1/spotify/now-playing` response:

```python
_playback_cache = {
    "track_id": str,
    "track_name": str,
    "artist_name": str,
    "duration_ms": int,
    "album_id": str,
    "art_url": str,
    "progress_ms": int,
    "is_playing": bool,
    "cached_at": float,   # time.time()
}
```

### Extended `/v1/spotify/now-playing` response

Adds `has_lyrics: bool` — determined by checking lrclib.net for the current track (result cached alongside parsed lyrics). The lrclib lookup is synchronous with a 3 s timeout and only occurs on track change (cache hit for subsequent polls of the same track). First `now-playing` response for a new track may be slightly slower.

```json
{
  "track_id": "...",
  "is_playing": true,
  "progress_ms": 42000,
  "duration_ms": 210000,
  "has_lyrics": true
}
```

---

## Lyrics rendering

### Image pipeline (per request, no caching)

1. Get base image via `fetch_and_build_base(art_url, album_id)` — uses disk cache if available, fetches from Spotify CDN otherwise. Returns 240×240 PIL image with existing bottom gradient and circular mask applied.
2. Apply **Gaussian blur** — radius 10px (`ImageFilter.GaussianBlur(radius=10)`)
3. Apply **full-screen black overlay** — 60% opacity (uniform, covers full 240×240)
4. Re-apply **circular mask** — radius 120px, to clean up any blur softening at circle edges
5. Call `composite_lyrics(img, prev, curr, next)` → JPEG encode at quality 75

### `composite_lyrics(base, prev, curr, next)`

New function in `album_art.py` alongside `composite_text()`.

- All three lines centered horizontally
- Stacked vertically, centered around the image midpoint (y=120)
- **`curr`**: white (`#ffffff`), bold, size = `LYRICS_FONT_SIZE` (default 15px), wraps
- **`prev` / `next`**: white at 35% opacity, size = `round(LYRICS_FONT_SIZE * 0.72)`, wraps
- Line spacing: 4px gap between lines
- Horizontal padding: 22px each side (196px usable width)
- **Instrumental gap**: `curr` is a dim `♪` (`rgba(255,255,255,0.3)`), `prev`/`next` show neighbouring lines normally
- **No previous line** (first lyric): prev slot rendered invisible (empty string, same height), layout stays stable

### Line selection

Given `effective_progress` and the sorted list of `(timestamp_ms, text)` tuples:

- `curr` = last line whose timestamp ≤ `effective_progress`
- `prev` = line before `curr` (or empty if none)
- `next` = line after `curr` (or empty if none)
- `X-Next-Lyric-Ms` = `next.timestamp_ms - effective_progress` (or a large value like 60000 if no next line)

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `LYRICS_FONT_SIZE` | `15` | Current lyric line font size in px |
| `LYRICS_LATENCY_OFFSET_MS` | `150` | Added to extrapolated progress before line selection |

Context line size = `round(LYRICS_FONT_SIZE * 0.72)` — no separate config.

Blur radius (10px) and dim opacity (60%) are hardcoded constants.

---

## Firmware (`src/main.cpp`)

### New state (Spotify screen)

```cpp
bool      lyricsMode       = false;
uint32_t  nextLyricFetchAt = 0;
```

### Behaviour

**On track change** (detected via `track_id` mismatch in now-playing poll):
- Read `has_lyrics` from JSON response
- If `has_lyrics`: set `lyricsMode = true`, set `nextLyricFetchAt = 0` (fetch immediately)
- If not: set `lyricsMode = false`, fetch album art as usual

**Main loop (SPOTIFY screen, `lyricsMode == true`):**
```
if millis() >= nextLyricFetchAt:
    fetch GET /v1/spotify/lyrics/frame
    decode JPEG via TJpgDec → render to display
    read X-Next-Lyric-Ms header → nextLyricFetchAt = millis() + nextLyricMs
```

**On pause/resume** (detected from `is_playing` flip in now-playing poll):
- Set `nextLyricFetchAt = 0` to trigger an immediate re-fetch with updated server state

**Progress bar**: unchanged — still drawn locally by ESP at (40, 210).

**Button handling**: unchanged — single click = play/pause, double = next, long = prev.

**No album art fetch** while `lyricsMode == true` (skipped, not cleared).

### No new Core 0 task

Lyrics frame fetches are infrequent (every 2–5 s). A brief blocking HTTP fetch on Core 1 is acceptable and consistent with existing Spotify polling behaviour.

---

## Fallback & error handling

| Scenario | Behaviour |
|---|---|
| lrclib returns no synced lyrics | `has_lyrics: false` → album art mode unchanged |
| `/v1/spotify/lyrics/frame` returns 404/error | ESP resets `lyricsMode = false`, shows album art |
| Album art cache miss on lyrics request | Server re-fetches art from Spotify before blurring |
| Track has no album art | Server returns 204; ESP stays on previous frame |
| `X-Next-Lyric-Ms` missing from response | ESP retries after 1000ms |

---

## Files changed

| File | Change |
|---|---|
| `server/routes/lyrics.py` | New — lyrics frame endpoint + lrclib client + playback cache |
| `server/routes/album_art.py` | Add `composite_lyrics()` function |
| `server/routes/spotify.py` | Export playback cache update; add `has_lyrics` to now-playing response |
| `server/main.py` | Register lyrics router |
| `src/main.cpp` | Add `lyricsMode`, `nextLyricFetchAt`, lyrics fetch in Spotify screen loop |
| `.env.example` | Document `LYRICS_FONT_SIZE`, `LYRICS_LATENCY_OFFSET_MS` |
