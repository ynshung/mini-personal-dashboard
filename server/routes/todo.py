import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
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


def _visible_tasks(data: dict) -> list[dict]:
    return [t for t in data["tasks"] if t["status"] in ("active", "done")]


# --- REST endpoints ---

class AddTask(BaseModel):
    title: str


class ReorderBody(BaseModel):
    ids: list[str]


@router.get("/todo")
def list_tasks():
    with _lock:
        data = _load()
    return _visible_tasks(data)


@router.get("/todo/archived")
def list_archived():
    with _lock:
        data = _load()
    return [t for t in data["tasks"] if t["status"] == "archived"]


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
        visible = _visible_tasks(data)
        if not visible:
            raise HTTPException(status_code=404, detail="No tasks")
        idx = min(selected, len(visible) - 1)
        target_id = visible[idx]["id"]
        for t in data["tasks"]:
            if t["id"] == target_id:
                if action == "archive":
                    t["status"] = "archived"
                else:
                    t["status"] = "active" if t["status"] == "done" else "done"
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


@router.patch("/todo/{task_id}/undone")
def mark_undone(task_id: str):
    with _lock:
        data = _load()
        for t in data["tasks"]:
            if t["id"] == task_id:
                t["status"] = "active"
                _save(data)
                return {"ok": True}
    raise HTTPException(status_code=404, detail="Task not found")


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


# --- SSE change feed ---

@router.get("/todo/events")
async def todo_events(request: Request):
    async def generator():
        last_mtime = TODOS_FILE.stat().st_mtime if TODOS_FILE.exists() else 0
        yield "data: connected\n\n"
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(1)
            mtime = TODOS_FILE.stat().st_mtime if TODOS_FILE.exists() else 0
            if mtime != last_mtime:
                last_mtime = mtime
                yield "data: changed\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- Image rendering ---

IMG_SIZE = 240
CIRCLE_RADIUS = 110
COL_WHITE = (255, 255, 255)
COL_DIM = (100, 100, 100)
COL_GREY = (136, 136, 136)
COL_BLACK = (0, 0, 0)

ROW_HEIGHT = 28
ROWS_VISIBLE = 5
CHECKBOX_SIZE = 11
CHECKBOX_X = 28
TEXT_X = 46
TEXT_MAX_W = 164
TASKS_START_Y = 64


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _draw_checkbox(draw: ImageDraw.ImageDraw, x: int, y: int, done: bool, col: tuple) -> None:
    draw.rectangle((x, y, x + CHECKBOX_SIZE, y + CHECKBOX_SIZE), outline=col, width=2)
    if done:
        draw.rectangle((x + 2, y + 2, x + CHECKBOX_SIZE - 2, y + CHECKBOX_SIZE - 2), fill=col)


