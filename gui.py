"""Standalone macOS GUI for flac2aac.

Bundle with PyInstaller using flac2aac_gui.spec.
"""

import logging
import multiprocessing
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from typing import Optional

from config import (
    Config,
    ConfigError,
    CoverFileConfig,
    EncodingConfig,
    LoudnessConfig,
    MetadataConfig,
    PathsConfig,
    ProcessingConfig,
)
from pipeline import Pipeline, ProcessingStats

_APP_TITLE = "flac2aac"
_WIN_WIDTH = 720
_WIN_HEIGHT = 640


def _bundled_ffmpeg() -> str:
    """Return path to bundled FFmpeg, falling back to system 'ffmpeg'."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = Path(base) / "ffmpeg"
        if candidate.exists():
            return str(candidate)
    return "ffmpeg"


class _QueueLogHandler(logging.Handler):
    """Forwards log records to a queue for the UI to consume."""

    def __init__(self, q: queue.Queue) -> None:
        super().__init__()
        self._queue = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
            self._queue.put({"type": "log", "text": text})
        except Exception:
            self.handleError(record)


class _ConversionWorker(threading.Thread):
    """Runs the pipeline on a background thread."""

    def __init__(
        self,
        config: Config,
        q: queue.Queue,
        cancel_event: threading.Event,
        dry_run: bool = False,
    ) -> None:
        super().__init__(daemon=True)
        self._config = config
        self._queue = q
        self._cancel_event = cancel_event
        self._dry_run = dry_run

    def run(self) -> None:
        root_logger = logging.getLogger()
        handler = _QueueLogHandler(self._queue)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%H:%M:%S"))
        root_logger.addHandler(handler)
        try:
            pipeline = Pipeline(
                self._config,
                dry_run=self._dry_run,
                cancel_event=self._cancel_event,
            )
            stats = pipeline.run()
            self._queue.put({"type": "done", "stats": stats})
        except Exception as exc:
            self._queue.put({"type": "error", "msg": str(exc)})
        finally:
            root_logger.removeHandler(handler)


class App(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title(_APP_TITLE)
        self.resizable(True, True)
        self.minsize(_WIN_WIDTH, _WIN_HEIGHT)

        self._queue: queue.Queue = queue.Queue()
        self._cancel_event: threading.Event = threading.Event()
        self._worker: Optional[_ConversionWorker] = None
        self._total_files = 0
        self._processed_files = 0

        self._build_ui()
        self._set_running(False)
        self.after(100, self._poll_queue)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}

        # ── Folders ──────────────────────────────────────────────────
        folder_frame = ttk.LabelFrame(self, text="Folders", padding=6)
        folder_frame.pack(fill="x", **pad)
        folder_frame.columnconfigure(1, weight=1)

        ttk.Label(folder_frame, text="Input:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._input_var = tk.StringVar()
        ttk.Entry(folder_frame, textvariable=self._input_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(folder_frame, text="Browse…", command=self._browse_input).grid(row=0, column=2, padx=(6, 0))

        ttk.Label(folder_frame, text="Output:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
        self._output_var = tk.StringVar()
        ttk.Entry(folder_frame, textvariable=self._output_var).grid(row=1, column=1, sticky="ew", pady=(4, 0))
        ttk.Button(folder_frame, text="Browse…", command=self._browse_output).grid(row=1, column=2, padx=(6, 0), pady=(4, 0))

        ttk.Label(folder_frame, text="RAM disk:").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
        self._workdir_var = tk.StringVar()
        ttk.Entry(folder_frame, textvariable=self._workdir_var).grid(row=2, column=1, sticky="ew", pady=(4, 0))
        ttk.Button(folder_frame, text="Browse…", command=self._browse_workdir).grid(row=2, column=2, padx=(6, 0), pady=(4, 0))

        rd_ctrl = ttk.Frame(folder_frame)
        rd_ctrl.grid(row=3, column=1, sticky="w", pady=(2, 0))
        ttk.Label(rd_ctrl, text="Size:").pack(side="left")
        self._ramdisk_size_var = tk.IntVar(value=1024)
        ttk.Spinbox(rd_ctrl, from_=256, to=8192, increment=256,
                    textvariable=self._ramdisk_size_var, width=6).pack(side="left", padx=(4, 2))
        ttk.Label(rd_ctrl, text="MB").pack(side="left")
        self._create_rd_btn = ttk.Button(rd_ctrl, text="Create", command=self._create_ramdisk, width=8)
        self._create_rd_btn.pack(side="left", padx=(12, 4))
        self._eject_rd_btn = ttk.Button(rd_ctrl, text="Eject", command=self._eject_ramdisk, width=8)
        self._eject_rd_btn.pack(side="left")
        ttk.Label(folder_frame, text="Optional — encode here first, then move to output", foreground="gray").grid(
            row=4, column=1, sticky="w")

        # ── Encoding ─────────────────────────────────────────────────
        enc_frame = ttk.LabelFrame(self, text="Encoding", padding=6)
        enc_frame.pack(fill="x", **pad)

        ttk.Label(enc_frame, text="VBR quality:").grid(row=0, column=0, sticky="w")
        self._quality_var = tk.StringVar(value="5")
        ttk.Combobox(enc_frame, textvariable=self._quality_var, values=["1", "2", "3", "4", "5"],
                     width=5, state="readonly").grid(row=0, column=1, sticky="w", padx=(6, 20))

        ttk.Label(enc_frame, text="Format:").grid(row=0, column=2, sticky="w")
        self._format_var = tk.StringVar(value="m4a")
        ttk.Combobox(enc_frame, textvariable=self._format_var, values=["m4a", "mp4"],
                     width=6, state="readonly").grid(row=0, column=3, sticky="w", padx=(6, 0))

        # ── Processing ───────────────────────────────────────────────
        proc_frame = ttk.LabelFrame(self, text="Processing", padding=6)
        proc_frame.pack(fill="x", **pad)

        ttk.Label(proc_frame, text="Workers:").grid(row=0, column=0, sticky="w")
        self._workers_var = tk.IntVar(value=4)
        ttk.Spinbox(proc_frame, from_=1, to=32, textvariable=self._workers_var,
                    width=5).grid(row=0, column=1, sticky="w", padx=(6, 20))

        self._overwrite_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(proc_frame, text="Overwrite existing files",
                        variable=self._overwrite_var).grid(row=0, column=2, sticky="w")

        # ── Loudness ─────────────────────────────────────────────────
        loud_frame = ttk.LabelFrame(self, text="Loudness", padding=6)
        loud_frame.pack(fill="x", **pad)

        self._rg_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(loud_frame, text="ReplayGain", variable=self._rg_var).grid(
            row=0, column=0, sticky="w")

        self._sc_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(loud_frame, text="iTunes SoundCheck", variable=self._sc_var).grid(
            row=0, column=1, sticky="w", padx=(20, 0))

        # ── Buttons + progress ────────────────────────────────────────
        ctrl_frame = ttk.Frame(self, padding=(10, 6))
        ctrl_frame.pack(fill="x")

        self._run_btn = ttk.Button(ctrl_frame, text="Run", command=self._on_run, width=10)
        self._run_btn.pack(side="left")
        self._dryrun_btn = ttk.Button(ctrl_frame, text="Dry Run",
                                      command=lambda: self._on_run(dry_run=True), width=10)
        self._dryrun_btn.pack(side="left", padx=(8, 0))
        self._cancel_btn = ttk.Button(ctrl_frame, text="Cancel", command=self._on_cancel, width=10)
        self._cancel_btn.pack(side="left", padx=(8, 0))

        self._progress = ttk.Progressbar(ctrl_frame, mode="determinate", length=200)
        self._progress.pack(side="left", padx=(16, 8), fill="x", expand=True)
        self._progress_label = ttk.Label(ctrl_frame, text="0 / 0")
        self._progress_label.pack(side="left")

        # ── Log ──────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="Log", padding=6)
        log_frame.pack(fill="both", expand=True, **pad)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self._log_text = scrolledtext.ScrolledText(
            log_frame, state="disabled", wrap="word",
            font=("Menlo", 11), relief="flat",
        )
        self._log_text.grid(row=0, column=0, sticky="nsew")

        self._status_label = ttk.Label(self, text="", anchor="w", padding=(10, 2))
        self._status_label.pack(fill="x")

    # ------------------------------------------------------------------
    # Folder pickers
    # ------------------------------------------------------------------

    def _browse_input(self) -> None:
        path = filedialog.askdirectory(title="Select input folder (FLAC files)")
        if path:
            self._input_var.set(path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Select output folder (AAC files)")
        if path:
            self._output_var.set(path)

    def _browse_workdir(self) -> None:
        path = filedialog.askdirectory(title="Select RAM disk / work directory")
        if path:
            self._workdir_var.set(path)

    def _create_ramdisk(self) -> None:
        size_mb = self._ramdisk_size_var.get()
        sectors = size_mb * 2048  # 512-byte sectors
        self._create_rd_btn.configure(state="disabled")
        self._eject_rd_btn.configure(state="disabled")
        self._progress.configure(mode="indeterminate")
        self._progress.start(15)
        self._progress_label.configure(text="Creating…")
        self._append_log(f"Creating {size_mb} MB RAM disk…")

        def _run() -> None:
            import subprocess
            try:
                # Step 1: allocate the device
                result = subprocess.run(
                    ["hdiutil", "attach", "-nomount", f"ram://{sectors}"],
                    capture_output=True, text=True, check=True,
                )
                device = result.stdout.strip()

                # Step 2: format as HFS+ named RAMDisk
                subprocess.run(
                    ["diskutil", "erasevolume", "HFS+", "RAMDisk", device],
                    capture_output=True, text=True, check=True,
                )
                self._queue.put({"type": "ramdisk_created", "path": "/Volumes/RAMDisk"})
            except subprocess.CalledProcessError as exc:
                err = (exc.stderr or exc.stdout or str(exc)).strip()
                self._queue.put({"type": "ramdisk_error", "msg": err})

        threading.Thread(target=_run, daemon=True).start()

    def _eject_ramdisk(self) -> None:
        path = self._workdir_var.get().strip() or "/Volumes/RAMDisk"
        self._create_rd_btn.configure(state="disabled")
        self._eject_rd_btn.configure(state="disabled")
        self._progress.configure(mode="indeterminate")
        self._progress.start(15)
        self._progress_label.configure(text="Ejecting…")
        self._append_log(f"Ejecting {path}…")

        def _run() -> None:
            import subprocess
            try:
                subprocess.run(
                    ["hdiutil", "detach", path],
                    capture_output=True, text=True, check=True,
                )
                self._queue.put({"type": "ramdisk_ejected", "path": path})
            except subprocess.CalledProcessError as exc:
                err = (exc.stderr or exc.stdout or str(exc)).strip()
                self._queue.put({"type": "ramdisk_error", "msg": err})

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # Config building
    # ------------------------------------------------------------------

    def _build_config(self) -> Config:
        input_dir = self._input_var.get().strip()
        output_dir = self._output_var.get().strip()
        if not input_dir:
            raise ConfigError("Input folder is required.")
        if not output_dir:
            raise ConfigError("Output folder is required.")

        work_dir = self._workdir_var.get().strip() or None

        return Config(
            paths=PathsConfig(
                input_dir=input_dir,
                output_dir=output_dir,
                ffmpeg_bin=_bundled_ffmpeg(),
                work_dir=work_dir,
            ),
            encoding=EncodingConfig(
                vbr_quality=int(self._quality_var.get()),
                output_format=self._format_var.get(),
            ),
            metadata=MetadataConfig(
                copy_artwork=True,
                cover_file=CoverFileConfig(),
            ),
            loudness=LoudnessConfig(
                enable_replaygain=self._rg_var.get(),
                enable_itunes_soundcheck=self._sc_var.get(),
            ),
            processing=ProcessingConfig(
                workers=int(self._workers_var.get()),
                overwrite_existing=self._overwrite_var.get(),
            ),
        )

    # ------------------------------------------------------------------
    # Run / cancel
    # ------------------------------------------------------------------

    def _on_run(self, dry_run: bool = False) -> None:
        try:
            config = self._build_config()
        except (ConfigError, ValueError) as exc:
            messagebox.showerror("Configuration error", str(exc))
            return

        # Warn if loudness options are enabled but r128gain isn't installed
        if not dry_run and (self._rg_var.get() or self._sc_var.get()):
            try:
                import r128gain  # noqa: F401
            except ImportError:
                answer = messagebox.askyesno(
                    "r128gain not installed",
                    "ReplayGain / iTunes SoundCheck tagging requires the r128gain package, "
                    "which is not installed in this environment.\n\n"
                    "Install it with:\n  pip install r128gain\n\n"
                    "Continue without loudness tagging?",
                )
                if not answer:
                    return

        self._total_files = 0
        self._processed_files = 0
        self._progress.configure(mode="determinate", value=0, maximum=1)
        self._progress_label.configure(text="0 / 0")
        self._status_label.configure(text="")
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

        self._cancel_event = threading.Event()
        self._worker = _ConversionWorker(config, self._queue, self._cancel_event, dry_run=dry_run)
        self._set_running(True)
        self._worker.start()

    def _on_cancel(self) -> None:
        if self._worker and self._worker.is_alive():
            self._cancel_event.set()
            self._append_log("Cancelling after current album…")

    # ------------------------------------------------------------------
    # Queue polling
    # ------------------------------------------------------------------

    def _poll_queue(self) -> None:
        try:
            for _ in range(50):
                msg = self._queue.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass

        self.after(100, self._poll_queue)

    def _handle_message(self, msg: dict) -> None:
        kind = msg.get("type")

        if kind == "log":
            text: str = msg["text"]
            self._append_log(text)

            # Parse total file count from pipeline log line
            if "file(s) to process" in text:
                try:
                    self._total_files = int(text.split("Found")[1].split("file")[0].strip())
                    self._progress.configure(maximum=max(self._total_files, 1), value=0)
                    self._progress_label.configure(text=f"0 / {self._total_files}")
                except (IndexError, ValueError):
                    pass

            # Advance progress on each successfully encoded file
            if "Encoded:" in text or "Encoding failed" in text or "Failed to process" in text:
                self._processed_files += 1
                self._progress.configure(value=self._processed_files)
                self._progress_label.configure(text=f"{self._processed_files} / {self._total_files}")

            # Switch to indeterminate during loudness analysis
            if "Analysing loudness" in text:
                self._progress.configure(mode="indeterminate")
                self._progress.start(15)

        elif kind == "done":
            stats: ProcessingStats = msg["stats"]
            self._progress.stop()
            self._progress.configure(mode="determinate", value=self._total_files)
            self._status_label.configure(
                text=f"Albums: {stats.albums_processed} ok, {stats.albums_failed} failed  |  "
                     f"Tracks: {stats.successful} ok, {stats.failed} failed, {stats.skipped} skipped"
            )
            self._set_running(False)

        elif kind == "error":
            self._progress.stop()
            self._progress.configure(mode="determinate")
            self._append_log(f"ERROR: {msg['msg']}")
            messagebox.showerror("Conversion error", msg["msg"])
            self._set_running(False)

        elif kind == "ramdisk_created":
            mount = msg["path"]
            self._workdir_var.set(mount)
            self._append_log(f"RAM disk ready at {mount}")
            self._progress.stop()
            self._progress.configure(mode="determinate", value=0)
            self._progress_label.configure(text="")
            self._create_rd_btn.configure(state="normal")
            self._eject_rd_btn.configure(state="normal")

        elif kind == "ramdisk_ejected":
            self._append_log(f"RAM disk ejected: {msg['path']}")
            self._workdir_var.set("")
            self._progress.stop()
            self._progress.configure(mode="determinate", value=0)
            self._progress_label.configure(text="")
            self._create_rd_btn.configure(state="normal")
            self._eject_rd_btn.configure(state="normal")

        elif kind == "ramdisk_error":
            self._append_log(f"RAM disk error: {msg['msg']}")
            messagebox.showerror("RAM disk error", msg["msg"])
            self._progress.stop()
            self._progress.configure(mode="determinate", value=0)
            self._progress_label.configure(text="")
            self._create_rd_btn.configure(state="normal")
            self._eject_rd_btn.configure(state="normal")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _append_log(self, text: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", text + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self._run_btn.configure(state=state)
        self._dryrun_btn.configure(state=state)
        self._cancel_btn.configure(state="normal" if running else "disabled")


if __name__ == "__main__":
    multiprocessing.freeze_support()  # required for PyInstaller + r128gain
    app = App()
    app.mainloop()
