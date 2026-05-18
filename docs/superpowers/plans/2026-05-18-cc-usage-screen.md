# CC Usage Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second display screen to the ESP32 firmware showing Claude Code plan usage, toggled by a button on GPIO21.

**Architecture:** All changes are in `src/main.cpp`. A `Screen` enum controls which screen is active; each screen polls its own endpoint independently. GPIO21 (`btn2`) toggles screens — on switch it renders stale data immediately, then fetches and re-renders. GPIO19 (`btn`) always sends Spotify commands regardless of active screen.

**Tech Stack:** Arduino/ESP32, TFT_eSPI (GC9A01 240×240), OneButtonTiny, ArduinoJson, NotoSans_Medium14 smooth font.

---

### Task 1: Add constants, types, and globals

**Files:**
- Modify: `src/main.cpp`

- [ ] **Step 1: Add `COL_RED`, `CC_POLL_INTERVAL_MS`, `Screen` enum, `CCUsage` struct, and globals**

In `src/main.cpp`, after the existing constants block (after line 22 `COL_BAR_ERROR`) and before the `CX` block, add:

```cpp
const uint16_t COL_RED = 0xF800;
const unsigned long CC_POLL_INTERVAL_MS = 30000;

enum Screen { SPOTIFY, CC_USAGE };
Screen activeScreen = SPOTIFY;

struct CCUsage {
    float  five_hour_pct    = -1;
    String five_hour_resets = "";
    float  seven_day_pct    = -1;
    String seven_day_resets = "";
};
CCUsage ccUsage;
unsigned long lastCCPoll = 0;
```

- [ ] **Step 2: Add `btn2` instance after `btn`**

After line 16 (`OneButtonTiny btn(19, false, false);`), add:

```cpp
OneButtonTiny btn2(21, false, false); // GPIO 21, active-high, no internal pull-up
```

- [ ] **Step 3: Build to verify no compile errors**

```bash
pio run
```

Expected: `SUCCESS` with no errors. (IDE clang errors about `Arduino.h` not found are false positives — ignore them. Only `pio run` output matters.)

- [ ] **Step 4: Commit**

```bash
git add src/main.cpp
git commit -m "feat: add CCUsage struct, Screen enum, btn2, COL_RED constant"
```

---

### Task 2: Add `drawCCUsage()` rendering function

**Files:**
- Modify: `src/main.cpp`

This function renders the full CC usage screen from the `ccUsage` global struct. It draws two metric blocks (5-HR and 7-DAY) with percentage text, a color-coded progress bar, and a reset time string. Null values (`pct == -1`) render as `—` with an empty bar.

- [ ] **Step 1: Add `usageColor()` helper and `drawCCUsage()` after `drawTick()`**

Add after `drawTick()` (after its closing `}`) and before the `// --- WiFi ---` comment:

```cpp
uint16_t usageColor(float pct) {
    if (pct >= 100.0f) return COL_RED;
    if (pct >= 61.0f)  return COL_BAR_ERROR;
    return COL_BAR_FILL;
}

void drawCCBlock(int y, float pct, const char* label, const String& resets) {
    const int LEFT  = 50;
    const int RIGHT = 190;
    const int BAR_W = 140;
    const int BAR_H = 4;

    tft.loadFont(NotoSans_Medium14);

    // Percentage (left) and label (right) on same row
    tft.setTextDatum(ML_DATUM);
    if (pct < 0) {
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.drawString("--", LEFT, y);
    } else {
        tft.setTextColor(usageColor(pct), TFT_BLACK);
        char buf[8];
        snprintf(buf, sizeof(buf), "%d%%", (int)pct);
        tft.drawString(buf, LEFT, y);
    }

    tft.setTextDatum(MR_DATUM);
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.drawString(label, RIGHT, y);

    tft.unloadFont();

    // Progress bar
    int barY = y + 13;
    tft.fillRoundRect(LEFT, barY, BAR_W, BAR_H, BAR_H / 2, COL_BAR_BG);
    if (pct >= 0) {
        float clamped = pct > 100.0f ? 100.0f : pct;
        int fillW = (int)(clamped / 100.0f * BAR_W);
        if (fillW > 0)
            tft.fillRoundRect(LEFT, barY, fillW, BAR_H, BAR_H / 2, usageColor(pct));
    }

    // Resets label
    if (resets.length() > 0) {
        tft.loadFont(NotoSans_Medium14);
        tft.setTextDatum(TL_DATUM);
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.drawString(resets.c_str(), LEFT, barY + BAR_H + 4);
        tft.unloadFont();
    }
}

void drawCCUsage() {
    tft.fillScreen(TFT_BLACK);

    // Title
    tft.loadFont(NotoSans_Medium14);
    tft.setTextDatum(MC_DATUM);
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.drawString("Usage", CX, 72);
    tft.unloadFont();

    drawCCBlock(100, ccUsage.five_hour_pct, "5-HR",  ccUsage.five_hour_resets);
    drawCCBlock(152, ccUsage.seven_day_pct, "7-DAY", ccUsage.seven_day_resets);
}
```

