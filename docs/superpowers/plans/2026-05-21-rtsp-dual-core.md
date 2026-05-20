# RTSP Dual-Core Split + Uncapped Grab Rate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split RTSP network fetching onto Core 0 and rendering onto Core 1 using ping-pong double buffering, and remove the 1s floor on the server-side grab interval.

**Architecture:** Two 32 KB static buffers protected by a pair of counting semaphores (`rtspFreeSem` init=2, `rtspReadySem` init=0). Core 0 runs `rtspNetTask` which fetches HTTP frames into the back buffer and signals Core 1. Core 1's `loop()` takes the ready semaphore, decodes the JPEG via TJpgDec (SPI stays on Core 1), then returns the buffer to Core 0. The task is created once in `setup()`, suspended initially, resumed/suspended on RTSP screen activation.

**Tech Stack:** ESP32 FreeRTOS (`xTaskCreatePinnedToCore`, `xSemaphoreCreateCounting`), Arduino HTTPClient/WiFiClient, TFT_eSPI, TJpg_Decoder, FastAPI + PyAV (server side).

---

## File Map

| File | Change |
|------|--------|
| `src/main.cpp` | Remove `fetchRtspFrame()`, add `rtspNetTask()`, new globals, update `setup()`, `activateScreen()`, `loop()`, button callbacks |
| `server/routes/rtsp.py` | Remove grab interval floor in `load_config()`; skip time check when `grab_interval == 0` in `_run()` |
| `server/rtsp_config.json.example` | Set `grab_interval_s: 0` on both streams |

---

## Task 1: Server — remove grab interval floor

**Files:**
- Modify: `server/routes/rtsp.py` (lines 213, 149)
- Modify: `server/rtsp_config.json.example`

- [ ] **Step 1: Update `load_config()` to allow grab_interval_s of 0**

In `server/routes/rtsp.py`, find the `StreamConfig(...)` block inside the list comprehension (around line 208–215). Change the `grab_interval` line:

```python
# Before:
grab_interval=max(float(s.get("grab_interval_s", 1.0)), 0.1),

# After:
grab_interval=float(s.get("grab_interval_s", 0.0)),
```

- [ ] **Step 2: Update `_run()` to encode every frame when grab_interval is 0**

In `server/routes/rtsp.py`, find the `if now - last_encode >= self.grab_interval:` block inside `_run()` (around line 149). Change the condition:

```python
# Before:
if now - last_encode >= self.grab_interval:
    img = frame.to_image().convert("RGB")
    img = resize_frame(img, self.mode)
    img = apply_circular_mask(img)
    buf = BytesIO()
    img.save(buf, "JPEG", quality=75, optimize=True)
    with self._lock:
        self._frame = buf.getvalue()
    last_encode = now

# After:
if self.grab_interval == 0.0 or now - last_encode >= self.grab_interval:
    img = frame.to_image().convert("RGB")
    img = resize_frame(img, self.mode)
    img = apply_circular_mask(img)
    buf = BytesIO()
    img.save(buf, "JPEG", quality=75, optimize=True)
    with self._lock:
        self._frame = buf.getvalue()
    last_encode = now
```

- [ ] **Step 3: Update the example config to use grab_interval_s 0**

Replace the contents of `server/rtsp_config.json.example`:

```json
{
  "idle_timeout_s": 10,
  "overlay": {
    "show_label": true,
    "show_dots": true,
    "label_y": 16,
    "dots_y": 218
  },
  "streams": [
    {
      "url": "rtsp://user:pass@192.168.1.100:554/stream1",
      "label": "Front Door",
      "mode": "fill",
      "grab_interval_s": 0
    },
    {
      "url": "rtsp://user:pass@192.168.1.101:554/stream1",
      "label": "Backyard",
      "mode": "fit",
      "grab_interval_s": 0
    }
  ]
}
```

- [ ] **Step 4: Verify server starts cleanly**

```bash
cd server
uv run uvicorn main:app --host 0.0.0.0 --port 7333
```

Expected: server starts with no errors. If you have a real RTSP stream configured, check that `/v1/rtsp/frame?index=0` still returns a JPEG.

---

## Task 2: Firmware — add dual-core globals

**Files:**
- Modify: `src/main.cpp` (around lines 70–74 where RTSP globals live)

- [ ] **Step 1: Replace the existing RTSP globals block**

Find this block (lines 70–73):

```cpp
const unsigned long RTSP_POLL_INTERVAL_MS = 1000;
int  rtspIndex       = 0;
int  rtspStreamCount = 1;
unsigned long lastRtspPoll = 0;
```

Replace it with:

