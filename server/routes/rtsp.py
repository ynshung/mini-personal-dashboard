import json
import logging
import threading
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from PIL import Image, ImageDraw

IMG_SIZE = 240
CIRCLE_RADIUS = 110


def resize_frame(img: Image.Image, mode: str) -> Image.Image:
    w, h = img.size
    if mode == "fill":
        scale = IMG_SIZE / min(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - IMG_SIZE) // 2
        top = (new_h - IMG_SIZE) // 2
        img = img.crop((left, top, left + IMG_SIZE, top + IMG_SIZE))
    else:  # fit
        scale = IMG_SIZE / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
        canvas.paste(img, ((IMG_SIZE - new_w) // 2, (IMG_SIZE - new_h) // 2))
        img = canvas
    return img


def apply_circular_mask(img: Image.Image) -> Image.Image:
    mask = Image.new("L", (IMG_SIZE, IMG_SIZE), 0)
    draw = ImageDraw.Draw(mask)
    cx, cy = IMG_SIZE // 2, IMG_SIZE // 2
    draw.ellipse(
        (cx - CIRCLE_RADIUS, cy - CIRCLE_RADIUS,
         cx + CIRCLE_RADIUS, cy + CIRCLE_RADIUS),
        fill=255,
    )
    bg = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
    return Image.composite(img, bg, mask)


class RtspGrabber:
    def __init__(self, url: str, mode: str, idle_timeout: float, grab_interval: float):
        self.url = url
        self.mode = mode
        self.idle_timeout = idle_timeout
        self.grab_interval = grab_interval
        self._frame: bytes | None = None
        self._lock = threading.Lock()
        self._last_poll = time.monotonic()
        self._thread: threading.Thread | None = None

    def touch(self) -> None:
        with self._lock:
            self._last_poll = time.monotonic()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def get_frame(self) -> bytes | None:
        with self._lock:
            return self._frame

    def _idle(self) -> bool:
        with self._lock:
            return time.monotonic() - self._last_poll > self.idle_timeout

    def _run(self) -> None:
        import av  # deferred to avoid hard failure if av not installed at import time
        backoff = 1.0
        while True:
            if self._idle():
                break
            try:
                container = av.open(
                    self.url,
                    options={"rtsp_transport": "tcp", "stimeout": "5000000"},
                )
                backoff = 1.0  # fix #4: reset backoff on successful connect
                try:
                    last_encode = 0.0
                    # stimeout covers connection timeout; mid-stream stalls require camera-side keepalives
                    for frame in container.decode(video=0):
                        if self._idle():
                            break
                        now = time.monotonic()
                        if now - last_encode >= self.grab_interval:
                            img = frame.to_image().convert("RGB")
                            img = resize_frame(img, self.mode)
                            img = apply_circular_mask(img)
                            buf = BytesIO()
                            img.save(buf, "JPEG", quality=75, optimize=True)
                            with self._lock:
                                self._frame = buf.getvalue()
                            last_encode = now
                finally:
                    container.close()
            except Exception as e:
                logging.warning("RtspGrabber %s error (backoff %.1fs): %s", self.url, backoff, e)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


CONFIG_PATH = Path(__file__).parent.parent / "rtsp_config.json"


@dataclass
class StreamConfig:
    url: str
    label: str
    mode: str
    grab_interval: float


@dataclass
class RtspConfig:
    idle_timeout: float
    streams: list[StreamConfig]


def load_config() -> RtspConfig:
    if not CONFIG_PATH.exists():
        return RtspConfig(idle_timeout=10.0, streams=[])
    with CONFIG_PATH.open() as f:
        data = json.load(f)
    streams = [
        StreamConfig(
            url=s["url"],
            label=s.get("label", f"Stream {i}"),
            mode=s.get("mode", "fill"),
            grab_interval=max(float(s.get("grab_interval_s", 1.0)), 0.1),
        )
        for i, s in enumerate(data.get("streams", []))
    ]
    return RtspConfig(
        idle_timeout=float(data.get("idle_timeout_s", 10.0)),
        streams=streams,
    )


_config: RtspConfig | None = None
_config_lock = threading.Lock()
_grabbers: dict[int, RtspGrabber] = {}
_grabbers_lock = threading.Lock()
_placeholder: bytes | None = None


def _get_config() -> RtspConfig:
    global _config
    with _config_lock:
        if _config is None:
            _config = load_config()
        return _config


def _make_placeholder() -> bytes:
    global _placeholder
    if _placeholder is None:
        img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
        img = apply_circular_mask(img)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=75)
        _placeholder = buf.getvalue()
    return _placeholder


router = APIRouter()


@router.get("/rtsp/frame")
async def get_rtsp_frame(index: int = Query(0, ge=0)):
    config = _get_config()
    if not config.streams:
        raise HTTPException(status_code=503, detail="No RTSP streams configured")
    if index >= len(config.streams):
        raise HTTPException(status_code=400, detail="Stream index out of range")

    stream_cfg = config.streams[index]

    with _grabbers_lock:
        if index not in _grabbers:
            _grabbers[index] = RtspGrabber(
                url=stream_cfg.url,
                mode=stream_cfg.mode,
                idle_timeout=config.idle_timeout,
                grab_interval=stream_cfg.grab_interval,
            )
        grabber = _grabbers[index]

    grabber.touch()
    if not grabber.is_running():
        grabber.start()

    frame = grabber.get_frame()
    if frame is None:
        frame = _make_placeholder()

    safe_label = stream_cfg.label.replace("\r", " ").replace("\n", " ")
    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={
            "X-Stream-Label": safe_label,
            "X-Stream-Count": str(len(config.streams)),
        },
    )
