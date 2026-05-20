# RTSP Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an RTSP camera viewer screen to the NodeMCU dashboard: the FastAPI server proxies H.264 RTSP streams as JPEG snapshots, and the ESP32 polls and renders them at ~1fps.

**Architecture:** A new FastAPI route (`/v1/rtsp/frame?index=N`) lazily starts a per-stream background thread (PyAV) that continuously decodes frames and caches the latest JPEG. The ESP32 polls at `RTSP_POLL_INTERVAL_MS` and displays the frame via TJpgDec, identical to the album art pipeline. Stream config lives in `server/rtsp_config.json` (gitignored).

**Tech Stack:** Python/FastAPI (server), PyAV (`av`) for RTSP decoding, Pillow for image processing, Arduino/C++ (firmware), TJpgDec for JPEG decode on ESP32.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `server/routes/rtsp.py` | Image helpers, `RtspGrabber`, FastAPI router |
| Create | `server/rtsp_config.json.example` | Config template (actual file is gitignored) |
| Create | `server/tests/__init__.py` | Makes tests a package |
| Create | `server/tests/test_rtsp.py` | Tests for image helpers and grabber state |
| Modify | `server/pyproject.toml` | Add `av` runtime dep, `pytest` dev dep |
| Modify | `server/main.py` | Register RTSP router |
| Modify | `.gitignore` | Add `server/rtsp_config.json` |
| Modify | `src/main.cpp` | RTSP screen, state, buttons, loop |

---

### Task 1: Project setup — dependency, config template, gitignore

**Files:**
- Modify: `server/pyproject.toml`
- Create: `server/rtsp_config.json.example`
- Modify: `.gitignore`

- [ ] **Step 1: Add `av` and `pytest` to pyproject.toml**

In `server/pyproject.toml`, replace the `dependencies` block and add an optional dev section:

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
]

[project.optional-dependencies]
dev = ["pytest>=8"]
```

- [ ] **Step 2: Install dependencies**

Run from `server/`:
```bash
uv sync --extra dev
```

Expected: resolves and installs `av` (PyAV) and `pytest`. Note: PyAV requires system ffmpeg libraries. On macOS: `brew install ffmpeg` if `uv sync` fails with a build error.

- [ ] **Step 3: Create `server/rtsp_config.json.example`**

```json
{
  "idle_timeout_s": 10,
  "streams": [
    {
      "url": "rtsp://user:pass@192.168.1.100:554/stream1",
      "label": "Front Door",
      "mode": "fill",
      "grab_interval_s": 1.0
    },
    {
      "url": "rtsp://user:pass@192.168.1.101:554/stream1",
      "label": "Backyard",
      "mode": "fit",
      "grab_interval_s": 1.0
    }
  ]
}
```

- `mode`: `"fill"` = center-crop to 240×240; `"fit"` = letterbox with black bars
- `grab_interval_s`: how often the server captures a new frame from the stream
- `idle_timeout_s`: seconds without a poll before the background grabber shuts down

- [ ] **Step 4: Add `server/rtsp_config.json` to `.gitignore`**

Append to `.gitignore`:
```
server/rtsp_config.json
```

- [ ] **Step 5: Commit**

```bash
git add server/pyproject.toml server/rtsp_config.json.example .gitignore
git commit -m "chore: add PyAV dep and RTSP config template"
```

---

### Task 2: Image processing helpers

**Files:**
- Create: `server/routes/rtsp.py` (helpers only — router added in Task 4)
- Create: `server/tests/__init__.py`
- Create: `server/tests/test_rtsp.py`

- [ ] **Step 1: Create empty test package**

Create `server/tests/__init__.py` as an empty file.

- [ ] **Step 2: Write failing tests for image helpers**

Create `server/tests/test_rtsp.py`:

```python
from io import BytesIO
from PIL import Image
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routes.rtsp import resize_frame, apply_circular_mask


