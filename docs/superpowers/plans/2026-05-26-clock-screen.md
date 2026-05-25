# Clock Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CLOCK screen as the initial default screen that shows date, weekday, and time (with seconds) using NTP, and replaces the IDLE screen — when the server becomes unreachable the display falls back to CLOCK and pings every 60 s to auto-restore the previous screen when the server comes back.

**Architecture:** The CLOCK screen is rendered entirely on-device using TFT_eSPI built-in fonts and NTP time (`configTime` / `getLocalTime` — no extra library needed). A lightweight `/v1/ping` endpoint on the server is polled every 60 s from CLOCK; on success with `clockFromIdle=true`, the firmware restores `prevScreen`. The IDLE enum value and `drawSleepScreen` are removed; all their responsibilities transfer to CLOCK.

**Tech Stack:** ESP32 Arduino / PlatformIO, TFT_eSPI, FastAPI (Python).

---

## File Map

| File | Change |
|------|--------|
| `server/routes/ping.py` | **Create** — single `GET /ping` route |
| `server/main.py` | **Modify** — register ping router |
| `src/main.cpp` | **Modify** — NTP init, CLOCK screen draw/tick, new enum, screen cycling, idle replacement, ping logic |

---

### Task 1: Server — Add `/v1/ping` endpoint

**Files:**
- Create: `server/routes/ping.py`
- Modify: `server/main.py`

- [ ] **Step 1: Create `server/routes/ping.py`**

```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
async def ping():
    return {"status": "ok"}
```

Auth is handled globally by the middleware in `main.py` — no per-route auth needed.

- [ ] **Step 2: Register the router in `server/main.py`**

Add the import after the existing router imports:

```python
from routes.ping import router as ping_router
```

Add the `include_router` call after the existing ones (before `if __name__ == "__main__":`):

```python
app.include_router(ping_router, prefix="/v1")
```

- [ ] **Step 3: Verify manually**

Start the dev server:
```bash
cd server
uv run uvicorn main:app --host 0.0.0.0 --port 7333 --reload
```

In another terminal:
```bash
curl -H "X-API-Key: <your_api_key>" http://localhost:7333/v1/ping
```
Expected: `{"status":"ok"}`

- [ ] **Step 4: Commit**

```bash
git add server/routes/ping.py server/main.py
git commit -m "feat(server): add GET /v1/ping health-check endpoint"
```

---

### Task 2: Firmware — NTP initialisation

**Files:**
- Modify: `src/main.cpp` (lines 1–15, config constants, `initWiFi` function ~line 302)

The ESP32 Arduino framework ships `configTime` and `getLocalTime` in `<time.h>` — no extra library needed.

- [ ] **Step 1: Add include and timezone constant near the top of `src/main.cpp`**

After `#include "claude_logo.h"` (line 9), add:

```cpp
#include <time.h>
```

After the `COL_RED` / timing constants block (around line 28), add:

```cpp
#define NTP_OFFSET_HOURS  8.0f       // UTC+8; supports fractional e.g. -5.5, 5.75
#define NTP_SERVER1       "pool.ntp.org"
#define NTP_SERVER2       "time.google.com"
const unsigned long CLOCK_TICK_MS       = 1000;
const unsigned long CLOCK_PING_MS       = 60000UL;
```

- [ ] **Step 2: Call `configTime` in `initWiFi` after WiFi connects**

Find `initWiFi()` (around line 302). After `Serial.printf("RSSI: %d\n", ...)` and before the closing `}`, add:

```cpp
    configTime((long)(NTP_OFFSET_HOURS * 3600), 0, NTP_SERVER1, NTP_SERVER2);
    Serial.println("NTP sync started");
```

- [ ] **Step 3: Verify build**

```bash
pio run
```
Expected: compiles with no errors.

- [ ] **Step 4: Commit**

```bash
git add src/main.cpp
git commit -m "feat(firmware): initialise NTP after WiFi connect (UTC+8 configurable)"
```

---

### Task 3: Firmware — CLOCK screen rendering functions

**Files:**
- Modify: `src/main.cpp` — add `drawClockScreen()` and `updateClockTime()` functions

The clock uses:
- `tft.setTextFont(6)` — TFT_eSPI built-in 48 px font (digits + colon), good for HH:MM:SS
- `NotoSans_Medium14` (already loaded) — for weekday and date lines
- `MC_DATUM` centering on `CX = 120`

Layout on the 240 × 240 circle (safe rendering area ≈ ±116 px from center):

```
y=80:  weekday    NotoSans_Medium14
y=105: date       NotoSans_Medium14
y=152: HH:MM:SS   Font 6 (48 px)
```

- [ ] **Step 1: Add `drawClockScreen()` after `drawSleepScreen()` (around line 108)**

