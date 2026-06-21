"""Standalone macOS GUI for flac2aac.

Bundle with PyInstaller using flac2aac_gui.spec.
"""

import atexit
import logging
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
from loudness import LoudnessProcessor
from pipeline import (
    PHASE_ENCODING,
    PHASE_LOUDNESS,
    PHASE_MOVING,
    PHASE_READY,
    PHASE_SCANNING,
    Pipeline,
    ProcessingStats,
    ProgressEvent,
)

_APP_TITLE = "flac2aac"
_WIN_WIDTH = 720
_WIN_HEIGHT = 640

# Maximum number of lines kept in the log widget. Older lines are
# discarded so a long run cannot grow the widget without bound.
_LOG_MAX_LINES = 5000


def _bundled_ffmpeg() -> str:
    """Return path to bundled FFmpeg, falling back to system 'ffmpeg'."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = Path(base) / "bin" / "ffmpeg"
        if candidate.exists():
            return str(candidate)
    return "ffmpeg"


def _bundled_rsgain() -> str:
    """Return path to bundled rsgain, falling back to system 'rsgain'."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = Path(base) / "bin" / "rsgain"
        if candidate.exists():
            return str(candidate)
    return "rsgain"


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
        import os

        # Prepend the bundled ffmpeg directory to PATH so any subprocess
        # (rsgain, etc.) that shells out to ffmpeg finds the right binary.
        ffmpeg_bin = self._config.paths.ffmpeg_bin
        ffmpeg_dir = str(Path(ffmpeg_bin).parent.resolve())
        prev_path = os.environ.get("PATH", "")
        if ffmpeg_dir and ffmpeg_dir != ".":
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + prev_path

        root_logger = logging.getLogger()
        prev_level = root_logger.level
        root_logger.setLevel(self._config.processing.log_level)
        handler = _QueueLogHandler(self._queue)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%H:%M:%S"))
        root_logger.addHandler(handler)
        try:
            def _on_progress(event: ProgressEvent) -> None:
                self._queue.put({
                    "type": "progress",
                    "phase": event.phase,
                    "album_index": event.album_index,
                    "album_total": event.album_total,
                    "track_index": event.track_index,
                    "track_total": event.track_total,
                    "files_done": event.files_done,
                    "files_total": event.files_total,
                })

            pipeline = Pipeline(
                self._config,
                dry_run=self._dry_run,
                cancel_event=self._cancel_event,
                progress_callback=_on_progress,
            )
            stats = pipeline.run()
            self._queue.put({"type": "done", "stats": stats})
        except Exception as exc:
            self._queue.put({"type": "error", "msg": str(exc)})
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(prev_level)
            os.environ["PATH"] = prev_path


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
        # Total files to encode (grand total across all albums). Set
        # when the pipeline emits its "ready" event so the progress bar
        # can be sized up front.
        self._total_files = 0
        # Monotonic grand total of files completed — used to ensure the
        # bar value never goes backward even if a stale event arrives.
        self._processed_files = 0
        # Running count of lines in the log widget, used to cap its size.
        self._log_line_count = 0
        # Tracks whether the RAM disk currently in use was created by
        # this app instance, so the close-time auto-eject only detaches
        # what we made ourselves.
        self._ramdisk_created_by_app = False
        # User toggle for close-time auto-eject. Defaulted to True per
        # the "always clean up" expectation.
        self._auto_eject_var = tk.BooleanVar(value=True)

        self._build_ui()
        self._set_running(False)
        self.after(100, self._poll_queue)
        # Intercept the window-close button (and Cmd-W on macOS) so we
        # can stop the worker and detach the RAM disk before exiting.
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Safety net for uncaught errors that propagate out of mainloop
        # before WM_DELETE_WINDOW can run.
        atexit.register(self._auto_eject_on_exit)

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
        opts_row = ttk.Frame(folder_frame)
        opts_row.grid(row=4, column=1, sticky="w")
        ttk.Label(
            opts_row, text="Optional — encode here first, then move to output",
            foreground="gray",
        ).pack(side="left")
        ttk.Checkbutton(
            opts_row, text="Auto-eject on exit", variable=self._auto_eject_var,
        ).pack(side="left", padx=(12, 0))

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

        # ── Cover Art ────────────────────────────────────────────────
        art_frame = ttk.LabelFrame(self, text="Cover Art", padding=6)
        art_frame.pack(fill="x", **pad)

        self._copy_artwork_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(art_frame, text="Embed artwork in M4A files",
                        variable=self._copy_artwork_var).grid(row=0, column=0, columnspan=2, sticky="w")

        self._cover_file_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(art_frame, text="Copy cover file per album",
                        variable=self._cover_file_var,
                        command=self._on_cover_file_toggle).grid(row=0, column=2, columnspan=2, sticky="w", padx=(20, 0))

        ttk.Label(art_frame, text="Max size:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._cover_max_size_var = tk.IntVar(value=2000)
        self._cover_max_size_spin = ttk.Spinbox(art_frame, from_=0, to=9999, increment=100,
                                                 textvariable=self._cover_max_size_var, width=6)
        self._cover_max_size_spin.grid(row=1, column=1, sticky="w", padx=(6, 4), pady=(4, 0))
        ttk.Label(art_frame, text="px (0 = no resize)").grid(row=1, column=2, sticky="w", pady=(4, 0))

        ttk.Label(art_frame, text="JPEG quality:").grid(row=1, column=3, sticky="w", padx=(20, 0), pady=(4, 0))
        self._cover_jpeg_quality_var = tk.IntVar(value=95)
        self._cover_jpeg_quality_spin = ttk.Spinbox(art_frame, from_=1, to=95, increment=5,
                                                     textvariable=self._cover_jpeg_quality_var, width=5)
        self._cover_jpeg_quality_spin.grid(row=1, column=4, sticky="w", padx=(6, 0), pady=(4, 0))

        # ── Loudness ─────────────────────────────────────────────────
        loud_frame = ttk.LabelFrame(self, text="Loudness", padding=6)
        loud_frame.pack(fill="x", **pad)

        self._rg_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(loud_frame, text="ReplayGain", variable=self._rg_var).grid(
            row=0, column=0, sticky="w")

        self._sc_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(loud_frame, text="iTunes SoundCheck", variable=self._sc_var).grid(
            row=0, column=1, sticky="w", padx=(20, 0))

        self._reuse_rg_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(loud_frame, text="Reuse existing ReplayGain tags",
                        variable=self._reuse_rg_var).grid(row=0, column=2, sticky="w", padx=(20, 0))

        ttk.Label(loud_frame, text="Reference:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._ref_loudness_var = tk.DoubleVar(value=-18.0)
        ttk.Spinbox(loud_frame, from_=-30, to=0, increment=0.5,
                    textvariable=self._ref_loudness_var, width=6,
                    format="%.1f").grid(row=1, column=1, sticky="w", padx=(6, 4), pady=(4, 0))
        ttk.Label(loud_frame, text="LUFS").grid(row=1, column=2, sticky="w", pady=(4, 0))

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

        # ── Activity indicator ──────────────────────────────────────
        # Live phase text ("Encoding album 3 / 12 — track 5 / 10",
        # "Analysing loudness — album 3 / 12", "Moving album to output").
        # Subtle gray, full-width, sits between the progress bar and
        # the log so the user can always see what the app is doing
        # without the bar itself having to flip to indeterminate.
        self._activity_label = ttk.Label(
            self, text="", anchor="w", padding=(10, 0), foreground="gray"
        )
        self._activity_label.pack(fill="x", pady=(0, 2))

        # ── Log ──────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="Log", padding=6)
        log_frame.pack(fill="both", expand=True, **pad)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self._log_text = scrolledtext.ScrolledText(
            log_frame, state="disabled", wrap="word",
            font=("Menlo", 9), relief="flat",
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

    def _on_cover_file_toggle(self) -> None:
        state = "normal" if self._cover_file_var.get() else "disabled"
        self._cover_max_size_spin.configure(state=state)
        self._cover_jpeg_quality_spin.configure(state=state)

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
                rsgain_bin=_bundled_rsgain(),
                work_dir=work_dir,
            ),
            encoding=EncodingConfig(
                vbr_quality=int(self._quality_var.get()),
                output_format=self._format_var.get(),
            ),
            metadata=MetadataConfig(
                copy_artwork=self._copy_artwork_var.get(),
                cover_file=CoverFileConfig(
                    enabled=self._cover_file_var.get(),
                    max_size=int(self._cover_max_size_var.get()),
                    jpeg_quality=int(self._cover_jpeg_quality_var.get()),
                ),
            ),
            loudness=LoudnessConfig(
                enable_replaygain=self._rg_var.get(),
                enable_itunes_soundcheck=self._sc_var.get(),
                reference_loudness=float(self._ref_loudness_var.get()),
                reuse_existing_replaygain=self._reuse_rg_var.get(),
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
        except (ConfigError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Configuration error", str(exc))
            return

        # Warn if loudness options are enabled but the rsgain binary is missing
        if not dry_run and (self._rg_var.get() or self._sc_var.get()):
            if not LoudnessProcessor(config).verify_rsgain():
                answer = messagebox.askyesno(
                    "rsgain not available",
                    f"ReplayGain / iTunes SoundCheck tagging requires the rsgain "
                    f"binary, which was not found ({config.paths.rsgain_bin}).\n\n"
                    f"Install it with 'brew install rsgain' or from "
                    f"https://github.com/complexlogic/rsgain.\n\n"
                    f"Continue without loudness tagging?",
                )
                if not answer:
                    return
                config.loudness.enable_replaygain = False
                config.loudness.enable_itunes_soundcheck = False

        self._total_files = 0
        self._processed_files = 0
        # Put the bar into indeterminate mode immediately so the user
        # sees motion during the scan. The "ready" event from the
        # pipeline will switch it back to determinate and size it
        # against the real file count — no more "0 / 0" flash.
        if str(self._progress["mode"]) == "indeterminate":
            self._progress.stop()
        self._progress.configure(mode="indeterminate", value=0, maximum=1)
        self._progress.start(15)
        self._progress_label.configure(text="")
        self._activity_label.configure(text="Scanning…")
        self._status_label.configure(text="")
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")
        self._log_line_count = 0

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
            self._append_log(msg["text"])

        elif kind == "progress":
            self._handle_progress(msg)

        elif kind == "done":
            stats: ProcessingStats = msg["stats"]
            self._progress.stop()
            # Snap the bar to the final known total so it lands at 100 %.
            if self._total_files > 0:
                self._progress.configure(mode="determinate", value=self._total_files)
            self._activity_label.configure(text="Done.")
            self._status_label.configure(
                text=f"Albums: {stats.albums_processed} ok, {stats.albums_failed} failed  |  "
                     f"Tracks: {stats.successful} ok, {stats.failed} failed, {stats.skipped} skipped"
            )
            self._set_running(False)

        elif kind == "error":
            self._progress.stop()
            self._progress.configure(mode="determinate")
            self._activity_label.configure(text="Error.")
            self._append_log(f"ERROR: {msg['msg']}")
            messagebox.showerror("Conversion error", msg["msg"])
            self._set_running(False)

        elif kind == "ramdisk_created":
            mount = msg["path"]
            self._workdir_var.set(mount)
            self._ramdisk_created_by_app = True
            self._append_log(f"RAM disk ready at {mount}")
            self._progress.stop()
            self._progress.configure(mode="determinate", value=0)
            self._progress_label.configure(text="")
            self._create_rd_btn.configure(state="normal")
            self._eject_rd_btn.configure(state="normal")

        elif kind == "ramdisk_ejected":
            self._append_log(f"RAM disk ejected: {msg['path']}")
            self._workdir_var.set("")
            self._ramdisk_created_by_app = False
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

    def _handle_progress(self, msg: dict) -> None:
        """Apply a structured progress event from the pipeline.

        The bar value is set from ``files_done`` and clamped so it can
        only ever increase — even if a stale event somehow arrives out
        of order. ``files_total`` is the only event that defines the
        bar's maximum; subsequent events do not shrink it.
        """
        phase: str = msg.get("phase", "")
        album_index: int = msg.get("album_index", 0)
        album_total: int = msg.get("album_total", 0)
        track_index: int = msg.get("track_index", 0)
        track_total: int = msg.get("track_total", 0)
        files_done: int = msg.get("files_done", 0)
        files_total: int = msg.get("files_total", 0)

        if phase == PHASE_SCANNING:
            # Bar is indeterminate while we don't know the totals yet.
            if str(self._progress["mode"]) != "indeterminate":
                self._progress.configure(mode="indeterminate", value=0, maximum=1)
                self._progress.start(15)
            self._activity_label.configure(text="Scanning…")
            self._progress_label.configure(text="")
            return

        if phase == PHASE_READY:
            self._total_files = max(files_total, 0)
            self._processed_files = 0
            # Switch back to determinate and size the bar once we know
            # the total file count.
            if str(self._progress["mode"]) == "indeterminate":
                self._progress.stop()
            self._progress.configure(
                mode="determinate", maximum=max(self._total_files, 1), value=0
            )
            self._progress_label.configure(
                text=f"0 / {self._total_files}" if self._total_files else "0 / 0"
            )
            self._activity_label.configure(text="Starting…")
            return

        if files_total > 0:
            # Lock the maximum in — later events never shrink it.
            if self._total_files != files_total:
                self._total_files = files_total
            if float(self._progress["maximum"]) != files_total:
                self._progress.configure(maximum=files_total)

        # Ensure the bar is in determinate mode for the actual work
        # (loudness and moving don't increment files_done, so we want
        # the fill to stay put rather than flip to indeterminate).
        if str(self._progress["mode"]) == "indeterminate":
            self._progress.stop()
            self._progress.configure(mode="determinate")

        # Monotonic clamp: never go below the value already shown.
        if files_done > self._processed_files:
            self._processed_files = files_done
        self._progress.configure(value=self._processed_files)
        if self._total_files > 0:
            self._progress_label.configure(
                text=f"{self._processed_files} / {self._total_files}"
            )

        # Activity text. 1-based album/track numbers read more naturally.
        if phase == PHASE_ENCODING:
            self._activity_label.configure(
                text=(
                    f"Encoding album {album_index + 1} / {album_total}  —  "
                    f"track {track_index} / {track_total}"
                )
            )
        elif phase == PHASE_LOUDNESS:
            self._activity_label.configure(
                text=f"Analysing loudness  —  album {album_index + 1} / {album_total}"
            )
        elif phase == PHASE_MOVING:
            self._activity_label.configure(
                text=f"Moving album to output  —  album {album_index + 1} / {album_total}"
            )

    def _append_log(self, text: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", text + "\n")
        self._log_line_count += 1
        # Cap the log size so a very long run can't grow the widget
        # without bound. Deleting from the top keeps the most recent
        # output visible.
        if self._log_line_count > _LOG_MAX_LINES:
            excess = self._log_line_count - _LOG_MAX_LINES
            self._log_text.delete("1.0", f"{excess + 1}.0")
            self._log_line_count = _LOG_MAX_LINES
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self._run_btn.configure(state=state)
        self._dryrun_btn.configure(state=state)
        # RAM disk operations are unsafe while a conversion is using it.
        self._create_rd_btn.configure(state=state)
        self._eject_rd_btn.configure(state=state)
        self._cancel_btn.configure(state="normal" if running else "disabled")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _detach_ramdisk(self) -> None:
        """Run ``hdiutil detach /Volumes/RAMDisk`` and log the outcome.

        The RAM disk the app creates is always mounted at
        ``/Volumes/RAMDisk`` (the volume label is hard-coded in
        ``_create_ramdisk``), so we always target that exact path.
        """
        import subprocess
        try:
            result = subprocess.run(
                ["hdiutil", "detach", "/Volumes/RAMDisk"],
                capture_output=True, text=True, timeout=5,
            )
        except subprocess.TimeoutExpired:
            self._append_log("Auto-eject: hdiutil detach timed out.")
            return
        except Exception as exc:  # pragma: no cover — defensive
            self._append_log(f"Auto-eject failed: {exc}")
            return
        if result.returncode == 0:
            self._append_log("Auto-ejected RAM disk on exit.")
        else:
            # Non-zero is normal when the disk is already gone.
            err = (result.stderr or result.stdout or "").strip()
            self._append_log(f"Auto-eject skipped: {err or 'disk already detached'}")

    def _auto_eject_on_exit(self) -> None:
        """atexit safety net: detach the RAM disk if the app created one.

        Runs on interpreter shutdown regardless of how we got there
        (WM_DELETE_WINDOW, uncaught exception, normal exit). Swallows
        everything — at this point Tk widgets may already be gone, so
        we can't reliably touch the log widget.
        """
        if not self._ramdisk_created_by_app:
            return
        try:
            if not self._auto_eject_var.get():
                return
        except Exception:
            return
        import subprocess
        try:
            subprocess.run(
                ["hdiutil", "detach", "/Volumes/RAMDisk"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            pass
        finally:
            self._ramdisk_created_by_app = False

    def _on_close(self) -> None:
        """Window-close handler: stop the worker, then detach the RAM disk."""
        # If a conversion is running, ask it to wind down so we don't
        # yank the scratch directory out from under the pipeline.
        if self._worker is not None and self._worker.is_alive():
            self._cancel_event.set()
            self._worker.join(timeout=2.0)

        if self._ramdisk_created_by_app and self._auto_eject_var.get():
            self._detach_ramdisk()
            # Clear the flag so the atexit fallback doesn't double-call.
            self._ramdisk_created_by_app = False

        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
