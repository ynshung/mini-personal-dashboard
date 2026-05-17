# Full-Screen Album Art Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the colored rectangle album art placeholder with actual Spotify album art rendered full-screen on the GC9A01 round display, composited server-side with Pillow.

**Architecture:** The FastAPI server gains a `/now-playing/art` endpoint that fetches album art from Spotify, composites it with a gradient overlay, text, and circular mask using Pillow, converts to RGB565, and streams the binary to the ESP32. The existing `/now-playing` endpoint is simplified to return only `track_id`, `is_playing`, `progress_ms`, `duration_ms`. The ESP32 polls the lightweight endpoint every 5s, fetches the heavy image only on track change, and locally interpolates the progress bar between polls.

**Tech Stack:** Python (FastAPI, Pillow, httpx), C++ (Arduino, TFT_eSPI, ArduinoJson, HTTPClient)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `server/routes/album_art.py` | Create | Pillow compositing pipeline: fetch art, resize, gradient, circular mask, text, RGB565 conversion, cache management |
| `server/routes/spotify.py` | Modify | Simplify `/now-playing` response; add `/now-playing/art` endpoint that delegates to `album_art.py` |
| `server/pyproject.toml` | Modify | Add `Pillow>=11` dependency |
| `.gitignore` | Modify | Add `server/.album_art_cache/` |
| `server/fonts/Inter-SemiBold.ttf` | Create | Bundled font for track title rendering |
| `server/fonts/Inter-Regular.ttf` | Create | Bundled font for artist name rendering |
| `src/main.cpp` | Modify | Simplified TrackState, streaming art fetch, end-of-song polling, updated rendering |

---

## Task 1: Add Pillow dependency and gitignore entry

**Files:**
- Modify: `server/pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Add Pillow to pyproject.toml**

In `server/pyproject.toml`, add `Pillow>=11` to the dependencies list:

```toml
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.34",
    "httpx>=0.28",
    "python-dotenv>=1.0",
    "Pillow>=11",
]
```

- [ ] **Step 2: Add cache directory to .gitignore**

Append to `.gitignore`:

```
server/.album_art_cache/
```

- [ ] **Step 3: Commit**

```bash
git add server/pyproject.toml .gitignore
git commit -m "chore: add Pillow dependency and gitignore album art cache"
```

---

## Task 2: Download bundled fonts

**Files:**
- Create: `server/fonts/Inter-SemiBold.ttf`
- Create: `server/fonts/Inter-Regular.ttf`

- [ ] **Step 1: Download Inter font files**

Download the Inter font family (OFL licensed) from Google Fonts. We need two weights:

```bash
mkdir -p server/fonts
curl -L -o /tmp/Inter.zip "https://fonts.google.com/download?family=Inter"
unzip -o /tmp/Inter.zip -d /tmp/Inter
cp /tmp/Inter/static/Inter_28pt-SemiBold.ttf server/fonts/Inter-SemiBold.ttf
cp /tmp/Inter/static/Inter_28pt-Regular.ttf server/fonts/Inter-Regular.ttf
rm -rf /tmp/Inter /tmp/Inter.zip
```

If the exact filenames inside the zip differ, look for the `static/` directory and pick the SemiBold and Regular variants of the 28pt optical size (best for small displays).

- [ ] **Step 2: Commit**

```bash
git add server/fonts/
git commit -m "chore: bundle Inter font for album art text rendering"
```

---

## Task 3: Implement the image compositing pipeline

**Files:**
- Create: `server/routes/album_art.py`

This is the core module. It handles: fetching album art, resizing, gradient overlay, circular mask, text compositing, RGB565 conversion, and cache management.

- [ ] **Step 1: Create `server/routes/album_art.py` with all compositing functions**

```python
import struct
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

CACHE_DIR = Path(__file__).parent.parent / ".album_art_cache"
FONTS_DIR = Path(__file__).parent.parent / "fonts"
MAX_CACHE_ENTRIES = 50
IMG_SIZE = 240
CIRCLE_RADIUS = 110


def _get_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


FONT_TITLE = _get_font("Inter-SemiBold.ttf", 15)
FONT_ARTIST = _get_font("Inter-Regular.ttf", 12)


def _prune_cache() -> None:
    if not CACHE_DIR.exists():
        return
    files = sorted(CACHE_DIR.glob("*.png"), key=lambda f: f.stat().st_atime)
    while len(files) > MAX_CACHE_ENTRIES:
        files.pop(0).unlink()


