"""Main processing pipeline orchestrator.

Coordinates scanning, encoding, metadata copying, and loudness tagging.
"""

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict

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
        
        if self.dry_run:
            self._print_dry_run_report(file_pairs)
            return self.stats
        
        # Group files by album directory
        album_groups = self._group_by_album(file_pairs)
        logger.info(f"Organized into {len(album_groups)} album(s)")
        
        # Process each album
        for album_dir, files in album_groups.items():
            logger.info(f"\nProcessing album: {album_dir}")
            self._process_album(files)
            self.stats.albums_processed += 1
        
        return self.stats
    
    def _group_by_album(self, file_pairs: List[tuple]) -> Dict[Path, List[tuple]]:
        """Group file pairs by source album directory.
        
        Args:
            file_pairs: List of (source, dest) tuples
            
        Returns:
            Dictionary mapping album dir to file pairs
        """
        groups = defaultdict(list)
        
        for source, dest in file_pairs:
            album_dir = self.scanner.get_album_dir(source)
            groups[album_dir].append((source, dest))
        
        return dict(groups)
    
    def _process_album(self, file_pairs: List[tuple]) -> None:
        """Process all files in an album.
        
        Args:
            file_pairs: List of (source, dest) tuples for the album
        """
        # Phase 1: Encode and copy metadata (parallel)
        logger.info(f"Encoding {len(file_pairs)} track(s)...")
        
        dest_files = []
        
        with ThreadPoolExecutor(max_workers=self.config.processing.workers) as executor:
            # Submit all encoding jobs
            futures = {
                executor.submit(self._encode_file, source, dest): (source, dest)
                for source, dest in file_pairs
            }
            
            # Collect results
            for future in as_completed(futures):
                source, dest = futures[future]
                try:
                    success = future.result()
                    if success:
                        dest_files.append(dest)
                        self.stats.successful += 1
                    else:
                        self.stats.failed += 1
                except Exception as e:
                    logger.error(f"Unexpected error processing {source.name}: {e}")
                    self.stats.failed += 1
        
        if not dest_files:
            logger.warning("No files successfully encoded in this album")
            return
        
        # Phase 2: Copy standalone cover file (sequential, after encoding)
        if file_pairs:
            source_album_dir = self.scanner.get_album_dir(file_pairs[0][0])
            dest_album_dir = self.scanner.get_album_dir(dest_files[0])
            
            logger.info("Processing cover art...")
            self.cover_manager.handle_cover_file(source_album_dir, dest_album_dir)
        
        # Phase 3: Loudness analysis and tagging (sequential, album-wide)
        logger.info("Analyzing loudness and adding tags...")
        self.loudness_processor.process_album(dest_files)
    
    def _encode_file(self, source: Path, dest: Path) -> bool:
        """Encode a single file with metadata.
        
        Args:
            source: Source FLAC file
            dest: Destination M4A file
            
        Returns:
            True if successful
        """
        try:
            # Encode
            self.encoder.encode(source, dest)
            
            # Copy metadata
            self.metadata_handler.copy_metadata(source, dest)
            
            return True
            
        except EncodingError as e:
            logger.error(f"Encoding failed for {source.name}: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to process {source.name}: {e}")
            return False
    
    def _print_dry_run_report(self, file_pairs: List[tuple]) -> None:
        """Print dry-run report.
        
        Args:
            file_pairs: List of (source, dest) tuples
        """
        logger.info("\n" + "="*60)
        logger.info("DRY RUN - Files to be processed:")
        logger.info("="*60)
        
        for source, dest in file_pairs:
            logger.info(f"  {source}")
            logger.info(f"  -> {dest}\n")
        
        logger.info("="*60)
        logger.info(f"Total: {len(file_pairs)} file(s)")
        logger.info("="*60)
