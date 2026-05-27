# Pinterest Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Pinterest screen to the ESP32 dashboard that displays smart-cropped full-screen photos from a Pinterest board, auto-rotating every 5 minutes with manual advance via button press.

**Architecture:** A new FastAPI router (`server/routes/pinterest.py`) mirrors the Spotify OAuth pattern — one-time browser auth flow, tokens cached to disk, access token auto-refreshed. The `/v1/pinterest/image` endpoint picks a random pin URL from an in-memory cache (refreshed hourly), smart-crops with `smartcrop`, applies the existing circular mask from `rtsp.py`, and caches processed JPEGs to disk. The ESP32 adds a `PINTEREST` enum variant, polls the endpoint on a 5-min timer, and advances on single button press.

**Tech Stack:** FastAPI, httpx, Pillow, smartcrop (new), Pinterest API v5, ESP32 Arduino / HTTPClient / TJpgDec

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `server/routes/pinterest.py` | **Create** | OAuth flow, pin list cache, image processing, `/pinterest/image` endpoint |
| `server/main.py` | **Modify** | Register `pinterest.router`; add auth paths to `OPEN_PATHS` |
| `server/pyproject.toml` | **Modify** | Add `smartcrop` dependency |
| `.gitignore` | **Modify** | Add `server/.pinterest_cache/` and `server/.pinterest_tokens.json` |
| `src/main.cpp` | **Modify** | `PINTEREST` enum, `fetchPinterestImage()`, `activateScreen`, `loop`, button handlers, screen cycle |

---

## Task 1: Dependencies & Gitignore

**Files:**
- Modify: `server/pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Add `smartcrop` to pyproject.toml**

Edit `server/pyproject.toml`, add `"smartcrop>=0.4"` to the `dependencies` list:

```toml
[project]
name = "personal-dashboard-nodemcu"
version = "0.1.0"
description = "FastAPI server and ESP32 firmware for a NodeMCU personal dashboard display"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.34",
    "httpx>=0.28",
    "python-dotenv>=1.0",
    "Pillow>=11",
    "av>=14",
    "smartcrop>=0.4",
]

[project.optional-dependencies]
dev = ["pytest>=8"]
```

- [ ] **Step 2: Add Pinterest cache files to .gitignore**

Edit `.gitignore`, add two lines after the existing `server/.album_art_cache/` entry:

```
server/.album_art_cache/
server/.pinterest_cache/
server/.pinterest_tokens.json
```

- [ ] **Step 3: Install the new dependency**

Run from `server/`:
```bash
uv sync
```
Expected: resolves and installs `smartcrop` with no errors.

- [ ] **Step 4: Commit**

```bash
git add server/pyproject.toml server/uv.lock .gitignore
git commit -m "chore: add smartcrop dependency, gitignore pinterest cache"
```

---

## Task 2: Pinterest Route — Auth Flow

**Files:**
- Create: `server/routes/pinterest.py`

- [ ] **Step 1: Create the file with auth helpers and endpoints**

Create `server/routes/pinterest.py` with the full content below. This task covers only the auth layer (tokens file, `_get_access_token()`, `/pinterest/auth`, `/pinterest/callback`). The image endpoint is added in Task 4.

```python
import hashlib
import json
import os
import random
import time
from io import BytesIO
from pathlib import Path

import httpx
import smartcrop
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, Response
from PIL import Image, ImageDraw, ImageFont

from routes.rtsp import IMG_SIZE, apply_circular_mask

router = APIRouter()

# --- Paths & constants ---

TOKENS_FILE = Path(__file__).parent.parent / ".pinterest_tokens.json"
CACHE_DIR   = Path(__file__).parent.parent / ".pinterest_cache"
FONTS_DIR   = Path(__file__).parent.parent / "fonts"
MAX_CACHE_ENTRIES = 50

PINTEREST_API_BASE = "https://api.pinterest.com/v5"
PINTEREST_AUTH_URL = "https://www.pinterest.com/oauth/"
PINTEREST_TOKEN_URL = f"{PINTEREST_API_BASE}/oauth/token"
REDIRECT_URI = "http://127.0.0.1:7333/v1/pinterest/callback"
SCOPES = "boards:read,pins:read"

COL_GREY = (82, 85, 82)

