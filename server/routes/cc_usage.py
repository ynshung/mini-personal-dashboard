import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import time
import os

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter()

CC_USAGE_CACHE_TTL = int(os.getenv("CC_USAGE_CACHE_TTL", "120"))
_CACHE_FILE = Path(__file__).parent.parent / ".cc_usage_cache.json"


def _load_cache() -> tuple[dict, float] | None:
    try:
        raw = json.loads(_CACHE_FILE.read_text())
        if time.time() - raw["ts"] < CC_USAGE_CACHE_TTL:
            return raw["data"], raw["ts"]
    except Exception:
        pass
    return None


def _save_error_cache(status: int, detail: str) -> None:
    try:
        _CACHE_FILE.write_text(json.dumps({"ts": time.time(), "data": {"error": True, "status": status, "detail": detail}}))
    except Exception:
        pass


def _format_refreshed_ago(age_secs: float) -> str:
    if age_secs < 30:
        return "just now"
    if age_secs < 60:
        return "a moment ago"
    if age_secs < 120:
        return "a minute ago"
    if age_secs < 180:
        return "2 minutes ago"
    return ">3 minutes ago"


def _save_cache(data: dict) -> None:
    try:
        _CACHE_FILE.write_text(json.dumps({"ts": time.time(), "data": data}))
    except Exception:
        pass


def _get_token() -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=401, detail="Claude Code credentials not found")
    try:
        return json.loads(result.stdout)["claudeAiOauth"]["accessToken"]
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(status_code=401, detail="Claude Code credentials malformed")


def _format_resets_at(resets_at: str | None) -> str | None:
    if resets_at is None:
        return None
    dt = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
    remaining = int((dt - datetime.now(timezone.utc)).total_seconds())
    if remaining <= 0:
        return "0 min"
    if remaining > 86400:
        local_dt = dt.astimezone()
        return local_dt.strftime("%a %-I:%M %p")
    hours, rem = divmod(remaining, 3600)
    minutes = rem // 60
    parts = []
    if hours:
        parts.append(f"{hours} hr")
    if minutes:
        parts.append(f"{minutes} min")
    return " ".join(parts) if parts else "0 min"


@router.get("/cc-usage")
async def cc_usage():
    cached = _load_cache()
    if cached is not None:
        data, ts = cached
        if data.get("error"):
            raise HTTPException(status_code=data["status"], detail=data["detail"])
        data["five_hour"]["resets_at"] = _format_resets_at(data["five_hour"]["resets_at"])
        data["seven_day"]["resets_at"] = _format_resets_at(data["seven_day"]["resets_at"])
        data["refreshed_ago"] = _format_refreshed_ago(time.time() - ts)
        return data

    token = _get_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
        )
    if resp.status_code == 401:
        detail = "Claude Code token expired — re-login via Claude Code"
        _save_error_cache(401, detail)
        raise HTTPException(status_code=401, detail=detail)
    if not resp.is_success:
        detail = f"Upstream error {resp.status_code}"
        _save_error_cache(502, detail)
        raise HTTPException(status_code=502, detail=detail)

    data = resp.json()
    five_hour = data.get("five_hour")
    seven_day = data.get("seven_day")

    result = {
        "five_hour": {
            "utilization": five_hour.get("utilization") if five_hour else None,
            "resets_at": five_hour.get("resets_at") if five_hour else None,
        },
        "seven_day": {
            "utilization": seven_day.get("utilization") if seven_day else None,
            "resets_at": seven_day.get("resets_at") if seven_day else None,
        },
    }
    _save_cache(result)
    result["five_hour"]["resets_at"] = _format_resets_at(result["five_hour"]["resets_at"])
    result["seven_day"]["resets_at"] = _format_resets_at(result["seven_day"]["resets_at"])
    result["refreshed_ago"] = "Just now"
    return result
