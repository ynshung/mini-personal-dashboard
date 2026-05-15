# Personal Dashboard — NodeMCU

A local HTTP server that exposes dashboard data for a NodeMCU display.

## Server

### Requirements

- Python 3.14+
- [uv](https://github.com/astral-sh/uv)
- macOS (Keychain access required for Claude Code credentials)

### Setup & Run

```bash
cd server
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 3737
```

### Testing

```bash
cd server
uv sync --group dev
uv run pytest tests/ -v
```

---

## Endpoints

### `GET /v1/cc-usage`

Returns Claude Code plan usage for the current 5-hour session window.

The token is read automatically from the macOS Keychain (`Claude Code-credentials`). If the token is expired, re-login via Claude Code.

**Response**

```json
{
  "five_hour": {
    "utilization": 34.0,
    "resets_at": "1 hr 10 min"
  },
  "seven_day": {
    "utilization": 66.0,
    "resets_at": "45 hr 10 min"
  }
}
```

| Field | Type | Description |
|---|---|---|
| `five_hour.utilization` | `float \| null` | 5-hour session usage as a percentage (0–100). `null` if not applicable to the plan. |
| `five_hour.resets_at` | `string \| null` | Time until the 5-hour window resets, e.g. `"1 hr 10 min"`. `null` if not applicable. |
| `seven_day.utilization` | `float \| null` | 7-day weekly usage as a percentage (0–100). `null` if not applicable to the plan. |
| `seven_day.resets_at` | `string \| null` | Time until the weekly window resets. Under 24 h: `"X hr Y min"`. Over 24 h: `"Sun 6:00 PM"` (local time). `null` if not applicable. |

**Error responses**

| Status | Cause |
|---|---|
| `401` | Keychain lookup failed or token expired |
| `502` | Unexpected response from Anthropic API |
