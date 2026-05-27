import hashlib
import json
import os
import random
import time
from io import BytesIO
from pathlib import Path

import httpx
import smartcrop
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, Response
from PIL import Image, ImageDraw, ImageFont

from routes.rtsp import IMG_SIZE, apply_circular_mask

router = APIRouter()

# --- Paths & constants ---

TOKENS_FILE = Path(__file__).parent.parent / ".pinterest_tokens.json"
CACHE_DIR   = Path(__file__).parent.parent / ".pinterest_cache"
FONTS_DIR   = Path(__file__).parent.parent / "fonts"
MAX_CACHE_ENTRIES = 50

PINTEREST_API_BASE = "https://api.pinterest.com/v5"
PINTEREST_AUTH_URL = "https://www.pinterest.com/oauth/"
PINTEREST_TOKEN_URL = f"{PINTEREST_API_BASE}/oauth/token"
REDIRECT_URI = "http://127.0.0.1:7333/v1/pinterest/callback"
SCOPES = "boards:read,pins:read"

COL_GREY = (82, 85, 82)

_FONT: ImageFont.FreeTypeFont | None = None


def _get_font() -> ImageFont.FreeTypeFont:
    global _FONT
    if _FONT is None:
        path = FONTS_DIR / "NotoSansCJK-Medium.ttc"
        try:
            _FONT = ImageFont.truetype(str(path), 14)
        except OSError:
            _FONT = ImageFont.load_default(14)
    return _FONT


# --- Token helpers (mirrors spotify.py) ---

def _client_id() -> str:
    value = os.getenv("PINTEREST_CLIENT_ID")
    if not value:
        raise HTTPException(status_code=500, detail="PINTEREST_CLIENT_ID not set in .env")
    return value


def _client_secret() -> str:
    value = os.getenv("PINTEREST_CLIENT_SECRET")
    if not value:
        raise HTTPException(status_code=500, detail="PINTEREST_CLIENT_SECRET not set in .env")
    return value


def _load_tokens() -> dict:
    if not TOKENS_FILE.exists():
        raise HTTPException(
            status_code=503,
            detail="Pinterest not authenticated. Visit /v1/pinterest/auth",
        )
    return json.loads(TOKENS_FILE.read_text())


def _save_tokens(tokens: dict) -> None:
    TOKENS_FILE.write_text(json.dumps(tokens))


async def _get_access_token() -> str:
    tokens = _load_tokens()

    if tokens.get("expires_at", 0) > time.time() + 30:
        return tokens["access_token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            PINTEREST_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            },
            auth=(_client_id(), _client_secret()),
        )

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Pinterest token refresh failed: {resp.status_code}")

    data = resp.json()
    tokens["access_token"] = data["access_token"]
    tokens["expires_at"] = time.time() + data["expires_in"]
    if "refresh_token" in data:
        tokens["refresh_token"] = data["refresh_token"]
    _save_tokens(tokens)
    return tokens["access_token"]


# --- Auth endpoints ---

@router.get("/pinterest/auth")
async def pinterest_auth():
    TOKENS_FILE.unlink(missing_ok=True)
    params = (
        f"?client_id={_client_id()}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={SCOPES}"
    )
    return RedirectResponse(url=PINTEREST_AUTH_URL + params)


@router.get("/pinterest/callback")
async def pinterest_callback(code: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            PINTEREST_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            auth=(_client_id(), _client_secret()),
        )

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Pinterest token exchange failed: {resp.status_code}")

    data = resp.json()
    _save_tokens({
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": time.time() + data["expires_in"],
    })
    return {"detail": "Pinterest authorized successfully"}


# --- Pin list cache ---

_pin_urls: list[str] = []
_pin_urls_fetched_at: float = 0.0
_PIN_CACHE_TTL = 3600.0   # refresh hourly
_PIN_MAX = 500             # cap at 500 pins total


