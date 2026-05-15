## Claude Code Plan Usage — How `utilization` and `resets_at` Are Retrieved

### Source
Usage data comes from Anthropic's private OAuth API.

### API Endpoint
```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <access_token>
anthropic-beta: oauth-2025-04-20
```

### Authentication
The access token is the Claude Code OAuth token stored locally:
- **macOS**: system Keychain, service name `Claude Code-credentials` → `security find-generic-password -s "Claude Code-credentials" -w`
- **Linux/Windows**: `~/.claude/.credentials.json` → field `claudeAiOauth.accessToken`

### Response Shape
```json
{
  "five_hour":        { "utilization": 0.42, "resets_at": "2026-05-15T16:00:00Z" },
  "seven_day":        { "utilization": 0.18, "resets_at": "2026-05-22T00:00:00Z" },
  "seven_day_sonnet": { "utilization": 0.15, "resets_at": "2026-05-22T00:00:00Z" },
  "seven_day_opus":   { "utilization": 0.05, "resets_at": "2026-05-22T00:00:00Z" }
}
```

- `utilization` — plan usage as a float from `0.0` to `1.0` (multiply by 100 for percentage). The server calculates this against the user's specific plan limits.
- `resets_at` — ISO 8601 UTC timestamp when the window resets.
- `five_hour` is the **current session** window (5-hour rolling block).
- `seven_day` is the **weekly** window. `seven_day_sonnet` and `seven_day_opus` are model-specific weekly sub-limits.
- A bucket value of `null` means the limit does not apply to the user's plan.

### Error handling
The token is managed entirely by Claude Code itself — it is refreshed automatically during normal Claude Code use. If the token is expired or missing, the API returns a non-200 response. There is no client-side refresh flow; the caller should surface an auth error and instruct the user to log in via Claude Code.

### Data Type

```typescript
const UsageApiBucketSchema = z.looseObject({
    utilization: z.number().nullable().optional(),
    resets_at: z.string().nullable().optional()
}).nullable().optional();
```