# Spotify Now Playing Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Hello World test code with a Spotify now-playing UI on the GC9A01 240×240 round TFT display.

**Architecture:** Single `src/main.cpp` file. State is tracked in a `TrackState` struct; `fetchNowPlaying()` compares incoming state to current and triggers either a full redraw (track/play-state changed) or an arc-only update (progress changed). No LED logic (pin 22 is unwired).

**Tech Stack:** ESP32 Arduino, TFT_eSPI v2.5, ArduinoJson v7, PlatformIO

---

## File Map

- **Modify:** `src/main.cpp` — replace entirely; all display logic lives here

---

## Task 1: Define state struct, colors, and helper functions

**Files:**
- Modify: `src/main.cpp`

Replace the entire file with the scaffold below. This compiles but doesn't render anything yet — verifiable with `pio run`.

- [ ] **Step 1: Replace `src/main.cpp` with the full scaffold**

```cpp
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <TFT_eSPI.h>

const char *ssid     = WIFI_SSID;
const char *password = WIFI_PASSWORD;
const char *serverUrl = SERVER_URL;
const char *apiKey   = API_KEY;
const char *hostname = "esp32-dashboard";

TFT_eSPI tft = TFT_eSPI();

// Color constants (RGB565)
const uint16_t COL_SPOTIFY_GREEN = 0x1DCA; // #1DB954
const uint16_t COL_GREY          = 0x52AA; // #555555
const uint16_t COL_DARK_GREY     = 0x2104; // #222222
const uint16_t COL_IDLE_RING     = 0x0841; // #0A0A0A
const uint16_t COL_PAUSED_ART    = 0x3186; // #303030
const uint16_t COL_DIM_WHITE     = 0xAD55; // ~#AAAAAA

// Arc geometry (display is 240x240)
const int CX           = 120;
const int CY           = 120;
const int ARC_R_OUTER  = 116;
const int ARC_R_INNER  = 111;
const int ART_X        = 72;   // (240 - 96) / 2
const int ART_Y        = 26;
const int ART_SIZE     = 96;

// Accent color palette for art placeholder (one per track)
const uint16_t ACCENT_COLORS[] = {
    0x035F, // deep blue
    0x780F, // purple
    0xD340, // orange
    0x07E4, // teal green
    0xB882, // muted red
};
const int ACCENT_COUNT = 5;

struct TrackState {
    bool     is_playing  = false;
    bool     has_track   = false;
    String   track       = "";
    String   artist      = "";
    uint32_t progress_ms = 0;
    uint32_t duration_ms = 0;
};

TrackState current;

const unsigned long POLL_INTERVAL_MS = 5000;
unsigned long lastPoll = 0;

// --- Helpers ---

String msToTime(uint32_t ms) {
    uint32_t secs = ms / 1000;
    uint32_t m    = secs / 60;
    uint32_t s    = secs % 60;
    return String(m) + ":" + (s < 10 ? "0" : "") + String(s);
}

uint8_t accentIndex(const String &track) {
    uint32_t h = 0;
    for (char c : track) h += (uint8_t)c;
    return h % ACCENT_COUNT;
}

String truncate(const String &text, uint8_t font, int maxPx) {
    if (tft.textWidth(text, font) <= maxPx) return text;
    String t = text;
    while (t.length() > 0 && tft.textWidth(t + "...", font) > maxPx)
        t = t.substring(0, t.length() - 1);
    return t + "...";
}

// --- WiFi ---

void initWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE, INADDR_NONE);
    WiFi.setHostname(hostname);
    WiFi.begin(ssid, password);
    Serial.print("Connecting to WiFi...");
    while (WiFi.status() != WL_CONNECTED) {
        Serial.print('.');
        delay(1000);
    }
    Serial.printf("\nConnected! IP: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("Hostname: %s\n", WiFi.getHostname());
    Serial.printf("RSSI: %d\n", WiFi.RSSI());
}

// --- Display (stubs, filled in Tasks 2-4) ---

void drawArcProgress(const TrackState &state) {}
void drawNowPlaying(const TrackState &state) {}

// --- Networking ---

void fetchNowPlaying() {}

// --- Arduino entry points ---

void setup() {
    Serial.begin(115200);
    tft.init();
    tft.setRotation(0);
    tft.fillScreen(TFT_BLACK);
    initWiFi();
}

void loop() {
    unsigned long now = millis();
    if (now - lastPoll >= POLL_INTERVAL_MS) {
        lastPoll = now;
        if (WiFi.status() == WL_CONNECTED) {
            fetchNowPlaying();
        } else {
            Serial.println("WiFi disconnected, reconnecting...");
            initWiFi();
        }
    }
}
```

