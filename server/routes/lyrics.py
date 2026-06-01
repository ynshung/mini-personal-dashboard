import asyncio
import json
import os
import re
import shutil
import time
from io import BytesIO
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from PIL import Image, ImageFilter

from routes.album_art import IMG_SIZE, fetch_cached_art, composite_lyrics

router = APIRouter()

LRCLIB_BASE = "https://lrclib.net/api/get"
LRCLIB_TIMEOUT = 15.0
LATENCY_OFFSET_MS = int(os.getenv("LYRICS_LATENCY_OFFSET_MS", "150"))
BLUR_RADIUS = 10
DIM_ALPHA = 0.6

LYRICS_CACHE_DIR = Path(__file__).parent.parent / ".lyrics_cache"

_ROMAJI_ENABLED = os.getenv("LYRICS_ROMAJI", "false").lower() == "true"
_kana_re = re.compile(r"[぀-ヿ]")

if _ROMAJI_ENABLED:
    import pykakasi as _pykakasi
    _kakasi = _pykakasi.kakasi()


def _to_romaji(text: str) -> str:
    if not _ROMAJI_ENABLED or not _kana_re.search(text):
        return text
    result = " ".join(item["hepburn"] for item in _kakasi.convert(text))
    result = re.sub(r"\s+([,\.!?])", r"\1", result)
    return result.capitalize()

# in-memory L1 cache; file cache is L2
# track_id → list[(timestamp_ms, text)] | None (None = no synced lyrics found)
_lyrics_cache: dict[str, list[tuple[int, str]] | None] = {}

_pre_render_tasks: set[str] = set()

def _get_current_track_id() -> str | None:
    frames_dir = LYRICS_CACHE_DIR / "current_frames"
    track_id_file = frames_dir / ".track_id"
    if track_id_file.exists():
        try:
            return track_id_file.read_text().strip()
        except Exception:
            return None
    return None

def _pre_render_worker(
    track_id: str,
    lines: list[tuple[int, str]],
    base_img_bytes: bytes,
) -> None:
    try:
        frames_dir = LYRICS_CACHE_DIR / "current_frames"
        tmp_dir = LYRICS_CACHE_DIR / "tmp_current_frames"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Write track_id file
        (tmp_dir / ".track_id").write_text(track_id)

        t_prep = time.perf_counter()
        base = Image.open(BytesIO(base_img_bytes)).convert("RGB")
        blurred = base.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
        dim_overlay = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
        blurred = Image.blend(blurred, dim_overlay, DIM_ALPHA)
        print(f"[lyrics pre-render] blur+dim prep: {(time.perf_counter() - t_prep)*1000:.1f} ms")

        frame_times = []
        for i in range(len(lines)):
            t_frame = time.perf_counter()
            prev, curr, next_text, _ = _select_lines(lines, i)
            prev, curr, next_text = _to_romaji(prev), _to_romaji(curr), _to_romaji(next_text)

            final = composite_lyrics(blurred, prev, curr, next_text)

            buf = BytesIO()
            final.save(buf, format="JPEG", quality=90, optimize=True)
            (tmp_dir / f"{i}.jpg").write_bytes(buf.getvalue())
            frame_times.append((time.perf_counter() - t_frame) * 1000)

        n = len(frame_times)
        total_ms = sum(frame_times)
        avg_ms = total_ms / n
        std_ms = (sum((t - avg_ms) ** 2 for t in frame_times) / n) ** 0.5
        print(f"[lyrics pre-render] {n} frames: {total_ms:.1f} ms total, {avg_ms:.1f} ± {std_ms:.1f} ms/frame")
        (tmp_dir / ".ready").write_text("ready")

        if frames_dir.exists():
            shutil.rmtree(frames_dir, ignore_errors=True)
        tmp_dir.rename(frames_dir)
    except Exception as e:
        print(f"Error pre-rendering frames for track {track_id}: {e}")
        shutil.rmtree(LYRICS_CACHE_DIR / "tmp_current_frames", ignore_errors=True)
    finally:
        _pre_render_tasks.discard(track_id)

