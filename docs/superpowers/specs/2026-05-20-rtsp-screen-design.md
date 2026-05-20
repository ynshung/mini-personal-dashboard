# RTSP Screen Design

**Date:** 2026-05-20

## Overview

Add an RTSP camera viewer as a third display screen on the NodeMCU dashboard. The server proxies H.264 RTSP streams as JPEG snapshots; the ESP32 polls and renders them at ~1fps. Users navigate streams with button 19 and cycle screens with button 21.

---

## Configuration

**File:** `server/rtsp_config.json` (gitignored — may contain credentials in URLs)

```json
{
  "idle_timeout_s": 10,
  "streams": [
    {
      "url": "rtsp://...",
      "label": "Front Door",
      "mode": "fill"
    },
    {
      "url": "rtsp://...",
      "label": "Backyard",
      "mode": "fit"
    }
  ]
}
```

- `idle_timeout_s`: seconds without a poll before the background grabber shuts down (default 10)
- `mode`: `"fill"` = center-crop to 240×240; `"fit"` = letterbox with black bars
- Circular mask applied server-side on all frames (matching album art pipeline)

---

## Server — `server/routes/rtsp.py`

**Endpoint:** `GET /v1/rtsp/frame?index=N`

On each request:
1. If no grabber exists for stream N, start one (lazy activation)
2. Reset the idle timer for stream N
3. Return the latest cached JPEG frame (240×240, circular mask applied)
4. If the grabber has not produced a frame yet, return a placeholder "loading" JPEG

**Background grabber per stream:**
- Opens RTSP connection via PyAV (`av.open(url, options={"rtsp_transport": "tcp"})`)
- Continuously decodes frames, converts to RGB, resizes/crops to 240×240 per `mode`, applies circular mask, encodes to JPEG, stores latest frame in memory
- Runs in a `threading.Thread` (one per active stream)
- Stops when idle timer fires (no poll within `idle_timeout_s`)
- On error: caches an error JPEG and retries with backoff

**Response headers** (body is pure JPEG):
- `X-Stream-Label`: label string for the current stream
- `X-Stream-Count`: total number of configured streams

**Multiple simultaneous grabbers:** switching streams quickly may leave the previous grabber running until its idle timer fires. With a short `idle_timeout_s` (5–15s), overlap is brief and bounded.

---

## Firmware — `src/main.cpp`

### State

```cpp
int rtspIndex = 0;
int rtspStreamCount = 1;         // updated from X-Stream-Count header
String rtspLabel = "";
unsigned long lastRtspPoll = 0;
const unsigned long RTSP_POLL_INTERVAL_MS = 1000; // ~1fps, adjustable
```

### Screen enum

```cpp
enum Screen { SPOTIFY, CC_USAGE, RTSP, IDLE };
```

### Button 21

- **Single click:** cycle forward — `SPOTIFY → RTSP → CC_USAGE → SPOTIFY`
- **Double click:** cycle backward — `SPOTIFY → CC_USAGE → RTSP → SPOTIFY`
- **Long press:** `ESP.restart()` (unchanged)

### Button 19 in RTSP mode

- **Single click:** `rtspIndex = (rtspIndex + 1) % rtspStreamCount`, fetch immediately
- **Double click:** `rtspIndex = (rtspIndex - 1 + rtspStreamCount) % rtspStreamCount`, fetch immediately
- **Long press:** no-op in RTSP mode (Spotify controls inactive)

### Frame fetch (`fetchRtspFrame()`)

- `GET /v1/rtsp/frame?index=rtspIndex` with `X-API-Key` header
- On 200: read `X-Stream-Label` and `X-Stream-Count` headers, decode JPEG via TJpgDec, draw to display, render label as small grey text at bottom (matching CC usage style)
- On error: call `drawStatus("Stream unavailable")`

### Entering RTSP screen

1. Clear display, call `drawStatus("Loading...")`
2. Call `fetchRtspFrame()` immediately
3. Poll at `RTSP_POLL_INTERVAL_MS` thereafter

### Idle screen handling

RTSP screen participates in the existing `serverUnreachableSince` / `IDLE_TIMEOUT_MS` mechanism unchanged. `prevScreen` captures `RTSP` so waking from idle returns to RTSP.

---

## Image Processing (server-side)

| `mode`   | Behaviour |
|----------|-----------|
| `"fill"` | Scale so shorter dimension = 240, center-crop to 240×240 |
| `"fit"`  | Scale so longer dimension = 240, pad remainder with black |

Circular mask (radius 110, matching album art) applied after resize/crop. Output encoded as JPEG quality 75.

---

## Dependencies

- **Server:** `av` (PyAV) for RTSP decoding; `Pillow` already present for image processing
- **Firmware:** no new libraries; reuses TJpgDec and HTTPClient
