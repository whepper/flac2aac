"""File discovery module for FLAC to AAC converter.

Scans input directory for FLAC files and generates output paths.
"""

import logging
from pathlib import Path
from typing import Iterator, List, Set, Tuple

from config import Config

logger = logging.getLogger(__name__)


class Scanner:
    """Discovers FLAC files and maps them to output paths."""
    
    def __init__(self, config: Config):
        """Initialize scanner.
        
        Args:
            config: Application configuration
        """
        self.config = config
        self.input_dir = config.paths.input_dir
        self.output_dir = config.paths.output_dir
        self.output_ext = f".{config.encoding.output_format}"
    
    def scan(self) -> Iterator[Tuple[Path, Path]]:
        """Recursively scan for FLAC files.
        
        Yields:
            Tuples of (source_path, destination_path)
        """
        if not self.input_dir.exists():
            logger.error(f"Input directory does not exist: {self.input_dir}")
            return
        
        if not self.input_dir.is_dir():
            logger.error(f"Input path is not a directory: {self.input_dir}")
            return
        
        logger.info(f"Scanning for FLAC files in: {self.input_dir}")

        # Case-insensitive walk, deduplicated by resolved path so that
        # case-insensitive filesystems (macOS default, Windows) don't
        # yield the same file twice.
        seen: Set[Path] = set()
        flac_files: List[Path] = []
        for path in self.input_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() != ".flac":
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            flac_files.append(path)

        logger.info(f"Found {len(flac_files)} FLAC file(s)")
        
        for source_path in flac_files:
            dest_path = self._get_destination_path(source_path)
            
            # Skip if exists and overwrite disabled
            if dest_path.exists() and not self.config.processing.overwrite_existing:
                logger.debug(f"Skipping existing file: {dest_path}")
                continue
            
            yield source_path, dest_path
    
    def _get_destination_path(self, source_path: Path) -> Path:
        """Generate output path mirroring input structure.
        
        Args:
            source_path: Input FLAC file path
            
        Returns:
            Output M4A file path
        """
        # Get relative path from input root
        relative_path = source_path.relative_to(self.input_dir)
        
        # Replace extension
        output_relative = relative_path.with_suffix(self.output_ext)
        
        # Construct full output path
        return self.output_dir / output_relative
