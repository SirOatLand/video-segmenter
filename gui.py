#!/usr/bin/env python3
"""Desktop GUI: scan videos/setlists, review & edit each song's cut points,
then run ffmpeg to cut them. Built on tkinter (stdlib, no extra installs).

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
from segment_planner import plan_segments

EDITABLE_COLUMNS = {"#2": "title", "#4": "start", "#5": "end"}


def format_timestamp(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def find_txt_for_video(video_path: Path, txt_dir: Path):
    candidate = txt_dir / (video_path.stem + ".txt")
    return candidate if candidate.exists() else None


class App:
    def __init__(self, root):
        self.root = root
        root.title("Song Segmenter")
        root.geometry("950x650")

        self.duration_cache = load_duration_cache()
        self.videos_data = {}   # str(video_path) -> {"txt", "duration", "segments"}
        self.video_order = []   # listbox row index -> str(video_path) or None
        self.selected_video = None
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
        self.use_itunes_var = tk.BooleanVar(value=True)
        self.reencode_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.overwrite_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Use iTunes duration lookup", variable=self.use_itunes_var).pack(side="left")
        ttk.Checkbutton(opts, text="Re-encode (frame-accurate)", variable=self.reencode_var).pack(side="left", padx=8)
        ttk.Checkbutton(opts, text="Dry run", variable=self.dry_run_var).pack(side="left", padx=8)
        ttk.Checkbutton(opts, text="Overwrite existing", variable=self.overwrite_var).pack(side="left", padx=8)

        btns = ttk.Frame(self.root, padding=8)
        btns.pack(fill="x")
        self.scan_btn = ttk.Button(btns, text="Scan", command=self.scan)
        self.scan_btn.pack(side="left")
        self.run_btn = ttk.Button(btns, text="Run", command=self.run)
        self.run_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")

        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=8)

        self.video_list = tk.Listbox(main, width=38)
        self.video_list.pack(side="left", fill="y")
        self.video_list.bind("<<ListboxSelect>>", self.on_select_video)

        table_frame = ttk.Frame(main)
        table_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))

        columns = ("index", "title", "artist", "start", "end", "note")
        widths = (40, 220, 140, 70, 70, 260)
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        for col, width in zip(columns, widths):
            self.tree.heading(col, text=col.capitalize())
            self.tree.column(col, width=width)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.on_double_click)

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
        if path:
            var.set(path)

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

    # ---------------- scanning ----------------

    def scan(self):
        videos_dir = Path(self.videos_dir_var.get())
        txt_dir = Path(self.txt_dir_var.get())
        if not videos_dir.is_dir() or not txt_dir.is_dir():
            messagebox.showerror("Error", "Pick valid videos and setlists directories first.")
            return

        videos = sorted(p for p in videos_dir.rglob("*") if p.suffix.lower() in VIDEO_EXTENSIONS)
        if not videos:
            self.log(f"No video files found in {videos_dir}")
            return

        self.scan_btn.configure(state="disabled")
        use_itunes = self.use_itunes_var.get()
        threading.Thread(target=self._scan_worker, args=(videos, txt_dir, use_itunes), daemon=True).start()

    def _scan_worker(self, videos, txt_dir, use_itunes):
        results = []  # (video_path, txt_path, duration, segments, label)
        for video_path in videos:
            txt_path = find_txt_for_video(video_path, txt_dir)
            if not txt_path:
                results.append((video_path, None, None, None, "no setlist"))
                continue
            entries = parse_setlist(txt_path)
            if not entries:
                results.append((video_path, txt_path, None, None, "empty setlist"))
                continue
            try:
                duration = probe_duration(video_path)
            except subprocess.CalledProcessError as e:
                self.log(f"ffprobe failed for {video_path.name}: {e.stderr}")
                results.append((video_path, txt_path, None, None, "ffprobe failed"))
                continue
            segments = plan_segments(entries, duration, use_itunes, self.duration_cache)
            results.append((video_path, txt_path, duration, segments, f"{len(segments)} songs"))

        save_duration_cache(self.duration_cache)
        self.root.after(0, self._populate_scan_results, results)

    def _populate_scan_results(self, results):
        self.videos_data.clear()
        self.video_order = []
        self.video_list.delete(0, "end")
        self.tree.delete(*self.tree.get_children())

        for video_path, txt_path, duration, segments, label in results:
            self.video_list.insert("end", f"{video_path.name}  ({label})")
            if segments is not None:
                self.videos_data[str(video_path)] = {"txt": txt_path, "duration": duration, "segments": segments}
                self.video_order.append(str(video_path))
            else:
                self.video_order.append(None)

        self.scan_btn.configure(state="normal")
        self.log(f"Scanned {len(results)} video(s).")

    def on_select_video(self, event):
        selection = self.video_list.curselection()
        if not selection:
            return
        video_key = self.video_order[selection[0]]
        self.tree.delete(*self.tree.get_children())
        self.selected_video = video_key
        if not video_key:
            return
        for seg in self.videos_data[video_key]["segments"]:
            self.tree.insert("", "end", iid=str(seg["index"]), values=(
                seg["index"], seg["title"], seg["artist"],
                format_timestamp(seg["start"]), format_timestamp(seg["end"]), seg["note"],
            ))

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

    # ---------------- running ----------------

    def run(self):
        if not self.videos_data:
            messagebox.showinfo("Nothing to run", "Scan a folder first.")
            return
        if not self.output_dir_var.get():
            messagebox.showerror("Error", "Pick an output directory first.")
            return

        output_dir = Path(self.output_dir_var.get())
        self.stop_event.clear()
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        total = sum(len(v["segments"]) for v in self.videos_data.values())
        self.progress.configure(maximum=max(total, 1), value=0)

        args = (output_dir, self.reencode_var.get(), self.dry_run_var.get(), self.overwrite_var.get())
        threading.Thread(target=self._run_worker, args=args, daemon=True).start()

    def stop(self):
        self.stop_event.set()
        self.log("Stop requested, finishing current segment...")

    def _run_worker(self, output_dir, reencode, dry_run, overwrite):
        done = 0
        for video_key, data in self.videos_data.items():
            if self.stop_event.is_set():
                break
            video_path = Path(video_key)
            out_subdir = output_dir / sanitize(video_path.stem)
            self.log(video_path.name)

            for seg in data["segments"]:
                if self.stop_event.is_set():
                    break
                idx = seg["index"]

                if seg["end"] <= seg["start"]:
                    self.log(f"  [{idx:02d}] skipping '{seg['title']}': non-positive duration")
                    done += 1
                    self.root.after(0, self._set_progress, done)
                    continue

                filename = f"{idx:02d} - {sanitize(seg['title'])}{video_path.suffix}"
                out_path = out_subdir / filename

                if out_path.exists() and not overwrite:
                    self.log(f"  [{idx:02d}] {filename} already exists, skipping")
                    done += 1
                    self.root.after(0, self._set_progress, done)
                    continue

                self.log(f"  [{idx:02d}] {seg['title']} / {seg['artist']}  ({seg['start']}s - {seg['end']}s)")
                try:
                    cut_segment(video_path, seg["start"], seg["end"], out_path, reencode, dry_run)
                except subprocess.CalledProcessError as e:
                    self.log(f"  ffmpeg failed: {e.stderr}")

                done += 1
                self.root.after(0, self._set_progress, done)

        self.log("Stopped." if self.stop_event.is_set() else "Done.")
        self.root.after(0, self._on_run_finished)

    def _set_progress(self, value):
        self.progress.configure(value=value)

    def _on_run_finished(self):
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
