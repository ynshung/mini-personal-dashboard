import json
import os
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

router = APIRouter()

TOKENS_FILE = Path(__file__).parent.parent / ".spotify_tokens.json"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
REDIRECT_URI = "http://127.0.0.1:3737/v1/spotify/callback"
SCOPES = "user-read-currently-playing user-read-playback-state"


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
    artists = ", ".join(a["name"] for a in item.get("artists", []))

    return {
        "is_playing": data.get("is_playing", False),
        "track": item.get("name"),
        "artist": artists,
        "progress_ms": data.get("progress_ms"),
        "duration_ms": item.get("duration_ms"),
    }
