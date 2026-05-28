# Todo Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `TODO` screen to the ESP32 dashboard — server-rendered JPEG list managed via a web UI, navigated by GPIO 19 button gestures.

**Architecture:** Tasks are persisted in `server/todos.json`. A new FastAPI router (`server/routes/todo.py`) handles all REST operations and renders the 240×240 JPEG via Pillow. The ESP32 tracks a local `todoSelectedIndex` integer and re-fetches the image on every button action.

**Tech Stack:** Python 3.12, FastAPI, Pillow, Arduino/ESP32 (HTTPClient, TJpg_Decoder, OneButtonTiny), HTML5 Drag and Drop API.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `server/routes/todo.py` | Create | All todo routes: data layer, REST endpoints, image rendering, web UI |
| `server/main.py` | Modify | Register todo router; add `/v1/todo/ui` to `OPEN_PATHS` |
| `.gitignore` | Modify | Add `server/todos.json` |
| `src/main.cpp` | Modify | Add `TODO` screen: enum, state, fetch, button handlers, screen cycle |

---

## Task 1: Create branch and server data layer

**Files:**
- Create: `server/routes/todo.py`

- [ ] **Step 1: Create the feature branch**

```bash
git checkout -b feat/todo-screen
```

- [ ] **Step 2: Create `server/routes/todo.py` with data layer**

```python
import json
import os
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

router = APIRouter()

TODOS_FILE = Path(__file__).parent.parent / "todos.json"
FONTS_DIR = Path(__file__).parent.parent / "fonts"
_lock = Lock()


def _get_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


FONT_TASK = _get_font("NotoSansCJK-Medium.ttc", 15)
FONT_LABEL = _get_font("NotoSansCJK-Regular.ttc", 11)


def _load() -> dict:
    if not TODOS_FILE.exists():
        return {"tasks": []}
    return json.loads(TODOS_FILE.read_text())


def _save(data: dict) -> None:
    tmp = TODOS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(TODOS_FILE)


def _active_tasks(data: dict) -> list[dict]:
    return [t for t in data["tasks"] if t["status"] == "active"]
```

- [ ] **Step 3: Commit**

```bash
git add server/routes/todo.py
git commit -m "feat(todo): add server data layer"
```

---

## Task 2: Add REST API endpoints

**Files:**
- Modify: `server/routes/todo.py`

- [ ] **Step 1: Append Pydantic models and all REST endpoints to `todo.py`**

Add after the data layer helpers:

```python
class AddTask(BaseModel):
    title: str


class ReorderBody(BaseModel):
    ids: list[str]


@router.get("/todo")
def list_tasks():
    with _lock:
        data = _load()
    return _active_tasks(data)


@router.post("/todo", status_code=201)
def add_task(body: AddTask):
    with _lock:
        data = _load()
        task = {
            "id": str(uuid.uuid4()),
            "title": body.title.strip(),
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        data["tasks"].append(task)
        _save(data)
    return task


@router.patch("/todo/action")
def action_by_index(selected: int = 0, action: str = "done"):
    if action not in ("done", "archive"):
        raise HTTPException(status_code=400, detail="action must be 'done' or 'archive'")
    with _lock:
        data = _load()
        active = _active_tasks(data)
        if not active:
            raise HTTPException(status_code=404, detail="No active tasks")
        idx = min(selected, len(active) - 1)
        target_id = active[idx]["id"]
        for t in data["tasks"]:
            if t["id"] == target_id:
                t["status"] = "done" if action == "done" else "archived"
                _save(data)
                return {"ok": True}
    raise HTTPException(status_code=404, detail="Task not found")


@router.patch("/todo/reorder")
def reorder_tasks(body: ReorderBody):
    with _lock:
        data = _load()
        id_to_task = {t["id"]: t for t in data["tasks"]}
        active_id_set = set(body.ids)
        reordered = [id_to_task[i] for i in body.ids if i in id_to_task]
        others = [t for t in data["tasks"] if t["id"] not in active_id_set]
        data["tasks"] = reordered + others
        _save(data)
    return {"ok": True}


@router.patch("/todo/{task_id}/done")
def mark_done(task_id: str):
    with _lock:
        data = _load()
        for t in data["tasks"]:
            if t["id"] == task_id:
                t["status"] = "done"
                _save(data)
                return {"ok": True}
    raise HTTPException(status_code=404, detail="Task not found")


@router.patch("/todo/{task_id}/archive")
def archive_task(task_id: str):
    with _lock:
        data = _load()
        for t in data["tasks"]:
            if t["id"] == task_id:
                t["status"] = "archived"
                _save(data)
                return {"ok": True}
    raise HTTPException(status_code=404, detail="Task not found")


@router.delete("/todo/{task_id}")
def delete_task(task_id: str):
    with _lock:
        data = _load()
        before = len(data["tasks"])
        data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]
        if len(data["tasks"]) == before:
            raise HTTPException(status_code=404, detail="Task not found")
        _save(data)
    return {"ok": True}
```

