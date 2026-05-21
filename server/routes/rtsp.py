import json
import logging
import threading
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont

IMG_SIZE = 240
CIRCLE_RADIUS = 120
CX = IMG_SIZE // 2

FONTS_DIR = Path(__file__).parent.parent / "fonts"
COL_OFF_WHITE  = (200, 200, 200)
COL_GREY   = (82,  85,  82)   # matches COL_GREY RGB565 0x52AA
COL_DIM    = (58,  57,  58)   # matches COL_BAR_BG RGB565 0x39C7

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / "NotoSansCJK-Medium.ttc"
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default(size)

_OVERLAY_FONT = _load_font(14)


def composite_overlay(
    img: Image.Image,
    index: int,
    total: int,
    label: str,
    label_y: int = 16,
    dots_y: int = 218,
) -> Image.Image:
    img = img.copy()
    draw = ImageDraw.Draw(img)

    if label:
        bbox = draw.textbbox((0, 0), label, font=_OVERLAY_FONT)
        text_w = bbox[2] - bbox[0]
        x = CX - text_w // 2
        draw.text((x, label_y), label, fill=COL_OFF_WHITE, font=_OVERLAY_FONT)

    if total > 1:
        dot_r, dot_gap = 3, 13
        n = min(total, 20)
        start_x = CX - ((n - 1) * dot_gap) // 2
        for i in range(n):
            x = start_x + i * dot_gap
            fill = COL_OFF_WHITE if i == index else COL_DIM
            draw.ellipse((x - dot_r, dots_y - dot_r, x + dot_r, dots_y + dot_r), fill=fill)

    return img


def resize_frame(img: Image.Image, mode: str) -> Image.Image:
    w, h = img.size
    if mode == "stretch":
        return img.resize((IMG_SIZE, IMG_SIZE), Image.LANCZOS)
    elif mode == "fill":
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
    def __init__(self, url: str, mode: str, idle_timeout: float, grab_interval: float,
                 label: str, overlay: "OverlayConfig | None", index: int, total: int):
        self.url = url
        self.mode = mode
        self.idle_timeout = idle_timeout
        self.grab_interval = grab_interval
        self._label = label
        self._overlay = overlay
        self._index = index
        self._total = total
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
        logging.info("RTSP stream started: %s", self.url)

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
                logging.info("RTSP stream stopped (idle): %s", self.url)
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
                        if self.grab_interval == 0.0 or now - last_encode >= self.grab_interval:
                            img = frame.to_image().convert("RGB")
                            img = resize_frame(img, self.mode)
                            img = apply_circular_mask(img)
                            if self._overlay is not None:
                                ov = self._overlay
                                img = composite_overlay(
                                    img, self._index,
                                    total=self._total if ov.show_dots else 1,
                                    label=self._label if ov.show_label else "",
                                    label_y=ov.label_y,
                                    dots_y=ov.dots_y,
                                )
                            buf = BytesIO()
                            img.save(buf, "JPEG", quality=75)
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
class OverlayConfig:
    show_label: bool = True
    show_dots: bool = True
    label_y: int = 16
    dots_y: int = 218


@dataclass
class StreamConfig:
    url: str
    label: str
    mode: str
    grab_interval: float


@dataclass
class RtspConfig:
    idle_timeout: float
    overlay: OverlayConfig | None
    streams: list[StreamConfig]


def load_config() -> RtspConfig:
    if not CONFIG_PATH.exists():
        return RtspConfig(idle_timeout=10.0, overlay=None, streams=[])
    with CONFIG_PATH.open() as f:
        data = json.load(f)
    overlay_data = data.get("overlay")
    overlay = (
        OverlayConfig(
            show_label=bool(overlay_data.get("show_label", True)),
            show_dots=bool(overlay_data.get("show_dots", True)),
            label_y=int(overlay_data.get("label_y", 16)),
            dots_y=int(overlay_data.get("dots_y", 218)),
        )
        if overlay_data is not None
        else None
    )
    streams = [
        StreamConfig(
            url=s["url"],
            label=s.get("label", f"Stream {i}"),
            mode=s.get("mode", "fill"),
            grab_interval=float(s.get("grab_interval_s", 0.0)),
        )
        for i, s in enumerate(data.get("streams", []))
    ]
    return RtspConfig(
        idle_timeout=float(data.get("idle_timeout_s", 10.0)),
        overlay=overlay,
        streams=streams,
    )


_config: RtspConfig | None = None
_config_lock = threading.Lock()
_grabbers: dict[int, RtspGrabber] = {}
_grabbers_lock = threading.Lock()
_placeholders: dict[int, bytes] = {}


def _get_config() -> RtspConfig:
    global _config
    with _config_lock:
        if _config is None:
            _config = load_config()
        return _config


def _make_placeholder(index: int, total: int, label: str, overlay: "OverlayConfig | None") -> bytes:
    if index not in _placeholders:
        img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        text = "Loading..."
        bbox = draw.textbbox((0, 0), text, font=_OVERLAY_FONT)
        x = CX - (bbox[2] - bbox[0]) // 2
        y = CX - (bbox[3] - bbox[1]) // 2
        draw.text((x, y), text, fill=COL_GREY, font=_OVERLAY_FONT)
        img = apply_circular_mask(img)
        if overlay is not None:
            img = composite_overlay(
                img, index,
                total=total if overlay.show_dots else 1,
                label=label if overlay.show_label else "",
                label_y=overlay.label_y,
                dots_y=overlay.dots_y,
            )
        buf = BytesIO()
        img.save(buf, "JPEG", quality=75)
        _placeholders[index] = buf.getvalue()
    return _placeholders[index]


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
                label=stream_cfg.label,
                overlay=config.overlay,
                index=index,
                total=len(config.streams),
            )
        grabber = _grabbers[index]

    grabber.touch()
    if not grabber.is_running():
        grabber.start()

    frame = grabber.get_frame()
    if frame is None:
        frame = _make_placeholder(index, len(config.streams), stream_cfg.label, config.overlay)

    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={"X-Stream-Count": str(len(config.streams))},
    )
