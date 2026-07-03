"""Decide each song's start/end cut points from parsed setlist entries, and
build each clip's output filename.

Pure planning logic (no ffmpeg calls), shared by the CLI (segment_songs.py)
and the GUI's review step (gui.py). Explicit end times from the setlist
(format E, start-end ranges) always win; otherwise, with use_itunes=True,
the official track length is looked up and capped at the next song's start.

Standalone debug:
    python segment_planner.py path\\to\\setlist.txt <video_duration_seconds>
"""
from duration_lookup import lookup_itunes_duration
from file_matching import extract_date
from setlist_parser import sanitize

# Windows forbids a literal "|" in filenames, so the separator uses the
# full-width lookalike "｜" (U+FF5C) -- consistent with how this archive's own
# filenames already substitute full-width characters for forbidden ASCII ones
# (e.g. "⧸" for "/").
FILENAME_SEPARATOR = "｜"


def plan_segments(entries, video_duration, use_itunes, duration_cache):
    """entries: list of {"start", "title", "artist", "end"} sorted by start (from setlist_parser).
    Returns a list of dicts: {"index", "title", "artist", "start", "end", "note", "itunes_matched"}.
    itunes_matched is True/False if a lookup was attempted, or None if it wasn't
    (use_itunes=False, or the setlist already gave an explicit end time)."""
    segments = []
    for idx, entry in enumerate(entries, start=1):
        start = entry["start"]
        next_start = entries[idx]["start"] if idx < len(entries) else video_duration
        end = next_start
        note = ""
        itunes_matched = None

        if entry.get("end") is not None:
            end = entry["end"]
            note = "explicit end time from setlist"
        elif use_itunes:
            official = lookup_itunes_duration(entry["title"], entry["artist"], duration_cache)
            itunes_matched = official is not None
            if official:
                proposed_end = start + official
                if proposed_end > next_start:
                    end = next_start
                    note = f"official {official:.0f}s overruns next song, capped"
                else:
                    end = proposed_end
                    note = f"official duration {official:.0f}s"
            else:
                note = "no iTunes match, using next song's start"

        segments.append({
            "index": idx,
            "title": entry["title"],
            "artist": entry["artist"],
            "start": start,
            "end": end,
            "note": note,
            "itunes_matched": itunes_matched,
        })
    return segments


def build_output_filename(video_path, seg):
    """Builds "[date] Title ｜ Artist.ext", appending " (NO MATCH)" before the
    extension if no iTunes match was found for this song."""
    date = extract_date(video_path.stem) or sanitize(video_path.stem)
    name = f"[{date}] {sanitize(seg['title'])}"
    if seg["artist"]:
        name += f" {FILENAME_SEPARATOR} {sanitize(seg['artist'])}"
    if seg["itunes_matched"] is False:
        name += " (NO MATCH)"
    return name + video_path.suffix


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from duration_lookup import load_duration_cache, save_duration_cache
    from setlist_parser import parse_setlist

    if len(sys.argv) != 3:
        print("usage: python segment_planner.py path\\to\\setlist.txt <video_duration_seconds>")
        raise SystemExit(1)

    entries = parse_setlist(Path(sys.argv[1]))
    cache = load_duration_cache()
    for seg in plan_segments(entries, float(sys.argv[2]), True, cache):
        print(seg)
    save_duration_cache(cache)