def _make_image(w: int, h: int, color=(255, 0, 0)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def test_resize_frame_fill_square_output():
    img = _make_image(640, 480)
    result = resize_frame(img, "fill")
    assert result.size == (240, 240)


def test_resize_frame_fill_no_black_bars():
    # 16:9 image filled to 240x240 — all pixels should be the source color, not black
    img = _make_image(1280, 720, color=(200, 100, 50))
    result = resize_frame(img, "fill")
    # Center pixel should be non-black (source color preserved)
    assert result.getpixel((120, 120)) != (0, 0, 0)


def test_resize_frame_fit_square_output():
    img = _make_image(640, 480)
    result = resize_frame(img, "fit")
    assert result.size == (240, 240)


def test_resize_frame_fit_has_black_bars():
    # 16:9 → fit → should have black bars top/bottom
    img = _make_image(1280, 720, color=(200, 100, 50))
    result = resize_frame(img, "fit")
    # Top-left corner should be black (letterbox area)
    assert result.getpixel((0, 0)) == (0, 0, 0)


def test_apply_circular_mask_corners_black():
    img = _make_image(240, 240, color=(255, 255, 255))
    result = apply_circular_mask(img)
    # Corners should be masked to black
    assert result.getpixel((0, 0)) == (0, 0, 0)
    assert result.getpixel((239, 0)) == (0, 0, 0)
    assert result.getpixel((0, 239)) == (0, 0, 0)
    assert result.getpixel((239, 239)) == (0, 0, 0)


def test_apply_circular_mask_center_preserved():
    img = _make_image(240, 240, color=(255, 0, 0))
    result = apply_circular_mask(img)
    # Center pixel should not be masked
    assert result.getpixel((120, 120)) == (255, 0, 0)
```

- [ ] **Step 3: Run tests to verify they fail**

Run from `server/`:
```bash
uv run pytest tests/test_rtsp.py -v
```

Expected: `ImportError` — `routes.rtsp` does not exist yet.

- [ ] **Step 4: Create `server/routes/rtsp.py` with image helpers**

```python
from io import BytesIO
from PIL import Image, ImageDraw

IMG_SIZE = 240
CIRCLE_RADIUS = 110


def resize_frame(img: Image.Image, mode: str) -> Image.Image:
    w, h = img.size
    if mode == "fill":
        scale = IMG_SIZE / min(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - IMG_SIZE) // 2
        top = (new_h - IMG_SIZE) // 2
        img = img.crop((left, top, left + IMG_SIZE, top + IMG_SIZE))
    else:  # fit
        scale = IMG_SIZE / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
        canvas.paste(img, ((IMG_SIZE - new_w) // 2, (IMG_SIZE - new_h) // 2))
        img = canvas
    return img


def apply_circular_mask(img: Image.Image) -> Image.Image:
    mask = Image.new("L", (IMG_SIZE, IMG_SIZE), 0)
    draw = ImageDraw.Draw(mask)
    cx, cy = IMG_SIZE // 2, IMG_SIZE // 2
    draw.ellipse(
        (cx - CIRCLE_RADIUS, cy - CIRCLE_RADIUS,
         cx + CIRCLE_RADIUS, cy + CIRCLE_RADIUS),
        fill=255,
    )
    bg = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
    return Image.composite(img, bg, mask)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_rtsp.py -v
```

Expected: 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add server/routes/rtsp.py server/tests/__init__.py server/tests/test_rtsp.py
git commit -m "feat: add RTSP image processing helpers with tests"
```

---

### Task 3: RtspGrabber class

**Files:**
- Modify: `server/routes/rtsp.py` (append `RtspGrabber`)
- Modify: `server/tests/test_rtsp.py` (append grabber tests)

- [ ] **Step 1: Write failing tests for grabber state**

Append to `server/tests/test_rtsp.py`:

```python
from routes.rtsp import RtspGrabber
import time


def test_grabber_frame_none_before_start():
    g = RtspGrabber("rtsp://fake", "fill", idle_timeout=10.0, grab_interval=1.0)
    assert g.get_frame() is None


def test_grabber_not_running_before_start():
    g = RtspGrabber("rtsp://fake", "fill", idle_timeout=10.0, grab_interval=1.0)
    assert not g.is_running()


def test_grabber_touch_does_not_crash():
    g = RtspGrabber("rtsp://fake", "fill", idle_timeout=10.0, grab_interval=1.0)
    g.touch()  # should not raise


def test_grabber_start_marks_running():
    g = RtspGrabber("rtsp://fake", "fill", idle_timeout=0.1, grab_interval=1.0)
    g.touch()
    g.start()
    assert g.is_running()
    # Let idle timeout fire (grab thread will exit quickly on connection failure)
    time.sleep(0.5)
    # Thread may have exited due to connection error — that's fine, no assertion on is_running here
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_rtsp.py::test_grabber_frame_none_before_start tests/test_rtsp.py::test_grabber_not_running_before_start tests/test_rtsp.py::test_grabber_touch_does_not_crash tests/test_rtsp.py::test_grabber_start_marks_running -v
```

Expected: `ImportError` — `RtspGrabber` not defined yet.

- [ ] **Step 3: Append `RtspGrabber` to `server/routes/rtsp.py`**

First add these two imports at the top of the file (after the existing `from io import BytesIO` line):

```python
import threading
import time
```

Then append the class:

```python
class RtspGrabber:
    def __init__(self, url: str, mode: str, idle_timeout: float, grab_interval: float):
        self.url = url
        self.mode = mode
        self.idle_timeout = idle_timeout
        self.grab_interval = grab_interval
        self._frame: bytes | None = None
        self._lock = threading.Lock()
        self._last_poll = time.monotonic()
        self._thread: threading.Thread | None = None

    def touch(self) -> None:
        self._last_poll = time.monotonic()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def get_frame(self) -> bytes | None:
        with self._lock:
            return self._frame

    def _run(self) -> None:
        import av  # imported here to avoid hard failure when av is not installed at import time
        backoff = 1.0
        while True:
            if time.monotonic() - self._last_poll > self.idle_timeout:
                break
            try:
                container = av.open(
                    self.url,
                    options={"rtsp_transport": "tcp", "stimeout": "5000000"},
                )
                last_encode = 0.0
                for frame in container.decode(video=0):
                    if time.monotonic() - self._last_poll > self.idle_timeout:
                        break
                    now = time.monotonic()
                    if now - last_encode >= self.grab_interval:
                        img = frame.to_image().convert("RGB")
                        img = resize_frame(img, self.mode)
                        img = apply_circular_mask(img)
                        buf = BytesIO()
                        img.save(buf, "JPEG", quality=75, optimize=True)
                        with self._lock:
                            self._frame = buf.getvalue()
                        last_encode = now
                        backoff = 1.0
                container.close()
            except Exception:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_rtsp.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server/routes/rtsp.py server/tests/test_rtsp.py
git commit -m "feat: add RtspGrabber background thread with tests"
```

---

### Task 4: FastAPI route, config loading, register in main.py

**Files:**
- Modify: `server/routes/rtsp.py` (prepend imports, append config + router)
- Modify: `server/main.py`

- [ ] **Step 1: Expand the imports at the top of `server/routes/rtsp.py`**

The file currently starts with `from io import BytesIO`, `import threading`, `import time`, and `from PIL import Image, ImageDraw`. Replace those lines with the full import block:

```python
import json
import threading
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from PIL import Image, ImageDraw
```

- [ ] **Step 2: Append config dataclasses, loader, placeholder, and router to `server/routes/rtsp.py`**

```python
CONFIG_PATH = Path(__file__).parent.parent / "rtsp_config.json"


@dataclass
class StreamConfig:
    url: str
    label: str
    mode: str
    grab_interval: float


@dataclass
class RtspConfig:
    idle_timeout: float
    streams: list[StreamConfig]


def load_config() -> RtspConfig:
    if not CONFIG_PATH.exists():
        return RtspConfig(idle_timeout=10.0, streams=[])
    with CONFIG_PATH.open() as f:
        data = json.load(f)
    streams = [
        StreamConfig(
            url=s["url"],
            label=s.get("label", f"Stream {i}"),
            mode=s.get("mode", "fill"),
            grab_interval=float(s.get("grab_interval_s", 1.0)),
        )
        for i, s in enumerate(data.get("streams", []))
    ]
    return RtspConfig(
        idle_timeout=float(data.get("idle_timeout_s", 10.0)),
        streams=streams,
    )


_config: RtspConfig | None = None
_grabbers: dict[int, RtspGrabber] = {}
_grabbers_lock = threading.Lock()
_placeholder: bytes | None = None


def _get_config() -> RtspConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _make_placeholder() -> bytes:
    global _placeholder
    if _placeholder is None:
        img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
        img = apply_circular_mask(img)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=75)
        _placeholder = buf.getvalue()
    return _placeholder


router = APIRouter()


@router.get("/rtsp/frame")
async def get_rtsp_frame(index: int = Query(0, ge=0)):
    config = _get_config()
    if not config.streams:
        raise HTTPException(status_code=503, detail="No RTSP streams configured")
    if index >= len(config.streams):
        raise HTTPException(status_code=400, detail="Stream index out of range")

    stream_cfg = config.streams[index]

    with _grabbers_lock:
        if index not in _grabbers:
            _grabbers[index] = RtspGrabber(
                url=stream_cfg.url,
                mode=stream_cfg.mode,
                idle_timeout=config.idle_timeout,
                grab_interval=stream_cfg.grab_interval,
            )
        grabber = _grabbers[index]

    grabber.touch()
    if not grabber.is_running():
        grabber.start()

    frame = grabber.get_frame()
    if frame is None:
        frame = _make_placeholder()

    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={
            "X-Stream-Label": stream_cfg.label,
            "X-Stream-Count": str(len(config.streams)),
        },
    )
```

- [ ] **Step 3: Register router in `server/main.py`**

Add after the existing router imports and `include_router` calls:

```python
from routes.rtsp import router as rtsp_router
# ...
app.include_router(rtsp_router, prefix="/v1")
```

Full updated `server/main.py`:

```python
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from routes.cc_usage import router as cc_usage_router
from routes.rtsp import router as rtsp_router
from routes.spotify import router as spotify_router

load_dotenv()

app = FastAPI()

OPEN_PATHS = {"/v1/spotify/auth", "/v1/spotify/callback"}


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
app.include_router(rtsp_router, prefix="/v1")
app.include_router(spotify_router, prefix="/v1")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7333)
```

- [ ] **Step 4: Verify server starts without errors**

Run from `server/`:
```bash
uv run uvicorn main:app --host 0.0.0.0 --port 7333 --reload
```

Expected: server starts. Visit `http://localhost:7333/docs` — `/v1/rtsp/frame` should appear in the Swagger UI. If no `rtsp_config.json` exists, `GET /v1/rtsp/frame` returns 503.

- [ ] **Step 5: Smoke-test with a real config (optional)**

Copy the example and fill in a real URL:
```bash
cp server/rtsp_config.json.example server/rtsp_config.json
# edit server/rtsp_config.json with a real rtsp:// URL
curl -H "X-API-Key: $API_KEY" "http://localhost:7333/v1/rtsp/frame?index=0" --output /tmp/frame.jpg && open /tmp/frame.jpg
```

Expected: a 240×240 circular JPEG of the camera feed.

- [ ] **Step 6: Commit**

```bash
git add server/routes/rtsp.py server/main.py
git commit -m "feat: add RTSP FastAPI route with lazy grabber and config"
```

---

### Task 5: Firmware — RTSP state and fetchRtspFrame()

**Files:**
- Modify: `src/main.cpp`

- [ ] **Step 1: Add RTSP to the Screen enum**

Find:
```cpp
enum Screen { SPOTIFY, CC_USAGE, IDLE };
```

Replace with:
```cpp
enum Screen { SPOTIFY, CC_USAGE, RTSP, IDLE };
```

- [ ] **Step 2: Add RTSP state variables**

After the existing `bool pollFailed = false;` line, add:

```cpp
const unsigned long RTSP_POLL_INTERVAL_MS = 1000;
int  rtspIndex       = 0;
int  rtspStreamCount = 1;
String rtspLabel     = "";
unsigned long lastRtspPoll = 0;
```

- [ ] **Step 3: Add `drawRtspLabel()` function**

Add after `drawSleepScreen()`:

```cpp
void drawRtspLabel() {
    tft.fillRect(0, 215, 240, 25, TFT_BLACK);
    if (rtspLabel.length() > 0) {
        tft.loadFont(NotoSans_Medium14);
        tft.setTextDatum(BC_DATUM);
        tft.setTextColor(COL_GREY, TFT_BLACK);
        tft.drawString(rtspLabel.c_str(), CX, 235);
        tft.unloadFont();
    }
}
```

- [ ] **Step 4: Add `fetchRtspFrame()` function**

Add after `drawRtspLabel()`:

```cpp
void fetchRtspFrame() {
    HTTPClient http;
    String url = String(serverUrl) + "/v1/rtsp/frame?index=" + String(rtspIndex);
    http.begin(url);
    http.addHeader("X-API-Key", apiKey);
    const char *headerKeys[] = {"X-Stream-Label", "X-Stream-Count"};
    http.collectHeaders(headerKeys, 2);

    int code = http.GET();
    if (code != 200) {
        Serial.printf("RTSP frame HTTP error: %d\n", code);
        http.end();
        if (serverUnreachableSince == 0) serverUnreachableSince = millis();
        drawStatus("Stream unavailable");
        return;
    }

    String label    = http.header("X-Stream-Label");
    String countStr = http.header("X-Stream-Count");
    if (label.length() > 0)    rtspLabel = label;
    if (countStr.length() > 0) rtspStreamCount = countStr.toInt();

    int contentLength = http.getSize();
    if (contentLength <= 0 || contentLength > 100000) {
        Serial.printf("RTSP unexpected size: %d\n", contentLength);
        http.end();
        return;
    }

    uint8_t *buf = (uint8_t *)malloc(contentLength);
    if (!buf) { http.end(); return; }

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
        Serial.printf("RTSP incomplete: %d/%d bytes\n", received, contentLength);
        free(buf);
        return;
    }

    serverUnreachableSince = 0;
    tft.startWrite();
    tft.setSwapBytes(true);
    TJpgDec.drawJpg(0, 0, buf, contentLength);
    tft.setSwapBytes(false);
    tft.endWrite();
    free(buf);

    drawRtspLabel();
    Serial.printf("RTSP frame: index=%d label=%s\n", rtspIndex, rtspLabel.c_str());
}
```

- [ ] **Step 5: Commit**

```bash
git add src/main.cpp
git commit -m "feat(fw): add RTSP screen state, fetchRtspFrame, drawRtspLabel"
```

---

### Task 6: Firmware — activateScreen helper and button handlers

**Files:**
- Modify: `src/main.cpp`

- [ ] **Step 1: Add `activateScreen()` helper before `wakeFromIdle()`**

Add before the existing `wakeFromIdle()` function:

```cpp
void activateScreen(Screen s) {
    activeScreen = s;
    serverUnreachableSince = 0;
    if (s == CC_USAGE) {
        ccNeedsFullRedraw = true;
        drawCCUsage();
        fetchCCUsage();
        lastCCPoll = millis();
    } else if (s == SPOTIFY) {
        pollFailed = false;
        hasArt = false;
        current.track_id = "\x01";
        drawStatus("Loading...");
        fetchNowPlaying();
        lastPoll = millis();
        lastTick = lastPoll;
    } else if (s == RTSP) {
        drawStatus("Loading...");
        fetchRtspFrame();
        lastRtspPoll = millis();
    }
}
```

- [ ] **Step 2: Replace `wakeFromIdle()` to use `activateScreen()`**

Replace the existing `wakeFromIdle()` function:

```cpp
void wakeFromIdle() {
    pollFailed = false;
    activateScreen(prevScreen);
}
```

- [ ] **Step 3: Update `btn` (GPIO 19) handlers in `setup()`**

Replace the three `btn.attach*` calls:

```cpp
btn.attachClick([]() {
    if (activeScreen == IDLE) { wakeFromIdle(); return; }
    if (activeScreen == RTSP) {
        rtspIndex = (rtspIndex + 1) % rtspStreamCount;
        fetchRtspFrame();
        lastRtspPoll = millis();
        return;
    }
    sendCommand("/v1/spotify/toggle");
});
btn.attachDoubleClick([]() {
    if (activeScreen == IDLE) { wakeFromIdle(); return; }
    if (activeScreen == RTSP) {
        rtspIndex = (rtspIndex - 1 + rtspStreamCount) % rtspStreamCount;
        fetchRtspFrame();
        lastRtspPoll = millis();
        return;
    }
    sendCommand("/v1/spotify/next");
});
btn.attachLongPressStart([]() {
    if (activeScreen == IDLE) { wakeFromIdle(); return; }
    if (activeScreen == RTSP) return;
    sendCommand("/v1/spotify/previous");
});
```

- [ ] **Step 4: Replace `btn2` (GPIO 21) handlers in `setup()`**

Replace the two existing `btn2.attach*` calls:

```cpp
btn2.attachClick([]() {
    if (activeScreen == IDLE) { wakeFromIdle(); return; }
    // Forward cycle: SPOTIFY -> RTSP -> CC_USAGE -> SPOTIFY
    Screen next;
    if (activeScreen == SPOTIFY)   next = RTSP;
    else if (activeScreen == RTSP) next = CC_USAGE;
    else                           next = SPOTIFY;
    activateScreen(next);
});
btn2.attachDoubleClick([]() {
    if (activeScreen == IDLE) { wakeFromIdle(); return; }
    // Backward cycle: SPOTIFY -> CC_USAGE -> RTSP -> SPOTIFY
    Screen prev;
    if (activeScreen == SPOTIFY)       prev = CC_USAGE;
    else if (activeScreen == CC_USAGE) prev = RTSP;
    else                               prev = SPOTIFY;
    activateScreen(prev);
});
btn2.attachLongPressStart([]() {
    ESP.restart();
});
```

- [ ] **Step 5: Commit**

```bash
git add src/main.cpp
git commit -m "feat(fw): add activateScreen helper, 3-way screen cycle, RTSP button controls"
```

---

### Task 7: Firmware — Main loop RTSP polling, build, and flash

**Files:**
- Modify: `src/main.cpp`

- [ ] **Step 1: Add RTSP case to `loop()`**

In `loop()`, after the `} else if (activeScreen == CC_USAGE) {` block and before the closing `}`, add:

```cpp
    } else if (activeScreen == RTSP) {
        if (now - lastRtspPoll >= RTSP_POLL_INTERVAL_MS) {
            lastRtspPoll = now;
            if (WiFi.status() == WL_CONNECTED)
                fetchRtspFrame();
        }
```

- [ ] **Step 2: Build firmware**

Run from project root:
```bash
pio run
```

Expected: compiles without errors. If you see "RTSP" enum conflicts or missing symbol errors, check that all Task 5 and Task 6 edits are saved.

- [ ] **Step 3: Flash and verify**

```bash
pio run --target upload && pio device monitor
```

Verification checklist:
- Device boots on CC_USAGE screen (default)
- Single-click btn2 → RTSP screen: shows "Loading..." then a camera frame
- Second single-click btn2 → CC_USAGE
- Third single-click btn2 → SPOTIFY
- While on RTSP: single-click btn19 → next stream (index increments, label updates)
- While on RTSP: double-click btn19 → previous stream (wraps around)
- Double-click btn2 cycles backward through screens
- Long-press btn2 restarts device
- After `idle_timeout_s` without ESP polling, server stops the grabber thread (check server logs)

- [ ] **Step 4: Final commit**

```bash
git add src/main.cpp
git commit -m "feat(fw): add RTSP polling loop, completing RTSP screen feature"
```
