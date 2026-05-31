#!/usr/bin/env python3
"""
Lyrics timing debugger — side-by-side comparison of instant vs offset display.

Left panel: lines advance exactly at LRC timestamps (offset=0).
Right panel: lines advance LATENCY_OFFSET_MS early (mirrors ESP32 firmware).

Usage:
    python3 scripts/lyrics_debug.py

Reads SERVER_URL, API_KEY, and LYRICS_LATENCY_OFFSET_MS from .env at the
project root. No extra dependencies — uses only the stdlib.
"""
import json
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Config from .env
# ---------------------------------------------------------------------------

ENV_FILE = Path(__file__).parent.parent / ".env"
LYRICS_CACHE_DIR = Path(__file__).parent.parent / "server" / ".lyrics_cache"


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


_env = _load_env()
SERVER_URL = _env.get("SERVER_URL", "http://localhost:7333").rstrip("/")
API_KEY = _env.get("API_KEY", "")
LATENCY_OFFSET_MS = int(_env.get("LYRICS_LATENCY_OFFSET_MS", "150"))
POLL_INTERVAL_S = 5.0

# ---------------------------------------------------------------------------
# ANSI
# ---------------------------------------------------------------------------

CLEAR = "\033[2J\033[H"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
RESET = "\033[0m"

COL_W = 38  # characters per lyric column

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ms_fmt(ms: int) -> str:
    neg = ms < 0
    ms = abs(ms)
    s = ms // 1000
    return f"{'-' if neg else ''}{s // 60}:{s % 60:02d}.{(ms % 1000) // 10:02d}"


def load_lyrics(track_id: str) -> list[tuple[int, str]] | None:
    path = LYRICS_CACHE_DIR / f"{track_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data is None:
        return None
    return [tuple(e) for e in data]


def compute_line(lines: list, progress_ms: int, offset_ms: int) -> tuple[int, int]:
    """Returns (curr_idx, next_line_at_ms) for the given offset."""
    adjusted = progress_ms + offset_ms
    curr_idx = -1
    for i, (ts, _) in enumerate(lines):
        if ts <= adjusted:
            curr_idx = i

    if curr_idx < 0:
        next_at = lines[0][0] - offset_ms
    elif curr_idx + 1 < len(lines):
        next_at = lines[curr_idx + 1][0] - offset_ms
    else:
        next_at = -1

    return curr_idx, next_at


def local_progress(state: dict) -> int:
    if not state["is_playing"]:
        return state["progress_ms"]
    elapsed_ms = (time.monotonic() - state["polled_at"]) * 1000
    return int(state["progress_ms"] + elapsed_ms)