- [ ] **Step 2: Commit**

```bash
git add server/routes/todo.py
git commit -m "feat(todo): add REST endpoints"
```

---

## Task 3: Add image rendering endpoint

**Files:**
- Modify: `server/routes/todo.py`

- [ ] **Step 1: Append the image rendering helpers and endpoint to `todo.py`**

```python
IMG_SIZE = 240
CIRCLE_RADIUS = 110
COL_WHITE = (255, 255, 255)
COL_DIM = (100, 100, 100)
COL_GREY = (136, 136, 136)
COL_BLACK = (0, 0, 0)

ROW_HEIGHT = 28
ROWS_VISIBLE = 6
CHECKBOX_SIZE = 11
CHECKBOX_X = 28
TEXT_X = 46
TEXT_MAX_W = 164  # 240 - TEXT_X - 30 (right margin)
LABEL_Y = 28      # "TODO" label centre
TASKS_START_Y = 52
COUNTER_Y = 224


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _render_todo_jpeg(selected: int) -> bytes:
    with _lock:
        data = _load()
    active = _active_tasks(data)

    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), COL_BLACK)
    draw = ImageDraw.Draw(img)

    if not active:
        draw.text((IMG_SIZE // 2, IMG_SIZE // 2), "All done!", fill=COL_WHITE,
                  font=FONT_TASK, anchor="mm")
    else:
        selected = min(selected, len(active) - 1)
        total = len(active)
        start = max(0, min(selected - 2, total - ROWS_VISIBLE))
        window = active[start: start + ROWS_VISIBLE]

        # "TODO" label
        draw.text((IMG_SIZE // 2, LABEL_Y), "TODO", fill=COL_GREY,
                  font=FONT_LABEL, anchor="mm")

        for i, task in enumerate(window):
            abs_idx = start + i
            is_selected = abs_idx == selected
            col = COL_WHITE if is_selected else COL_DIM
            cy = TASKS_START_Y + i * ROW_HEIGHT

            # Checkbox
            box_y = cy - CHECKBOX_SIZE // 2
            draw.rectangle(
                (CHECKBOX_X, box_y, CHECKBOX_X + CHECKBOX_SIZE, box_y + CHECKBOX_SIZE),
                outline=col,
                width=2,
            )

            # Task text
            title = _truncate(draw, task["title"], FONT_TASK, TEXT_MAX_W)
            draw.text((TEXT_X, cy), title, fill=col, font=FONT_TASK, anchor="lm")

        # "N / M tasks" counter
        counter = f"{selected + 1} / {total} tasks"
        draw.text((IMG_SIZE // 2, COUNTER_Y), counter, fill=COL_GREY,
                  font=FONT_LABEL, anchor="mm")

    # Circular mask
    cx, cy_center = IMG_SIZE // 2, IMG_SIZE // 2
    mask = Image.new("L", (IMG_SIZE, IMG_SIZE), 0)
    ImageDraw.Draw(mask).ellipse(
        (cx - CIRCLE_RADIUS, cy_center - CIRCLE_RADIUS,
         cx + CIRCLE_RADIUS, cy_center + CIRCLE_RADIUS),
        fill=255,
    )
    result = Image.composite(img, Image.new("RGB", (IMG_SIZE, IMG_SIZE), COL_BLACK), mask)

    buf = BytesIO()
    result.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


@router.get("/todo/image")
def todo_image(selected: int = 0):
    jpeg = _render_todo_jpeg(selected)
    return Response(content=jpeg, media_type="image/jpeg")
```

- [ ] **Step 2: Commit**

```bash
git add server/routes/todo.py
git commit -m "feat(todo): add Pillow image rendering endpoint"
```

---

## Task 4: Add web UI endpoint

**Files:**
- Modify: `server/routes/todo.py`

- [ ] **Step 1: Append the web UI endpoint to `todo.py`**

