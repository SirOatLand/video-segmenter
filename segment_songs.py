#!/usr/bin/env python3
"""Cut archived singing-stream videos into per-song clips using timestamped
setlist .txt files (e.g. saved top comments of the form "2:28  Song / Artist").

Usage:
    python segment_songs.py --videos-dir D:\\Flashdrive\\videos --txt-dir D:\\Flashdrive\\setlists --output-dir D:\\Songs --dry-run

Made of separately-debuggable pieces:
    setlist_parser.py   - parsing the setlist .txt files
    duration_lookup.py  - iTunes official-duration lookup + cache
    video_tools.py       - ffprobe/ffmpeg probing and cutting
    segment_planner.py  - decides each song's start/end cut points
    segment_songs.py    - this file: wires them together, CLI
    gui.py               - desktop GUI with a review/edit step before cutting
"""
import argparse
import subprocess
from pathlib import Path

from setlist_parser import parse_setlist, sanitize
from duration_lookup import load_duration_cache, save_duration_cache
from video_tools import VIDEO_EXTENSIONS, probe_duration, cut_segment
from segment_planner import plan_segments


def find_txt_for_video(video_path: Path, txt_dir: Path):
    candidate = txt_dir / (video_path.stem + ".txt")
    return candidate if candidate.exists() else None


def process_video(video_path: Path, txt_path: Path, output_dir: Path,
                   reencode: bool, dry_run: bool, overwrite: bool,
                   use_itunes: bool, duration_cache: dict):
    entries = parse_setlist(txt_path)
    if not entries:
        print(f"  no setlist entries found in {txt_path.name}, skipping")
        return

    duration = probe_duration(video_path)
    out_subdir = output_dir / sanitize(video_path.stem)
    segments = plan_segments(entries, duration, use_itunes, duration_cache)

    for seg in segments:
        idx = seg["index"]
        if seg["note"]:
            print(f"  [{idx:02d}] {seg['note']}")

        if seg["end"] <= seg["start"]:
            print(f"  [{idx:02d}] skipping '{seg['title']}': non-positive duration")
            continue

        filename = f"{idx:02d} - {sanitize(seg['title'])}{video_path.suffix}"
        out_path = out_subdir / filename

        if out_path.exists() and not overwrite:
            print(f"  [{idx:02d}] {filename} already exists, skipping")
            continue

        print(f"  [{idx:02d}] {seg['title']} / {seg['artist']}  ({seg['start']}s - {seg['end']}s)")
        cut_segment(video_path, seg["start"], seg["end"], out_path, reencode, dry_run)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--videos-dir", required=True, type=Path)
    parser.add_argument("--txt-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--reencode", action="store_true",
                         help="Re-encode instead of stream-copy (slower, frame-accurate cuts)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files")
    parser.add_argument("--dry-run", action="store_true", help="Print ffmpeg commands without running them")
    parser.add_argument("--duration-source", choices=["itunes", "next-song"], default="itunes",
                         help="'itunes' (default) trims each clip to the official track length "
                              "looked up via the iTunes Search API, capped at the next song's start. "
                              "'next-song' cuts from this song's timestamp to the next one's, as before.")
    args = parser.parse_args()

    videos = sorted(p for p in args.videos_dir.rglob("*") if p.suffix.lower() in VIDEO_EXTENSIONS)
    if not videos:
        print(f"No video files found in {args.videos_dir}")
        return

    use_itunes = args.duration_source == "itunes"
    duration_cache = load_duration_cache() if use_itunes else {}

    for video_path in videos:
        print(video_path.name)
        txt_path = find_txt_for_video(video_path, args.txt_dir)
        if not txt_path:
            print("  no matching .txt file found, skipping")
            continue
        try:
            process_video(video_path, txt_path, args.output_dir, args.reencode, args.dry_run,
                           args.overwrite, use_itunes, duration_cache)
        except subprocess.CalledProcessError as e:
            print(f"  ffmpeg/ffprobe failed: {e.stderr}")
        except Exception as e:
            print(f"  error: {e}")
        finally:
            if use_itunes:
                save_duration_cache(duration_cache)


if __name__ == "__main__":
    main()