async def fetch_and_build_base(art_url: str, album_id: str) -> Image.Image:
    cache_path = CACHE_DIR / f"{album_id}.png"

    if cache_path.exists():
        cache_path.touch()
        return Image.open(cache_path).convert("RGB")

    async with httpx.AsyncClient() as client:
        resp = await client.get(art_url, timeout=10)
    resp.raise_for_status()

    img = Image.open(BytesIO(resp.content)).convert("RGB")
    img = img.resize((IMG_SIZE, IMG_SIZE), Image.LANCZOS)

    gradient = Image.new("L", (IMG_SIZE, IMG_SIZE), 0)
    for y in range(IMG_SIZE):
        if y <= 120:
            alpha = 0
        elif y <= 132:
            alpha = 0
        else:
            alpha = int((y - 132) / (IMG_SIZE - 132) * 204)
        for x in range(IMG_SIZE):
            gradient.putpixel((x, y), alpha)

    black = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
    img = Image.composite(black, img, gradient)

    mask = Image.new("L", (IMG_SIZE, IMG_SIZE), 0)
    draw = ImageDraw.Draw(mask)
    cx, cy = IMG_SIZE // 2, IMG_SIZE // 2
    draw.ellipse(
        (cx - CIRCLE_RADIUS, cy - CIRCLE_RADIUS,
         cx + CIRCLE_RADIUS, cy + CIRCLE_RADIUS),
        fill=255,
    )
    bg = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
    img = Image.composite(img, bg, mask)

    CACHE_DIR.mkdir(exist_ok=True)
    img.save(cache_path, "PNG")
    _prune_cache()

    return img


def composite_text(base: Image.Image, track: str, artist: str) -> Image.Image:
    img = base.copy()
    draw = ImageDraw.Draw(img)

    title_bbox = draw.textbbox((0, 0), track, font=FONT_TITLE)
    title_w = title_bbox[2] - title_bbox[0]
    if title_w > 180:
        while title_w > 170 and len(track) > 0:
            track = track[:-1]
            title_bbox = draw.textbbox((0, 0), track + "...", font=FONT_TITLE)
            title_w = title_bbox[2] - title_bbox[0]
        track = track + "..."
        title_bbox = draw.textbbox((0, 0), track, font=FONT_TITLE)
        title_w = title_bbox[2] - title_bbox[0]

    title_x = (IMG_SIZE - title_w) // 2
    draw.text((title_x, 187), track, fill=(255, 255, 255), font=FONT_TITLE)

    artist_bbox = draw.textbbox((0, 0), artist, font=FONT_ARTIST)
    artist_w = artist_bbox[2] - artist_bbox[0]
    if artist_w > 180:
        while artist_w > 170 and len(artist) > 0:
            artist = artist[:-1]
            artist_bbox = draw.textbbox((0, 0), artist + "...", font=FONT_ARTIST)
            artist_w = artist_bbox[2] - artist_bbox[0]
        artist = artist + "..."
        artist_bbox = draw.textbbox((0, 0), artist, font=FONT_ARTIST)
        artist_w = artist_bbox[2] - artist_bbox[0]

    artist_x = (IMG_SIZE - artist_w) // 2
    draw.text((artist_x, 205), artist, fill=(179, 179, 179), font=FONT_ARTIST)

    return img


def to_rgb565(img: Image.Image) -> bytes:
    pixels = img.tobytes()
    out = bytearray(IMG_SIZE * IMG_SIZE * 2)
    for i in range(IMG_SIZE * IMG_SIZE):
        r, g, b = pixels[i * 3], pixels[i * 3 + 1], pixels[i * 3 + 2]
        rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        struct.pack_into(">H", out, i * 2, rgb565)
    return bytes(out)
```

- [ ] **Step 2: Verify module imports cleanly**

```bash
cd server && uv run python -c "from routes.album_art import fetch_and_build_base, composite_text, to_rgb565; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add server/routes/album_art.py
git commit -m "feat: add Pillow compositing pipeline for album art"
```

---

## Task 4: Simplify `/now-playing` and add `/now-playing/art` endpoint

**Files:**
- Modify: `server/routes/spotify.py`

- [ ] **Step 1: Simplify the `/now-playing` response**

Replace the `spotify_now_playing` handler (lines 162–195 of `server/routes/spotify.py`) with:

```python
@router.get("/spotify/now-playing")
async def spotify_now_playing():
    token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.spotify.com/v1/me/player/currently-playing",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 204:
        return {"is_playing": False}

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Spotify API error: {resp.status_code}")

    data = resp.json()

    if data.get("currently_playing_type") != "track":
        return {"is_playing": False}

    item = data.get("item", {})

    return {
        "track_id": item.get("id", ""),
        "is_playing": data.get("is_playing", False),
        "progress_ms": data.get("progress_ms", 0),
        "duration_ms": item.get("duration_ms", 0),
    }
