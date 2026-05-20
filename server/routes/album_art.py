import struct
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

CACHE_DIR = Path(__file__).parent.parent / ".album_art_cache"
FONTS_DIR = Path(__file__).parent.parent / "fonts"
MAX_CACHE_ENTRIES = 50
IMG_SIZE = 240
CIRCLE_RADIUS = 124


def _get_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


FONT_TITLE = _get_font("NotoSansCJK-Medium.ttc", 15)
FONT_ARTIST = _get_font("NotoSansCJK-Regular.ttc", 12)

TITLE_Y = 160
ARTIST_Y = 182
TITLE_MAX_WIDTH = 190
TITLE_TRUNCATE_WIDTH = TITLE_MAX_WIDTH - 8
ARTIST_MAX_WIDTH = 180
ARTIST_TRUNCATE_WIDTH = ARTIST_MAX_WIDTH - 8
GRADIENT_START_Y = 132
GRADIENT_MAX_ALPHA = 204


def _prune_cache() -> None:
    if not CACHE_DIR.exists():
        return
    files = sorted(CACHE_DIR.glob("*.jpg"), key=lambda f: f.stat().st_atime)
    while len(files) > MAX_CACHE_ENTRIES:
        files.pop(0).unlink()


async def fetch_and_build_base(art_url: str, album_id: str) -> Image.Image:
    cache_path = CACHE_DIR / f"{album_id}.jpg"

    if cache_path.exists():
        cache_path.touch()
        return Image.open(cache_path).convert("RGB")

    async with httpx.AsyncClient() as client:
        resp = await client.get(art_url, timeout=10)
    resp.raise_for_status()

    img = Image.open(BytesIO(resp.content)).convert("RGB")
    img = img.resize((IMG_SIZE, IMG_SIZE), Image.LANCZOS)

    gradient = Image.new("L", (IMG_SIZE, IMG_SIZE), 0)
    for y in range(IMG_SIZE):
        if y <= GRADIENT_START_Y:
            alpha = 0
        else:
            alpha = int((y - GRADIENT_START_Y) / (IMG_SIZE - GRADIENT_START_Y) * GRADIENT_MAX_ALPHA)
        for x in range(IMG_SIZE):
            gradient.putpixel((x, y), alpha)

    black = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
    img = Image.composite(black, img, gradient)

    mask = Image.new("L", (IMG_SIZE, IMG_SIZE), 0)
    draw = ImageDraw.Draw(mask)
    cx, cy = IMG_SIZE // 2, IMG_SIZE // 2
    draw.ellipse(
        (cx - CIRCLE_RADIUS, cy - CIRCLE_RADIUS,
         cx + CIRCLE_RADIUS, cy + CIRCLE_RADIUS),
        fill=255,
    )
    bg = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
    img = Image.composite(img, bg, mask)

    CACHE_DIR.mkdir(exist_ok=True)
    img.save(cache_path, "JPEG", quality=75, optimize=True)
    _prune_cache()

    return img


def composite_text(base: Image.Image, track: str, artist: str) -> Image.Image:
    img = base.copy()
    draw = ImageDraw.Draw(img)

    title_bbox = draw.textbbox((0, 0), track, font=FONT_TITLE)
    title_w = title_bbox[2] - title_bbox[0]
    if title_w > TITLE_MAX_WIDTH:
        while title_w > TITLE_TRUNCATE_WIDTH and len(track) > 0:
            track = track[:-1]
            title_bbox = draw.textbbox((0, 0), track + "...", font=FONT_TITLE)
            title_w = title_bbox[2] - title_bbox[0]
        track = track + "..."
        title_bbox = draw.textbbox((0, 0), track, font=FONT_TITLE)
        title_w = title_bbox[2] - title_bbox[0]

    title_x = (IMG_SIZE - title_w) // 2
    draw.text((title_x, TITLE_Y), track, fill=(255, 255, 255), font=FONT_TITLE)

    artist_bbox = draw.textbbox((0, 0), artist, font=FONT_ARTIST)
    artist_w = artist_bbox[2] - artist_bbox[0]
    if artist_w > ARTIST_MAX_WIDTH:
        while artist_w > ARTIST_TRUNCATE_WIDTH and len(artist) > 0:
            artist = artist[:-1]
            artist_bbox = draw.textbbox((0, 0), artist + "...", font=FONT_ARTIST)
            artist_w = artist_bbox[2] - artist_bbox[0]
        artist = artist + "..."
        artist_bbox = draw.textbbox((0, 0), artist, font=FONT_ARTIST)
        artist_w = artist_bbox[2] - artist_bbox[0]

    artist_x = (IMG_SIZE - artist_w) // 2
    draw.text((artist_x, ARTIST_Y), artist, fill=(179, 179, 179), font=FONT_ARTIST)

    return img


def to_rgb565(img: Image.Image) -> bytes:
    pixels = img.tobytes()
    out = bytearray(IMG_SIZE * IMG_SIZE * 2)
    for i in range(IMG_SIZE * IMG_SIZE):
        r, g, b = pixels[i * 3], pixels[i * 3 + 1], pixels[i * 3 + 2]
        rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        struct.pack_into(">H", out, i * 2, rgb565)
    return bytes(out)
