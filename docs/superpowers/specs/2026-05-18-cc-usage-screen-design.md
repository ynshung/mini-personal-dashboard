# CC Usage Screen Design

**Date:** 2026-05-18

## Overview

Add a second display screen to the ESP32 firmware showing Claude Code plan usage. A dedicated button on GPIO21 toggles between the existing Spotify screen and the new CC usage screen.

## Hardware

- **GPIO21** — `OneButtonTiny btn2(21, false, false)` — identical configuration to GPIO19 (active-high, no internal pull-up)
- **GPIO19** — existing button; gestures (click/double-click/long-press) always send Spotify commands regardless of active screen

## Screen Switching

- Single click on GPIO21 toggles `activeScreen` between `SPOTIFY` and `CC_USAGE`
- On switch:
  1. Immediately render the new screen from cached struct (stale data, for instant feedback)
  2. Immediately fetch fresh data and re-render
  3. Reset the active screen's poll timer so the next refresh is a full interval later
- Each screen only polls its own endpoint while active — Spotify polling pauses on the CC screen and vice versa

## State

Add a `Screen` enum and `activeScreen` global:

```cpp
enum Screen { SPOTIFY, CC_USAGE };
Screen activeScreen = SPOTIFY;
```

Add a `CCUsage` struct:

```cpp
struct CCUsage {
    float  five_hour_pct    = -1;  // -1 = null (plan doesn't have this window)
    String five_hour_resets = "";
    float  seven_day_pct    = -1;
    String seven_day_resets = "";
};
CCUsage ccUsage;
```

Add `lastCCPoll` timer (mirrors `lastPoll` for Spotify).

## Networking

`fetchCCUsage()` calls `GET /v1/cc-usage` with the `X-API-Key` header. Parses:
- `five_hour.utilization` → `ccUsage.five_hour_pct` (store `-1` if key is missing or null)
- `five_hour.resets_at` → `ccUsage.five_hour_resets`
- `seven_day.utilization` → `ccUsage.seven_day_pct`
- `seven_day.resets_at` → `ccUsage.seven_day_resets`

On HTTP error or JSON parse failure: call `drawStatus("CC usage unavailable")`.

Poll interval: **30 seconds** while `activeScreen == CC_USAGE`.

## Rendering

`drawCCUsage()` draws on a black 240×240 canvas. Layout (all coordinates approximate, centered on the round display):

```
         Usage          ← small grey label, y≈80
  ┌────────────────────┐
  │ 50%        [5-HR]  │   ← y≈105
  │ ████████░░░░░░░░░  │   ← progress bar, y≈120
  │ Resets in 1 hr 22m │   ← grey, y≈130
  └────────────────────┘
  ┌────────────────────┐
  │ 11%        [7-DAY] │   ← y≈150
  │ ██░░░░░░░░░░░░░░░  │   ← progress bar, y≈165
  │ Resets in 6d 8h    │   ← grey, y≈175
  └────────────────────┘
```

**Color coding** for percentage text and bar fill:
- 0–60% → white (`COL_BAR_FILL`)
- 61–99% → orange (`COL_BAR_ERROR`)
- 100% → red (`COL_RED`)

**Null handling** (`pct == -1`): show `—` in place of percentage, flat/empty bar, empty resets string.

**Font:** `NotoSans_Medium14` (same as existing status text via `loadFont`/`unloadFont`).

## Loop Changes

```
loop():
  btn.tick()   // GPIO19 — always
  btn2.tick()  // GPIO21 — always

  if activeScreen == SPOTIFY:
    run existing Spotify poll + tick logic unchanged

  if activeScreen == CC_USAGE:
    if now - lastCCPoll >= 30000:
      lastCCPoll = now
      fetchCCUsage()
      drawCCUsage()
```

## New Constants

```cpp
const uint16_t COL_RED = 0xF800;  // pure red in RGB565 — new constant
// COL_BAR_FILL (white) and COL_BAR_ERROR (orange) reused from existing constants
const unsigned long CC_POLL_INTERVAL_MS = 30000;
```

## Out of Scope

- No server-side changes needed; `/v1/cc-usage` already exists
- No animation or transition between screens
- No handling of multiple simultaneous button presses