async def pre_render_track_lyrics(
    track_id: str,
    lines: list[tuple[int, str]],
    album_id: str,
    art_url: str,
) -> None:
    if track_id in _pre_render_tasks:
        return
    frames_dir = LYRICS_CACHE_DIR / "current_frames"
    if (frames_dir / ".ready").exists() and _get_current_track_id() == track_id:
        return

    _pre_render_tasks.add(track_id)
    try:
        base_img = await fetch_cached_art(art_url, album_id)
        buf = BytesIO()
        base_img.save(buf, format="JPEG")
        base_img_bytes = buf.getvalue()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            _pre_render_worker,
            track_id,
            lines,
            base_img_bytes,
        )
    except Exception as e:
        print(f"Failed to start pre-rendering for track {track_id}: {e}")
    finally:
        _pre_render_tasks.discard(track_id)


def _lyrics_cache_path(track_id: str) -> Path:
    return LYRICS_CACHE_DIR / f"{track_id}.json"


def _load_from_file(track_id: str) -> list[tuple[int, str]] | None | ...:
    """Return parsed lines, None (no lyrics), or ... (not cached)."""
    path = _lyrics_cache_path(track_id)
    if not path.exists():
        return ...
    data = json.loads(path.read_text())
    if data is None:
        return None
    return [tuple(entry) for entry in data]


def _save_to_file(track_id: str, lines: list[tuple[int, str]] | None) -> None:
    LYRICS_CACHE_DIR.mkdir(exist_ok=True)
    _lyrics_cache_path(track_id).write_text(json.dumps(lines))

# Populated by spotify.py on each now-playing poll
_playback_cache: dict = {}


def update_playback_cache(
    track_id: str,
    track_name: str,
    artist_name: str,
    duration_ms: int,
    album_id: str,
    art_url: str,
    progress_ms: int,
    is_playing: bool,
) -> None:
    _playback_cache.update({
        "track_id": track_id,
        "track_name": track_name,
        "artist_name": artist_name,
        "duration_ms": duration_ms,
        "album_id": album_id,
        "art_url": art_url,
        "progress_ms": progress_ms,
        "is_playing": is_playing,
        "cached_at": time.time(),
    })


def _parse_lrc(synced_lyrics: str) -> list[tuple[int, str]]:
    pattern = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")
    lines = []
    for raw in synced_lyrics.splitlines():
        m = pattern.match(raw.strip())
        if m:
            ts_ms = int((int(m.group(1)) * 60 + float(m.group(2))) * 1000)
            lines.append((ts_ms, m.group(3).strip()))
    return sorted(lines, key=lambda x: x[0])


