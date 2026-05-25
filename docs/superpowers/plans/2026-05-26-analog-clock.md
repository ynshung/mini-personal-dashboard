# Analog Clock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the digital clock rendering with a minimal analog clock face using a full-screen sprite for flicker-free 25 FPS animation.

**Architecture:** A `TFT_eSprite` (240×240, 16-bit) is created on CLOCK screen activation. Every 40 ms the entire clock face is redrawn into the sprite (tick marks, hands, date text, center dot) and pushed to the display in one SPI transaction. Hand positions are computed from NTP time with sub-second interpolation via `millis()`. All existing CLOCK behavior (idle fallback, ping recovery, screen cycling) is unchanged.

**Tech Stack:** ESP32 Arduino / PlatformIO, TFT_eSPI (`TFT_eSprite`, `drawWideLine`, `fillSmoothCircle`).

---

## File Map

| File | Change |
|------|--------|
| `src/main.cpp` | **Modify** — add sprite global, rewrite `drawClockScreen()` and `updateClockTime()`, change `CLOCK_TICK_MS`, add sprite cleanup in `activateScreen()` |

---

### Task 1: Add sprite global and update tick interval

**Files:**
- Modify: `src/main.cpp` (lines 18, 25)

- [ ] **Step 1: Add the sprite global**

After line 18 (`TFT_eSPI tft = TFT_eSPI();`), add:

```cpp
TFT_eSprite clockSprite(&tft);
```

- [ ] **Step 2: Change `CLOCK_TICK_MS` from 1000 to 40**

Change line 25 from:

```cpp
const unsigned long CLOCK_TICK_MS  = 1000;
```

to:

```cpp
const unsigned long CLOCK_TICK_MS  = 40;
```

- [ ] **Step 3: Add clock date cache variable**

After the `lastClockPing` declaration (line 43), add:

```cpp
char clockDateBuf[16] = "";
```

This caches the date string so it isn't recomputed every 40 ms frame.

- [ ] **Step 4: Build firmware**

```bash
pio run
```