_FONT: ImageFont.FreeTypeFont | None = None


def _get_font() -> ImageFont.FreeTypeFont:
    global _FONT
    if _FONT is None:
        path = FONTS_DIR / "NotoSansCJK-Medium.ttc"
        try:
            _FONT = ImageFont.truetype(str(path), 14)
        except OSError:
            _FONT = ImageFont.load_default(14)
    return _FONT


# --- Token helpers (mirrors spotify.py) ---

def _client_id() -> str:
    value = os.getenv("PINTEREST_CLIENT_ID")
    if not value:
        raise HTTPException(status_code=500, detail="PINTEREST_CLIENT_ID not set in .env")
    return value


def _client_secret() -> str:
    value = os.getenv("PINTEREST_CLIENT_SECRET")
    if not value:
        raise HTTPException(status_code=500, detail="PINTEREST_CLIENT_SECRET not set in .env")
    return value


def _load_tokens() -> dict:
    if not TOKENS_FILE.exists():
        raise HTTPException(
            status_code=503,
            detail="Pinterest not authenticated. Visit /v1/pinterest/auth",
        )
    return json.loads(TOKENS_FILE.read_text())


def _save_tokens(tokens: dict) -> None:
    TOKENS_FILE.write_text(json.dumps(tokens))


async def _get_access_token() -> str:
    tokens = _load_tokens()

    if tokens.get("expires_at", 0) > time.time() + 30:
        return tokens["access_token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            PINTEREST_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            },
            auth=(_client_id(), _client_secret()),
        )

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Pinterest token refresh failed: {resp.status_code}")

    data = resp.json()
    tokens["access_token"] = data["access_token"]
    tokens["expires_at"] = time.time() + data["expires_in"]
    if "refresh_token" in data:
        tokens["refresh_token"] = data["refresh_token"]
    _save_tokens(tokens)
    return tokens["access_token"]


# --- Auth endpoints ---

@router.get("/pinterest/auth")
async def pinterest_auth():
    TOKENS_FILE.unlink(missing_ok=True)
    params = (
        f"?client_id={_client_id()}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={SCOPES}"
    )
    return RedirectResponse(url=PINTEREST_AUTH_URL + params)


@router.get("/pinterest/callback")
async def pinterest_callback(code: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            PINTEREST_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            auth=(_client_id(), _client_secret()),
        )

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Pinterest token exchange failed: {resp.status_code}")

    data = resp.json()
    _save_tokens({
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": time.time() + data["expires_in"],
    })
    return {"detail": "Pinterest authorized successfully"}
```

- [ ] **Step 2: Commit**

```bash
git add server/routes/pinterest.py
git commit -m "feat(pinterest): add OAuth auth flow"
```

---

## Task 3: Pinterest Route — Pin List Cache

**Files:**
- Modify: `server/routes/pinterest.py`

- [ ] **Step 1: Add module-level cache state and `_ensure_pin_urls()`**

Append the following to `server/routes/pinterest.py`, after the `pinterest_callback` function:

```python
# --- Pin list cache ---

_pin_urls: list[str] = []
_pin_urls_fetched_at: float = 0.0
_PIN_CACHE_TTL = 3600.0   # refresh hourly
_PIN_MAX = 500             # cap at 500 pins total


async def _ensure_pin_urls(token: str) -> None:
    """Populate _pin_urls from the Pinterest API, or use the in-memory cache."""
    global _pin_urls, _pin_urls_fetched_at

    if _pin_urls and (time.time() - _pin_urls_fetched_at) < _PIN_CACHE_TTL:
        return

    board_id = os.getenv("PINTEREST_BOARD_ID")
    if not board_id:
        raise HTTPException(status_code=500, detail="PINTEREST_BOARD_ID not set in .env")

    urls: list[str] = []
    bookmark: str | None = None

    async with httpx.AsyncClient() as client:
        while len(urls) < _PIN_MAX:
            params: dict = {"page_size": 250}
            if bookmark:
                params["bookmark"] = bookmark

            resp = await client.get(
                f"{PINTEREST_API_BASE}/boards/{board_id}/pins",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=15,
            )
            if not resp.is_success:
                print(f"[Pinterest] Board API error {resp.status_code}: {resp.text[:200]}")
                break

            data = resp.json()
            for pin in data.get("items", []):
                media = pin.get("media", {})
                if media.get("media_type") != "image":
                    continue
                images = media.get("images", {})
                # Prefer 1200x, fall back to smaller sizes
                img_data = (
                    images.get("1200x")
                    or images.get("600x")
                    or images.get("400x300")
                )
                if img_data and img_data.get("url"):
                    urls.append(img_data["url"])

            bookmark = data.get("bookmark")
            if not bookmark:
                break

    _pin_urls = urls
    _pin_urls_fetched_at = time.time()
    print(f"[Pinterest] Cached {len(_pin_urls)} pin image URLs from board '{board_id}'")