```python
_UI_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Todo</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:system-ui,sans-serif;background:#111;color:#eee;padding:16px;max-width:520px;margin:0 auto}}
    h1{{font-size:1.1rem;margin-bottom:14px;letter-spacing:.08em;color:#aaa;text-transform:uppercase}}
    .add-row{{display:flex;gap:8px;margin-bottom:18px}}
    input[type=text]{{flex:1;padding:9px 12px;border-radius:8px;border:1px solid #333;background:#1e1e1e;color:#eee;font-size:1rem}}
    input[type=text]:focus{{outline:none;border-color:#555}}
    .btn{{padding:9px 14px;border-radius:8px;border:none;cursor:pointer;font-size:.85rem;font-weight:600}}
    .btn-add{{background:#1a73e8;color:#fff}}
    .task-list{{list-style:none}}
    .task-item{{display:flex;align-items:center;gap:8px;padding:10px 6px;border-bottom:1px solid #222;cursor:default}}
    .task-item.dragging{{opacity:.35}}
    .task-item.drag-over{{border-top:2px solid #1a73e8}}
    .drag-handle{{color:#444;cursor:grab;font-size:1.1rem;user-select:none;padding:0 2px}}
    .task-title{{flex:1;font-size:.95rem}}
    .btn-done{{background:#2e7d32;color:#fff}}
    .btn-archive{{background:#424242;color:#ccc}}
    .btn-del{{background:#7f0000;color:#fff}}
  </style>
</head>
<body>
  <h1>Todo</h1>
  <div class="add-row">
    <input type="text" id="new-task" placeholder="New task…" autocomplete="off">
    <button class="btn btn-add" onclick="addTask()">Add</button>
  </div>
  <ul class="task-list" id="task-list"></ul>
  <script>
    const KEY = '{api_key}';
    const H = {{'X-API-Key': KEY, 'Content-Type': 'application/json'}};
    let dragSrc = null;

    async function load() {{
      const r = await fetch('/v1/todo', {{headers: H}});
      const tasks = await r.json();
      const list = document.getElementById('task-list');
      list.innerHTML = '';
      tasks.forEach(t => {{
        const li = document.createElement('li');
        li.className = 'task-item';
        li.draggable = true;
        li.dataset.id = t.id;
        li.innerHTML =
          '<span class="drag-handle" title="Drag to reorder">⠿</span>' +
          '<span class="task-title">' + esc(t.title) + '</span>' +
          '<button class="btn btn-done" onclick="act(\\'' + t.id + '\\',\\'done\\')">Done</button>' +
          '<button class="btn btn-archive" onclick="act(\\'' + t.id + '\\',\\'archive\\')">Archive</button>' +
          '<button class="btn btn-del" onclick="del(\\'' + t.id + '\\')">Delete</button>';
        li.addEventListener('dragstart', () => {{ dragSrc = li; li.classList.add('dragging'); }});
        li.addEventListener('dragend',   () => {{ li.classList.remove('dragging'); dragSrc = null; }});
        li.addEventListener('dragover',  e => {{ e.preventDefault(); li.classList.add('drag-over'); }});
        li.addEventListener('dragleave', () => li.classList.remove('drag-over'));
        li.addEventListener('drop', e => {{
          e.preventDefault(); li.classList.remove('drag-over');
          if (!dragSrc || dragSrc === li) return;
          const items = [...list.querySelectorAll('.task-item')];
          const si = items.indexOf(dragSrc), di = items.indexOf(li);
          list.insertBefore(dragSrc, si < di ? li.nextSibling : li);
          const ids = [...list.querySelectorAll('.task-item')].map(el => el.dataset.id);
          fetch('/v1/todo/reorder', {{method:'PATCH', headers:H, body:JSON.stringify({{ids}})}});
        }});
        list.appendChild(li);
      }});
    }}

    function esc(s) {{ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}

    async function addTask() {{
      const inp = document.getElementById('new-task');
      const title = inp.value.trim(); if (!title) return;
      await fetch('/v1/todo', {{method:'POST', headers:H, body:JSON.stringify({{title}})}});
      inp.value = ''; load();
    }}

    async function act(id, a) {{
      await fetch('/v1/todo/' + id + '/' + a, {{method:'PATCH', headers:H}});
      load();
    }}

    async function del(id) {{
      await fetch('/v1/todo/' + id, {{method:'DELETE', headers:H}});
      load();
    }}

    document.getElementById('new-task').addEventListener('keydown', e => {{ if (e.key==='Enter') addTask(); }});
    load();
  </script>
</body>
</html>
"""


@router.get("/todo/ui", response_class=Response)
def todo_ui():
    api_key = os.getenv("API_KEY", "")
    html = _UI_HTML.format(api_key=api_key)
    return Response(content=html, media_type="text/html")
```

