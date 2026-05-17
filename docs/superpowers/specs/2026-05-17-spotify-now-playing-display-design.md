# Spotify Now Playing Display — Design Spec

**Date:** 2026-05-17
**Status:** Approved

## Context

The ESP32 firmware currently polls the `/v1/spotify/now-playing` endpoint and blinks an LED. The GC9A01 240×240 round TFT is wired and working (verified with Hello World). This spec covers replacing the test code with a Spotify now-playing UI.

---

## Layout

240×240 circular display. All coordinates are for the 240×240 pixel space.

```
        ┌─────────────────────┐
        │   [ arc ring ]      │  ← 5px wide, radius ~112, centered at (120,120)
        │                     │
        │   ┌─────────┐       │
        │   │  96×96  │       │  ← album art placeholder (solid color square)
        │   │ (color) │       │    top: ~26px, centered horizontally
        │   └─────────┘       │
        │                     │
        │   Track Name        │  ← bold, size 2, y ~138px
        │   Artist Name       │  ← grey, size 1, y ~158px
        │   1:24 / 3:20       │  ← dim, size 1, y ~174px
        │                     │
        └─────────────────────┘
```

---

## States

| State | Arc color | Art | Text |
|-------|-----------|-----|------|
| Playing | Green `#1DB954` | Solid accent color | White track, grey artist |
| Paused | Grey `#555555` | Same color, dimmed | Dimmed white track |
| Idle (nothing playing) | Dark grey `#222222` | Dark `#1A1A1A` | "Not playing" in grey, no artist/time |

---

## Data Flow

```
loop() every 5s
  → fetchNowPlaying()
      → HTTP GET /v1/spotify/now-playing
      → parse: is_playing, track, artist, progress_ms, duration_ms
  → if track or is_playing changed → full redraw
  → else if progress changed → redraw arc only
```

Only redraw what changed to avoid flicker.

---

## Rendering

- **Background:** `fillScreen(TFT_BLACK)` on full redraws only
- **Arc ring:** `tft.drawArc(120, 120, 116, 111, 0, progress_deg, color, TFT_BLACK)` where `progress_deg = (progress_ms / duration_ms) * 360`. Start angle 0 = top (270° in TFT_eSPI convention — adjust accordingly).
- **Art placeholder:** `tft.fillRoundRect(cx-48, 26, 96, 96, 8, accent_color)` where accent color cycles through a small palette per track (hash of track name → index).
- **Track text:** `tft.setFont()` + `tft.drawString()`, truncated to fit 180px width.
- **Artist text:** smaller, grey.
- **Time:** `mm:ss / mm:ss` format, dimmed.

---

## Files Changed

- `src/main.cpp` — all display logic lives here (single file, keep it simple for now)
  - Remove: Hello World test code in `setup()`
  - Remove: LED blink logic (LED_PIN=22 is unwired)
  - Add: `drawNowPlaying()` — full redraw
  - Add: `drawArcProgress()` — arc-only redraw
  - Add: `msToTime()` helper — formats milliseconds to `mm:ss`
  - Update: `fetchNowPlaying()` — store progress_ms, duration_ms; detect state changes
- `platformio.ini` — no new libraries needed (TFT_eSPI already includes `drawArc`)

---

## Verification

1. `pio run --target upload` — should compile cleanly
2. On boot: display shows idle state ("Not playing") while WiFi connects, then updates within 5s
3. Start a Spotify track: art placeholder appears, track/artist shown, arc fills green
4. Pause: arc turns grey, art dims
5. Skip track: full redraw with new info within 5s
6. `pio device monitor` — confirm no HTTP errors, JSON parse errors, or stack overflows