def poll_now_playing() -> dict | None:
    url = f"{SERVER_URL}/v1/spotify/now-playing"
    req = urllib.request.Request(url, headers={"X-API-Key": API_KEY} if API_KEY else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def trunc(text: str, width: int) -> str:
    return text[:width - 1] + "…" if len(text) > width else text


def lyric_rows(lines: list | None, idx: int, placeholder: str) -> tuple[str, str, str]:
    """Return (prev, curr, next) display strings."""
    if not lines:
        return "", placeholder, ""
    if idx < 0:
        return "", "♪", lines[0][1] if lines else ""
    prev = lines[idx - 1][1] if idx > 0 else ""
    curr = lines[idx][1] or "♪"
    nxt = lines[idx + 1][1] if idx + 1 < len(lines) else ""
    return prev, curr, nxt

# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render(state: dict, status_msg: str) -> None:
    lines = state["lines"]
    prog = local_progress(state)

    print(CLEAR, end="")

    # Header
    track_id = state["track_id"] or "–"
    short_id = trunc(track_id, 44)
    play_icon = "▶" if state["is_playing"] else "⏸"
    print(f"{CYAN}{BOLD}{short_id}{RESET}")
    print(f"{play_icon}  {ms_fmt(prog)} / {ms_fmt(state['duration_ms'])}")
    print()

    if not lines:
        print(f"  {DIM}{status_msg or '(no lyrics)'}{RESET}")
        print()
        print(f"{DIM}LATENCY_OFFSET_MS={LATENCY_OFFSET_MS}  server={SERVER_URL}  Ctrl+C to quit{RESET}")
        return

    # Column headers
    left_hdr = f"instant  (offset=0ms)"
    right_hdr = f"with offset  ({LATENCY_OFFSET_MS}ms)"
    print(f"  {DIM}{left_hdr:<{COL_W}}  {right_hdr}{RESET}")
    print(f"  {DIM}{'─' * COL_W}  {'─' * COL_W}{RESET}")

    # Lyric rows for each panel
    l_prev, l_curr, l_next = lyric_rows(lines, state["line_idx_instant"], status_msg)
    r_prev, r_curr, r_next = lyric_rows(lines, state["line_idx_offset"], status_msg)

    lp = trunc(l_prev, COL_W)
    rp = trunc(r_prev, COL_W)
    lc = trunc(l_curr, COL_W)
    rc = trunc(r_curr, COL_W)
    ln = trunc(l_next, COL_W)
    rn = trunc(r_next, COL_W)

    print(f"  {DIM}{lp:<{COL_W}}  {rp}{RESET}")
    print(f"  {BOLD}{lc:<{COL_W}}  {rc}{RESET}")
    print(f"  {DIM}{ln:<{COL_W}}  {rn}{RESET}")

    print()
    print(f"{DIM}LATENCY_OFFSET_MS={LATENCY_OFFSET_MS}  server={SERVER_URL}  Ctrl+C to quit{RESET}")

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> None:
    state: dict = {
        "track_id": None,
        "duration_ms": 0,
        "is_playing": False,
        "progress_ms": 0,
        "polled_at": time.monotonic(),
        # instant panel (offset=0)
        "line_idx_instant": -1,
        "next_at_instant": -1,
        # offset panel (LATENCY_OFFSET_MS)
        "line_idx_offset": -1,
        "next_at_offset": -1,
        "lines": None,
    }
    last_poll = 0.0
    wake_instant = time.monotonic()
    wake_offset = time.monotonic()
    status_msg = "Connecting…"

    try:
        while True:
            now = time.monotonic()

            # Poll server every POLL_INTERVAL_S
            if now - last_poll >= POLL_INTERVAL_S:
                data = poll_now_playing()
                last_poll = now

                if data is None:
                    status_msg = "Server unreachable"
                elif not data.get("has_lyrics"):
                    status_msg = "no lyrics" if data.get("is_playing") else "not playing"
                    state["is_playing"] = data.get("is_playing", False)
                    state["lines"] = None
                else:
                    track_id = data["track_id"]
                    status_msg = ""

                    if track_id != state["track_id"]:
                        state["lines"] = load_lyrics(track_id)
                        state["track_id"] = track_id
                        if state["lines"] is None:
                            status_msg = "lyrics not yet cached"

                    state["duration_ms"] = data["duration_ms"]
                    state["is_playing"] = data["is_playing"]
                    state["progress_ms"] = data["progress_ms"]
                    state["polled_at"] = now

                    # Sync both panels from server's current_line
                    server_line = data.get("current_line", -1)
                    state["line_idx_instant"] = server_line
                    state["line_idx_offset"] = server_line

                    # Recompute next wake for each panel
                    if state["lines"]:
                        prog = local_progress(state)
                        _, next_i = compute_line(state["lines"], prog, 0)
                        _, next_o = compute_line(state["lines"], prog, LATENCY_OFFSET_MS)
                        state["next_at_instant"] = next_i
                        state["next_at_offset"] = next_o
                        if next_i >= 0:
                            wake_instant = now + max(50, next_i - prog) / 1000.0
                        if next_o >= 0:
                            wake_offset = now + max(50, next_o - prog) / 1000.0

            # Advance instant panel
            if state["lines"] and state["next_at_instant"] >= 0 and now >= wake_instant:
                prog = local_progress(state)
                new_idx, next_at = compute_line(state["lines"], prog, 0)
                if new_idx != state["line_idx_instant"] and new_idx >= 0:
                    state["line_idx_instant"] = new_idx
                state["next_at_instant"] = next_at
                if next_at >= 0:
                    wake_instant = now + max(50, next_at - local_progress(state)) / 1000.0
                else:
                    wake_instant = now + 3600.0

            # Advance offset panel
            if state["lines"] and state["next_at_offset"] >= 0 and now >= wake_offset:
                prog = local_progress(state)
                new_idx, next_at = compute_line(state["lines"], prog, LATENCY_OFFSET_MS)
                if new_idx != state["line_idx_offset"] and new_idx >= 0:
                    state["line_idx_offset"] = new_idx
                state["next_at_offset"] = next_at
                if next_at >= 0:
                    wake_offset = now + max(50, next_at - local_progress(state)) / 1000.0
                else:
                    wake_offset = now + 3600.0

            render(state, status_msg)
            time.sleep(0.1)

    except KeyboardInterrupt:
        print(f"\n{RESET}Stopped.")


if __name__ == "__main__":
    main()
