"""Look up official song durations via the iTunes Search API, with on-disk caching.

Two things make real-world setlist titles hard to match directly:
  - transient network/parse errors look identical to a genuine "not found"
    unless distinguished, so a single flaky request can wrongly tag a real
    song as unmatched
  - messy titles with parenthetical qualifiers (cover/arrangement notes) can
    have the actual searchable title either inside or outside the parens,
    depending on how the fan who wrote the setlist phrased it

Standalone debug:
    python duration_lookup.py "Song Title" "Artist Name"
"""
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_CACHE_PATH = Path(__file__).parent / ".itunes_duration_cache.json"

PAREN_RE = re.compile(r'[\(（]([^\)）]*)[\)）]')


def load_duration_cache() -> dict:
    if ITUNES_CACHE_PATH.exists():
        try:
            return json.loads(ITUNES_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError):
            pass
    return {}


def save_duration_cache(cache: dict):
    ITUNES_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _title_variants(title: str):
    """Alternate search titles for messy titles with parenthetical qualifiers,
    e.g. "どんぐりこらこら(どんぐりころころ a cappella＋Arrange)" (the real title is
    inside the parens) vs "夜空ノムコウ(ほくろ女体化笑い我慢Ver.)" (it's outside) --
    try the original first, then with the parens removed, then just the inside."""
    variants = [title]

    without_parens = PAREN_RE.sub("", title).strip()
    if without_parens and without_parens not in variants:
        variants.append(without_parens)

    m = PAREN_RE.search(title)
    if m:
        inner = m.group(1).strip()
        if inner and inner not in variants:
            variants.append(inner)

    return variants


def _search_once(title: str, artist: str):
    """Single iTunes Search API request. Returns (seconds, transient_error) --
    seconds is None if no result was found OR the request failed; transient_error
    is True only for network/parsing failures (as opposed to a clean "not found"),
    which is what's worth retrying."""
    query = urllib.parse.urlencode({"term": f"{artist} {title}".strip(), "entity": "song", "limit": 1})
    url = f"{ITUNES_SEARCH_URL}?{query}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        results = data.get("results") or []
        if results and results[0].get("trackTimeMillis"):
            return results[0]["trackTimeMillis"] / 1000, False
        return None, False
    except (OSError, json.JSONDecodeError):
        return None, True


def _search_with_retries(title: str, artist: str, attempts: int = 3):
    for attempt in range(attempts):
        seconds, transient = _search_once(title, artist)
        time.sleep(0.2)  # stay polite to the unauthenticated API
        if seconds is not None:
            return seconds
        if not transient:
            return None
        if attempt < attempts - 1:
            time.sleep(0.5 * (attempt + 1))  # backoff before retrying a transient failure
    print(f"    itunes lookup kept failing (network/parse error) for '{title}' / '{artist}', giving up")
    return None


def lookup_itunes_duration(title: str, artist: str, cache: dict):
    """Return the official track length (seconds), or None if no match was found
    under any title variant. Results are cached on disk (keyed by the original
    title/artist) since the same song/artist pair recurs across many videos."""
    key = f"{artist}||{title}"
    if key in cache:
        return cache[key]

    seconds = None
    for variant in _title_variants(title):
        seconds = _search_with_retries(variant, artist)
        if seconds is not None:
            break

    cache[key] = seconds
    return seconds


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print('usage: python duration_lookup.py "Song Title" "Artist Name"')
        raise SystemExit(1)

    cache = load_duration_cache()
    result = lookup_itunes_duration(sys.argv[1], sys.argv[2], cache)
    save_duration_cache(cache)
    print(f"{result:.0f} seconds" if result else "no match")
