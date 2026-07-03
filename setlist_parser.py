"""Parse timestamped setlist .txt files (saved top comments) into song entries.

Standalone debug:
    python setlist_parser.py path\\to\\setlist.txt
"""
import re
from pathlib import Path

INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')

# Matches lines like: "2:28　StaRt ／ Mrs.GREEN APPLE" (mm:ss or h:mm:ss,
# full-width space before the title, full-width slash "／" before the artist).
TIMESTAMP_LINE = re.compile(
    r'^\s*(?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})\s+(?P<title>.+?)\s*／\s*(?P<artist>.+?)\s*$'
)

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


def parse_setlist(txt_path: Path):
    entries = []
    for line in read_text_robust(txt_path).splitlines():
        m = TIMESTAMP_LINE.match(line)
        if not m:
            continue
        entries.append({
            "start": parse_timestamp(m.group("ts")),
            "title": m.group("title").strip(),
            "artist": m.group("artist").strip(),
        })
    entries.sort(key=lambda e: e["start"])
    return entries


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python setlist_parser.py path\\to\\setlist.txt")
        raise SystemExit(1)

    for entry in parse_setlist(Path(sys.argv[1])):
        print(entry)
