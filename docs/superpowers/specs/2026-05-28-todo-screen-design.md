# Todo Screen Design

**Date:** 2026-05-28
**Branch:** main (feature will be implemented on a new branch off main)

## Overview

A new `TODO` screen for the 240×240 round ESP32 display. Tasks are managed via a minimal web UI and persisted in a JSON file on the server. The ESP32 button controls mark tasks done, scroll through the list, and archive items.

## Data Model

**File:** `server/todos.json` (gitignored)

```json
{
  "tasks": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "title": "Call the bank",
      "status": "active",
      "created_at": "2026-05-28T10:00:00"
    }
  ]
}
```

**Status values:** `active` | `done` | `archived`

The file is read on server startup and written atomically on every mutation. Only `active` tasks are shown on the display.

## Server Routes

**File:** `server/routes/todo.py`  
**Registered in:** `server/main.py` as `app.include_router(todo_router, prefix="/v1")`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/v1/todo/image?selected=N` | Returns 240×240 JPEG of the todo list |
| `GET` | `/v1/todo` | Returns JSON array of active tasks |
| `POST` | `/v1/todo` | Add task — body: `{"title": "..."}` |
| `PATCH` | `/v1/todo/{id}/done` | Mark task done (web UI) |
| `PATCH` | `/v1/todo/{id}/archive` | Archive task (web UI) |
| `PATCH` | `/v1/todo/action?selected=N&action=done` | Mark Nth active task done (ESP32) |
| `PATCH` | `/v1/todo/action?selected=N&action=archive` | Archive Nth active task (ESP32) |
| `PATCH` | `/v1/todo/reorder` | Reorder tasks — body: `{"ids": ["uuid", ...]}` |
| `DELETE` | `/v1/todo/{id}` | Delete task permanently |
| `GET` | `/v1/todo/ui` | Serve the web UI HTML page |

`/v1/todo/ui` is added to `OPEN_PATHS` in `main.py` so it is accessible from a phone browser without an `X-API-Key` header.

The image endpoint clamps `selected` to `len(active_tasks) - 1`, so the ESP32 can increment freely without bounds checking.

## Image Rendering Pipeline

All rendering is server-side using Pillow, consistent with the existing album art and Pinterest pipelines.

- Load active tasks from `todos.json`
- Window 6 tasks around `selected` index
- **Selected task:** full brightness, white checkbox outline, white text
- **Other tasks:** 40% opacity, grey checkbox outline
- `"TODO"` label at top (small, grey, letter-spaced)
- `"N / M tasks"` counter at bottom
- **Empty state:** `"All done!"` centred on the display
- Circular mask applied at radius 110 px (consistent with other screens)
- JPEG encode at quality 75
- No caching — renders are fast and tasks change infrequently

## ESP32 Firmware

**New screen constant:** `TODO`

**Screen cycle (GPIO 21):**
- Forward: `CLOCK → CC_USAGE → RTSP → SPOTIFY → TODO → CLOCK`
- Backward: `CLOCK → TODO → SPOTIFY → RTSP → CC_USAGE → CLOCK`

**Local state:** `int todoSelectedIndex = 0` — reset to `0` on `activateScreen(TODO)`.

**GPIO 19 gestures on TODO screen:**

| Gesture | Action |
|---------|--------|
| Single click | `PATCH /v1/todo/action?selected=N&action=done` → reset `todoSelectedIndex = 0` → re-fetch image |
| Double click | `todoSelectedIndex++` → re-fetch image |
| Long press | `PATCH /v1/todo/action?selected=N&action=archive` → reset `todoSelectedIndex = 0` → re-fetch image |

The ESP32 only tracks the integer `todoSelectedIndex` — no UUID handling needed. The server resolves the index to a task ID internally.

## Web UI

Served at `GET /v1/todo/ui` as an inline HTML string from the route handler.

- Text input + **Add** button at the top
- Live list of active tasks, each row has **Done**, **Archive**, and **Delete** buttons
- Rows are drag-to-reorder using the HTML5 Drag and Drop API; new order is persisted via `PATCH /v1/todo/reorder` with the full ordered array of IDs
- Calls the REST API via `fetch()` and refreshes the list on each action
- No framework — plain HTML/CSS/JS, self-contained in the route response

## Error Handling

- If `todos.json` does not exist on startup, the server creates it with an empty task list
- `PATCH /done` and `PATCH /archive` on a non-existent ID return `404`
- On HTTP error from any todo endpoint, the ESP32 treats it like a server failure (increments `pollFailed`) but does not fall back to the clock screen — todo errors are non-critical