```

- [ ] **Step 2: Commit**

```bash
git add server/routes/pinterest.py
git commit -m "feat(pinterest): add pin list cache with hourly refresh"
```

---

## Task 4: Pinterest Route — Image Processing & Endpoint

**Files:**
- Modify: `server/routes/pinterest.py`

- [ ] **Step 1: Add image processing helpers and the `/pinterest/image` endpoint**

Append the following to `server/routes/pinterest.py`, after `_ensure_pin_urls`:

```python
# --- Image processing ---

def _prune_cache() -> None:
    if not CACHE_DIR.exists():
        return
    files = sorted(CACHE_DIR.glob("*.jpg"), key=lambda f: f.stat().st_atime)
    while len(files) > MAX_CACHE_ENTRIES:
        files.pop(0).unlink()


def _make_placeholder() -> bytes:
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _get_font()
    text = "No images"
    bbox = draw.textbbox((0, 0), text, font=font)
    x = IMG_SIZE // 2 - (bbox[2] - bbox[0]) // 2
    y = IMG_SIZE // 2 - (bbox[3] - bbox[1]) // 2
    draw.text((x, y), text, fill=COL_GREY, font=font)
    img = apply_circular_mask(img)
    buf = BytesIO()
    img.save(buf, "JPEG", quality=75)
    return buf.getvalue()


def _process_image(img: Image.Image) -> bytes:
    """Smart-crop to square, resize to 240×240, apply circular mask, encode JPEG."""
    # 1. Smart-crop: find best min(w,h) × min(w,h) region
    min_dim = min(img.width, img.height)
    sc = smartcrop.SmartCrop()
    result = sc.crop(img, min_dim, min_dim)
    crop = result["top_crop"]
    img = img.crop((
        crop["x"],
        crop["y"],
        crop["x"] + crop["width"],
        crop["y"] + crop["height"],
    ))
    # 2. Resize to display size
    img = img.resize((IMG_SIZE, IMG_SIZE), Image.LANCZOS)
    # 3. Circular mask
    img = apply_circular_mask(img)
    # 4. Encode
    buf = BytesIO()
    img.save(buf, "JPEG", quality=75, optimize=True)
    return buf.getvalue()


# --- Image endpoint ---

@router.get("/pinterest/image")
async def pinterest_image():
    token = await _get_access_token()
    await _ensure_pin_urls(token)

    if not _pin_urls:
        return Response(content=_make_placeholder(), media_type="image/jpeg")

    url = random.choice(_pin_urls)
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    cache_path = CACHE_DIR / f"{url_hash}.jpg"

    print(f"[Pinterest] {url}")

    if cache_path.exists():
        cache_path.touch()
        return Response(content=cache_path.read_bytes(), media_type="image/jpeg")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        jpeg = _process_image(img)
    except Exception as e:
        print(f"[Pinterest] Image fetch/process failed: {e}")
        return Response(content=_make_placeholder(), media_type="image/jpeg")

    CACHE_DIR.mkdir(exist_ok=True)
    cache_path.write_bytes(jpeg)
    _prune_cache()

    return Response(content=jpeg, media_type="image/jpeg")
```

- [ ] **Step 2: Commit**

```bash
git add server/routes/pinterest.py
git commit -m "feat(pinterest): add image processing and /pinterest/image endpoint"
```

---

## Task 5: Register Router in `server/main.py`

**Files:**
- Modify: `server/main.py`

- [ ] **Step 1: Import the router and update `OPEN_PATHS` and `include_router`**

Replace the contents of `server/main.py` with:

```python
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from routes.cc_usage import router as cc_usage_router
from routes.pinterest import router as pinterest_router
from routes.rtsp import router as rtsp_router
from routes.spotify import router as spotify_router

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)-9s %(name)s - %(message)s")