- [ ] **Step 2: Verify it compiles**

```bash
pio run
```

Expected: `SUCCESS` with no errors. Warnings about unused functions are fine.

- [ ] **Step 3: Commit**

```bash
git add src/main.cpp
git commit -m "feat: scaffold TrackState, helpers, and display stubs"
```

---

## Task 2: Implement `drawArcProgress()`

**Files:**
- Modify: `src/main.cpp` — replace the `drawArcProgress` stub

The arc ring lives at radius 116 (outer) / 111 (inner), centered at (120, 120). A background full-circle ring is drawn first in a dim color, then the progress arc is drawn on top in green (playing) or grey (paused).

- [ ] **Step 1: Replace the `drawArcProgress` stub**

Find and replace:
```cpp
void drawArcProgress(const TrackState &state) {}
```

With:
```cpp
void drawArcProgress(const TrackState &state) {
    uint16_t bgRing = state.has_track ? COL_DARK_GREY : COL_IDLE_RING;
    tft.drawArc(CX, CY, ARC_R_OUTER, ARC_R_INNER, 0, 360, bgRing, TFT_BLACK);

    if (!state.has_track || state.duration_ms == 0) return;

    uint32_t deg = (uint32_t)((float)state.progress_ms / state.duration_ms * 360.0f);
    if (deg > 360) deg = 360;
    if (deg == 0)  return;

    uint16_t arcCol = state.is_playing ? COL_SPOTIFY_GREEN : COL_GREY;
    tft.drawArc(CX, CY, ARC_R_OUTER, ARC_R_INNER, 0, deg, arcCol, TFT_BLACK);
}
```

- [ ] **Step 2: Verify it compiles**

```bash
pio run
```

Expected: `SUCCESS`.

- [ ] **Step 3: Commit**

```bash
git add src/main.cpp
git commit -m "feat: implement arc progress ring drawing"
```

---

## Task 3: Implement `drawNowPlaying()`

**Files:**
- Modify: `src/main.cpp` — replace the `drawNowPlaying` stub

Full redraw: clears screen, draws arc ring, album art placeholder, track name, artist, and elapsed/total time. Text is center-aligned at x=120 using `TC_DATUM`.

- [ ] **Step 1: Replace the `drawNowPlaying` stub**

Find and replace:
```cpp
void drawNowPlaying(const TrackState &state) {}
```

With:
```cpp
void drawNowPlaying(const TrackState &state) {
    tft.fillScreen(TFT_BLACK);
    drawArcProgress(state);

    // Album art placeholder
    uint16_t artCol;
    if (!state.has_track) {
        artCol = 0x1082; // near-black
    } else if (!state.is_playing) {
        artCol = COL_PAUSED_ART;
    } else {
        artCol = ACCENT_COLORS[accentIndex(state.track)];
    }
    tft.fillRoundRect(ART_X, ART_Y, ART_SIZE, ART_SIZE, 8, artCol);

    // Text
    tft.setTextDatum(TC_DATUM);

    if (!state.has_track) {
        tft.setTextFont(2);
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.drawString("Not playing", CX, 142);
        return;
    }

    uint16_t trackCol = state.is_playing ? TFT_WHITE : COL_DIM_WHITE;

    tft.setTextFont(2);
    tft.setTextColor(trackCol, TFT_BLACK);
    tft.drawString(truncate(state.track, 2, 180), CX, 138);

    tft.setTextFont(1);
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.drawString(truncate(state.artist, 1, 180), CX, 158);

    tft.setTextFont(1);
    tft.setTextColor(COL_DARK_GREY, TFT_BLACK);
    String timeStr = msToTime(state.progress_ms) + " / " + msToTime(state.duration_ms);
    tft.drawString(timeStr, CX, 174);
}
```

