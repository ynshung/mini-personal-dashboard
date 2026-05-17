# Button Playback Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a single three-pin button to GPIO 19 and use gesture detection (single/double/long press) to control Spotify playback (toggle, next, previous) via the FastAPI server.

**Architecture:** OneButtonTiny handles gesture classification on the ESP32; each gesture fires an HTTP POST to a server endpoint. The server gains a new `/toggle` endpoint (checks current playback state and calls play or pause accordingly); the now-redundant `/play` and `/pause` endpoints are removed.

**Tech Stack:** C++/Arduino (ESP32), OneButtonTiny library, FastAPI/Python, Spotify Web API

---

## File Map

| File | Change |
|------|--------|
| `server/routes/spotify.py` | Remove `POST /spotify/play` and `POST /spotify/pause`; add `POST /spotify/toggle` |
| `platformio.ini` | Add `mathertel/OneButton` to `lib_deps` |
| `src/main.cpp` | Include `OneButtonTiny.h`, declare `btn`, add `sendCommand()`, register callbacks, call `btn.tick()` in `loop()` |

---

### Task 1: Add toggle endpoint and remove play/pause from server

**Files:**
- Modify: `server/routes/spotify.py`

- [ ] **Step 1: Remove the `spotify_play` and `spotify_pause` route handlers**

Delete these two functions (and their decorators) from `server/routes/spotify.py`:

```python
@router.post("/spotify/play")
async def spotify_play():
    ...

@router.post("/spotify/pause")
async def spotify_pause():
    ...
```

- [ ] **Step 2: Add the toggle endpoint**

Add this function after the `spotify_previous` handler:

```python
@router.post("/spotify/toggle")
async def spotify_toggle():
    token = await _get_access_token()
    async with httpx.AsyncClient() as client:
        state_resp = await client.get(
            "https://api.spotify.com/v1/me/player",
            headers={"Authorization": f"Bearer {token}"},
        )

    is_playing = False
    if state_resp.status_code == 200:
        is_playing = state_resp.json().get("is_playing", False)

    action = "pause" if is_playing else "play"
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"https://api.spotify.com/v1/me/player/{action}",
            headers={"Authorization": f"Bearer {token}"},
        )

    if not resp.is_success and resp.status_code != 204:
        raise HTTPException(status_code=resp.status_code, detail=f"Spotify API error: {resp.text}")

    return Response(status_code=204)
```

- [ ] **Step 3: Verify the server starts without errors**

From `server/`:
```bash
uv run uvicorn main:app --host 0.0.0.0 --port 7333
```
Expected: server starts, no import errors or tracebacks.

- [ ] **Step 4: Smoke-test the toggle endpoint with curl**

```bash
curl -X POST http://localhost:7333/v1/spotify/toggle \
  -H "X-API-Key: <your-api-key>" \
  -v
```
Expected: `HTTP/1.1 204 No Content`. Spotify playback should toggle. Run a second time to toggle back.

- [ ] **Step 5: Verify play/pause endpoints are gone**

```bash
curl -X POST http://localhost:7333/v1/spotify/play \
  -H "X-API-Key: <your-api-key>" \
  -v
```
Expected: `HTTP/1.1 404 Not Found`.

- [ ] **Step 6: Commit**

```bash
git add server/routes/spotify.py
git commit -m "feat: add toggle endpoint, remove unused play/pause endpoints"
```

---

### Task 2: Add OneButtonTiny library to firmware

**Files:**
- Modify: `platformio.ini`

- [ ] **Step 1: Add the library dependency**

In `platformio.ini`, append `mathertel/OneButton@^2.6` to `lib_deps`:

```ini
lib_deps =
    bblanchon/ArduinoJson@^7
    bodmer/TFT_eSPI@^2.5
    mathertel/OneButton@^2.6
```

- [ ] **Step 2: Verify the library resolves**

From the project root:
```bash
pio pkg install
```
Expected: resolves and downloads `OneButton` with no errors.

- [ ] **Step 3: Commit**

```bash
git add platformio.ini
git commit -m "feat: add OneButton library for button gesture detection"
```

---

### Task 3: Add button handling to firmware

**Files:**
- Modify: `src/main.cpp`

- [ ] **Step 1: Add the OneButtonTiny include and button declaration**

At the top of `src/main.cpp`, after the existing includes:

```cpp
#include <OneButtonTiny.h>
```

After the `TFT_eSPI tft = TFT_eSPI();` line:

```cpp
OneButtonTiny btn(19, true); // GPIO 19, active-low
```

- [ ] **Step 2: Add the `sendCommand` helper function**

Add this function in the `// --- Networking ---` section, before `fetchNowPlaying()`:

```cpp
void sendCommand(const char* path) {
    if (WiFi.status() != WL_CONNECTED) return;
    HTTPClient http;
    http.begin(String(serverUrl) + path);
    http.addHeader("X-API-Key", apiKey);
    int code = http.POST("");
    http.end();
    Serial.printf("sendCommand %s -> %d\n", path, code);
    if (code == 204) {
        delay(200);
        lastPoll = 0;
    }
}
```

- [ ] **Step 3: Register button callbacks in `setup()`**

At the end of `setup()`, before the closing `}`:

```cpp
btn.attachClick([]() { sendCommand("/v1/spotify/toggle"); });
btn.attachDoubleClick([]() { sendCommand("/v1/spotify/next"); });
btn.attachLongPressStart([]() { sendCommand("/v1/spotify/previous"); });
```

- [ ] **Step 4: Call `btn.tick()` in `loop()`**

At the very top of `loop()`, before the `unsigned long now = millis();` line:

```cpp
btn.tick();
```

- [ ] **Step 5: Build to verify no compile errors**

```bash
pio run
```
Expected: `SUCCESS` with no errors or warnings about undeclared identifiers.

- [ ] **Step 6: Flash and manual test**

```bash
pio run --target upload && pio device monitor
```

Test each gesture while a track is playing:
- **Single press** → playback toggles (pause/resume); serial shows `sendCommand /v1/spotify/toggle -> 204`
- **Double press** → skips to next track; serial shows `sendCommand /v1/spotify/next -> 204`
- **Long press (~1s)** → goes to previous track; serial shows `sendCommand /v1/spotify/previous -> 204`

After each command, verify the display updates within ~5 seconds (the forced poll fires after 200ms delay).

- [ ] **Step 7: Commit**

```bash
git add src/main.cpp
git commit -m "feat: add single-button playback control with gesture detection"
```
