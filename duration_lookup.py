"""Look up official song durations via the iTunes Search API, with on-disk caching.

Standalone debug:
    python duration_lookup.py "Song Title" "Artist Name"
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_CACHE_PATH = Path(__file__).parent / ".itunes_duration_cache.json"


def load_duration_cache() -> dict:
    if ITUNES_CACHE_PATH.exists():
        try:
            return json.loads(ITUNES_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError):
            pass
    return {}


def save_duration_cache(cache: dict):
    ITUNES_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def lookup_itunes_duration(title: str, artist: str, cache: dict):
    """Return the official track length (seconds), or None if no match / request failed.
    Results are cached on disk since the same song/artist pair recurs across many videos."""
    key = f"{artist}||{title}"
    if key in cache:
        return cache[key]

    query = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 1})
    url = f"{ITUNES_SEARCH_URL}?{query}"
    seconds = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        results = data.get("results") or []
        if results and results[0].get("trackTimeMillis"):
            seconds = results[0]["trackTimeMillis"] / 1000
    except (OSError, json.JSONDecodeError) as e:
        print(f"    itunes lookup failed for '{title}' / '{artist}': {e}")
    time.sleep(0.2)  # stay polite to the unauthenticated API

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