async def _ensure_pin_urls(token: str) -> None:
    """Populate _pin_urls from the Pinterest API, or use the in-memory cache."""
    global _pin_urls, _pin_urls_fetched_at

    if _pin_urls and (time.time() - _pin_urls_fetched_at) < _PIN_CACHE_TTL:
        return

    board_id = os.getenv("PINTEREST_BOARD_ID")
    if not board_id:
        raise HTTPException(status_code=500, detail="PINTEREST_BOARD_ID not set in .env")

    urls: list[str] = []
    bookmark: str | None = None

    async with httpx.AsyncClient() as client:
        while len(urls) < _PIN_MAX:
            params: dict = {"page_size": 250}
            if bookmark:
                params["bookmark"] = bookmark

            resp = await client.get(
                f"{PINTEREST_API_BASE}/boards/{board_id}/pins",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=15,
            )
            if not resp.is_success:
                print(f"[Pinterest] Board API error {resp.status_code}: {resp.text[:200]}")
                break

            data = resp.json()
            for pin in data.get("items", []):
                media = pin.get("media", {})
                if media.get("media_type") != "image":
                    continue
                images = media.get("images", {})
                # Prefer 1200x, fall back to smaller sizes
                img_data = (
                    images.get("1200x")
                    or images.get("600x")
                    or images.get("400x300")
                )
                if img_data and img_data.get("url"):
                    urls.append(img_data["url"])

            bookmark = data.get("bookmark")
            if not bookmark:
                break

    _pin_urls = urls
    _pin_urls_fetched_at = time.time()
    print(f"[Pinterest] Cached {len(_pin_urls)} pin image URLs from board '{board_id}'")


# --- Image processing ---

def _prune_cache() -> None:
    if not CACHE_DIR.exists():
        return
    files = sorted(CACHE_DIR.glob("*.jpg"), key=lambda f: f.stat().st_atime)
    while len(files) > MAX_CACHE_ENTRIES:
        files.pop(0).unlink()


def _make_placeholder() -> bytes:
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _get_font()
    text = "No images"
    bbox = draw.textbbox((0, 0), text, font=font)
    x = IMG_SIZE // 2 - (bbox[2] - bbox[0]) // 2
    y = IMG_SIZE // 2 - (bbox[3] - bbox[1]) // 2
    draw.text((x, y), text, fill=COL_GREY, font=font)
    img = apply_circular_mask(img)
    buf = BytesIO()
    img.save(buf, "JPEG", quality=75)
    return buf.getvalue()


def _process_image(img: Image.Image) -> bytes:
    """Smart-crop to square, resize to 240x240, apply circular mask, encode JPEG."""
    # 1. Smart-crop: find best min(w,h) x min(w,h) region
    min_dim = min(img.width, img.height)
    sc = smartcrop.SmartCrop()
    result = sc.crop(img, min_dim, min_dim)
    crop = result["top_crop"]
    img = img.crop((
        crop["x"],
        crop["y"],
        crop["x"] + crop["width"],
        crop["y"] + crop["height"],
    ))
    # 2. Resize to display size
    img = img.resize((IMG_SIZE, IMG_SIZE), Image.LANCZOS)
    # 3. Circular mask
    img = apply_circular_mask(img)
    # 4. Encode
    buf = BytesIO()
    img.save(buf, "JPEG", quality=75, optimize=True)
    return buf.getvalue()


# --- Image endpoint ---

@router.get("/pinterest/image")
async def pinterest_image():
    token = await _get_access_token()
    await _ensure_pin_urls(token)

    if not _pin_urls:
        return Response(content=_make_placeholder(), media_type="image/jpeg")

    url = random.choice(_pin_urls)
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    cache_path = CACHE_DIR / f"{url_hash}.jpg"

    print(f"[Pinterest] {url}")

    if cache_path.exists():
        cache_path.touch()
        return Response(content=cache_path.read_bytes(), media_type="image/jpeg")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        jpeg = _process_image(img)
    except Exception as e:
        print(f"[Pinterest] Image fetch/process failed: {e}")
        return Response(content=_make_placeholder(), media_type="image/jpeg")

    CACHE_DIR.mkdir(exist_ok=True)
    cache_path.write_bytes(jpeg)
    _prune_cache()

    return Response(content=jpeg, media_type="image/jpeg")
