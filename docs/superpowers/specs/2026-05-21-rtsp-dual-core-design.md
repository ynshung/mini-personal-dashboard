# RTSP Dual-Core Split + Uncapped Grab Rate

**Date:** 2026-05-21  
**Status:** Approved

## Goal

Remove the single-threaded bottleneck on the RTSP screen. Currently `fetchRtspFrame()` runs entirely on Core 1 inside `loop()`, blocking HTTP fetch, buffer read, JPEG decode, and SPI render sequentially. Splitting network work onto Core 0 lets decode+render proceed on Core 1 without waiting for the next HTTP response.

Also remove the 1s floor on the server-side grab interval so the grabber thread can encode frames faster than once per second.

## Architecture

```
Core 0 — rtspNetTask              Core 1 — loop()
─────────────────────────         ──────────────────────────
take(rtspFreeSem)                 btn.tick() / btn2.tick()
HTTP GET /v1/rtsp/frame           idle timeout check
readBytes → rtspBuf[writeIdx]     if xSemaphoreTake(rtspReadySem, 0):
rtspBufLen[writeIdx] = received     TJpgDec.drawJpg(rtspBuf[readIdx])
give(rtspReadySem)                  readIdx ^= 1
writeIdx ^= 1                       give(rtspFreeSem)
→ repeat immediately              activateScreen / screen polling
```

## Synchronization

Two counting semaphores manage the ping-pong:

| Semaphore | Init | Producer | Consumer |
|-----------|------|----------|----------|
| `rtspFreeSem` | 2 | `take` before fetch | `give` after render |
| `rtspReadySem` | 0 | `give` after fetch | `take` before render |

Two static buffers `rtspBuf[2][32768]` (~64 KB total). Each side advances its own index (`writeIdx` / `readIdx`) mod 2 after each operation. No mutex needed — producer and consumer always operate on different buffer slots.

## Firmware Changes (`src/main.cpp`)

### New globals
```cpp
static uint8_t        rtspBuf[2][32768];
static int            rtspBufLen[2]  = {0, 0};
static volatile int   rtspWriteIdx   = 0;
static volatile int   rtspReadIdx    = 1;  // starts on the other slot
static SemaphoreHandle_t rtspFreeSem;
static SemaphoreHandle_t rtspReadySem;
static TaskHandle_t   rtspNetTaskHandle = nullptr;
```

### `rtspNetTask(void*)` — pinned to Core 0
- Loops indefinitely: `take(rtspFreeSem)` → HTTP GET → `readBytes` into `rtspBuf[writeIdx]` → `give(rtspReadySem)` → `writeIdx ^= 1`
- On HTTP/network error: `give(rtspFreeSem)` back (don't consume the slot), short `vTaskDelay`, retry
- Uses the current `rtspIndex` value (updated by buttons on Core 1 — single `volatile int` read is atomic on ESP32)
- Updates `rtspStreamCount` from `X-Stream-Count` header (same as before)
- Updates `serverUnreachableSince` on error

### `loop()` — Core 1
- Replaces `fetchRtspFrame()` call: `xSemaphoreTake(rtspReadySem, 0)` (non-blocking) → if taken: `TJpgDec.drawJpg(rtspBuf[rtspReadIdx], rtspBufLen[rtspReadIdx])` → `rtspReadIdx ^= 1` → `give(rtspFreeSem)`
- Remove `RTSP_POLL_INTERVAL_MS` check — render fires whenever a new frame is ready
- On render error (incomplete frame): still `give(rtspFreeSem)` to unblock Core 0

### `activateScreen(RTSP)`
- Reset semaphores: drain and re-initialise to `free=2, ready=0` to avoid stale frames from a previous session
- `vTaskResume(rtspNetTaskHandle)`
- Show "Loading..." (same as before)

### Leaving RTSP (any `activateScreen` call for a non-RTSP screen)
- `vTaskSuspend(rtspNetTaskHandle)`
- No semaphore reset needed immediately — reset happens on next RTSP activation

### `setup()`
- Create semaphores: `rtspFreeSem = xSemaphoreCreateCounting(2, 2)`, `rtspReadySem = xSemaphoreCreateCounting(2, 0)`
- `xTaskCreatePinnedToCore(rtspNetTask, "rtspNet", 8192, nullptr, 1, &rtspNetTaskHandle, 0)`
- Immediately suspend: `vTaskSuspend(rtspNetTaskHandle)`

### Button callbacks (RTSP screen)
- Just update `rtspIndex` (and clamp with `rtspStreamCount`) — no explicit fetch trigger needed since Core 0 fetches continuously

### Remove
- `RTSP_POLL_INTERVAL_MS` constant
- `lastRtspPoll` variable and its `millis()` comparisons
- The old `fetchRtspFrame()` function

## Server Changes (`server/routes/rtsp.py`)

### `load_config()`
- Change `grab_interval=max(float(s.get("grab_interval_s", 1.0)), 0.1)` to `grab_interval=float(s.get("grab_interval_s", 0.0))`
- No minimum floor — `0.0` means encode every decoded frame

### `RtspGrabber._run()`
- When `self.grab_interval == 0`: skip the `if now - last_encode >= self.grab_interval` check, encode every frame
- When `self.grab_interval > 0`: existing throttle logic unchanged

### `rtsp_config.json.example`
- Change `grab_interval_s` from `1.0` to `0` in both stream entries

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| HTTP error on Core 0 | Give `rtspFreeSem` back, short delay, retry; update `serverUnreachableSince` |
| Malloc / size error on Core 0 | Same as HTTP error |
| Incomplete read on Core 0 | Give `rtspFreeSem` back (don't signal ready), retry |
| Screen switch while Core 0 mid-fetch | `vTaskSuspend` interrupts cleanly at next FreeRTOS scheduler point; semaphores reset on next RTSP activation |

## Constraints

- `TFT_eSPI` and `TJpgDec` remain Core 1 only — no SPI calls in `rtspNetTask`
- `rtspIndex` reads on Core 0 are safe: single `volatile int` aligned reads are atomic on Xtensa LX6
- Stack size for `rtspNetTask`: 8 KB (HTTPClient + WiFiClient fit comfortably)
- Total new static memory: 64 KB for `rtspBuf[2][32768]`