- [ ] **Step 2: Verify it compiles**

```bash
pio run
```

Expected: `SUCCESS`.

- [ ] **Step 3: Update `setup()` to show idle state while WiFi connects**

Find and replace in `setup()`:
```cpp
    tft.fillScreen(TFT_BLACK);
    initWiFi();
```

With:
```cpp
    drawNowPlaying(current); // shows "Not playing" while connecting
    initWiFi();
```

- [ ] **Step 4: Compile again**

```bash
pio run
```

Expected: `SUCCESS`.

- [ ] **Step 5: Commit**

```bash
git add src/main.cpp
git commit -m "feat: implement full display redraw for now-playing screen"
```

---

## Task 4: Implement `fetchNowPlaying()` with smart redraw

**Files:**
- Modify: `src/main.cpp` — replace the `fetchNowPlaying` stub

Parse all fields from the API response, compare with current state, and trigger a full redraw only when the track or play-state changes. Otherwise do an arc-only update (no flicker).

- [ ] **Step 1: Replace the `fetchNowPlaying` stub**

Find and replace:
```cpp
void fetchNowPlaying() {}
```

With:
```cpp
void fetchNowPlaying() {
    HTTPClient http;
    http.begin(String(serverUrl) + "/v1/spotify/now-playing");
    http.addHeader("X-API-Key", apiKey);

    int code = http.GET();
    if (code != 200) {
        Serial.printf("HTTP error: %d\n", code);
        http.end();
        return;
    }

    String payload = http.getString();
    http.end();

    JsonDocument doc;
    if (deserializeJson(doc, payload)) {
        Serial.println("JSON parse error");
        return;
    }

    TrackState next;
    next.is_playing  = doc["is_playing"]  | false;
    next.track       = doc["track"]       | "";
    next.artist      = doc["artist"]      | "";
    next.progress_ms = doc["progress_ms"] | 0;
    next.duration_ms = doc["duration_ms"] | 0;
    next.has_track   = next.track.length() > 0;

    bool identity_changed = (next.track != current.track) ||
                            (next.is_playing != current.is_playing);
    current = next;

    if (identity_changed) {
        drawNowPlaying(current);
    } else {
        drawArcProgress(current);
    }

    if (current.has_track) {
        Serial.printf("%s - %s  [%s / %s]  %s\n",
            current.artist.c_str(),
            current.track.c_str(),
            msToTime(current.progress_ms).c_str(),
            msToTime(current.duration_ms).c_str(),
            current.is_playing ? "playing" : "paused");
    } else {
        Serial.println("Not playing");
    }
}
```

- [ ] **Step 2: Verify it compiles**

```bash
pio run
```

Expected: `SUCCESS`.

- [ ] **Step 3: Commit**

```bash
git add src/main.cpp
git commit -m "feat: implement Spotify polling with smart display redraw"
```

---

## Task 5: Flash and verify end-to-end

- [ ] **Step 1: Flash to device**

```bash
pio run --target upload
```

Expected: `SUCCESS` followed by upload completion.

- [ ] **Step 2: Open serial monitor**

```bash
pio device monitor
```

Expected sequence:
```
Connecting to WiFi....
Connected! IP: 192.168.x.x
Hostname: esp32-dashboard
RSSI: -xx
```
Then every 5 seconds, one of:
```
Artist - Track  [1:24 / 3:20]  playing
```
or:
```
Not playing
```

- [ ] **Step 3: Visual checks on device**

| Scenario | Expected display |
|----------|-----------------|
| Boot (before WiFi) | Black screen, "Not playing" text, dim arc ring |
| Nothing playing | Same idle state |
| Track playing | Colored art square, track + artist text, green arc filling by progress |
| Paused | Same art (darker), track text dimmed, grey arc |
| Track skipped | Full redraw within 5s — new accent color |
| Long track name | Text ends with `...` and doesn't overflow |

- [ ] **Step 4: Commit if any tweaks were made during testing**

```bash
git add src/main.cpp
git commit -m "fix: display tuning after on-device testing"
```
