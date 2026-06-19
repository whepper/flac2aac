"""Main processing pipeline orchestrator.

Coordinates scanning, encoding, metadata copying, and loudness tagging.
When a work_dir is configured, all processing is done there first and
finished albums are moved to output_dir in a single operation, minimising
writes to the final storage device (ideal for RAM disk workflows).
"""

import logging
import os
import shutil
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Dict, Optional, Tuple

from config import Config
from encoder import Encoder, EncodingError
from loudness import LoudnessProcessor
from metadata import MetadataHandler, CoverManager
from scanner import Scanner

logger = logging.getLogger(__name__)


# Phase identifiers used in ProgressEvent. Kept as plain strings so the
# GUI can switch on them without importing the dataclass.
PHASE_SCANNING = "scanning"
PHASE_READY = "ready"
PHASE_ENCODING = "encoding"
PHASE_LOUDNESS = "loudness"
PHASE_MOVING = "moving"


@dataclass
class ProcessingStats:
    """Statistics for processing run."""
    total_files: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    albums_processed: int = 0
    albums_failed: int = 0


@dataclass
class ProgressEvent:
    """Structured progress notification emitted by the pipeline.

    All counters are 0-based where applicable. ``files_done`` is a
    monotonically increasing grand total of files that have completed
    encoding, which the GUI uses to drive a progress bar that only
    ever moves right.
    """
    phase: str
    album_index: int = 0
    album_total: int = 0
    track_index: int = 0
    track_total: int = 0
    files_done: int = 0
    files_total: int = 0


ProgressCallback = Callable[[ProgressEvent], None]


