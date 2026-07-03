"""Decide each song's start/end cut points from parsed setlist entries.

Pure planning logic (no ffmpeg calls), shared by the CLI (segment_songs.py)
and the GUI's review/edit step (gui.py).

Standalone debug:
    python segment_planner.py path\\to\\setlist.txt <video_duration_seconds>
"""
from duration_lookup import lookup_itunes_duration


def plan_segments(entries, video_duration, use_itunes, duration_cache):
    """entries: list of {"start", "title", "artist"} sorted by start (from setlist_parser).
    Returns a list of dicts: {"index", "title", "artist", "start", "end", "note"}."""
    segments = []
    for idx, entry in enumerate(entries, start=1):
        start = entry["start"]
        next_start = entries[idx]["start"] if idx < len(entries) else video_duration
        end = next_start
        note = ""

        if use_itunes:
            official = lookup_itunes_duration(entry["title"], entry["artist"], duration_cache)
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
        })
    return segments


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
