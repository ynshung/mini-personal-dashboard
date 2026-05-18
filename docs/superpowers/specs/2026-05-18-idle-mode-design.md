# Idle Mode Design

**Date:** 2026-05-18  
**Status:** Approved

## Summary

When the server is unreachable for 10 consecutive minutes (across any active screen), the device enters an idle mode that stops all polling and shows a sleep screen. Any button press wakes the device and returns to the previously active screen.

## Motivation

Without idle mode the device retries indefinitely when the server is down, burning CPU and WiFi radio unnecessarily. The 10-minute threshold is long enough to survive transient outages without annoying the user, but short enough to avoid wasting resources during extended downtime.

## State Changes

### `Screen` enum
Add `IDLE` to the existing `{ SPOTIFY, CC_USAGE }` enum:
```cpp
enum Screen { SPOTIFY, CC_USAGE, IDLE };
```

### New constant
```cpp
const unsigned long IDLE_TIMEOUT_MS = 10UL * 60UL * 1000UL; // 10 minutes
```

Defined alongside the other `*_INTERVAL_MS` constants at the top of `main.cpp` for easy adjustment.

### New variables
```cpp
Screen prevScreen = CC_USAGE;           // screen to restore on wake
unsigned long serverUnreachableSince = 0; // 0 = reachable; nonzero = millis() of first failure
```

`pollFailed` (Spotify progress bar coloring) is unchanged and is additionally reset to `false` on wake.

## Failure Tracking

Both `fetchNowPlaying` and `fetchCCUsage` call a shared helper on failure/success:

- **On any fetch failure** (non-200 HTTP or JSON parse error): if `serverUnreachableSince == 0`, set it to `millis()`.
- **On any fetch success**: reset `serverUnreachableSince = 0`.

The 10-minute window is measured from the first consecutive failure, not from the last poll attempt. A single success resets the clock.

## Idle Trigger

In `loop()`, after `btn.tick()` / `btn2.tick()`, before any polling logic:

```cpp
if (serverUnreachableSince > 0 && activeScreen != IDLE
    && (now - serverUnreachableSince) >= IDLE_TIMEOUT_MS) {
    prevScreen = activeScreen;
    activeScreen = IDLE;
    drawSleepScreen();
}
```

While `activeScreen == IDLE`, the polling blocks (`if (activeScreen == SPOTIFY)` etc.) are skipped naturally — no additional guard needed.

## Sleep Screen

```cpp
void drawSleepScreen() {
    tft.fillScreen(TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.loadFont(NotoSans_Medium14);
    tft.drawString("zzz", CX, 105);
    tft.drawString("Press any button to wake", CX, 135);
    tft.unloadFont();
}
```

## Wake Behavior

All button callbacks (3 on `btn`, 1 on `btn2`) check for idle at entry:

```cpp
if (activeScreen == IDLE) { wakeFromIdle(); return; }
```

`wakeFromIdle()`:
1. `activeScreen = prevScreen`
2. `serverUnreachableSince = 0`
3. `pollFailed = false`
4. Force immediate fetch: set `lastPoll = 0` and `lastCCPoll = 0`
5. Redraw and fetch the restored screen (same logic as the `btn2` screen-switch handler)

The wake button press executes **only** the wake action — no Spotify command is sent, no screen switch occurs.

## Sequence

```
fetch fails → serverUnreachableSince set
... 10 min pass with no success ...
loop() → activeScreen = IDLE, drawSleepScreen()
--- polling stops ---
button press → wakeFromIdle()
  → restore prevScreen, reset timers
  → immediate redraw + fetch
--- normal polling resumes ---
```

## Out of Scope

- Dimming / partial sleep before full idle
- WiFi disconnect handling (unchanged — handled separately in loop)
- Idle timeout countdown display
