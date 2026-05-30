# Lyrics Romaji Conversion

**Date:** 2026-05-30

## Summary

When Japanese lyrics are detected, convert them to romaji before rendering on the display. Controlled by an env var, disabled by default.

## Detection

A lyric line is considered Japanese if it contains at least one hiragana or katakana character:

```python
re.search(r'[぀-ヿ]', text)
```

This avoids false positives on Chinese text (which lacks kana) while covering all real Japanese lyrics, which almost always contain hiragana or katakana.

## Conversion

**Library:** `pykakasi` — pure Python, no system dependencies, handles hiragana/katakana/kanji → romaji.

A module-level `Kakasi` instance is initialized once at startup, only when `LYRICS_ROMAJI=true`, to avoid the init cost when disabled.

## Helper Function

`_to_romaji(text: str) -> str` in `server/routes/lyrics.py`:

- If `LYRICS_ROMAJI` is false → passthrough, returns `text` unchanged
- If line contains kana → convert with pykakasi, return space-joined romaji tokens
- Otherwise → return `text` unchanged (English lines, `♪`, empty strings pass through)

## Integration Point

Called on `prev`, `curr`, and `next_text` inside `spotify_lyrics_frame`, just before they are passed to `composite_lyrics`. No changes to `_lyrics_cache`, `_parse_lrc`, or `album_art.py`.

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `LYRICS_ROMAJI` | `false` | Set to `true` to enable Japanese → romaji conversion |

## Dependencies

Add `pykakasi` to `server/pyproject.toml` dependencies.

## Font

No change — `NotoSansCJK-Medium.ttc` renders Latin characters fine.
