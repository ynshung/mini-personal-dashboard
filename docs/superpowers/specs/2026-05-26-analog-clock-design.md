# Analog Clock Screen Design

**Date:** 2026-05-26
**Status:** Approved
**Scope:** Replace the digital clock rendering with a minimal analog clock face on the ESP32 CLOCK screen

---

## Summary

Redesign the existing digital clock screen (weekday + date + HH:MM:SS text) into a minimal analog clock with smooth sweeping second hand. The clock is rendered entirely on-device using a full-screen `TFT_eSprite` for flicker-free animation at 25 FPS. No server-side changes. All existing CLOCK screen behavior (idle fallback, ping recovery, screen cycling) is preserved.

## Requirements

- Minimal analog clock on the 240×240 round GC9A01 display
- 12 short radial tick lines as hour markers
- White hour and minute hands
- Red accent second hand with smooth sweeping motion (~25 FPS)
- Short counterweight tail on the second hand
- Small date text below center (e.g. "Mon 26")
- Flicker-free rendering via TFT_eSprite
- No server dependency for clock rendering (NTP time only)

## Rendering Approach: Full-Screen Sprite

A `TFT_eSprite` of 240×240 at 16-bit color depth (~113 KB) is allocated on CLOCK screen activation and persists while the screen is active. Each frame redraws the entire sprite and pushes it to the display in a single SPI transaction.

### Frame pipeline (every 40 ms)

1. Fill sprite black
2. Draw 12 tick marks (static geometry from precomputed sin/cos at 30° intervals)
3. Draw date text below center (string recomputed only on activation or midnight)
4. Compute hand angles from `getLocalTime()` + sub-second `millis()` interpolation
5. Draw hour, minute, second hands using `drawWideLine`
6. Draw center dot (filled white circle)
7. `pushSprite(0, 0)` — single SPI push to display

### Timing

- `CLOCK_TICK_MS` changes from 1000 to 40 (25 FPS)
- Sub-second interpolation: fractional seconds derived from `millis() % 1000`
- Hour and minute hands also move smoothly (gradual advancement, not ticking)

### RAM budget

- Sprite: 240 × 240 × 2 = 115,200 bytes (~113 KB)
- ESP32 SRAM: ~520 KB total
- RTSP double buffers: 64 KB (static arrays, always allocated but unused when not on RTSP screen)
- Fits comfortably; optionally free sprite on screen switch away from CLOCK

## Clock Face Geometry

All coordinates relative to center (120, 120).

### Tick marks

- 12 radial lines at 30° intervals
- Outer radius: 108 px from center
- Inner radius: 94 px from center (tick length: 14 px)
- Color: `COL_GREY` (0x52AA)
- Width: 2 px (`drawWideLine`)

### Hands

| Hand   | Length | Width | Color                    |
|--------|--------|-------|--------------------------|
| Hour   | 55 px  | 4 px  | White (TFT_WHITE)        |
| Minute | 80 px  | 3 px  | White (TFT_WHITE)        |
| Second | 90 px  | 2 px  | Red (`COL_RED`, 0xC9E7)  |

- Second hand has a ~15 px tail extending past center (counterweight)
- All hands use `drawWideLine` for rounded ends
- Center dot: filled white circle, radius 5 px

### Date text

- Format: "Mon 26" (abbreviated weekday + day-of-month)
- Position: y=155, centered horizontally
- Font: `NotoSans_Medium14` (already available)
- Color: `COL_GREY`
- String recomputed on screen activation, midnight crossover, or date change — not every frame

### No outer circle

The physical GC9A01 display is already round. Black background blends with the bezel — no drawn border needed.

## Code Changes

All changes are in `src/main.cpp`. No server changes.

### New global

```cpp
TFT_eSprite clockSprite(&tft);
```

### Modified functions

**`drawClockScreen()`** — creates the sprite (if not already created), sets 16-bit color depth, draws initial frame, pushes to display.

**`updateClockTime(bool forceDate)`** — complete rewrite:
- Renders full analog clock face to sprite each frame
- `forceDate` controls whether the date string is recomputed (on activation/midnight)
- Falls back to "Syncing time..." text in sprite if NTP not yet synced

### Modified constants

- `CLOCK_TICK_MS`: 1000 → 40

### Preserved behavior (no changes)

- `Screen` enum (`CLOCK` at index 0)
- `clockFromIdle` flag and `/v1/ping` recovery logic
- `CLOCK_PING_MS` (60 s)
- `activateScreen(CLOCK)` structure
- Button handlers (no-op on CLOCK screen for btn1)
- Screen cycling order
- Idle timeout fallback to CLOCK
- `IDLE_TIMEOUT_MS` constant

### Optional optimization

Free the sprite on screen switch away from CLOCK (`clockSprite.deleteSprite()` in `activateScreen` when leaving CLOCK). Recreate on next activation. Frees ~113 KB for other screens. Not critical since RAM fits, but good practice.