def _render_todo_jpeg(selected: int) -> bytes:
    with _lock:
        data = _load()
    visible = _visible_tasks(data)

    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), COL_BLACK)
    draw = ImageDraw.Draw(img)

    if not visible:
        draw.text((IMG_SIZE // 2, IMG_SIZE // 2), "All done!", fill=COL_WHITE,
                  font=FONT_TASK, anchor="mm")
    else:
        selected = min(selected, len(visible) - 1)
        total = len(visible)
        start = max(0, min(selected - 2, total - ROWS_VISIBLE))
        window = visible[start: start + ROWS_VISIBLE]


        for i, task in enumerate(window):
            abs_idx = start + i
            is_selected = abs_idx == selected
            col = COL_WHITE if is_selected else COL_DIM
            cy = TASKS_START_Y + i * ROW_HEIGHT

            box_y = cy - CHECKBOX_SIZE // 2
            _draw_checkbox(draw, CHECKBOX_X, box_y, task["status"] == "done", col)

            title = _truncate(draw, task["title"], FONT_TASK, TEXT_MAX_W)
            draw.text((TEXT_X, cy), title, fill=col, font=FONT_TASK, anchor="lm")


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


# --- Web UI ---

_UI_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Todo</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,sans-serif;background:#111;color:#eee;padding:16px;max-width:520px;margin:0 auto}
    h1{font-size:1.1rem;margin-bottom:14px;letter-spacing:.08em;color:#aaa;text-transform:uppercase}
    h2{font-size:.8rem;letter-spacing:.1em;color:#555;text-transform:uppercase;margin:18px 0 6px}
    .add-row{display:flex;gap:8px;margin-bottom:18px}
    input[type=text]{flex:1;padding:9px 12px;border-radius:8px;border:1px solid #333;background:#1e1e1e;color:#eee;font-size:1rem}
    input[type=text]:focus{outline:none;border-color:#555}
    .btn{padding:7px 12px;border-radius:8px;border:none;cursor:pointer;font-size:.8rem;font-weight:600}
    .btn-add{padding:9px 14px;font-size:.85rem;background:#1a73e8;color:#fff}
    .btn-archive{background:#37474f;color:#ccc}
    .btn-del{background:#7f0000;color:#fff}
    .task-list{list-style:none}
    .task-item{position:relative;display:flex;align-items:center;gap:8px;padding:9px 6px;border-bottom:1px solid #1e1e1e}
    .task-item.dragging{opacity:.35}
    .task-item.drag-over{border-top:2px solid #1a73e8}
    .drag-handle{color:#333;cursor:grab;font-size:1rem;user-select:none;padding:0 2px}
    .checkbox{width:18px;height:18px;min-width:18px;border:2px solid #555;border-radius:3px;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:.7rem;color:#fff}
    .checkbox.checked{background:#2e7d32;border-color:#2e7d32}
    .task-title{flex:1;font-size:.95rem}
    .task-title.done{text-decoration:line-through;color:#555}
    .menu-wrap{position:relative}
    .kebab-btn{background:none;border:none;color:#555;font-size:1.2rem;padding:0 4px;line-height:1;cursor:pointer;opacity:0;transition:opacity .15s}
    .task-item:hover .kebab-btn{opacity:1}
    .actions{position:absolute;right:0;top:100%;z-index:10;background:#1e1e1e;border:1px solid #333;border-radius:8px;padding:4px;display:none;flex-direction:column;gap:4px;min-width:110px;box-shadow:0 4px 12px #0008}
    .actions .btn{text-align:left;border-radius:6px}
    .menu-wrap:hover .actions{display:flex}
  </style>
</head>
<body>
  <h1>Todo</h1>
  <div class="add-row">
    <input type="text" id="new-task" placeholder="New task…" autocomplete="off">
    <button class="btn btn-add" onclick="addTask()">Add</button>
  </div>
  <ul class="task-list" id="task-list"></ul>
  <h2>Archived</h2>
  <ul class="task-list" id="archived-list"></ul>
  <script>
    const KEY = '__API_KEY__';
    const H = {'X-API-Key': KEY, 'Content-Type': 'application/json'};
    let dragSrc = null;

    async function load() {
      const [visR, archR] = await Promise.all([
        fetch('/v1/todo', {headers: H}),
        fetch('/v1/todo/archived', {headers: H}),
      ]);
      const visible  = await visR.json();
      const archived = await archR.json();
      renderMain(visible);
      renderArchived(archived);
    }

    function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

    function renderMain(tasks) {
      const list = document.getElementById('task-list');
      list.innerHTML = '';
      tasks.forEach(t => {
        const isDone = t.status === 'done';
        const li = document.createElement('li');
        li.className = 'task-item';
        li.draggable = true;
        li.dataset.id = t.id;
        li.innerHTML =
          '<span class="drag-handle">⣿</span>' +
          '<span class="checkbox' + (isDone ? ' checked' : '') + '" onclick="toggle(\\'' + t.id + '\\',' + isDone + ')">' + (isDone ? '✓' : '') + '</span>' +
          '<span class="task-title' + (isDone ? ' done' : '') + '">' + esc(t.title) + '</span>' +
          '<div class="menu-wrap">' +
            '<button class="kebab-btn">⋮</button>' +
            '<div class="actions">' +
              '<button class="btn btn-archive" onclick="act(\\'' + t.id + '\\',\\'archive\\')">Archive</button>' +
              '<button class="btn btn-del"     onclick="del(\\'' + t.id + '\\')">Delete</button>' +
            '</div>' +
          '</div>';
        li.addEventListener('dragstart', () => { dragSrc = li; li.classList.add('dragging'); });
        li.addEventListener('dragend',   () => { li.classList.remove('dragging'); dragSrc = null; });
        li.addEventListener('dragover',  e => { e.preventDefault(); li.classList.add('drag-over'); });
        li.addEventListener('dragleave', () => li.classList.remove('drag-over'));
        li.addEventListener('drop', e => {
          e.preventDefault(); li.classList.remove('drag-over');
          if (!dragSrc || dragSrc === li) return;
          const items = [...list.querySelectorAll('.task-item')];
          const si = items.indexOf(dragSrc), di = items.indexOf(li);
          list.insertBefore(dragSrc, si < di ? li.nextSibling : li);
          const ids = [...list.querySelectorAll('.task-item')].map(el => el.dataset.id);
          fetch('/v1/todo/reorder', {method:'PATCH', headers:H, body:JSON.stringify({ids})});
        });
        list.appendChild(li);
      });
    }

    function renderArchived(tasks) {
      const list = document.getElementById('archived-list');
      list.innerHTML = '';
      tasks.forEach(t => {
        const li = document.createElement('li');
        li.className = 'task-item';
        li.innerHTML =
          '<span class="task-title done">' + esc(t.title) + '</span>' +
          '<div class="menu-wrap">' +
            '<button class="kebab-btn">⋮</button>' +
            '<div class="actions">' +
              '<button class="btn btn-archive" onclick="act(\\'' + t.id + '\\',\\'undone\\')">Restore</button>' +
              '<button class="btn btn-del"     onclick="del(\\'' + t.id + '\\')">Delete</button>' +
            '</div>' +
          '</div>';
        list.appendChild(li);
      });
    }

    function toggle(id, isDone) {
      act(id, isDone ? 'undone' : 'done');
    }

    async function addTask() {
      const inp = document.getElementById('new-task');
      const title = inp.value.trim(); if (!title) return;
      await fetch('/v1/todo', {method:'POST', headers:H, body:JSON.stringify({title})});
      inp.value = ''; load();
    }

    async function act(id, a) {
      await fetch('/v1/todo/' + id + '/' + a, {method:'PATCH', headers:H});
      load();
    }

    async function del(id) {
      await fetch('/v1/todo/' + id, {method:'DELETE', headers:H});
      load();
    }

    document.getElementById('new-task').addEventListener('keydown', e => { if (e.key==='Enter') addTask(); });
    load();

    const es = new EventSource('/v1/todo/events');
    es.onmessage = e => { if (e.data === 'changed') load(); };
  </script>
</body>
</html>
"""


@router.get("/todo/ui", response_class=Response)
def todo_ui():
    return Response(content=_UI_HTML, media_type="text/html")
