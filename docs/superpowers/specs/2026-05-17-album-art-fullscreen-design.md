# Full-Screen Album Art Display — Design Spec

**Date:** 2026-05-17
**Status:** Draft

## Context

The ESP32 + GC9A01 round display currently shows a colored rectangle placeholder for album art. This spec covers replacing it with actual Spotify album art rendered full-screen, with a gradient overlay and text composited server-side using Pillow.

---

## Architecture Overview

```
ESP32                              FastAPI Server                    Spotify API
 │                                     │                                │
 │── GET /now-playing ────────────────>│                                │
 │   (every 5s, lightweight)           │── GET currently-playing ──────>│
 │<── {track_id, is_playing,           │<── track data + art URL ──────│
 │     progress_ms, duration_ms} ─────│                                │
 │                                     │                                │
 │   [if track_id changed]             │                                │
 │── GET /now-playing/art ────────────>│                                │
 │                                     │── check cache (album_id) ─────│
 │                                     │── [miss] fetch JPEG ──────────>│
 │                                     │── Pillow: resize + gradient    │
 │                                     │   + text + circular mask       │
 │                                     │   + RGB565 conversion          │
 │<── raw RGB565 binary (115,200 B) ──│                                │
 │                                     │                                │
 │   [between polls]                   │                                │
 │   locally interpolate progress bar  │                                │
```

---

## Server Changes

### 1. Simplify `/v1/spotify/now-playing`

Reduce the response to only what the ESP32 needs for polling:

```json
{
  "track_id": "6rqhFgbbKwnb9MLmUQDhG6",
  "is_playing": true,
  "progress_ms": 45000,
  "duration_ms": 210000
}
```

Remove: `track`, `artist`, `album_art_url` (no longer needed by ESP32).

When nothing is playing, return:

```json
{
  "is_playing": false
}
```

### 2. New endpoint: `GET /v1/spotify/now-playing/art`

Returns a raw RGB565 binary image (115,200 bytes = 240 × 240 × 2).

**Flow:**
1. Call Spotify API to get current track (reuses existing `_get_access_token()`)
2. Extract: album art URL, album ID, track name, artist name
3. Check cache for base image at `server/.album_art_cache/{album_id}.png`
4. On cache miss: fetch JPEG from Spotify, resize to 240×240, apply gradient overlay, apply circular mask, save base image to cache
5. On cache hit: load base image from cache
6. Composite track name + artist text onto a copy of the base image
7. Convert to RGB565, return as `application/octet-stream`
8. Return `204 No Content` if nothing is playing

**Cache:**
- Location: `server/.album_art_cache/`
- Key: Spotify album ID (e.g., `4aawyAB9vmqN3uQ7FjRGTy.png`)
- Stores the base image (art + gradient + circular mask) as PNG — text is NOT cached since it varies per track
- Max ~50 entries; prune oldest on write
- Added to `.gitignore`

### 3. New dependency: Pillow

Add `Pillow>=11` to `pyproject.toml`.

---

## Image Compositing Pipeline (Pillow)

All processing happens in a helper module `server/routes/album_art.py`.

### Step 1: Fetch & resize
- Fetch album art JPEG from Spotify URL (httpx)
- Resize to 240×240 with `Image.LANCZOS` resampling

### Step 2: Gradient overlay
- Create a gradient alpha mask:
  - Rows 0–120 (top half): fully transparent
  - Rows 132–240: linear fade from 0% to 80% black opacity
- Composite a black layer with this gradient onto the album art

### Step 3: Circular mask
- Create a circle of radius 110px centered at (120, 120)
- Apply as alpha mask — pixels outside the circle become black
- Radius 110 instead of 120 avoids sub-pixel artifacts at the display edge

### Step 4: Save base image to cache
- Save as PNG for lossless re-use

### Step 5: Composite text (per-request, not cached)
- Track title: white, ~15px, semibold, horizontally centered, y ≈ 187 (78% of 240)
- Artist name: white at 70% opacity, ~12px, regular weight, centered, y ≈ 200
- Font: bundled TTF (e.g., Inter or system sans-serif fallback)

### Step 6: Convert to RGB565
- For each pixel (r, g, b): `((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)`
- Output as big-endian uint16 (TFT_eSPI default byte order)
- Total: 240 × 240 × 2 = 115,200 bytes