```cpp
// Draws the full clock screen (static structure + current time).
// Call on screen activation and after returning from another screen.
void drawClockScreen() {
    tft.fillScreen(TFT_BLACK);
    // Time will be drawn by updateClockTime(); call it immediately after.
}

// Draws (or refreshes) the time portion of the clock.
// Pass forceDate=true to also redraw weekday and date (on activation or midnight).
void updateClockTime(bool forceDate) {
    struct tm t;
    if (!getLocalTime(&t, 100)) {
        // NTP not yet synced
        tft.setTextDatum(MC_DATUM);
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.loadFont(NotoSans_Medium14);
        tft.drawString("Syncing time...", CX, CX);
        tft.unloadFont();
        return;
    }

    if (forceDate) {
        // Weekday
        const char* weekdays[] = {"Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"};
        tft.fillRect(0, 65, 240, 20, TFT_BLACK);
        tft.setTextDatum(MC_DATUM);
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.loadFont(NotoSans_Medium14);
        tft.drawString(weekdays[t.tm_wday], CX, 75);

        // Date: "26 May 2026"
        const char* months[] = {"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"};
        char dateBuf[16];
        snprintf(dateBuf, sizeof(dateBuf), "%d %s %d",
                 t.tm_mday, months[t.tm_mon], t.tm_year + 1900);
        tft.fillRect(0, 92, 240, 20, TFT_BLACK);
        tft.drawString(dateBuf, CX, 102);
        tft.unloadFont();
    }

    // Time HH:MM:SS — clear previous digits then redraw
    tft.fillRect(20, 126, 200, 52, TFT_BLACK);
    char timeBuf[12];
    snprintf(timeBuf, sizeof(timeBuf), "%02d:%02d:%02d",
             t.tm_hour, t.tm_min, t.tm_sec);
    tft.setTextDatum(MC_DATUM);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.setTextFont(6);
    tft.drawString(timeBuf, CX, 152);
    tft.setTextFont(1); // reset to default
}
```

- [ ] **Step 2: Verify build**

```bash
pio run
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/main.cpp
git commit -m "feat(firmware): add drawClockScreen and updateClockTime rendering functions"
```

---

### Task 4: Firmware — CLOCK screen integration

**Files:**
- Modify: `src/main.cpp` — enum, state variables, `activateScreen`, `loop`, `setup` (button handlers + initial screen)

This task wires everything together:
- Removes `IDLE` from the enum and removes `drawSleepScreen` / `wakeFromIdle`
- Adds `CLOCK` as enum value 0
- Adds `clockFromIdle` flag and `lastClockPing` / `lastClockTick` timestamps
- Updates `activateScreen` to handle CLOCK
- Updates `loop` to tick the clock and run the ping
- Updates button cycling to include CLOCK at position 0
- Changes startup screen to `CLOCK`
- Replaces the idle-timeout block with a CLOCK fallback

- [ ] **Step 1: Replace the `Screen` enum and remove IDLE state variables**

Find (around line 30):
```cpp
enum Screen { SPOTIFY, CC_USAGE, RTSP, IDLE };
Screen activeScreen = CC_USAGE;
Screen prevScreen = CC_USAGE;
unsigned long serverUnreachableSince = 0;
```

Replace with:
```cpp
enum Screen { CLOCK, SPOTIFY, CC_USAGE, RTSP };
Screen activeScreen = CLOCK;
Screen prevScreen   = CC_USAGE;
unsigned long serverUnreachableSince = 0;
bool clockFromIdle = false;
unsigned long lastClockTick = 0;
unsigned long lastClockPing = 0;
```

- [ ] **Step 2: Remove `drawSleepScreen` and `wakeFromIdle`**

Delete the `drawSleepScreen()` function (lines 99–107):
```cpp
void drawSleepScreen() {
    tft.fillScreen(TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.loadFont(NotoSans_Medium14);
    tft.drawString("zZzZz", CX, 105);
    tft.drawString("Press to wake", CX, 130);
    tft.unloadFont();
}
```

Delete `wakeFromIdle()` (lines 573–575):
```cpp
void wakeFromIdle() {
    activateScreen(prevScreen);
}
```

- [ ] **Step 3: Update `activateScreen` — add CLOCK case, remove IDLE handling**

Find the body of `activateScreen` (around line 540). Add the CLOCK case before the CC_USAGE case:

```cpp
    if (s == CLOCK) {
        lastClockTick = millis();
        lastClockPing = millis();
        drawClockScreen();
        updateClockTime(true);
    } else if (s == CC_USAGE) {
```

Also remove the reference to `IDLE` in the RTSP suspend guard at the top of `activateScreen` — it currently reads:
```cpp
void activateScreen(Screen s) {
    if (activeScreen == RTSP && s != RTSP && rtspNetTaskHandle != nullptr)
        vTaskSuspend(rtspNetTaskHandle);
```
That line doesn't mention IDLE so it stays as-is.

- [ ] **Step 4: Replace the idle-timeout block in `loop`**

Find (around line 638):
```cpp
    if (serverUnreachableSince > 0 && activeScreen != IDLE
            && (now - serverUnreachableSince) >= IDLE_TIMEOUT_MS) {
        prevScreen = activeScreen;
        activeScreen = IDLE;
        drawSleepScreen();
    }
```

Replace with:
```cpp
    if (serverUnreachableSince > 0 && activeScreen != CLOCK
            && (now - serverUnreachableSince) >= IDLE_TIMEOUT_MS) {
        prevScreen = activeScreen;
        clockFromIdle = true;
        activateScreen(CLOCK);
    }
```

