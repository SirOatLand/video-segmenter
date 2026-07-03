"""Persistent record of which songs have already been cut, keyed by video ID
and song index. Unlike checking whether the output file still exists, this
survives moving/renaming/deleting the cut clips afterward.

Standalone debug:
    python completion_tracker.py <video_id> <song_index>
"""
import json
from pathlib import Path

COMPLETION_PATH = Path(__file__).parent / ".completed_songs.json"


def _key(video_id: str, index: int) -> str:
    return f"{video_id}::{index}"


def load_completed() -> set:
    if COMPLETION_PATH.exists():
        try:
            return set(json.loads(COMPLETION_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, UnicodeError):
            pass
    return set()


def save_completed(completed: set):
    COMPLETION_PATH.write_text(json.dumps(sorted(completed), ensure_ascii=False, indent=2), encoding="utf-8")


def is_completed(completed: set, video_id, index: int) -> bool:
    return bool(video_id) and _key(video_id, index) in completed


def mark_completed(completed: set, video_id, index: int):
    if video_id:
        completed.add(_key(video_id, index))


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("usage: python completion_tracker.py <video_id> <song_index>")
        raise SystemExit(1)

    completed = load_completed()
    print(is_completed(completed, sys.argv[1], int(sys.argv[2])))
