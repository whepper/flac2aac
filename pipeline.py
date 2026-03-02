"""Main processing pipeline orchestrator.

Coordinates scanning, encoding, metadata copying, and loudness tagging.
When a work_dir is configured, all processing is done there first and
finished albums are moved to output_dir in a single operation, minimising
writes to the final storage device (ideal for RAM disk workflows).
"""

import logging
import shutil
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional, Tuple

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

        # Process each album
        for album_dir, files in album_groups.items():
            logger.info(f"\nProcessing album: {album_dir}")
            self._process_album(files)
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
            groups[self.scanner.get_album_dir(source)].append((source, dest))
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
        source_album_dir = self.scanner.get_album_dir(file_pairs[0][0])
        final_album_dir = self.scanner.get_album_dir(file_pairs[0][1])

        if self.use_work_dir:
            work_album_dir = self._make_work_album_dir(final_album_dir)
            logger.info(f"  Work directory: {work_album_dir}")
        else:
            work_album_dir = final_album_dir

        try:
            # Phase 1 & 2: Encode + copy metadata (parallel)
            logger.info(f"  Encoding {len(file_pairs)} track(s)...")
            work_dest_files = self._encode_album(
                file_pairs, source_album_dir, work_album_dir
            )

            if not work_dest_files:
                logger.warning("  No files successfully encoded in this album")
                return

            # Phase 3: Cover art
            logger.info("  Processing cover art...")
            self.cover_manager.handle_cover_file(source_album_dir, work_album_dir)

            # Phase 4 & 5: Loudness analysis + iTunNORM
            logger.info("  Analysing loudness and adding tags...")
            self.loudness_processor.process_album(work_dest_files)

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
    ) -> List[Path]:
        """Encode all tracks for one album into work_album_dir.

        Returns list of successfully encoded destination paths.
        """
        # Remap destination paths to work directory
        work_file_pairs: List[Tuple[Path, Path]] = []
        for source, final_dest in file_pairs:
            relative = source.with_suffix(f".{self.config.encoding.output_format}").name
            work_dest = work_album_dir / relative
            work_file_pairs.append((source, work_dest))

        work_dest_files: List[Path] = []

        with ThreadPoolExecutor(max_workers=self.config.processing.workers) as executor:
            futures = {
                executor.submit(self._encode_file, src, dst): (src, dst)
                for src, dst in work_file_pairs
            }
            for future in as_completed(futures):
                src, dst = futures[future]
                try:
                    if future.result():
                        work_dest_files.append(dst)
                        self.stats.successful += 1
                    else:
                        self.stats.failed += 1
                except Exception as exc:
                    logger.error(f"  Unexpected error processing {src.name}: {exc}")
                    self.stats.failed += 1

        return work_dest_files

    def _encode_file(self, source: Path, dest: Path) -> bool:
        """Encode a single file and copy its metadata.

        Returns True on success.
        """
        try:
            self.encoder.encode(source, dest)
            self.metadata_handler.copy_metadata(source, dest)
            return True
        except EncodingError as exc:
            logger.error(f"  Encoding failed for {source.name}: {exc}")
            return False
        except Exception as exc:
            logger.error(f"  Failed to process {source.name}: {exc}")
            return False

    def _make_work_album_dir(self, final_album_dir: Path) -> Path:
        """Create a uniquely named album directory inside work_dir.

        Uses the same relative path as the final destination so that
        log messages are easy to correlate.
        """
        relative = final_album_dir.relative_to(self.config.paths.output_dir)
        work_album_dir = self.config.paths.work_dir / relative
        work_album_dir.mkdir(parents=True, exist_ok=True)
        return work_album_dir

    def _move_album_to_output(
        self, work_album_dir: Path, final_album_dir: Path
    ) -> None:
        """Move a completed album from work_dir to output_dir.

        Creates the parent directory in output_dir if needed,
        then moves every file individually so that an existing
        partial output is overwritten correctly.
        """
        final_album_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"  Moving album to output: {final_album_dir}")

        for work_file in work_album_dir.iterdir():
            dest_file = final_album_dir / work_file.name
            if dest_file.exists():
                dest_file.unlink()
            shutil.move(str(work_file), str(dest_file))
            logger.debug(f"  Moved: {work_file.name}")

        # Remove now-empty work album directory
        try:
            work_album_dir.rmdir()
        except OSError:
            # Not empty — leave cleanup to OS / next run
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