app = FastAPI()

OPEN_PATHS = {
    "/v1/spotify/auth",
    "/v1/spotify/callback",
    "/v1/pinterest/auth",
    "/v1/pinterest/callback",
}


@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    if os.getenv("DEVELOPMENT_MODE", "").lower() in ("1", "true", "yes"):
        return await call_next(request)
    if request.url.path not in OPEN_PATHS:
        expected = os.getenv("API_KEY")
        key = request.headers.get("X-API-Key")
        if not expected or key != expected:
            return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
    return await call_next(request)


app.include_router(cc_usage_router, prefix="/v1")
app.include_router(pinterest_router, prefix="/v1")
app.include_router(rtsp_router, prefix="/v1")
app.include_router(spotify_router, prefix="/v1")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7333)
```

- [ ] **Step 2: Start the server and verify the routes exist**

Run from `server/`:
```bash
uv run uvicorn main:app --host 0.0.0.0 --port 7333 --reload
```
Expected: no import errors. In another terminal:
```bash
curl -s http://localhost:7333/openapi.json | python3 -c "import json,sys; paths=json.load(sys.stdin)['paths']; print([p for p in paths if 'pinterest' in p])"
```
Expected output:
```
['/v1/pinterest/auth', '/v1/pinterest/callback', '/v1/pinterest/image']
```

- [ ] **Step 3: Commit**

```bash
git add server/main.py
git commit -m "feat(pinterest): register router in main.py"
```

---

## Task 6: Firmware — Enum, State, `fetchPinterestImage()`

**Files:**
- Modify: `src/main.cpp`

- [ ] **Step 1: Add `PINTEREST` to the `Screen` enum**

Find line 37:
```cpp
enum Screen { CLOCK, SPOTIFY, CC_USAGE, RTSP };
```
Replace with:
```cpp
enum Screen { CLOCK, SPOTIFY, CC_USAGE, RTSP, PINTEREST };
```

- [ ] **Step 2: Add Pinterest state variables**

Find the line (around line 78):
```cpp
static volatile int rtspIndex       = 0;
```
Add the following two lines immediately **before** it:
```cpp
unsigned long lastPinterestFetch = 0;
const unsigned long PINTEREST_ROTATE_MS = 300000UL; // 5 minutes
```

- [ ] **Step 3: Add `fetchPinterestImage()` function**

Add this function immediately after `fetchAlbumArt()` (after its closing `}`, around line 480):

```cpp
void fetchPinterestImage() {
    if (WiFi.status() != WL_CONNECTED) return;

    HTTPClient http;
    http.begin(String(serverUrl) + "/v1/pinterest/image");
    http.addHeader("X-API-Key", apiKey);

    int code = http.GET();
    if (code != 200) {
        Serial.printf("Pinterest HTTP error: %d\n", code);
        http.end();
        if (serverUnreachableSince == 0) serverUnreachableSince = millis();
        if (!pollFailed) {
            pollFailed = true;
            drawStatus("Server unreachable");
        }
        return;
    }

    int contentLength = http.getSize();
    if (contentLength <= 0 || contentLength > 100000) {
        Serial.printf("Pinterest unexpected size: %d\n", contentLength);
        http.end();
        return;
    }

    uint8_t *buf = (uint8_t *)malloc(contentLength);
    if (!buf) {
        Serial.println("Pinterest malloc failed");
        http.end();
        return;
    }

    WiFiClient *stream = http.getStreamPtr();
    int received = 0;
    while (received < contentLength && stream->connected()) {
        int avail = stream->available();
        if (avail > 0) {
            int toRead = min(avail, contentLength - received);
            stream->readBytes(buf + received, toRead);
            received += toRead;
        } else {
            delay(1);
        }
    }
    http.end();

    if (received != contentLength) {
        Serial.printf("Pinterest incomplete: %d/%d bytes\n", received, contentLength);
        free(buf);
        return;
    }

    tft.startWrite();
    tft.setSwapBytes(true);
    TJpgDec.drawJpg(0, 0, buf, contentLength);
    tft.setSwapBytes(false);
    tft.endWrite();

    free(buf);
    serverUnreachableSince = 0;
    pollFailed = false;
    lastPinterestFetch = millis();
    Serial.println("Pinterest image loaded");
}
```

- [ ] **Step 4: Commit**

```bash
git add src/main.cpp
git commit -m "feat(pinterest): add PINTEREST enum, state vars, fetchPinterestImage()"
```

---

## Task 7: Firmware — `activateScreen`, `loop`, Buttons & Screen Cycle

**Files:**
- Modify: `src/main.cpp`

- [ ] **Step 1: Add PINTEREST case to `activateScreen()`**

Find in `activateScreen()` (around line 653):
```cpp
    } else if (s == RTSP) {
```
Add the PINTEREST branch immediately **after** the closing `}` of the `RTSP` block (after the `vTaskResume` line and its `}`):
```cpp
    } else if (s == PINTEREST) {
        drawStatus("Loading...");
        fetchPinterestImage();
        lastPinterestFetch = millis();
    }