- [ ] **Step 2: Build to verify**

```bash
pio run
```

Expected: `SUCCESS`.

- [ ] **Step 3: Commit**

```bash
git add src/main.cpp
git commit -m "feat: add drawCCUsage rendering function"
```

---

### Task 3: Add `fetchCCUsage()` networking function

**Files:**
- Modify: `src/main.cpp`

`fetchCCUsage()` calls `GET /v1/cc-usage`, parses the JSON into the `ccUsage` global, then calls `drawCCUsage()`. On any error it calls `drawStatus("CC usage unavailable")`.

- [ ] **Step 1: Add `fetchCCUsage()` in the `// --- Networking ---` section, after `fetchNowPlaying()`**

Add after the closing `}` of `fetchNowPlaying()`:

```cpp
void fetchCCUsage() {
    HTTPClient http;
    http.begin(String(serverUrl) + "/v1/cc-usage");
    http.addHeader("X-API-Key", apiKey);

    int code = http.GET();
    if (code != 200) {
        Serial.printf("CC usage HTTP error: %d\n", code);
        http.end();
        drawStatus("CC usage unavailable");
        return;
    }

    String payload = http.getString();
    http.end();

    JsonDocument doc;
    if (deserializeJson(doc, payload)) {
        Serial.println("CC usage JSON parse error");
        drawStatus("CC usage unavailable");
        return;
    }

    JsonVariant fh = doc["five_hour"];
    if (fh.isNull() || fh["utilization"].isNull()) {
        ccUsage.five_hour_pct    = -1;
        ccUsage.five_hour_resets = "";
    } else {
        ccUsage.five_hour_pct    = fh["utilization"].as<float>();
        ccUsage.five_hour_resets = fh["resets_at"] | "";
    }

    JsonVariant sd = doc["seven_day"];
    if (sd.isNull() || sd["utilization"].isNull()) {
        ccUsage.seven_day_pct    = -1;
        ccUsage.seven_day_resets = "";
    } else {
        ccUsage.seven_day_pct    = sd["utilization"].as<float>();
        ccUsage.seven_day_resets = sd["resets_at"] | "";
    }

    Serial.printf("CC usage: 5h=%.1f%% 7d=%.1f%%\n",
        ccUsage.five_hour_pct, ccUsage.seven_day_pct);
    drawCCUsage();
}
```

- [ ] **Step 2: Build to verify**

```bash
pio run
```

Expected: `SUCCESS`.

- [ ] **Step 3: Commit**

```bash
git add src/main.cpp
git commit -m "feat: add fetchCCUsage networking function"
```

---

### Task 4: Wire up button, `setup()`, and `loop()`

**Files:**
- Modify: `src/main.cpp`

Wire `btn2` click to toggle screens. Update `loop()` to tick `btn2` and split polling by `activeScreen`.

- [ ] **Step 1: Add `btn2` click handler in `setup()`**

