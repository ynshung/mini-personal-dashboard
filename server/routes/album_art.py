import os
import struct
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

CACHE_DIR = Path(__file__).parent.parent / ".album_art_cache"
FONTS_DIR = Path(__file__).parent.parent / "fonts"
MAX_CACHE_ENTRIES = 50
IMG_SIZE = 240
CIRCLE_RADIUS = 120


def _get_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


FONT_TITLE = _get_font("NotoSansCJK-Medium.ttc", 15)
FONT_ARTIST = _get_font("NotoSansCJK-Regular.ttc", 12)

LYRICS_FONT_SIZE = int(os.getenv("LYRICS_FONT_SIZE", "17"))
LYRICS_CTX_SIZE = round(LYRICS_FONT_SIZE * 0.7)
FONT_LYRICS_CURR = _get_font("NotoSansCJK-Medium.ttc", LYRICS_FONT_SIZE)
FONT_LYRICS_CTX = _get_font("NotoSansCJK-Regular.ttc", LYRICS_CTX_SIZE)
LYRICS_MAX_WIDTH = 196      # 240 - 22px padding each side (current line)
LYRICS_CTX_MAX_WIDTH = 152  # narrower for prev/next lines
LYRICS_LINE_GAP = 10
LYRICS_LINE_SPACING = 4  # extra px between wrapped lines of the current lyric

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
    ratio = max(IMG_SIZE / img.width, IMG_SIZE / img.height)
    img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
    left = (img.width - IMG_SIZE) // 2
    top = (img.height - IMG_SIZE) // 2
    img = img.crop((left, top, left + IMG_SIZE, top + IMG_SIZE))

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


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    if not text:
        return [""]
    words = text.split(" ")
    if len(words) > 1:
        # Latin-like text: wrap at word boundaries
        result: list[str] = []
        current = ""
        for word in words:
            test = (current + " " + word).strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = test
            else:
                if current:
                    result.append(current)
                current = word
        if current:
            result.append(current)
        return result or [""]
    else:
        # CJK or single word: wrap at character boundaries
        result = []
        current = ""
        for char in text:
            test = current + char
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = test
            else:
                if current:
                    result.append(current)
                current = char
        if current:
            result.append(current)
        return result or [""]


def composite_lyrics(base: Image.Image, prev: str, curr: str, next_: str) -> Image.Image:
    img = base.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Measure line heights with a representative mixed string
    curr_bbox = draw.textbbox((0, 0), "Ag界", font=FONT_LYRICS_CURR)
    curr_line_h = curr_bbox[3] - curr_bbox[1]
    ctx_bbox = draw.textbbox((0, 0), "Ag界", font=FONT_LYRICS_CTX)
    ctx_line_h = ctx_bbox[3] - ctx_bbox[1]

    def _truncate(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
        if not text or draw.textbbox((0, 0), text, font=font)[2] <= max_w:
            return text
        while text and draw.textbbox((0, 0), text + "…", font=font)[2] > max_w:
            text = text[:-1]
        return text + "…"

    prev_text = _truncate(prev, FONT_LYRICS_CTX, LYRICS_CTX_MAX_WIDTH)
    curr_lines = _wrap_text(draw, curr, FONT_LYRICS_CURR, LYRICS_MAX_WIDTH)
    next_text = _truncate(next_, FONT_LYRICS_CTX, LYRICS_CTX_MAX_WIDTH)

    total_h = (
        ctx_line_h
        + LYRICS_LINE_GAP
        + len(curr_lines) * curr_line_h + max(0, len(curr_lines) - 1) * LYRICS_LINE_SPACING
        + LYRICS_LINE_GAP
        + ctx_line_h
    )
    y = (IMG_SIZE - total_h) // 2

    dim_fill = (255, 255, 255, 89)  # white at ~35% opacity

    if prev_text:
        w = draw.textbbox((0, 0), prev_text, font=FONT_LYRICS_CTX)[2]
        draw.text(((IMG_SIZE - w) // 2, y), prev_text, fill=dim_fill, font=FONT_LYRICS_CTX)
    y += ctx_line_h

    y += LYRICS_LINE_GAP

    for line in curr_lines:
        if line:
            w = draw.textbbox((0, 0), line, font=FONT_LYRICS_CURR)[2]
            draw.text(((IMG_SIZE - w) // 2, y), line, fill=(255, 255, 255, 255), font=FONT_LYRICS_CURR)
        y += curr_line_h + LYRICS_LINE_SPACING

    y += LYRICS_LINE_GAP

    if next_text:
        w = draw.textbbox((0, 0), next_text, font=FONT_LYRICS_CTX)[2]
        draw.text(((IMG_SIZE - w) // 2, y), next_text, fill=dim_fill, font=FONT_LYRICS_CTX)

    return Image.alpha_composite(img, overlay).convert("RGB")


def to_rgb565(img: Image.Image) -> bytes:
    pixels = img.tobytes()
    out = bytearray(IMG_SIZE * IMG_SIZE * 2)
    for i in range(IMG_SIZE * IMG_SIZE):
        r, g, b = pixels[i * 3], pixels[i * 3 + 1], pixels[i * 3 + 2]
        rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        struct.pack_into(">H", out, i * 2, rgb565)
    return bytes(out)