```

The resulting end of `activateScreen()` will look like:
```cpp
    } else if (s == RTSP) {
        // drain any stale semaphore counts, then reset to initial state
        while (xSemaphoreTake(rtspReadySem, 0) == pdTRUE) {}
        while (xSemaphoreTake(rtspFreeSem, 0) == pdTRUE) {}
        xSemaphoreGive(rtspFreeSem);
        xSemaphoreGive(rtspFreeSem);
        rtspWriteIdx   = 0;
        rtspReadIdx    = 0;
        rtspFetchError = false;
        rtspErrorShown = false;
        drawStatus("Loading...");
        vTaskResume(rtspNetTaskHandle);
    } else if (s == PINTEREST) {
        drawStatus("Loading...");
        fetchPinterestImage();
        lastPinterestFetch = millis();
    }
}
```

- [ ] **Step 2: Add PINTEREST polling to `loop()`**

Find in `loop()` (around line 779):
```cpp
    } else if (activeScreen == RTSP) {
```
Add the PINTEREST branch immediately after the closing `}` of the `RTSP` block:
```cpp
    } else if (activeScreen == PINTEREST) {
        if (now - lastPinterestFetch >= PINTEREST_ROTATE_MS) {
            fetchPinterestImage();
        }
    }
```

- [ ] **Step 3: Update `btn` (GPIO 19) single-click handler for PINTEREST**

Find:
```cpp
    btn.attachClick([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) {
            rtspIndex = (rtspIndex + 1) % rtspStreamCount;
            return;
        }
        sendCommand("/v1/spotify/toggle");
    });
```
Replace with:
```cpp
    btn.attachClick([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) {
            rtspIndex = (rtspIndex + 1) % rtspStreamCount;
            return;
        }
        if (activeScreen == PINTEREST) {
            fetchPinterestImage();
            return;
        }
        sendCommand("/v1/spotify/toggle");
    });
```

- [ ] **Step 4: Update `btn` double-click and long-press handlers for PINTEREST**

Find:
```cpp
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
Replace with:
```cpp
    btn.attachDoubleClick([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) {
            rtspIndex = (rtspIndex - 1 + rtspStreamCount) % rtspStreamCount;
            return;
        }
        if (activeScreen == PINTEREST) return;
        sendCommand("/v1/spotify/next");
    });
    btn.attachLongPressStart([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) return;
        if (activeScreen == PINTEREST) return;
        sendCommand("/v1/spotify/previous");
    });
```

- [ ] **Step 5: Update `btn2` (GPIO 21) screen cycle — forward**

Find:
```cpp
    btn2.attachClick([]() {
        // Forward cycle: CLOCK -> CC_USAGE -> RTSP -> SPOTIFY -> CLOCK
        Screen next;
        if      (activeScreen == CLOCK)    next = CC_USAGE;
        else if (activeScreen == CC_USAGE) next = RTSP;
        else if (activeScreen == RTSP)     next = SPOTIFY;
        else                               next = CLOCK;
        activateScreen(next);
    });
```
Replace with:
```cpp
    btn2.attachClick([]() {
        // Forward cycle: CLOCK -> CC_USAGE -> RTSP -> SPOTIFY -> PINTEREST -> CLOCK
        Screen next;
        if      (activeScreen == CLOCK)      next = CC_USAGE;
        else if (activeScreen == CC_USAGE)   next = RTSP;
        else if (activeScreen == RTSP)       next = SPOTIFY;
        else if (activeScreen == SPOTIFY)    next = PINTEREST;
        else                                 next = CLOCK;
        activateScreen(next);
    });
```