Expected: compiles with no errors (sprite is declared but unused — no warning expected since it's a global).

- [ ] **Step 5: Commit**

```bash
git add src/main.cpp
git commit -m "feat(clock): add sprite global, date cache, set 25 FPS tick rate"
```

---

### Task 2: Rewrite `drawClockScreen()` and `updateClockTime()`

**Files:**
- Modify: `src/main.cpp` (lines 109–150)

- [ ] **Step 1: Replace `drawClockScreen()` (lines 109–111)**

Replace:

```cpp
void drawClockScreen() {
    tft.fillScreen(TFT_BLACK);
}
```

with:

```cpp
void drawClockScreen() {
    clockSprite.setColorDepth(16);
    clockSprite.createSprite(240, 240);
    clockDateBuf[0] = '\0';
}
```

This creates the sprite on activation. The first frame is drawn by `updateClockTime(true)` which `activateScreen(CLOCK)` calls immediately after.

- [ ] **Step 2: Replace `updateClockTime()` (lines 113–150)**

Replace the entire `updateClockTime` function with:

```cpp
void updateClockTime(bool forceDate) {
    struct tm t;
    if (!getLocalTime(&t, 100)) {
        clockSprite.fillSprite(TFT_BLACK);
        clockSprite.setTextDatum(MC_DATUM);
        clockSprite.setTextColor(COL_GREY, TFT_BLACK);
        clockSprite.loadFont(NotoSans_Medium14);
        clockSprite.drawString("Syncing time...", CX, CX);
        clockSprite.unloadFont();
        clockSprite.pushSprite(0, 0);
        return;
    }

    // Recompute date string on activation or midnight
    if (forceDate) {
        const char* wdays[] = {"Sun","Mon","Tue","Wed","Thu","Fri","Sat"};
        snprintf(clockDateBuf, sizeof(clockDateBuf), "%s %d",
                 wdays[t.tm_wday], t.tm_mday);
    }

    clockSprite.fillSprite(TFT_BLACK);

    // --- Tick marks: 12 radial lines at 30° intervals ---
    const float outerR = 108.0f;
    const float innerR = 94.0f;
    for (int i = 0; i < 12; i++) {
        float angle = i * 30.0f * DEG_TO_RAD;
        float sinA = sinf(angle);
        float cosA = cosf(angle);
        float ox = CX + sinA * outerR;
        float oy = CX - cosA * outerR;
        float ix = CX + sinA * innerR;
        float iy = CX - cosA * innerR;
        clockSprite.drawWideLine(ix, iy, ox, oy, 2.0f, COL_GREY, TFT_BLACK);
    }

    // --- Date text below center ---
    if (clockDateBuf[0] != '\0') {
        clockSprite.setTextDatum(MC_DATUM);
        clockSprite.setTextColor(COL_GREY, TFT_BLACK);
        clockSprite.loadFont(NotoSans_Medium14);
        clockSprite.drawString(clockDateBuf, CX, 155);
        clockSprite.unloadFont();
    }

    // --- Hand angles (smooth interpolation) ---
    unsigned long ms = millis() % 1000;
    float sec  = t.tm_sec + ms / 1000.0f;
    float min  = t.tm_min + sec / 60.0f;
    float hour = (t.tm_hour % 12) + min / 60.0f;

    float secAngle  = sec  * 6.0f   * DEG_TO_RAD;  // 360/60 = 6° per second
    float minAngle  = min  * 6.0f   * DEG_TO_RAD;  // 360/60 = 6° per minute
    float hourAngle = hour * 30.0f  * DEG_TO_RAD;  // 360/12 = 30° per hour

    // --- Hour hand: 55 px, 4 px wide, white ---
    float hx = CX + sinf(hourAngle) * 55.0f;
    float hy = CX - cosf(hourAngle) * 55.0f;
    clockSprite.drawWideLine(CX, CX, hx, hy, 4.0f, TFT_WHITE, TFT_BLACK);

    // --- Minute hand: 80 px, 3 px wide, white ---
    float mx = CX + sinf(minAngle) * 80.0f;
    float my = CX - cosf(minAngle) * 80.0f;
    clockSprite.drawWideLine(CX, CX, mx, my, 3.0f, TFT_WHITE, TFT_BLACK);

    // --- Second hand: 90 px + 15 px tail, 2 px wide, red ---
    float sx = CX + sinf(secAngle) * 90.0f;
    float sy = CX - cosf(secAngle) * 90.0f;
    float tx = CX - sinf(secAngle) * 15.0f;
    float ty = CX + cosf(secAngle) * 15.0f;
    clockSprite.drawWideLine(tx, ty, sx, sy, 2.0f, COL_RED, TFT_BLACK);

    // --- Center dot ---
    clockSprite.fillSmoothCircle(CX, CX, 5, TFT_WHITE, TFT_BLACK);

    clockSprite.pushSprite(0, 0);
}
```

- [ ] **Step 3: Build firmware**

```bash
pio run
```

Expected: compiles with no errors.

- [ ] **Step 4: Commit**

```bash
git add src/main.cpp
git commit -m "feat(clock): rewrite as analog clock with sprite-based rendering"
```

---

### Task 3: Free sprite on screen switch

**Files:**
- Modify: `src/main.cpp` — `activateScreen()` function (line 592)

- [ ] **Step 1: Add sprite cleanup at the top of `activateScreen()`**

Find (line 592–597):

```cpp
void activateScreen(Screen s) {
    if (activeScreen == RTSP && s != RTSP && rtspNetTaskHandle != nullptr)
        vTaskSuspend(rtspNetTaskHandle);
    activeScreen = s;
    serverUnreachableSince = 0;
    pollFailed = false;
```

Replace with:

```cpp
void activateScreen(Screen s) {
    if (activeScreen == RTSP && s != RTSP && rtspNetTaskHandle != nullptr)
        vTaskSuspend(rtspNetTaskHandle);
    if (activeScreen == CLOCK && s != CLOCK)
        clockSprite.deleteSprite();
    activeScreen = s;
    serverUnreachableSince = 0;
    pollFailed = false;
```

This frees ~113 KB when leaving the CLOCK screen.

- [ ] **Step 2: Build firmware**

```bash
pio run
```

Expected: compiles with no errors.

- [ ] **Step 3: Commit**

```bash
git add src/main.cpp
git commit -m "perf(clock): free sprite RAM on screen switch away from CLOCK"
```

---

### Task 4: Flash and verify on hardware

**Files:** None (verification only)

- [ ] **Step 1: Flash firmware to device**

```bash
pio run --target upload
```

Expected: uploads successfully.

- [ ] **Step 2: Open serial monitor**

```bash
pio device monitor
```

Expected: see `NTP sync started` after WiFi connect, then clock screen activates.

- [ ] **Step 3: Verify visually**

Check:
- 12 grey tick marks evenly spaced around the dial
- White hour and minute hands pointing to correct time
- Red second hand sweeping smoothly (no ticking, no flicker)
- Second hand has a short tail past center
- Date text (e.g. "Mon 26") visible below center in grey
- White center dot

- [ ] **Step 4: Verify screen cycling**

Press GPIO 21 button:
- Single click: CLOCK → CC_USAGE (sprite freed, CC_USAGE renders normally)
- Continue cycling through all screens and back to CLOCK
- CLOCK should recreate sprite and display correctly each time

- [ ] **Step 5: Verify idle fallback**

- Stop the server while on CC_USAGE or another server-dependent screen
- After 10 minutes (or temporarily reduce `IDLE_TIMEOUT_MS` for testing), the display should fall back to the analog clock
- Restart the server — clock should ping `/v1/ping` and auto-restore the previous screen within 60 s

---

## Self-Review Checklist

- [x] Sprite global declared — Task 1 Step 1
- [x] `CLOCK_TICK_MS` changed 1000 → 40 — Task 1 Step 2
- [x] Date string cached in `clockDateBuf`, recomputed on `forceDate` — Task 1 Step 3, Task 2 Step 2
- [x] `drawClockScreen()` creates sprite — Task 2 Step 1
- [x] `updateClockTime()` renders full analog face to sprite — Task 2 Step 2
- [x] 12 tick marks at 30° intervals, outerR=108, innerR=94, COL_GREY, width 2 — Task 2 Step 2
- [x] Hour hand: 55 px, 4 px wide, TFT_WHITE — Task 2 Step 2
- [x] Minute hand: 80 px, 3 px wide, TFT_WHITE — Task 2 Step 2
- [x] Second hand: 90 px + 15 px tail, 2 px wide, COL_RED — Task 2 Step 2
- [x] Smooth interpolation via `millis() % 1000` — Task 2 Step 2
- [x] Center dot: `fillSmoothCircle` r=5, TFT_WHITE — Task 2 Step 2
- [x] Date text: "Mon 26" format, y=155, NotoSans_Medium14, COL_GREY — Task 2 Step 2
- [x] "Syncing time..." fallback drawn to sprite — Task 2 Step 2
- [x] Sprite freed on screen switch — Task 3 Step 1
- [x] No changes to: screen enum, button handlers, clockFromIdle/ping logic, screen cycling order, IDLE_TIMEOUT_MS — verified by diffing only modified functions
- [x] No placeholders, TBDs, or incomplete steps
- [x] All type/function names consistent across tasks (`clockSprite`, `clockDateBuf`, `drawClockScreen`, `updateClockTime`)