class Pipeline:
    """Main processing pipeline."""

    def __init__(
        self,
        config: Config,
        dry_run: bool = False,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ):
        """Initialize pipeline.

        Args:
            config: Application configuration
            dry_run: If True, only scan and report without processing
            cancel_event: Optional event; when set the album loop stops cleanly
            progress_callback: Optional callable receiving :class:`ProgressEvent`
                notifications as the pipeline advances. Exceptions raised by
                the callback are logged and swallowed so they cannot break
                the run. When ``None`` (the default, used by the CLI) no
                events are emitted.
        """
        self.config = config
        self.dry_run = dry_run
        self.cancel_event = cancel_event
        self.progress_callback = progress_callback
        self.use_work_dir = (
            config.paths.work_dir is not None
            and str(config.paths.work_dir).strip() != ""
        )

        # Initialize components
        self.scanner = Scanner(config)
        self.encoder = Encoder(config)
        self.metadata_handler = MetadataHandler(config)
        self.cover_manager = CoverManager(config)
        self.loudness_processor = LoudnessProcessor(config)

        self.stats = ProcessingStats()

    def run(self) -> ProcessingStats:
        """Run the complete pipeline.

        Returns:
            Processing statistics
        """
        # Verify FFmpeg availability
        if not self.dry_run and not self.encoder.verify_ffmpeg():
            raise RuntimeError("FFmpeg with libfdk_aac not available")

        # Verify rsgain availability when ReplayGain tagging is requested.
        # reuse_existing_replaygain is a best-effort fast path: if any source
        # FLAC is untagged the pipeline falls back to calling rsgain, so the
        # binary must be present regardless of that setting.
        if (
            not self.dry_run
            and self.config.loudness.enable_replaygain
            and not self.loudness_processor.verify_rsgain()
        ):
            raise RuntimeError(
                f"rsgain binary not available: {self.config.paths.rsgain_bin}. "
                "Install from https://github.com/complexlogic/rsgain or set "
                "[paths] rsgain_bin in config.toml"
            )

        # Fail fast on obvious configuration problems before spending
        # time scanning the source tree.
        if not self.dry_run:
            self._validate_paths()

        # Scan for files
        self._emit_progress(ProgressEvent(phase=PHASE_SCANNING))
        logger.info("Scanning for FLAC files...")
        file_pairs = list(self.scanner.scan())
        self.stats.skipped = self.scanner.skipped

        self.stats.total_files = len(file_pairs) + self.stats.skipped
        logger.info(
            f"Found {len(file_pairs)} file(s) to process "
            f"({self.stats.skipped} skipped)"
        )

        if self.use_work_dir:
            logger.info(f"Working directory: {self.config.paths.work_dir}")
        else:
            logger.info("Working directory: disabled (writing directly to output)")

        if self.dry_run:
            self._print_dry_run_report(file_pairs)
            return self.stats

        # Group files by album directory
        album_groups = self._group_by_album(file_pairs)
        album_total = len(album_groups)
        logger.info(f"Organised into {album_total} album(s)")

        # Single "ready" event with the full picture (album count and
        # file count). The UI uses this to switch the bar from
        # indeterminate to determinate and to size it against the real
        # total. Emitted even in the no-files case so the UI gets a
        # clean "Starting…" → "Done." sequence.
        self._emit_progress(ProgressEvent(
            phase=PHASE_READY,
            album_total=album_total,
            files_done=0,
            files_total=self.stats.total_files,
        ))

        if not file_pairs and self.stats.skipped == 0:
            logger.warning("No FLAC files found to process")
            return self.stats

        # Process each album. One album failing shouldn't take down
        # the whole library pass — log the error, count it, and move
        # on. The per-album cleanup inside _process_album already
        # takes care of work_dir state.
        files_done = 0
        for album_index, (album_dir, files) in enumerate(album_groups.items()):
            if self.cancel_event and self.cancel_event.is_set():
                logger.info("Conversion cancelled by user.")
                break
            logger.info(f"\nProcessing album: {album_dir}")
            try:
                files_done = self._process_album(
                    files,
                    album_index=album_index,
                    album_total=album_total,
                    files_done=files_done,
                )
            except Exception as exc:
                logger.error(f"  Album failed, continuing: {album_dir}: {exc}")
                self.stats.albums_failed += 1
                # Even on failure, advance the counter so the bar keeps
                # moving right — the user still wants to see overall
                # progress through the library.
                files_done += len(files)
                continue
            self.stats.albums_processed += 1

        return self.stats

    def _emit_progress(self, event: ProgressEvent) -> None:
        """Forward a progress event to the callback, if one was supplied.

        Exceptions in the callback are caught and logged so a buggy UI
        hook cannot abort a long-running encode.
        """
        if not self.progress_callback:
            return
        try:
            self.progress_callback(event)
        except Exception:
            logger.exception("Progress callback raised; ignoring.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _group_by_album(
        self, file_pairs: List[Tuple[Path, Path]]
    ) -> Dict[Path, List[Tuple[Path, Path]]]:
        """Group file pairs by source album directory."""
        groups: Dict[Path, List[Tuple[Path, Path]]] = defaultdict(list)
        for source, dest in file_pairs:
            groups[source.parent].append((source, dest))
        return dict(groups)

    def _process_album(
        self,
        file_pairs: List[Tuple[Path, Path]],
        album_index: int = 0,
        album_total: int = 0,
        files_done: int = 0,
    ) -> int:
        """Process all files in one album.

        When work_dir is enabled:
          1. Create a temporary album sub-directory inside work_dir.
          2. Encode + tag every track there (parallel).
          3. Copy cover art into the work directory.
          4. Run loudness analysis + iTunNORM on the work copies.
          5. Move the finished album directory to output_dir.
          6. Clean up any leftover temp directory.

        When work_dir is disabled the same steps run directly in
        output_dir (original behaviour).

        Returns:
            Updated ``files_done`` count after this album has been
            processed (or attempted), used by the caller to keep the
            progress bar monotonic.
        """
        source_album_dir = file_pairs[0][0].parent
        final_album_dir = file_pairs[0][1].parent
        track_total = len(file_pairs)

        if self.use_work_dir:
            work_album_dir = self._make_work_album_dir(final_album_dir)
            logger.info(f"  Work directory: {work_album_dir}")
        else:
            work_album_dir = final_album_dir

        try:
            # Phase 1 & 2: Encode + copy metadata (parallel).
            # _encode_album emits its own per-track progress events;
            # we just pass the album context through.
            logger.info(f"  Encoding {track_total} track(s)...")
            encoded_pairs = self._encode_album(
                file_pairs,
                source_album_dir,
                work_album_dir,
                album_index=album_index,
                album_total=album_total,
                track_total=track_total,
                files_done=files_done,
            )

            if not encoded_pairs:
                logger.warning("  No files successfully encoded in this album")
                return files_done + track_total

            work_dest_files = [dest for _, dest in encoded_pairs]
            files_done += len(encoded_pairs)

            # Phase 3: Cover art
            logger.info("  Processing cover art...")
            self.cover_manager.handle_cover_file(source_album_dir, work_album_dir)

            # Phase 4 & 5: Loudness analysis + iTunNORM. The bar holds
            # at the post-encode value here — the user just sees a
            # "Analysing loudness" message in the activity label.
            self._emit_progress(ProgressEvent(
                phase=PHASE_LOUDNESS,
                album_index=album_index,
                album_total=album_total,
                track_index=track_total,
                track_total=track_total,
                files_done=files_done,
                files_total=self.stats.total_files,
            ))
            logger.info("  Analysing loudness and adding tags...")
            self.loudness_processor.process_album(
                work_dest_files, source_pairs=encoded_pairs
            )

            # Phase 6: Move finished album to output_dir
            if self.use_work_dir:
                self._emit_progress(ProgressEvent(
                    phase=PHASE_MOVING,
                    album_index=album_index,
                    album_total=album_total,
                    track_index=track_total,
                    track_total=track_total,
                    files_done=files_done,
                    files_total=self.stats.total_files,
                ))
                self._move_album_to_output(work_album_dir, final_album_dir)

            return files_done

        except Exception:
            # On failure clean up the work directory so no half-finished
            # files are left behind on the RAM disk.
            if self.use_work_dir and work_album_dir.exists():
                logger.warning(f"  Cleaning up work directory after error: {work_album_dir}")
                shutil.rmtree(work_album_dir, ignore_errors=True)
            raise

    def _encode_album(
        self,
        file_pairs: List[Tuple[Path, Path]],
        source_album_dir: Path,
        work_album_dir: Path,
        album_index: int = 0,
        album_total: int = 0,
        track_total: int = 0,
        files_done: int = 0,
    ) -> List[Tuple[Path, Path]]:
        """Encode all tracks for one album into work_album_dir.

        Returns the subset of ``(source, work_dest)`` pairs that were
        successfully encoded — the source paths are kept alongside so
        downstream stages (cover art, RG reuse) can look up metadata
        on the original FLAC without re-deriving it.

        Emits a ``PHASE_ENCODING`` progress event for every completed
        track (success or failure) so the GUI can advance its bar in
        real time. ``files_done`` is incremented in lockstep with the
        GUI's monotonic counter.
        """
        work_file_pairs: List[Tuple[Path, Path]] = []
        for source, final_dest in file_pairs:
            relative = source.with_suffix(f".{self.config.encoding.output_format}").name
            work_dest = work_album_dir / relative
            work_file_pairs.append((source, work_dest))

        encoded_pairs: List[Tuple[Path, Path]] = []
        track_done = 0

        with ThreadPoolExecutor(max_workers=self.config.processing.workers) as executor:
            futures = {
                executor.submit(self._encode_file, src, dst): (src, dst)
                for src, dst in work_file_pairs
            }
            for future in as_completed(futures):
                src, dst = futures[future]
                try:
                    if future.result():
                        encoded_pairs.append((src, dst))
                        self.stats.successful += 1
                    else:
                        self.stats.failed += 1
                except Exception as exc:
                    logger.error(f"  Unexpected error processing {src.name}: {exc}")
                    self.stats.failed += 1
                track_done += 1
                self._emit_progress(ProgressEvent(
                    phase=PHASE_ENCODING,
                    album_index=album_index,
                    album_total=album_total,
                    track_index=track_done,
                    track_total=track_total,
                    files_done=files_done + track_done,
                    files_total=self.stats.total_files,
                ))

        return encoded_pairs

    def _encode_file(self, source: Path, dest: Path) -> bool:
        """Encode a single file and copy its metadata.

        Returns True on success. On failure any partially-written
        destination file is removed so the next run does not skip it
        via the "output already exists" shortcut.
        """
        try:
            self.encoder.encode(source, dest)
            self.metadata_handler.copy_metadata(source, dest)
            return True
        except EncodingError as exc:
            logger.error(f"  Encoding failed for {source.name}: {exc}")
            dest.unlink(missing_ok=True)
            return False
        except Exception as exc:
            logger.error(f"  Failed to process {source.name}: {exc}")
            dest.unlink(missing_ok=True)
            return False

    def _make_work_album_dir(self, final_album_dir: Path) -> Path:
        """Create a uniquely named album directory inside work_dir.

        Uses the same relative path as the final destination so that
        log messages are easy to correlate.
        """
        output_dir = self.config.paths.output_dir
        try:
            relative = final_album_dir.relative_to(output_dir)
        except ValueError as e:
            raise RuntimeError(
                f"Album destination {final_album_dir} is not inside output_dir "
                f"{output_dir}; cannot place it in work_dir"
            ) from e
        work_album_dir = self.config.paths.work_dir / relative
        work_album_dir.mkdir(parents=True, exist_ok=True)
        return work_album_dir

    def _validate_paths(self) -> None:
        """Validate configured paths before scanning.

        * ``input_dir`` must exist and be a readable directory.
        * ``output_dir`` (and ``work_dir``, if configured) are created if
          missing and must be writable.

        Called before scanning so misconfiguration fails fast with a
        clear error instead of crashing mid-encode or silently
        reporting "no FLAC files found".
        """
        input_dir = self.config.paths.input_dir
        if not input_dir.exists():
            raise RuntimeError(f"input_dir does not exist: {input_dir}")
        if not input_dir.is_dir():
            raise RuntimeError(f"input_dir is not a directory: {input_dir}")
        if not os.access(input_dir, os.R_OK):
            raise RuntimeError(f"input_dir is not readable: {input_dir}")

        writable = [("output_dir", self.config.paths.output_dir)]
        if self.use_work_dir:
            writable.append(("work_dir", self.config.paths.work_dir))

        for name, path in writable:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise RuntimeError(f"Cannot create {name} {path}: {e}") from e
            if not os.access(path, os.W_OK):
                raise RuntimeError(f"{name} is not writable: {path}")

    def _move_album_to_output(
        self, work_album_dir: Path, final_album_dir: Path
    ) -> None:
        """Move a completed album from work_dir to output_dir.

        Creates the parent directory in output_dir if needed, then moves
        every file individually. We try an atomic rename first (works
        when work_dir and output_dir share a filesystem) and fall back
        to ``shutil.move`` for the common cross-filesystem case — for
        example, a tmpfs work_dir paired with a disk output_dir.
        """
        final_album_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"  Moving album to output: {final_album_dir}")

        for work_file in work_album_dir.iterdir():
            dest_file = final_album_dir / work_file.name
            if dest_file.exists() and not self.config.processing.overwrite_existing:
                logger.debug(f"  Skipping existing output file: {dest_file.name}")
                work_file.unlink(missing_ok=True)
                continue
            try:
                work_file.replace(dest_file)
            except OSError:
                # Cross-device or platform where rename can't overwrite.
                shutil.move(str(work_file), str(dest_file))
            logger.debug(f"  Moved: {work_file.name}")

        try:
            work_album_dir.rmdir()
        except OSError:
            logger.debug(f"  Could not remove work dir (not empty): {work_album_dir}")

    def _print_dry_run_report(self, file_pairs: List[Tuple[Path, Path]]) -> None:
        """Print dry-run report."""
        logger.info("\n" + "=" * 60)
        logger.info("DRY RUN - Files to be processed:")
        logger.info("=" * 60)
        for source, dest in file_pairs:
            logger.info(f"  {source}")
            logger.info(f"  -> {dest}\n")
        logger.info("=" * 60)
        logger.info(f"Total: {len(file_pairs)} file(s)")
        logger.info("=" * 60)
