"""ffmpeg/ffprobe helpers: probing video duration and cutting segments.

Standalone debug:
    python video_tools.py path\\to\\video.mp4
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".ts", ".flv", ".mov", ".webm", ".avi", ".m2ts"}

FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"


def _find_ffmpeg_fallback():
    """If ffmpeg/ffprobe aren't resolvable on PATH (e.g. just installed via winget
    but this process's PATH predates it), search common Windows install locations
    instead of forcing the user to restart their terminal/IDE."""
    if sys.platform != "win32":
        return None

    search_roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        Path(os.environ.get("USERPROFILE", "")) / "scoop" / "apps" / "ffmpeg",
        Path("C:/ffmpeg"),
        Path("C:/Program Files/ffmpeg"),
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        for exe in root.rglob("ffprobe.exe"):
            return exe.parent
    return None


def _ensure_ffmpeg_available():
    global FFMPEG_BIN, FFPROBE_BIN
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return

    bin_dir = _find_ffmpeg_fallback()
    if bin_dir is None:
        raise RuntimeError(
            "ffmpeg/ffprobe not found on PATH and no fallback install was found. "
            "Install ffmpeg (e.g. `winget install --id=Gyan.FFmpeg`), then restart "
            "your terminal/IDE so the updated PATH takes effect."
        )
    FFMPEG_BIN = str(bin_dir / "ffmpeg.exe")
    FFPROBE_BIN = str(bin_dir / "ffprobe.exe")


_ensure_ffmpeg_available()


def probe_duration(video_path: Path) -> float:
    result = subprocess.run(
        [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(video_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def cut_segment(video_path: Path, start: float, end: float, out_path: Path,
                 reencode: bool, dry_run: bool):
    cmd = [FFMPEG_BIN, "-y", "-ss", str(start), "-i", str(video_path), "-t", str(end - start)]
    cmd += ["-c:v", "libx264", "-c:a", "aac"] if reencode else ["-c", "copy"]
    cmd += [str(out_path)]
    if dry_run:
        print("    DRY RUN:", " ".join(cmd))
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python video_tools.py path\\to\\video.mp4")
        raise SystemExit(1)

    print(f"duration: {probe_duration(Path(sys.argv[1])):.1f}s")
