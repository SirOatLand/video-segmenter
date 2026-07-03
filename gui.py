#!/usr/bin/env python3
"""Desktop GUI: list videos/setlists instantly, check the streams (and songs)
you want, then scan (parse + ffprobe + iTunes lookup) and process (cut) only
what's checked. Built on tkinter (stdlib, no extra installs).

Usage:
    python gui.py
"""
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from setlist_parser import parse_setlist, parse_timestamp, sanitize
from duration_lookup import load_duration_cache, save_duration_cache
from video_tools import VIDEO_EXTENSIONS, probe_duration, cut_segment
from segment_planner import plan_segments, build_output_filename
from file_matching import build_txt_index, find_txt_for_video, extract_video_id
from completion_tracker import load_completed, save_completed, is_completed, mark_completed

# song table columns = ("include", "index", "title", "artist", "start", "end", "note")
SONG_INCLUDE_COLUMN = "#1"
EDITABLE_COLUMNS = {"#3": "title", "#5": "start", "#6": "end"}
# stream list columns = ("include", "name")
STREAM_INCLUDE_COLUMN = "#1"
CHECK_ON, CHECK_OFF = "☑", "☐"


def format_timestamp(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class App:
    def __init__(self, root):
        self.root = root
        root.title("Song Segmenter")
        root.geometry("950x650")

        self.duration_cache = load_duration_cache()
        self.completed = load_completed()
        self.txt_dir = None
        self.txt_index = {}
        self.videos_data = {}    # str(video_path) -> {"txt", "duration", "segments"}, only once scanned
        self.video_checked = {}  # str(video_path) -> bool, checkbox state in the stream list
        self.selected_video = None  # currently-viewed stream (shown in the song table)
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()

        self._build_widgets()
        self.root.after(100, self._poll_log_queue)

    # ---------------- widgets ----------------

    def _build_widgets(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        self.videos_dir_var = tk.StringVar()
        self.txt_dir_var = tk.StringVar()
        self.output_dir_var = tk.StringVar()
        self._dir_row(top, "Videos dir:", self.videos_dir_var, 0)
        self._dir_row(top, "Setlists dir:", self.txt_dir_var, 1)
        self._dir_row(top, "Output dir:", self.output_dir_var, 2)

        opts = ttk.Frame(self.root, padding=(8, 0))
        opts.pack(fill="x")
        self.reencode_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.overwrite_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Re-encode (frame-accurate)", variable=self.reencode_var).pack(side="left")
        ttk.Checkbutton(opts, text="Dry run", variable=self.dry_run_var).pack(side="left", padx=8)
        ttk.Checkbutton(opts, text="Overwrite existing", variable=self.overwrite_var).pack(side="left", padx=8)

        btns = ttk.Frame(self.root, padding=8)
        btns.pack(fill="x")
        self.scan_btn = ttk.Button(btns, text="Scan Checked Streams", command=self.scan_checked)
        self.scan_btn.pack(side="left")
        self.process_btn = ttk.Button(btns, text="Process Checked Streams", command=self.process_checked)
        self.process_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(btns, text="Check All Streams",
                   command=lambda: self.set_all_streams_checked(True)).pack(side="left", padx=(16, 0))
        ttk.Button(btns, text="Uncheck All Streams",
                   command=lambda: self.set_all_streams_checked(False)).pack(side="left", padx=4)
        ttk.Button(btns, text="Check All Songs",
                   command=lambda: self.set_all_songs_selected(True)).pack(side="left", padx=(16, 0))
        ttk.Button(btns, text="Uncheck All Songs",
                   command=lambda: self.set_all_songs_selected(False)).pack(side="left", padx=4)

        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=8)

        video_columns = ("include", "name")
        self.video_tree = ttk.Treeview(main, columns=video_columns, show="headings")
        self.video_tree.heading("include", text="")
        self.video_tree.heading("name", text="Stream")
        self.video_tree.column("include", width=30, anchor="center")
        self.video_tree.column("name", width=280, anchor="w")
        self.video_tree.pack(side="left", fill="y")
        self.video_tree.bind("<Button-1>", self.on_video_click)
        self.video_tree.bind("<<TreeviewSelect>>", self.on_select_video)

        table_frame = ttk.Frame(main)
        table_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))

        columns = ("include", "index", "title", "artist", "start", "end", "note")
        widths = (30, 40, 220, 140, 70, 70, 260)
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        for col, width in zip(columns, widths):
            self.tree.heading(col, text="" if col == "include" else col.capitalize())
            self.tree.column(col, width=width, anchor="center" if col == "include" else "w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Button-1>", self.on_song_click)

        bottom = ttk.Frame(self.root, padding=8)
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x")

        self.log_text = tk.Text(self.root, height=10, state="disabled")
        self.log_text.pack(fill="both", padx=8, pady=(0, 8))

    def _dir_row(self, parent, label, var, row):
        ttk.Label(parent, text=label, width=12).grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=var, width=70).grid(row=row, column=1, sticky="we", padx=4)
        ttk.Button(parent, text="Browse", command=lambda: self._browse(var)).grid(row=row, column=2)
        parent.columnconfigure(1, weight=1)

    def _browse(self, var):
        path = filedialog.askdirectory()
        if not path:
            return
        var.set(path)
        videos_dir_str = self.videos_dir_var.get()
        txt_dir_str = self.txt_dir_var.get()
        if videos_dir_str and txt_dir_str and Path(videos_dir_str).is_dir() and Path(txt_dir_str).is_dir():
            self.list_files()

    def log(self, msg):
        self.log_queue.put(msg)

    def _poll_log_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(100, self._poll_log_queue)

    # ---------------- lightweight file listing (no ffprobe, no parsing, no network) ----------------

    def list_files(self):
        videos_dir_str = self.videos_dir_var.get()
        txt_dir_str = self.txt_dir_var.get()
        if not videos_dir_str or not txt_dir_str:
            messagebox.showerror("Error", "Pick both a videos directory and a setlists directory first.")
            return

        videos_dir = Path(videos_dir_str)
        txt_dir = Path(txt_dir_str)
        if not videos_dir.is_dir() or not txt_dir.is_dir():
            messagebox.showerror("Error", "Pick valid videos and setlists directories first.")
            return

        videos = sorted(p for p in videos_dir.rglob("*") if p.suffix.lower() in VIDEO_EXTENSIONS)
        if not videos:
            self.log(f"No video files found in {videos_dir}")
            return

        self.txt_dir = txt_dir
        self.txt_index = build_txt_index(txt_dir)
        self.videos_data.clear()
        self.video_checked.clear()
        self.selected_video = None
        self.video_tree.delete(*self.video_tree.get_children())
        self.tree.delete(*self.tree.get_children())

        for video_path in videos:
            video_key = str(video_path)
            txt_path = find_txt_for_video(video_path, txt_dir, self.txt_index)
            label = "setlist found" if txt_path else "no setlist"
            self.video_checked[video_key] = False
            self.video_tree.insert("", "end", iid=video_key, values=(
                CHECK_OFF, f"{video_path.name}  ({label})",
            ))

        self.log(f"Listed {len(videos)} video(s). Check the ones you want, then click "
                 f"\"Scan Checked Streams\" to parse them and look up song durations.")

    def on_select_video(self, event):
        selection = self.video_tree.selection()
        if not selection:
            return
        self.selected_video = selection[0]
        self.tree.delete(*self.tree.get_children())
        if self.selected_video not in self.videos_data:
            return
        for seg in self.videos_data[self.selected_video]["segments"]:
            check = CHECK_ON if seg.get("selected", True) else CHECK_OFF
            self.tree.insert("", "end", iid=str(seg["index"]), values=(
                check, seg["index"], seg["title"], seg["artist"],
                format_timestamp(seg["start"]), format_timestamp(seg["end"]), seg["note"],
            ))

    def on_video_click(self, event):
        region = self.video_tree.identify("region", event.x, event.y)
        if region != "cell" or self.video_tree.identify_column(event.x) != STREAM_INCLUDE_COLUMN:
            return
        row_id = self.video_tree.identify_row(event.y)
        if not row_id:
            return
        self.video_checked[row_id] = not self.video_checked.get(row_id, False)
        self.video_tree.set(row_id, "include", CHECK_ON if self.video_checked[row_id] else CHECK_OFF)

    def set_all_streams_checked(self, checked: bool):
        check = CHECK_ON if checked else CHECK_OFF
        for video_key in self.video_checked:
            self.video_checked[video_key] = checked
            self.video_tree.set(video_key, "include", check)

    # ---------------- scanning checked streams (parse + ffprobe + iTunes, cached) ----------------

    def scan_checked(self):
        checked = [k for k, v in self.video_checked.items() if v]
        if not checked:
            messagebox.showinfo("No streams checked", "Check at least one stream in the list on the left first.")
            return

        self.scan_btn.configure(state="disabled")
        self.progress.configure(maximum=len(checked), value=0)
        self.log(f"Scanning {len(checked)} checked stream(s)...")
        threading.Thread(target=self._scan_checked_worker, args=(checked,), daemon=True).start()

    def _scan_checked_worker(self, checked_keys):
        try:
            for i, video_key in enumerate(checked_keys, start=1):
                video_path = Path(video_key)
                self.log(f"  [{i}/{len(checked_keys)}] {video_path.name}")
                self.root.after(0, self._set_progress, i)
                try:
                    txt_path = find_txt_for_video(video_path, self.txt_dir, self.txt_index)
                    if not txt_path:
                        self.log("    no matching setlist found, skipping")
                        continue
                    entries = parse_setlist(txt_path)
                    if not entries:
                        self.log("    no setlist entries parsed, skipping")
                        continue
                    duration = probe_duration(video_path)
                    segments = plan_segments(entries, duration, True, self.duration_cache)
                    video_id = extract_video_id(video_path.stem)
                    for seg in segments:
                        seg["selected"] = True
                        if is_completed(self.completed, video_id, seg["index"]):
                            seg["note"] = (seg["note"] + "; " if seg["note"] else "") + "already done"
                    self.videos_data[video_key] = {"txt": txt_path, "duration": duration, "segments": segments}
                    self.log(f"    found {len(segments)} song(s)")
                except Exception as e:
                    self.log(f"    error: {e}")
        finally:
            save_duration_cache(self.duration_cache)
            self.root.after(0, self._on_scan_checked_finished)

    def _on_scan_checked_finished(self):
        self.scan_btn.configure(state="normal")
        if self.selected_video in self.videos_data:
            self.tree.delete(*self.tree.get_children())
            for seg in self.videos_data[self.selected_video]["segments"]:
                check = CHECK_ON if seg.get("selected", True) else CHECK_OFF
                self.tree.insert("", "end", iid=str(seg["index"]), values=(
                    check, seg["index"], seg["title"], seg["artist"],
                    format_timestamp(seg["start"]), format_timestamp(seg["end"]), seg["note"],
                ))
        self.log("Finished scanning checked streams.")

    def set_all_songs_selected(self, selected: bool):
        if not self.selected_video or self.selected_video not in self.videos_data:
            return
        check = CHECK_ON if selected else CHECK_OFF
        for seg in self.videos_data[self.selected_video]["segments"]:
            seg["selected"] = selected
            self.tree.set(str(seg["index"]), "include", check)

    def on_song_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell" or self.tree.identify_column(event.x) != SONG_INCLUDE_COLUMN:
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id or not self.selected_video:
            return
        segments = self.videos_data[self.selected_video]["segments"]
        seg = next(s for s in segments if s["index"] == int(row_id))
        seg["selected"] = not seg.get("selected", True)
        self.tree.set(row_id, "include", CHECK_ON if seg["selected"] else CHECK_OFF)

    # ---------------- editing ----------------

    def on_double_click(self, event):
        if not self.selected_video:
            return
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not row_id or col_id not in EDITABLE_COLUMNS:
            return
        field = EDITABLE_COLUMNS[col_id]
        x, y, w, h = self.tree.bbox(row_id, col_id)
        value = self.tree.set(row_id, field)

        entry = ttk.Entry(self.tree)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, value)
        entry.focus()
        entry.select_range(0, "end")

        def commit(event=None):
            new_value = entry.get()
            entry.destroy()
            self._apply_edit(row_id, field, new_value)

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", lambda e: entry.destroy())

    def _apply_edit(self, row_id, field, new_value):
        segments = self.videos_data[self.selected_video]["segments"]
        seg = next(s for s in segments if s["index"] == int(row_id))
        new_value = new_value.strip()
        if field in ("start", "end"):
            try:
                seconds = parse_timestamp(new_value)
            except ValueError:
                messagebox.showerror("Invalid value", f"Could not parse '{new_value}' as mm:ss")
                return
            seg[field] = seconds
            self.tree.set(row_id, field, format_timestamp(seconds))
        else:
            seg[field] = new_value
            self.tree.set(row_id, field, new_value)

    # ---------------- processing (cutting) checked streams ----------------

    def process_checked(self):
        checked_keys = [k for k, v in self.video_checked.items() if v]
        if not checked_keys:
            messagebox.showinfo("Nothing to process", "Check at least one stream in the list on the left first.")
            return
        if not self.output_dir_var.get():
            messagebox.showerror("Error", "Pick an output directory first.")
            return

        unscanned = [k for k in checked_keys if k not in self.videos_data]
        for k in unscanned:
            self.log(f"{Path(k).name}: checked but not scanned yet, skipping "
                     f"(click \"Scan Checked Streams\" first)")

        targets = {k: self.videos_data[k] for k in checked_keys if k in self.videos_data}
        if not targets:
            messagebox.showinfo("Nothing to process", "None of the checked streams have been scanned yet.")
            return

        output_dir = Path(self.output_dir_var.get())
        self.stop_event.clear()
        self.process_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        total = sum(sum(1 for s in v["segments"] if s.get("selected", True)) for v in targets.values())
        self.progress.configure(maximum=max(total, 1), value=0)

        args = (targets, output_dir, self.reencode_var.get(), self.dry_run_var.get(), self.overwrite_var.get())
        threading.Thread(target=self._run_worker, args=args, daemon=True).start()

    def stop(self):
        self.stop_event.set()
        self.log("Stop requested, finishing current segment...")

    def _run_worker(self, videos_to_process, output_dir, reencode, dry_run, overwrite):
        done = 0
        for video_key, data in videos_to_process.items():
            if self.stop_event.is_set():
                break
            video_path = Path(video_key)
            out_subdir = output_dir / sanitize(video_path.stem)
            video_id = extract_video_id(video_path.stem)
            selected_segments = [s for s in data["segments"] if s.get("selected", True)]
            if not selected_segments:
                continue
            self.log(video_path.name)

            for seg in selected_segments:
                if self.stop_event.is_set():
                    break
                idx = seg["index"]

                if is_completed(self.completed, video_id, idx):
                    self.log(f"  [{idx:02d}] '{seg['title']}' already completed (previous run), skipping")
                    done += 1
                    self.root.after(0, self._set_progress, done)
                    continue

                if seg["end"] <= seg["start"]:
                    self.log(f"  [{idx:02d}] skipping '{seg['title']}': non-positive duration")
                    done += 1
                    self.root.after(0, self._set_progress, done)
                    continue

                filename = build_output_filename(video_path, seg)
                out_path = out_subdir / filename

                if out_path.exists() and not overwrite:
                    self.log(f"  [{idx:02d}] {filename} already exists, skipping")
                    if not dry_run:
                        mark_completed(self.completed, video_id, idx)
                        save_completed(self.completed)
                    done += 1
                    self.root.after(0, self._set_progress, done)
                    continue

                self.log(f"  [{idx:02d}] {seg['title']} / {seg['artist']}  ({seg['start']}s - {seg['end']}s)")
                try:
                    cut_segment(video_path, seg["start"], seg["end"], out_path, reencode, dry_run)
                    if not dry_run:
                        mark_completed(self.completed, video_id, idx)
                        save_completed(self.completed)
                except subprocess.CalledProcessError as e:
                    self.log(f"  ffmpeg failed: {e.stderr}")

                done += 1
                self.root.after(0, self._set_progress, done)

        self.log("Stopped." if self.stop_event.is_set() else "Done.")
        self.root.after(0, self._on_run_finished)

    def _set_progress(self, value):
        self.progress.configure(value=value)

    def _on_run_finished(self):
        self.process_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
