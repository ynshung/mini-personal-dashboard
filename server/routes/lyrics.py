import asyncio
import json
import os
import re
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
    prev, curr, next_text = _to_romaji(prev), _to_romaji(curr), _to_romaji(next_text)

    art_url = _playback_cache.get("art_url", "")
    album_id = _playback_cache.get("album_id", "")
    if not art_url or not album_id:
        raise HTTPException(status_code=503, detail="Album art metadata unavailable")

    base = await fetch_cached_art(art_url, album_id)

    blurred = base.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
    dim_overlay = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
    blurred = Image.blend(blurred, dim_overlay, DIM_ALPHA)

    final = composite_lyrics(blurred, prev, curr, next_text)

    buf = BytesIO()
    final.save(buf, format="JPEG", quality=90, optimize=True)

    return Response(
        content=buf.getvalue(),
        media_type="image/jpeg",
        headers={"X-Next-Line-At-Ms": str(next_line_at - LATENCY_OFFSET_MS if next_line_at >= 0 else -1)},
    )