- [ ] **Step 2: Commit**

```bash
git add server/routes/todo.py
git commit -m "feat(todo): add web UI endpoint"
```

---

## Task 5: Register routes, update gitignore, add OPEN_PATHS

**Files:**
- Modify: `server/main.py`
- Modify: `.gitignore`

- [ ] **Step 1: Register the todo router in `server/main.py`**

Add import at the top (after existing router imports):
```python
from routes.todo import router as todo_router
```

Add `/v1/todo/ui` to the existing `OPEN_PATHS` set in `server/main.py`:
```python
OPEN_PATHS = {
    # ... existing entries ...
    "/v1/todo/ui",
}
```

Register the router (after existing `app.include_router` lines):
```python
app.include_router(todo_router, prefix="/v1")
```

- [ ] **Step 2: Add `todos.json` to `.gitignore`**

Open the root `.gitignore` and add:
```
server/todos.json
server/todos.json.tmp
```

- [ ] **Step 3: Verify the server starts cleanly**

```bash
cd server && uv run uvicorn main:app --host 0.0.0.0 --port 7333 --reload
```

Expected: server starts with no import errors. Visit `http://localhost:7333/v1/todo/ui` in a browser — the todo UI should load with an empty list.

- [ ] **Step 4: Smoke-test the API**

```bash
# Add a task (replace YOUR_KEY with value from .env)
curl -s -X POST http://localhost:7333/v1/todo \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "Test task"}' | python3 -m json.tool

# List tasks
curl -s http://localhost:7333/v1/todo \
  -H "X-API-Key: YOUR_KEY" | python3 -m json.tool

# Fetch the display image (saves to /tmp/todo.jpg to inspect)
curl -s "http://localhost:7333/v1/todo/image?selected=0" \
  -H "X-API-Key: YOUR_KEY" -o /tmp/todo.jpg && open /tmp/todo.jpg
```

Expected: task appears in list; JPEG shows the task centred and highlighted.

- [ ] **Step 5: Commit**

```bash
git add server/main.py .gitignore
git commit -m "feat(todo): register router, open UI path, gitignore todos.json"
```

---

## Task 6: ESP32 firmware — TODO screen

**Files:**
- Modify: `src/main.cpp`

- [ ] **Step 1: Add `TODO` to the `Screen` enum and declare state variable**

Find line 37:
```cpp
enum Screen { CLOCK, SPOTIFY, CC_USAGE, RTSP };
```
Replace with:
```cpp
enum Screen { CLOCK, SPOTIFY, CC_USAGE, RTSP, TODO };
```

After line 88 (`static volatile bool rtspFetchError = false;`), add:
```cpp
int todoSelectedIndex = 0;
```

- [ ] **Step 2: Add `fetchTodoImage()` and `sendTodoAction()` functions**

Add these after `sendCommand()` (around line 496):

```cpp
void fetchTodoImage() {
    if (WiFi.status() != WL_CONNECTED) return;
    HTTPClient http;
    String url = String(serverUrl) + "/v1/todo/image?selected=" + todoSelectedIndex;
    http.begin(url);
    http.addHeader("X-API-Key", apiKey);
    int code = http.GET();
    if (code != 200) {
        Serial.printf("Todo image HTTP error: %d\n", code);
        http.end();
        return;  // non-critical: don't set serverUnreachableSince, no clock fallback
    }
    int contentLength = http.getSize();
    if (contentLength <= 0 || contentLength > 65536) {
        Serial.printf("Todo image unexpected size: %d\n", contentLength);
        http.end();
        return;
    }
    uint8_t *buf = (uint8_t *)malloc(contentLength);
    if (!buf) { Serial.println("Todo malloc failed"); http.end(); return; }
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
    if (received == contentLength) {
        tft.startWrite();
        tft.setSwapBytes(true);
        TJpgDec.drawJpg(0, 0, buf, contentLength);
        tft.setSwapBytes(false);
        tft.endWrite();
        serverUnreachableSince = 0;
    } else {
        Serial.printf("Todo incomplete: %d/%d\n", received, contentLength);
    }
    free(buf);
}

void sendTodoAction(const char* action) {
    if (WiFi.status() != WL_CONNECTED) return;
    HTTPClient http;
    String url = String(serverUrl) + "/v1/todo/action?selected=" + todoSelectedIndex + "&action=" + action;
    http.begin(url);
    http.addHeader("X-API-Key", apiKey);
    int code = http.sendRequest("PATCH", "");
    http.end();
    if (code == 200) {
        todoSelectedIndex = 0;
        fetchTodoImage();
    } else {
        Serial.printf("Todo action HTTP error: %d\n", code);
        // non-critical: don't set serverUnreachableSince, no clock fallback
    }
}
```

