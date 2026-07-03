"""Match video files to their setlist .txt files by the embedded video ID
(e.g. "...[ZQtzTPBRSiM].mkv" <-> "...[ZQtzTPBRSiM].txt") rather than requiring
the whole filename to match. Falls back to same-stem matching for files with
no bracketed ID, so a plain "video.mp4" / "video.txt" pair still works.

Standalone debug:
    python file_matching.py path\\to\\videos_dir path\\to\\txt_dir
"""
import re
from pathlib import Path

VIDEO_ID_RE = re.compile(r'\[([A-Za-z0-9_-]{6,15})\]\s*$')
DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})')


def extract_video_id(stem: str):
    m = VIDEO_ID_RE.search(stem)
    return m.group(1) if m else None


def extract_date(stem: str):
    """Pull the leading "YYYY-MM-DD" off a video filename, if present."""
    m = DATE_RE.match(stem)
    return m.group(1) if m else None


def build_txt_index(txt_dir: Path):
    """Map video-id -> txt Path for every .txt file under txt_dir (recursive)."""
    index = {}
    for txt_path in txt_dir.rglob("*.txt"):
        vid = extract_video_id(txt_path.stem)
        if vid:
            index[vid] = txt_path
    return index


def find_txt_for_video(video_path: Path, txt_dir: Path, txt_index: dict):
    vid = extract_video_id(video_path.stem)
    if vid and vid in txt_index:
        return txt_index[vid]

    # fall back to same-stem-different-directory matching (no bracketed ID present)
    candidate = txt_dir / (video_path.stem + ".txt")
    return candidate if candidate.exists() else None


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("usage: python file_matching.py path\\to\\videos_dir path\\to\\txt_dir")
        raise SystemExit(1)

    videos_dir = Path(sys.argv[1])
    txt_dir = Path(sys.argv[2])
    index = build_txt_index(txt_dir)
    print(f"indexed {len(index)} setlist file(s) by video ID")

    from video_tools import VIDEO_EXTENSIONS

    videos = sorted(p for p in videos_dir.rglob("*") if p.suffix.lower() in VIDEO_EXTENSIONS)
    matched = 0
    for video_path in videos:
        txt_path = find_txt_for_video(video_path, txt_dir, index)
        status = txt_path.name if txt_path else "NO MATCH"
        if txt_path:
            matched += 1
        print(f"  {video_path.name}  ->  {status}")
    print(f"{matched}/{len(videos)} videos matched")