```

- [ ] **Step 2: Add the `/now-playing/art` endpoint**

Add these imports at the top of `server/routes/spotify.py`:

```python
from fastapi.responses import Response
from routes.album_art import fetch_and_build_base, composite_text, to_rgb565
```

Then add the new endpoint after the existing `/now-playing` handler:

```python
@router.get("/spotify/now-playing/art")
async def spotify_now_playing_art():
    token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.spotify.com/v1/me/player/currently-playing",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 204:
        return Response(status_code=204)

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Spotify API error: {resp.status_code}")

    data = resp.json()

    if data.get("currently_playing_type") != "track":
        return Response(status_code=204)

    item = data.get("item", {})
    artists = ", ".join(a["name"] for a in item.get("artists", []))
    images = item.get("album", {}).get("images", [])
    art_url = next((img["url"] for img in reversed(images) if img["width"] >= 240), None)
    album_id = item.get("album", {}).get("id", "unknown")

    if not art_url:
        return Response(status_code=204)

    base = await fetch_and_build_base(art_url, album_id)
    final = composite_text(base, item.get("name", ""), artists)
    rgb565 = to_rgb565(final)

    return Response(content=rgb565, media_type="application/octet-stream")
```

- [ ] **Step 3: Verify server starts and endpoints respond**

```bash
cd server && uv sync && uv run uvicorn main:app --host 0.0.0.0 --port 7333 &
sleep 2
curl -s -H "X-API-Key: $(grep API_KEY ../.env | cut -d= -f2)" http://localhost:7333/v1/spotify/now-playing | python -m json.tool
curl -s -o /tmp/art_test.bin -w "%{size_download}" -H "X-API-Key: $(grep API_KEY ../.env | cut -d= -f2)" http://localhost:7333/v1/spotify/now-playing/art
kill %1
```

Expected for `/now-playing`: JSON with `track_id`, `is_playing`, `progress_ms`, `duration_ms` (no `track`, `artist`, or `album_art_url`).

Expected for `/now-playing/art`: file size of `115200` bytes (if a track is playing), or empty response with 204 status.

- [ ] **Step 4: Commit**

```bash
git add server/routes/spotify.py
git commit -m "feat: simplify now-playing response and add album art endpoint"
```

---

## Task 5: Update ESP32 firmware — simplified polling and art streaming

**Files:**
- Modify: `src/main.cpp`

- [ ] **Step 1: Replace the full `src/main.cpp`**

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

const uint16_t COL_GREY      = 0x52AA;
const uint16_t COL_BAR_BG    = 0x39C7; // white at 25% opacity on black
const uint16_t COL_BAR_FILL  = 0xE71C; // white at 90% opacity on black

const int CX    = 120;
const int BAR_X = 40;
const int BAR_Y = 210;
const int BAR_W = 160;
const int BAR_H = 3;

struct TrackState {
    bool     is_playing  = false;
    String   track_id    = "";
    uint32_t progress_ms = 0;
    uint32_t duration_ms = 0;
};

TrackState current;

const unsigned long POLL_INTERVAL_MS = 5000;
const unsigned long TICK_INTERVAL_MS = 250;
unsigned long lastPoll    = 0;
unsigned long lastTick    = 0;
unsigned long lastFetchMs = 0;
bool hasArt = false;

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

// --- Display ---

void drawProgressBar(uint32_t progress_ms, uint32_t duration_ms, bool is_playing) {
    tft.fillRect(BAR_X, BAR_Y, BAR_W, BAR_H, COL_BAR_BG);
    if (duration_ms == 0) return;
    int fillW = (int)((float)progress_ms / duration_ms * BAR_W);
    if (fillW > BAR_W) fillW = BAR_W;
    if (fillW > 0 && is_playing)
        tft.fillRect(BAR_X, BAR_Y, fillW, BAR_H, COL_BAR_FILL);
}

void drawTick() {
    if (!current.is_playing || current.duration_ms == 0) return;
    uint32_t estimated = current.progress_ms + (uint32_t)(millis() - lastFetchMs);
    if (estimated > current.duration_ms) estimated = current.duration_ms;
    drawProgressBar(estimated, current.duration_ms, true);
}

void drawIdle() {
    tft.fillScreen(TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextFont(2);
    tft.setTextColor(COL_GREY, TFT_BLACK);
    tft.drawString("Not playing", CX, CX);
    hasArt = false;
}

// --- Album art streaming ---

bool fetchAlbumArt() {
    HTTPClient http;
    http.begin(String(serverUrl) + "/v1/spotify/now-playing/art");
    http.addHeader("X-API-Key", apiKey);

    int code = http.GET();
    if (code == 204) {
        http.end();
        return false;
    }
    if (code != 200) {
        Serial.printf("Art fetch HTTP error: %d\n", code);
        http.end();
        return false;
    }

    int contentLength = http.getSize();
    if (contentLength != 240 * 240 * 2) {
        Serial.printf("Art unexpected size: %d\n", contentLength);
        http.end();
        return false;
    }

    WiFiClient *stream = http.getStreamPtr();
    uint16_t rowBuf[240];
    int y = 0;

    tft.startWrite();
    while (y < 240 && stream->connected()) {
        size_t avail = stream->available();
        if (avail < 480) {
            delay(1);
            continue;
        }
        stream->readBytes((uint8_t *)rowBuf, 480);
        tft.pushImage(0, y, 240, 1, rowBuf);
        y++;
    }
    tft.endWrite();

    http.end();

    if (y == 240) {
        Serial.println("Album art loaded");
        hasArt = true;
        return true;
    }

    Serial.printf("Art incomplete: %d/240 rows\n", y);
    return false;
}

// --- Networking ---

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
    next.track_id    = doc["track_id"]    | "";
    next.progress_ms = doc["progress_ms"] | 0;
    next.duration_ms = doc["duration_ms"] | 0;

    bool track_changed = (next.track_id != current.track_id);
    bool play_state_changed = (next.is_playing != current.is_playing);

    current = next;
    lastFetchMs = millis();

    if (current.track_id.length() == 0) {
        if (hasArt || track_changed) drawIdle();
        return;
    }

    if (track_changed) {
        fetchAlbumArt();
        drawProgressBar(current.progress_ms, current.duration_ms, current.is_playing);
    } else if (play_state_changed) {
        drawProgressBar(current.progress_ms, current.duration_ms, current.is_playing);
    }

    Serial.printf("[%s] %s  [%u/%u ms]\n",
        current.is_playing ? "PLAY" : "PAUSE",
        current.track_id.c_str(),
        current.progress_ms,
        current.duration_ms);
}

// --- Arduino entry points ---

void setup() {
    Serial.begin(115200);
    tft.init();
    tft.setRotation(0);
    drawIdle();
    initWiFi();
}

void loop() {
    unsigned long now = millis();

    // End-of-song poll: immediately check when estimated progress reaches duration
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
            initWiFi();
        }
    }

    if (current.is_playing && (now - lastTick >= TICK_INTERVAL_MS)) {
        lastTick = now;
        drawTick();
    }
}
```

