import json
import subprocess
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter()


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
        raise HTTPException(status_code=401, detail="Claude Code token expired — re-login via Claude Code")
    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Upstream error {resp.status_code}")

    data = resp.json()
    five_hour = data.get("five_hour")
    seven_day = data.get("seven_day")

    return {
        "five_hour": {
            "utilization": five_hour.get("utilization") if five_hour else None,
            "resets_at": _format_resets_at(five_hour.get("resets_at") if five_hour else None),
        },
        "seven_day": {
            "utilization": seven_day.get("utilization") if seven_day else None,
            "resets_at": _format_resets_at(seven_day.get("resets_at") if seven_day else None),
        },
    }
