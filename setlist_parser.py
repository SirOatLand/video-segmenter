"""Parse timestamped setlist .txt files (saved top comments) into song entries.

Standalone debug:
    python setlist_parser.py path\\to\\setlist.txt
"""
import re
from pathlib import Path

INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')

TS = r'(?:\d{1,2}:)?\d{1,2}:\d{2}'

# Setlist comments are hand-written by different fans with different habits.
# Try patterns from most to least specific; first match wins.

# "05:02 - 08:55   紅蓮華/ LiSA"  (explicit start-end range, gives the real
# performed duration directly instead of needing next-song-start or iTunes)
RANGE_WITH_ARTIST = re.compile(
    rf'^\s*(?P<ts>{TS})\s*-\s*(?P<end_ts>{TS})\s*(?P<title>.+?)\s*[／/]\s*(?P<artist>.+?)\s*$'
)

# "【00:02:20】「ミックスナッツ」Official髭男dism" (bracketed chapter markers;
# non-song entries like "【00:09:19】トーク" have no 「quoted title」 so they
# never match this pattern and are skipped automatically, no denylist needed)
BRACKET_QUOTED = re.compile(
    r'^\s*【(?P<ts>' + TS + r')】\s*「(?P<title>.+?)」\s*(?P<artist>.+?)\s*$'
)

# "2:28　StaRt ／ Mrs.GREEN APPLE" (full- or half-width slash before the artist)
TS_SLASH_ARTIST = re.compile(
    rf'^\s*(?P<ts>{TS})\s*(?P<title>.+?)\s*[／/]\s*(?P<artist>.+?)\s*$'
)

# "8:39 together - あきよしふみえ" (ASCII hyphen before the artist)
TS_HYPHEN_ARTIST = re.compile(
    rf'^\s*(?P<ts>{TS})\s*(?P<title>.+?)\s*-\s*(?P<artist>.+?)\s*$'
)

# "1:18　春泥棒" (no artist given, e.g. theme-locked "縛り" streams) or
# "09:47　1曲目：心の話" (numbered original songs, prefix stripped after match)
TS_TITLE_ONLY = re.compile(
    rf'^\s*(?P<ts>{TS})\s*(?P<title>.+?)\s*$'
)

SONG_NUMBER_PREFIX = re.compile(r'^\d+曲目[:：]\s*')

LINE_PATTERNS = (RANGE_WITH_ARTIST, BRACKET_QUOTED, TS_SLASH_ARTIST, TS_HYPHEN_ARTIST, TS_TITLE_ONLY)

TEXT_ENCODINGS = ("utf-8-sig", "utf-16", "cp932")


def read_text_robust(path: Path) -> str:
    last_error = None
    for encoding in TEXT_ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeError as e:
            last_error = e
    raise last_error


def parse_timestamp(ts: str) -> int:
    seconds = 0
    for part in ts.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def sanitize(name: str) -> str:
    name = INVALID_FILENAME_CHARS.sub("", name)
    return name.strip().rstrip(".")


def parse_line(line: str):
    """Try each known setlist line format in turn. Returns an entry dict or None."""
    for pattern in LINE_PATTERNS:
        m = pattern.match(line)
        if not m:
            continue
        title = SONG_NUMBER_PREFIX.sub("", m.group("title").strip())
        if not title:
            continue
        groups = m.groupdict()
        return {
            "start": parse_timestamp(m.group("ts")),
            "end": parse_timestamp(groups["end_ts"]) if groups.get("end_ts") else None,
            "title": title,
            "artist": groups.get("artist", "").strip(),
        }
    return None


def parse_setlist(txt_path: Path):
    entries = []
    for line in read_text_robust(txt_path).splitlines():
        entry = parse_line(line)
        if entry:
            entries.append(entry)
    entries.sort(key=lambda e: e["start"])
    return entries


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python setlist_parser.py path\\to\\setlist.txt")
        raise SystemExit(1)

    for entry in parse_setlist(Path(sys.argv[1])):
        print(entry)