- [ ] **Step 3: Add `TODO` case to `activateScreen()`**

Find the end of `activateScreen()` — the closing `}` after the `RTSP` block (around line 665). Add before that closing `}`:

```cpp
    } else if (s == TODO) {
        todoSelectedIndex = 0;
        drawStatus("Loading...");
        fetchTodoImage();
    }
```

- [ ] **Step 4: Add `TODO` handling to GPIO 19 button callbacks in `setup()`**

Update `btn.attachClick`:
```cpp
    btn.attachClick([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) {
            rtspIndex = (rtspIndex + 1) % rtspStreamCount;
            return;
        }
        if (activeScreen == TODO) {
            sendTodoAction("done");
            return;
        }
        sendCommand("/v1/spotify/toggle");
    });
```

Update `btn.attachDoubleClick`:
```cpp
    btn.attachDoubleClick([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) {
            rtspIndex = (rtspIndex - 1 + rtspStreamCount) % rtspStreamCount;
            return;
        }
        if (activeScreen == TODO) {
            todoSelectedIndex++;
            fetchTodoImage();
            return;
        }
        sendCommand("/v1/spotify/next");
    });
```

Update `btn.attachLongPressStart`:
```cpp
    btn.attachLongPressStart([]() {
        if (activeScreen == CLOCK) return;
        if (activeScreen == RTSP) return;
        if (activeScreen == TODO) {
            sendTodoAction("archive");
            return;
        }
        sendCommand("/v1/spotify/previous");
    });
```

- [ ] **Step 5: Update GPIO 21 screen cycle**

Replace the `btn2.attachClick` lambda:
```cpp
    btn2.attachClick([]() {
        // Forward cycle: CLOCK -> CC_USAGE -> RTSP -> SPOTIFY -> TODO -> CLOCK
        Screen next;
        if      (activeScreen == CLOCK)    next = CC_USAGE;
        else if (activeScreen == CC_USAGE) next = RTSP;
        else if (activeScreen == RTSP)     next = SPOTIFY;
        else if (activeScreen == SPOTIFY)  next = TODO;
        else                               next = CLOCK;
        activateScreen(next);
    });
```

Replace the `btn2.attachDoubleClick` lambda:
```cpp
    btn2.attachDoubleClick([]() {
        // Backward cycle: CLOCK -> TODO -> SPOTIFY -> RTSP -> CC_USAGE -> CLOCK
        Screen target;
        if      (activeScreen == CLOCK)    target = TODO;
        else if (activeScreen == TODO)     target = SPOTIFY;
        else if (activeScreen == SPOTIFY)  target = RTSP;
        else if (activeScreen == RTSP)     target = CC_USAGE;
        else                               target = CLOCK;
        activateScreen(target);
    });
```

- [ ] **Step 6: Build the firmware to verify no compile errors**

```bash
pio run
```

Expected: `SUCCESS` with no errors. (IDE clang warnings about Arduino.h are harmless — use `pio run` as the source of truth.)

- [ ] **Step 7: Commit**

```bash
git add src/main.cpp
git commit -m "feat(todo): add TODO screen to ESP32 firmware"
```

---

## Task 7: Flash and verify end-to-end

- [ ] **Step 1: Flash to device**

```bash
pio run --target upload
```

- [ ] **Step 2: Open serial monitor and cycle to TODO screen**

```bash
pio device monitor
```

Press GPIO 21 to cycle forward until `TODO` screen appears. Expected serial output:
```
Todo image HTTP error: ...  (if no tasks yet — empty state shows "All done!")
```
Or if tasks exist, the JPEG renders.

- [ ] **Step 3: Add a task via the web UI and verify it appears**

Open `http://<server-ip>:7333/v1/todo/ui` on your phone. Add a task. Press GPIO 21 to re-enter the TODO screen (or wait — the screen re-fetches on activation). The task should appear on the display.

- [ ] **Step 4: Test button gestures**

With at least 3 tasks in the list:
- Double-click GPIO 19 → highlight moves to next task
- Single-click GPIO 19 → current task marked done, display resets to task 1
- Long-press GPIO 19 → current task archived, display resets to task 1

- [ ] **Step 5: Final commit and open PR**

```bash
git push -u origin feat/todo-screen
```

Then open a PR from `feat/todo-screen` → `main`.