- [ ] **Step 5: Add CLOCK branch to `loop`**

After the existing `if (activeScreen == SPOTIFY) {` block (just before or after the CC_USAGE block), add:

```cpp
    } else if (activeScreen == CLOCK) {
        if (now - lastClockTick >= CLOCK_TICK_MS) {
            lastClockTick = now;
            // Redraw date only at midnight (seconds reset to 0 after a 23:59:59 tick)
            struct tm t; bool gotTime = getLocalTime(&t, 0);
            bool midnight = gotTime && t.tm_hour == 0 && t.tm_min == 0 && t.tm_sec == 0;
            updateClockTime(midnight);
        }
        if (clockFromIdle && WiFi.status() == WL_CONNECTED
                && (now - lastClockPing) >= CLOCK_PING_MS) {
            lastClockPing = now;
            HTTPClient http;
            http.begin(String(serverUrl) + "/v1/ping");
            http.addHeader("X-API-Key", apiKey);
            int code = http.GET();
            http.end();
            if (code == 200) {
                clockFromIdle = false;
                serverUnreachableSince = 0;
                activateScreen(prevScreen);
            }
        }
```

- [ ] **Step 6: Update button 1 (GPIO 19) handlers in `setup`**

Find the `btn.attachClick` lambda (around line 589). Remove all `IDLE` references:

```cpp
    btn.attachClick([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) {
            rtspIndex = (rtspIndex + 1) % rtspStreamCount;
            return;
        }
        sendCommand("/v1/spotify/toggle");
    });
    btn.attachDoubleClick([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) {
            rtspIndex = (rtspIndex - 1 + rtspStreamCount) % rtspStreamCount;
            return;
        }
        sendCommand("/v1/spotify/next");
    });
    btn.attachLongPressStart([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) return;
        sendCommand("/v1/spotify/previous");
    });
```

- [ ] **Step 7: Update button 2 (GPIO 21) cycling to include CLOCK**

Replace the `btn2.attachClick` and `btn2.attachDoubleClick` lambdas:

```cpp
    btn2.attachClick([]() {
        // Forward cycle: CLOCK -> SPOTIFY -> RTSP -> CC_USAGE -> CLOCK
        Screen next;
        if      (activeScreen == CLOCK)    next = SPOTIFY;
        else if (activeScreen == SPOTIFY)  next = RTSP;
        else if (activeScreen == RTSP)     next = CC_USAGE;
        else                               next = CLOCK;
        clockFromIdle = false;
        activateScreen(next);
    });
    btn2.attachDoubleClick([]() {
        // Backward cycle: CLOCK -> CC_USAGE -> RTSP -> SPOTIFY -> CLOCK
        Screen target;
        if      (activeScreen == CLOCK)    target = CC_USAGE;
        else if (activeScreen == CC_USAGE) target = RTSP;
        else if (activeScreen == RTSP)     target = SPOTIFY;
        else                               target = CLOCK;
        clockFromIdle = false;
        activateScreen(target);
    });
```

The `btn2.attachLongPressStart` (`ESP.restart()`) is unchanged.

- [ ] **Step 8: Change initial screen in `setup`**

Find in `setup()` (after semaphore / task creation, before button setup):

The initial screen is now `CLOCK` (already set by the variable initialiser in Step 1). Add the initial activation call after `vTaskSuspend(rtspNetTaskHandle);`:

```cpp
    activateScreen(CLOCK);
```

Remove the old `drawStatus("Connecting to server...");` call right before the rtsp semaphore setup (if it's still there) — `activateScreen(CLOCK)` handles first draw.

- [ ] **Step 9: Build firmware**

```bash
pio run
```
Expected: compiles with no errors or warnings about undeclared identifiers.

- [ ] **Step 10: Commit**

```bash
git add src/main.cpp
git commit -m "feat(firmware): add CLOCK screen, replace idle with server-ping fallback"
```

---

## Self-Review Checklist

- [x] `/v1/ping` endpoint added and registered — Task 1
- [x] NTP `configTime` called after WiFi connects — Task 2
- [x] `drawClockScreen` / `updateClockTime` render date + weekday + HH:MM:SS — Task 3
- [x] `IDLE` enum value removed, `CLOCK` at index 0 — Task 4 Step 1
- [x] `drawSleepScreen` / `wakeFromIdle` removed — Task 4 Step 2
- [x] `activateScreen(CLOCK)` initialises tick/ping timestamps and draws — Task 4 Step 3
- [x] Idle-timeout block now calls `activateScreen(CLOCK)` with `clockFromIdle=true` — Task 4 Step 4
- [x] CLOCK loop: 1 s tick updates time; 60 s ping restores `prevScreen` when `clockFromIdle` — Task 4 Step 5
- [x] Button 1 is no-op on CLOCK — Task 4 Step 6
- [x] Button 2 cycles CLOCK→SPOTIFY→RTSP→CC_USAGE→CLOCK — Task 4 Step 7
- [x] Startup calls `activateScreen(CLOCK)` — Task 4 Step 8
- [x] No IDLE references remain in enum, loop, or button handlers