- [ ] **Step 2: Verify firmware compiles**

```bash
pio run
```

Expected: `SUCCESS` with no errors. Warnings about unused variables are acceptable.

- [ ] **Step 3: Commit**

```bash
git add src/main.cpp
git commit -m "feat: stream full-screen album art from server to GC9A01 display"
```

---

## Task 6: End-to-end verification

This is a manual testing task — no code changes.

- [ ] **Step 1: Install server dependencies**

```bash
cd server && uv sync
```

- [ ] **Step 2: Start the server**

```bash
cd server && uv run uvicorn main:app --host 0.0.0.0 --port 7333 --reload
```

- [ ] **Step 3: Test `/now-playing` returns simplified JSON**

```bash
curl -s -H "X-API-Key: YOUR_KEY" http://localhost:7333/v1/spotify/now-playing | python -m json.tool
```

Verify: response has only `track_id`, `is_playing`, `progress_ms`, `duration_ms` — no `track`, `artist`, or `album_art_url`.

- [ ] **Step 4: Test `/now-playing/art` returns correct binary size**

```bash
curl -s -o /tmp/art.bin -w "bytes: %{size_download}, status: %{http_code}\n" -H "X-API-Key: YOUR_KEY" http://localhost:7333/v1/spotify/now-playing/art
```

Verify: `bytes: 115200, status: 200` (when a track is playing). Check `server/.album_art_cache/` has a `.png` file.

- [ ] **Step 5: Flash firmware and verify display**

```bash
pio run --target upload && pio device monitor
```

Verify:
1. Boot → shows "Not playing" on black screen
2. Play a Spotify track → album art appears full-screen within 5–10s
3. Text (track + artist) visible in gradient region at bottom
4. Progress bar animates smoothly
5. Skip track → new art loads within 5–10s
6. Pause → progress bar stops filling
7. Serial monitor → no HTTP errors, JSON parse errors, or stack overflows
8. Let a song finish → next song auto-detected and art reloads
