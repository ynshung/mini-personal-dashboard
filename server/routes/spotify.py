import json
import os
import time
from io import BytesIO
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, Response
from routes.album_art import fetch_and_build_base, composite_text, to_rgb565

router = APIRouter()

TOKENS_FILE = Path(__file__).parent.parent / ".spotify_tokens.json"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
REDIRECT_URI = "http://127.0.0.1:7333/v1/spotify/callback"
SCOPES = "user-read-currently-playing user-read-playback-state user-modify-playback-state"


def _client_id() -> str:
    value = os.getenv("SPOTIFY_CLIENT_ID")
    if not value:
        raise HTTPException(status_code=500, detail="SPOTIFY_CLIENT_ID not set in .env")
    return value


def _client_secret() -> str:
    value = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not value:
        raise HTTPException(status_code=500, detail="SPOTIFY_CLIENT_SECRET not set in .env")
    return value


def _load_tokens() -> dict:
    if not TOKENS_FILE.exists():
        raise HTTPException(status_code=401, detail="Not authorized — visit /v1/spotify/auth")
    return json.loads(TOKENS_FILE.read_text())


def _save_tokens(tokens: dict) -> None:
    TOKENS_FILE.write_text(json.dumps(tokens))


async def _get_access_token() -> str:
    tokens = _load_tokens()

    if tokens.get("expires_at", 0) > time.time() + 30:
        return tokens["access_token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            },
            auth=(_client_id(), _client_secret()),
        )

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Token refresh failed: {resp.status_code}")

    data = resp.json()
    tokens["access_token"] = data["access_token"]
    tokens["expires_at"] = time.time() + data["expires_in"]
    if "refresh_token" in data:
        tokens["refresh_token"] = data["refresh_token"]
    _save_tokens(tokens)

    return tokens["access_token"]


@router.get("/spotify/auth")
async def spotify_auth():
    TOKENS_FILE.unlink(missing_ok=True)
    params = (
        f"?client_id={_client_id()}"
        f"&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={SCOPES.replace(' ', '%20')}"
    )
    return RedirectResponse(url=SPOTIFY_AUTH_URL + params)


@router.get("/spotify/callback")
async def spotify_callback(code: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            auth=(_client_id(), _client_secret()),
        )

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {resp.status_code}")

    data = resp.json()
    _save_tokens({
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": time.time() + data["expires_in"],
    })

    return {"detail": "Spotify authorized successfully"}


@router.post("/spotify/play")
async def spotify_play():
    token = await _get_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            "https://api.spotify.com/v1/me/player/play",
            headers={"Authorization": f"Bearer {token}"},
        )
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail=f"Spotify API error: {resp.text}")
    return {"detail": "Playback resumed"}


@router.post("/spotify/pause")
async def spotify_pause():
    token = await _get_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            "https://api.spotify.com/v1/me/player/pause",
            headers={"Authorization": f"Bearer {token}"},
        )
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail=f"Spotify API error: {resp.text}")
    return {"detail": "Playback paused"}


@router.post("/spotify/next")
async def spotify_next():
    token = await _get_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.spotify.com/v1/me/player/next",
            headers={"Authorization": f"Bearer {token}"},
        )
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail=f"Spotify API error: {resp.text}")
    return {"detail": "Skipped to next track"}


@router.post("/spotify/previous")
async def spotify_previous():
    token = await _get_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.spotify.com/v1/me/player/previous",
            headers={"Authorization": f"Bearer {token}"},
        )
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail=f"Spotify API error: {resp.text}")
    return {"detail": "Skipped to previous track"}


@router.get("/spotify/now-playing")
async def spotify_now_playing():
    token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.spotify.com/v1/me/player/currently-playing",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 204:
        return {"is_playing": False}

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Spotify API error: {resp.status_code}")

    data = resp.json()

    if data.get("currently_playing_type") != "track":
        return {"is_playing": False}

    item = data.get("item", {})

    return {
        "track_id": item.get("id", ""),
        "is_playing": data.get("is_playing", False),
        "progress_ms": data.get("progress_ms", 0),
        "duration_ms": item.get("duration_ms", 0),
    }


@router.get("/spotify/now-playing/art/jpeg")
async def spotify_now_playing_art_jpeg():
    token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.spotify.com/v1/me/player/currently-playing",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 204:
        return Response(status_code=204)

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Spotify API error: {resp.status_code}")

    data = resp.json()

    if data.get("currently_playing_type") != "track":
        return Response(status_code=204)

    item = data.get("item", {})
    artists = ", ".join(a["name"] for a in item.get("artists", []))
    images = item.get("album", {}).get("images", [])
    art_url = next((img["url"] for img in reversed(images) if img["width"] >= 240), None)
    album_id = item.get("album", {}).get("id", "unknown")

    if not art_url:
        return Response(status_code=204)

    base = await fetch_and_build_base(art_url, album_id)
    final = composite_text(base, item.get("name", ""), artists)

    buf = BytesIO()
    final.save(buf, format="JPEG", quality=90)

    return Response(content=buf.getvalue(), media_type="image/jpeg")


@router.get("/spotify/now-playing/art")
async def spotify_now_playing_art():
    token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.spotify.com/v1/me/player/currently-playing",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 204:
        return Response(status_code=204)

    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Spotify API error: {resp.status_code}")

    data = resp.json()

    if data.get("currently_playing_type") != "track":
        return Response(status_code=204)

    item = data.get("item", {})
    artists = ", ".join(a["name"] for a in item.get("artists", []))
    images = item.get("album", {}).get("images", [])
    art_url = next((img["url"] for img in reversed(images) if img["width"] >= 240), None)
    album_id = item.get("album", {}).get("id", "unknown")

    if not art_url:
        return Response(status_code=204)

    base = await fetch_and_build_base(art_url, album_id)
    final = composite_text(base, item.get("name", ""), artists)
    rgb565 = to_rgb565(final)

    return Response(content=rgb565, media_type="application/octet-stream")