async def _fetch_lrclib(
    track_name: str, artist_name: str, duration_ms: int
) -> list[tuple[int, str]] | None:
    """Return parsed lines, None (no synced lyrics on lrclib), or ... (transient error)."""
    params = {
        "track_name": track_name,
        "artist_name": artist_name,
        "duration": duration_ms / 1000,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(LRCLIB_BASE, params=params, timeout=LRCLIB_TIMEOUT)
    except Exception:
        return ...

    if resp.status_code == 404:
        return None
    if not resp.is_success:
        return ...

    data = resp.json()
    synced = data.get("syncedLyrics")
    if not synced:
        return None

    parsed = _parse_lrc(synced)
    return parsed if parsed else None


_fetch_in_progress: set[str] = set()


async def _fetch_and_cache(
    track_id: str, track_name: str, artist_name: str, duration_ms: int
) -> None:
    lines = await _fetch_lrclib(track_name, artist_name, duration_ms)
    if lines is not ...:
        _save_to_file(track_id, lines)
        _lyrics_cache[track_id] = lines
        if lines is not None:
            art_url = _playback_cache.get("art_url")
            album_id = _playback_cache.get("album_id")
            if art_url and album_id:
                asyncio.create_task(
                    pre_render_track_lyrics(track_id, lines, album_id, art_url)
                )
    _fetch_in_progress.discard(track_id)


async def get_has_lyrics(
    track_id: str, track_name: str, artist_name: str, duration_ms: int
) -> bool:
    if track_id not in _lyrics_cache:
        cached = _load_from_file(track_id)
        if cached is ...:
            if track_id not in _fetch_in_progress:
                _fetch_in_progress.add(track_id)
                asyncio.create_task(
                    _fetch_and_cache(track_id, track_name, artist_name, duration_ms)
                )
            return False  # still fetching — next poll will get the result
        else:
            _lyrics_cache[track_id] = cached

    lines = _lyrics_cache[track_id]
    if lines is not None:
        art_url = _playback_cache.get("art_url")
        album_id = _playback_cache.get("album_id")
        frames_dir = LYRICS_CACHE_DIR / "current_frames"
        already_ready = (frames_dir / ".ready").exists() and _get_current_track_id() == track_id
        if art_url and album_id and not already_ready and track_id not in _pre_render_tasks:
            asyncio.create_task(
                pre_render_track_lyrics(track_id, lines, album_id, art_url)
            )

    return _lyrics_cache[track_id] is not None


def get_current_line(track_id: str, progress_ms: int) -> tuple[int, int]:
    """Return (current_line_index, next_line_at_ms) for the given track and progress.

    Returns (-1, first_line_ts) before the first line, or (-1, -1) if no lyrics."""
    lines = _lyrics_cache.get(track_id)
    if not lines:
        return -1, -1

    adjusted = progress_ms + LATENCY_OFFSET_MS
    curr_idx = -1
    for i, (ts, _) in enumerate(lines):
        if ts <= adjusted:
            curr_idx = i

    if curr_idx < 0:
        next_at = lines[0][0] - LATENCY_OFFSET_MS
    elif curr_idx + 1 < len(lines):
        next_at = lines[curr_idx + 1][0] - LATENCY_OFFSET_MS
    else:
        next_at = -1

    return curr_idx, next_at


def _select_lines(
    lines: list[tuple[int, str]], line_index: int
) -> tuple[str, str, str, int]:
    """Return (prev, curr, next_text, next_line_at_ms) for the given line index."""
    if not lines:
        return "", "♪", "", -1

    curr_idx = max(0, min(line_index, len(lines) - 1))

    prev = lines[curr_idx - 1][1] if curr_idx > 0 else ""
    curr = lines[curr_idx][1] if curr_idx >= 0 else ""

    if curr_idx < 0:
        next_entry = lines[0]
    elif curr_idx + 1 < len(lines):
        next_entry = lines[curr_idx + 1]
    else:
        next_entry = None

    next_text = next_entry[1] if next_entry is not None else ""
    next_line_at = next_entry[0] if next_entry is not None else -1

    if not prev and curr_idx > 0:
        prev = "♪"
    if not curr:
        curr = "♪"
    if next_entry is not None and not next_text:
        next_text = "♪"

    return prev, curr, next_text, next_line_at



@router.get("/spotify/lyrics/frame")
async def spotify_lyrics_frame(line: int):
    if not _playback_cache:
        return Response(status_code=204)

    track_id = _playback_cache.get("track_id", "")
    if not track_id:
        return Response(status_code=204)

    lines = _lyrics_cache.get(track_id)
    if not lines:
        raise HTTPException(status_code=404, detail="No synced lyrics for this track")

    prev, curr, next_text, next_line_at = _select_lines(lines, line)

    # Check for pre-rendered frame first
    frames_dir = LYRICS_CACHE_DIR / "current_frames"
    if (frames_dir / ".ready").exists() and _get_current_track_id() == track_id:
        frame_path = frames_dir / f"{line}.jpg"
        if frame_path.exists():
            try:
                t_read = time.perf_counter()
                content = frame_path.read_bytes()
                return Response(
                    content=content,
                    media_type="image/jpeg",
                    headers={"X-Next-Line-At-Ms": str(next_line_at - LATENCY_OFFSET_MS if next_line_at >= 0 else -1)},
                )
            except Exception:
                pass

    # Fallback to on-demand render (and trigger pre-rendering in background just in case)
    art_url = _playback_cache.get("art_url", "")
    album_id = _playback_cache.get("album_id", "")
    if art_url and album_id:
        asyncio.create_task(
            pre_render_track_lyrics(track_id, lines, album_id, art_url)
        )

    prev, curr, next_text = _to_romaji(prev), _to_romaji(curr), _to_romaji(next_text)

    t_render = time.perf_counter()
    base = await fetch_cached_art(art_url, album_id)

    blurred = base.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
    dim_overlay = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
    blurred = Image.blend(blurred, dim_overlay, DIM_ALPHA)

    final = composite_lyrics(blurred, prev, curr, next_text)

    buf = BytesIO()
    final.save(buf, format="JPEG", quality=90, optimize=True)
    print(f"[lyrics on-demand] line {line}: {(time.perf_counter() - t_render)*1000:.1f} ms")

    return Response(
        content=buf.getvalue(),
        media_type="image/jpeg",
        headers={"X-Next-Line-At-Ms": str(next_line_at - LATENCY_OFFSET_MS if next_line_at >= 0 else -1)},
    )