---

## Display Layout (Round 240×240)

```
        ╭───────────────────────╮
       ╱                         ╲
      │                           │
      │                           │
      │       Album Art           │  ← full 240×240, clipped to circle
      │     (from server)         │
      │                           │
      │                           │
      │   ─ ─ gradient fade ─ ─   │  ← starts ~55% height
      │                           │
      │      Track Title          │  ← white, ~78% from top (baked in image)
      │      Artist Name          │  ← white 70% opacity (baked in image)
      │                           │
      │   ▰▰▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱   │  ← progress bar (drawn by ESP32)
      │                           │
       ╲                         ╱
        ╰───────────────────────╯
```

**Progress bar (ESP32-drawn):**
- Position: centered at y ≈ 210 (88% from top)
- Width: 160px (40px margin each side), height: 3px
- Background: white at 25% opacity → RGB565 `0x39C7`
- Fill (playing): white at 90% opacity → RGB565 `0xE71C`
- Fill (paused): white at 25% opacity (same as background — bar appears empty)
- Rounded ends not feasible at 3px height — flat is fine

---

## ESP32 Firmware Changes

### Simplified `TrackState`

```cpp
struct TrackState {
    bool     is_playing  = false;
    String   track_id    = "";
    uint32_t progress_ms = 0;
    uint32_t duration_ms = 0;
};
```

### Polling logic

- **Regular poll:** every 5s (same as current)
- **End-of-song poll:** when estimated progress (`progress_ms + (millis() - lastFetchMs)`) reaches `duration_ms`, immediately poll `/now-playing` to check for track change — don't wait for the next 5s tick
- After an end-of-song poll, reset the regular 5s timer to avoid a double poll

### Modified `fetchNowPlaying()`

- Parse simplified JSON: `track_id`, `is_playing`, `progress_ms`, `duration_ms`
- If `track_id` changed → call `fetchAlbumArt()`
- If `is_playing` changed → redraw progress bar with new color

### New `fetchAlbumArt()`

- `GET /v1/spotify/now-playing/art`
- Stream response directly to display using `tft.pushImage()` row by row
- Each row = 480 bytes (240 pixels × 2 bytes RGB565)
- Read from HTTP stream in 480-byte chunks, push each row to display
- No full-image buffer in RAM — keeps memory usage at ~500 bytes

### Modified `drawTick()`

- Only draws the progress bar — no longer redraws time text (it's baked in the image)
- Progress bar sits in the fully darkened gradient region, so drawing over it is clean

### Modified `drawNowPlaying()`

- For idle state (nothing playing): `fillScreen(TFT_BLACK)` with centered "Not playing" text (same as current)
- For active state: replaced by `fetchAlbumArt()` + progress bar draw

---

## Idle / Error States

| State | Display |
|-------|---------|
| Nothing playing | Black screen, "Not playing" centered (current behavior) |
| Art fetch failed | Keep previous image on screen, log error to Serial |
| WiFi disconnected | Keep last display, reconnect in background |

---

## Files Changed

### Server
- `server/routes/spotify.py` — simplify `/now-playing` response, add `/now-playing/art` endpoint
- `server/routes/album_art.py` — new module: image fetch, Pillow compositing pipeline, cache management
- `server/main.py` — no changes needed (album_art is internal to the spotify router)
- `server/pyproject.toml` — add `Pillow>=11`
- `.gitignore` — add `server/.album_art_cache/`

### Firmware
- `src/main.cpp` — simplified TrackState, streaming art fetch, updated rendering logic

### Assets
- `server/fonts/` — bundled TTF font for text rendering (Inter or similar)

---

## Verification

1. `cd server && uv sync` — installs Pillow
2. `uv run uvicorn main:app --host 0.0.0.0 --port 7333 --reload` — server starts
3. `curl http://localhost:7333/v1/spotify/now-playing` — returns simplified JSON
4. `curl -o art.bin http://localhost:7333/v1/spotify/now-playing/art` — returns 115,200 bytes
5. `pio run --target upload` — firmware compiles and flashes
6. Play a Spotify track → album art appears full-screen within 5s
7. Skip track → new art loads within 5s
8. Pause → progress bar turns grey
9. Check `server/.album_art_cache/` → cached base images present
10. `pio device monitor` — no errors, memory usage stable
