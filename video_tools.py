"""ffmpeg/ffprobe helpers: probing video duration and cutting segments.

Standalone debug:
    python video_tools.py path\\to\\video.mp4
"""
import json
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".ts", ".flv", ".mov", ".webm", ".avi", ".m2ts"}


def probe_duration(video_path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(video_path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def cut_segment(video_path: Path, start: float, end: float, out_path: Path,
                 reencode: bool, dry_run: bool):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", str(video_path), "-t", str(end - start)]
    cmd += ["-c:v", "libx264", "-c:a", "aac"] if reencode else ["-c", "copy"]
    cmd += [str(out_path)]
    if dry_run:
        print("    DRY RUN:", " ".join(cmd))
        return
    subprocess.run(cmd, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python video_tools.py path\\to\\video.mp4")
        raise SystemExit(1)

    print(f"duration: {probe_duration(Path(sys.argv[1])):.1f}s")
