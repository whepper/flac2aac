"""Main processing pipeline orchestrator.

Coordinates scanning, encoding, metadata copying, and loudness tagging.
When a work_dir is configured, all processing is done there first and
finished albums are moved to output_dir in a single operation, minimising
writes to the final storage device (ideal for RAM disk workflows).
"""

import logging
import os
import shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple

from config import Config
from encoder import Encoder, EncodingError
from loudness import LoudnessProcessor
from metadata import MetadataHandler, CoverManager
from scanner import Scanner

logger = logging.getLogger(__name__)


@dataclass
class ProcessingStats:
    """Statistics for processing run."""
    total_files: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    albums_processed: int = 0
    albums_failed: int = 0


class Pipeline:
    """Main processing pipeline."""

    def __init__(self, config: Config, dry_run: bool = False):
        """Initialize pipeline.

        Args:
            config: Application configuration
            dry_run: If True, only scan and report without processing
        """
        self.config = config
        self.dry_run = dry_run
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

        # Fail fast on obvious configuration problems before spending
        # time scanning the source tree.
        if not self.dry_run:
            self._validate_paths()

        # Scan for files
        logger.info("Scanning for FLAC files...")
        file_pairs = list(self.scanner.scan())

        if not file_pairs:
            logger.warning("No FLAC files found to process")
            return self.stats

        self.stats.total_files = len(file_pairs)
        logger.info(f"Found {self.stats.total_files} file(s) to process")

        if self.use_work_dir:
            logger.info(f"Working directory: {self.config.paths.work_dir}")
        else:
            logger.info("Working directory: disabled (writing directly to output)")

        if self.dry_run:
            self._print_dry_run_report(file_pairs)
            return self.stats

        # Group files by album directory
        album_groups = self._group_by_album(file_pairs)
        logger.info(f"Organised into {len(album_groups)} album(s)")

        # Process each album. One album failing shouldn't take down
        # the whole library pass — log the error, count it, and move
        # on. The per-album cleanup inside _process_album already
        # takes care of work_dir state.
        for album_dir, files in album_groups.items():
            logger.info(f"\nProcessing album: {album_dir}")
            try:
                self._process_album(files)
            except Exception as exc:
                logger.error(f"  Album failed, continuing: {album_dir}: {exc}")
                self.stats.albums_failed += 1
                continue
            self.stats.albums_processed += 1

        return self.stats

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

    def _process_album(self, file_pairs: List[Tuple[Path, Path]]) -> None:
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
        """
        source_album_dir = file_pairs[0][0].parent
        final_album_dir = file_pairs[0][1].parent

        if self.use_work_dir:
            work_album_dir = self._make_work_album_dir(final_album_dir)
            logger.info(f"  Work directory: {work_album_dir}")
        else:
            work_album_dir = final_album_dir

        try:
            # Phase 1 & 2: Encode + copy metadata (parallel)
            logger.info(f"  Encoding {len(file_pairs)} track(s)...")
            encoded_pairs = self._encode_album(
                file_pairs, source_album_dir, work_album_dir
            )

            if not encoded_pairs:
                logger.warning("  No files successfully encoded in this album")
                return

            work_dest_files = [dest for _, dest in encoded_pairs]

            # Phase 3: Cover art
            logger.info("  Processing cover art...")
            self.cover_manager.handle_cover_file(source_album_dir, work_album_dir)

            # Phase 4 & 5: Loudness analysis + iTunNORM
            logger.info("  Analysing loudness and adding tags...")
            self.loudness_processor.process_album(
                work_dest_files, source_pairs=encoded_pairs
            )

            # Phase 6: Move finished album to output_dir
            if self.use_work_dir:
                self._move_album_to_output(work_album_dir, final_album_dir)

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
    ) -> List[Tuple[Path, Path]]:
        """Encode all tracks for one album into work_album_dir.

        Returns the subset of ``(source, work_dest)`` pairs that were
        successfully encoded — the source paths are kept alongside so
        downstream stages (cover art, RG reuse) can look up metadata
        on the original FLAC without re-deriving it.
        """
        work_file_pairs: List[Tuple[Path, Path]] = []
        for source, final_dest in file_pairs:
            relative = source.with_suffix(f".{self.config.encoding.output_format}").name
            work_dest = work_album_dir / relative
            work_file_pairs.append((source, work_dest))

        encoded_pairs: List[Tuple[Path, Path]] = []

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