- [ ] **Step 6: Update `btn2` backward cycle**

Find:
```cpp
    btn2.attachDoubleClick([]() {
        // Backward cycle: CLOCK -> SPOTIFY -> RTSP -> CC_USAGE -> CLOCK
        Screen target;
        if      (activeScreen == CLOCK)    target = SPOTIFY;
        else if (activeScreen == SPOTIFY)  target = RTSP;
        else if (activeScreen == RTSP)     target = CC_USAGE;
        else                               target = CLOCK;
        activateScreen(target);
    });
```
Replace with:
```cpp
    btn2.attachDoubleClick([]() {
        // Backward cycle: CLOCK -> PINTEREST -> SPOTIFY -> RTSP -> CC_USAGE -> CLOCK
        Screen target;
        if      (activeScreen == CLOCK)      target = PINTEREST;
        else if (activeScreen == PINTEREST)  target = SPOTIFY;
        else if (activeScreen == SPOTIFY)    target = RTSP;
        else if (activeScreen == RTSP)       target = CC_USAGE;
        else                                 target = CLOCK;
        activateScreen(target);
    });
```

- [ ] **Step 7: Verify firmware compiles**

Run from project root:
```bash
pio run
```
Expected: `SUCCESS` with no errors. Warnings about unused variables are acceptable.

- [ ] **Step 8: Commit**

```bash
git add src/main.cpp
git commit -m "feat(pinterest): add PINTEREST screen to ESP32 firmware"
```

---

## Setup Steps (User Action Required)

Before the Pinterest screen works end-to-end, the user must:

1. **Register a Pinterest app** at [developers.pinterest.com](https://developers.pinterest.com/) → create an app → note `App ID` (client_id) and `App Secret` (client_secret) → add `http://127.0.0.1:7333/v1/pinterest/callback` as a redirect URI.

2. **Add to `.env`** (project root):
   ```
   PINTEREST_CLIENT_ID=<your App ID>
   PINTEREST_CLIENT_SECRET=<your App Secret>
   PINTEREST_BOARD_ID=<board ID or username/board-slug>
   ```
   Board ID can be found in the Pinterest board URL: `pinterest.com/<username>/<board-name>` → use `<username>/<board-name>` format, or get the numeric ID from the API.

3. **Authorize**: start the server, then visit `http://localhost:7333/v1/pinterest/auth` in a browser. After approving, `server/.pinterest_tokens.json` is created.

---

## Verification

1. **Server routes exist:**
   ```bash
   curl -s http://localhost:7333/openapi.json | python3 -c "import json,sys; print([p for p in json.load(sys.stdin)['paths'] if 'pinterest' in p])"
   ```
   Expected: `['/v1/pinterest/auth', '/v1/pinterest/callback', '/v1/pinterest/image']`

2. **Auth flow:** Visit `http://localhost:7333/v1/pinterest/auth` → redirects to Pinterest → after approval, see `{"detail": "Pinterest authorized successfully"}` → `server/.pinterest_tokens.json` exists.

3. **Image endpoint:**
   ```bash
   curl -H "X-API-Key: $API_KEY" http://localhost:7333/v1/pinterest/image --output /tmp/test.jpg && open /tmp/test.jpg
   ```
   Expected: opens a 240×240 circular smart-cropped Pinterest photo. Server terminal prints `[Pinterest] https://i.pinimg.com/1200x/...`

4. **Disk cache:** run the same `curl` again immediately — server logs the same URL, returns instantly from `.pinterest_cache/`.

5. **Firmware:** flash with `pio run --target upload`, switch to Pinterest screen with GPIO 21 → image loads. Wait or press GPIO 19 single-click → new image. Auto-rotates after 5 minutes.

6. **Server fallback:** stop the server while on Pinterest screen → after 2 minutes device switches to Clock screen.