In `setup()`, after the existing `btn.attachLongPressStart(...)` line, add:

```cpp
btn2.attachClick([]() {
    activeScreen = (activeScreen == SPOTIFY) ? CC_USAGE : SPOTIFY;
    if (activeScreen == CC_USAGE) {
        drawCCUsage();
        fetchCCUsage();
        lastCCPoll = millis();
    } else {
        // Switching back to Spotify: the screen was wiped by CC usage.
        // Set a sentinel track_id so fetchNowPlaying() always sees a track
        // change — triggering art re-fetch if playing, or drawIdle() if not.
        hasArt = false;
        current.track_id = "\x01";
        drawStatus("Loading...");
        fetchNowPlaying();
        lastPoll = millis();
        lastTick = lastPoll;
    }
});
```

- [ ] **Step 2: Update `loop()` to tick `btn2` and guard Spotify logic behind `activeScreen`**

Replace the entire `loop()` function with:

```cpp
void loop() {
    btn.tick();
    btn2.tick();
    unsigned long now = millis();

    if (activeScreen == SPOTIFY) {
        // End-of-song poll
        if (current.is_playing && current.duration_ms > 0) {
            uint32_t estimated = current.progress_ms + (uint32_t)(now - lastFetchMs);
            if (estimated >= current.duration_ms) {
                if (WiFi.status() == WL_CONNECTED) {
                    fetchNowPlaying();
                    lastPoll = millis();
                    lastTick = lastPoll;
                }
            }
        }

        if (now - lastPoll >= POLL_INTERVAL_MS) {
            lastPoll = now;
            if (WiFi.status() == WL_CONNECTED) {
                fetchNowPlaying();
                lastTick = now;
            } else {
                Serial.println("WiFi disconnected, reconnecting...");
                if (!hasArt) drawStatus("WiFi disconnected");
                initWiFi();
                drawStatus("Connecting to server...");
            }
        }

        if (current.is_playing && (now - lastTick >= TICK_INTERVAL_MS)) {
            lastTick = now;
            drawTick();
        }
    }

    if (activeScreen == CC_USAGE) {
        if (now - lastCCPoll >= CC_POLL_INTERVAL_MS) {
            lastCCPoll = now;
            fetchCCUsage();
        }
    }
}
```

- [ ] **Step 3: Build to verify**

```bash
pio run
```

Expected: `SUCCESS`.

- [ ] **Step 4: Commit**

```bash
git add src/main.cpp
git commit -m "feat: wire GPIO21 screen toggle and split loop polling by active screen"
```

---

### Task 5: Flash and verify on device

**Files:** none (verification only)

- [ ] **Step 1: Flash firmware**

```bash
pio run --target upload
```

- [ ] **Step 2: Open serial monitor**

```bash
pio device monitor
```

- [ ] **Step 3: Verify Spotify screen (default)**

On boot, confirm the display shows the Spotify screen as before (album art / "Not playing" / "Connecting to server..."). Confirm GPIO19 single/double/long-press still controls playback.

- [ ] **Step 4: Switch to CC usage screen**

Press GPIO21 once. Confirm:
- Display clears and shows `Usage` title immediately (stale `--` values on first boot)
- Within a second, fresh data loads showing 5-HR and 7-DAY percentages with color-coded bars
- Serial monitor shows `CC usage: 5h=XX.X% 7d=XX.X%%`

- [ ] **Step 5: Verify color thresholds**

Check that bar and percentage text color matches:
- 0–60% → white/light grey
- 61–99% → orange
- 100% → red

(If your usage happens to be in only one range, trust the logic — the thresholds are `pct >= 100`, `pct >= 61`, else white.)

- [ ] **Step 6: Switch back to Spotify**

Press GPIO21 again. Confirm Spotify screen restores instantly from cache, then refreshes within 5s.

- [ ] **Step 7: Verify GPIO19 works on CC screen**

While on CC screen, press GPIO19 (single click). Confirm play/pause toggles on Spotify without switching screens.