```cpp
int  rtspIndex       = 0;
int  rtspStreamCount = 1;

static uint8_t           rtspBuf[2][32768];
static int               rtspBufLen[2]       = {0, 0};
static volatile int      rtspWriteIdx        = 0;
static volatile int      rtspReadIdx         = 0;
static SemaphoreHandle_t rtspFreeSem         = nullptr;
static SemaphoreHandle_t rtspReadySem        = nullptr;
static TaskHandle_t      rtspNetTaskHandle   = nullptr;
static volatile bool     rtspFetchError      = false;
static bool              rtspErrorShown      = false;
```

- [ ] **Step 2: Build to confirm no compile errors**

```bash
pio run
```

Expected: `SUCCESS` with no errors (there will be warnings about `rtspBuf` being unused — that's fine at this stage).

---

## Task 3: Firmware — implement rtspNetTask

**Files:**
- Modify: `src/main.cpp` — replace `fetchRtspFrame()` with `rtspNetTask()`

- [ ] **Step 1: Delete the `fetchRtspFrame()` function**

Remove lines 102–161 (the entire `fetchRtspFrame()` function):

```cpp
void fetchRtspFrame() {
    HTTPClient http;
    ...
    Serial.printf("RTSP frame: index=%d\n", rtspIndex);
}
```

- [ ] **Step 2: Add `rtspNetTask()` in its place**

Insert this function where `fetchRtspFrame()` was (before `drawProgressBar`):

```cpp
void rtspNetTask(void *) {
    for (;;) {
        xSemaphoreTake(rtspFreeSem, portMAX_DELAY);

        if (WiFi.status() != WL_CONNECTED) {
            xSemaphoreGive(rtspFreeSem);
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        HTTPClient http;
        String url = String(serverUrl) + "/v1/rtsp/frame?index=" + String(rtspIndex);
        http.begin(url);
        http.addHeader("X-API-Key", apiKey);
        const char *headerKeys[] = {"X-Stream-Count"};
        http.collectHeaders(headerKeys, 1);

        int code = http.GET();
        if (code != 200) {
            Serial.printf("RTSP HTTP error: %d\n", code);
            http.end();
            if (serverUnreachableSince == 0) serverUnreachableSince = millis();
            rtspFetchError = true;
            xSemaphoreGive(rtspFreeSem);
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        String countStr = http.header("X-Stream-Count");
        if (countStr.length() > 0) rtspStreamCount = countStr.toInt();

        int contentLength = http.getSize();
        if (contentLength <= 0 || contentLength > (int)sizeof(rtspBuf[0])) {
            Serial.printf("RTSP unexpected size: %d\n", contentLength);
            http.end();
            xSemaphoreGive(rtspFreeSem);
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        WiFiClient *stream = http.getStreamPtr();
        int received = 0;
        while (received < contentLength && stream->connected()) {
            int avail = stream->available();
            if (avail > 0) {
                int toRead = min(avail, contentLength - received);
                stream->readBytes(rtspBuf[rtspWriteIdx] + received, toRead);
                received += toRead;
            } else {
                vTaskDelay(1);
            }
        }
        http.end();

        if (received != contentLength) {
            Serial.printf("RTSP incomplete: %d/%d\n", received, contentLength);
            xSemaphoreGive(rtspFreeSem);
            continue;
        }

        rtspBufLen[rtspWriteIdx] = received;
        serverUnreachableSince = 0;
        rtspFetchError = false;
        rtspWriteIdx ^= 1;
        xSemaphoreGive(rtspReadySem);
        Serial.printf("RTSP frame ready: index=%d size=%d\n", rtspIndex, received);
    }
}
```

- [ ] **Step 3: Build to confirm no compile errors**

```bash
pio run
```

Expected: `SUCCESS`. The function references `rtspFreeSem`, `rtspReadySem`, `rtspBuf`, `rtspWriteIdx` — all defined in Task 2.

---

## Task 4: Firmware — update loop() RTSP render block

**Files:**
- Modify: `src/main.cpp` — RTSP branch inside `loop()` (around lines 646–652)

- [ ] **Step 1: Replace the RTSP polling block in `loop()`**

Find this block:

```cpp
} else if (activeScreen == RTSP) {
    if (now - lastRtspPoll >= RTSP_POLL_INTERVAL_MS) {
        lastRtspPoll = now;
        if (WiFi.status() == WL_CONNECTED)
            fetchRtspFrame();
    }
}
```

Replace with:

```cpp
} else if (activeScreen == RTSP) {
    if (xSemaphoreTake(rtspReadySem, 0) == pdTRUE) {
        int idx = rtspReadIdx;
        tft.startWrite();
        tft.setSwapBytes(true);
        TJpgDec.drawJpg(0, 0, rtspBuf[idx], rtspBufLen[idx]);
        tft.setSwapBytes(false);
        tft.endWrite();
        rtspReadIdx ^= 1;
        xSemaphoreGive(rtspFreeSem);
        rtspErrorShown = false;
    } else if (rtspFetchError && !rtspErrorShown) {
        drawStatus("Stream unavailable");
        rtspErrorShown = true;
    }
}
```

- [ ] **Step 2: Build to confirm no compile errors**

```bash
pio run
```

Expected: `SUCCESS`.

---

## Task 5: Firmware — update setup(), activateScreen(), and button callbacks

**Files:**
- Modify: `src/main.cpp` — `setup()`, `activateScreen()`, `btn.attachClick`, `btn.attachDoubleClick`

- [ ] **Step 1: Add semaphore and task creation to `setup()`**

Find the line `btn.attachClick([]() {` inside `setup()`. Insert these lines immediately before it:

```cpp
rtspFreeSem  = xSemaphoreCreateCounting(2, 2);
rtspReadySem = xSemaphoreCreateCounting(2, 0);
xTaskCreatePinnedToCore(rtspNetTask, "rtspNet", 8192, nullptr, 1, &rtspNetTaskHandle, 0);
vTaskSuspend(rtspNetTaskHandle);
```

- [ ] **Step 2: Update `activateScreen()` — suspend task when leaving RTSP, reset state when entering RTSP**

Find the start of `activateScreen()`:

```cpp
void activateScreen(Screen s) {
    activeScreen = s;
    serverUnreachableSince = 0;
    pollFailed = false;
```

Replace with:

```cpp
void activateScreen(Screen s) {
    if (activeScreen == RTSP && s != RTSP && rtspNetTaskHandle != nullptr)
        vTaskSuspend(rtspNetTaskHandle);
    activeScreen = s;
    serverUnreachableSince = 0;
    pollFailed = false;
```

- [ ] **Step 3: Update the RTSP case inside `activateScreen()`**

Find:

```cpp
} else if (s == RTSP) {
    drawStatus("Loading...");
    fetchRtspFrame();
    lastRtspPoll = millis();
}
```

Replace with:

```cpp
} else if (s == RTSP) {
    // drain any stale semaphore counts, then reset to initial state
    while (xSemaphoreTake(rtspReadySem, 0) == pdTRUE) {}
    while (xSemaphoreTake(rtspFreeSem, 0) == pdTRUE) {}
    xSemaphoreGive(rtspFreeSem);
    xSemaphoreGive(rtspFreeSem);
    rtspWriteIdx  = 0;
    rtspReadIdx   = 0;
    rtspFetchError  = false;
    rtspErrorShown  = false;
    drawStatus("Loading...");
    vTaskResume(rtspNetTaskHandle);
}
```

- [ ] **Step 4: Update `btn.attachClick` — remove fetchRtspFrame() call and lastRtspPoll update**

Find:

```cpp
if (activeScreen == RTSP) {
    rtspIndex = (rtspIndex + 1) % rtspStreamCount;
    fetchRtspFrame();
    lastRtspPoll = millis();
    return;
}
```

Replace with:

```cpp
if (activeScreen == RTSP) {
    rtspIndex = (rtspIndex + 1) % rtspStreamCount;
    return;
}
```

- [ ] **Step 5: Update `btn.attachDoubleClick` — same removal**

Find:

```cpp
if (activeScreen == RTSP) {
    rtspIndex = (rtspIndex - 1 + rtspStreamCount) % rtspStreamCount;
    fetchRtspFrame();
    lastRtspPoll = millis();
    return;
}
```

Replace with:

```cpp
if (activeScreen == RTSP) {
    rtspIndex = (rtspIndex - 1 + rtspStreamCount) % rtspStreamCount;
    return;
}
```

- [ ] **Step 6: Final build**

```bash
pio run
```

Expected: `SUCCESS` with no errors. If `lastRtspPoll` or `RTSP_POLL_INTERVAL_MS` still appear anywhere you'll get an "undeclared identifier" error — search and remove any remaining references.

- [ ] **Step 7: Flash and verify on device**

```bash
pio run --target upload && pio device monitor
```

Expected serial output when switching to RTSP screen:
```
RTSP frame ready: index=0 size=<N>
RTSP frame ready: index=0 size=<N>
...
```

Frames should appear in rapid succession (much faster than 1/s). Pressing btn (GPIO 19) should update `rtspIndex` and new frames should begin arriving for the new stream index within one fetch cycle.
