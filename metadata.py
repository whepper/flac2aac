"""Metadata handling module for FLAC and M4A files.

Handles tag mapping, cover art extraction and embedding.
"""

import logging
import shutil
from pathlib import Path
from typing import Optional, Dict, Any

try:
    from mutagen.flac import FLAC, Picture
    from mutagen.mp4 import MP4, MP4Cover
except ImportError:
    raise ImportError(
        "mutagen package required. Install with: pip install mutagen"
    )

try:
    from PIL import Image
except ImportError:
    Image = None
    logging.warning("Pillow not installed. PNG to JPEG conversion disabled.")

from config import Config

logger = logging.getLogger(__name__)


# Vorbis Comment to MP4 atom mapping
TAG_MAPPING = {
    'title': '©nam',
    'artist': '©ART',
    'albumartist': 'aART',
    'album': '©alb',
    'date': '©day',
    'year': '©day',
    'tracknumber': 'trkn',
    'discnumber': 'disk',
    'genre': '©gen',
    'comment': '©cmt',
    'composer': '©wrt',
    'lyrics': '©lyr',
    'copyright': 'cprt',
}


class MetadataHandler:
    """Handles metadata transfer between FLAC and M4A."""
    
    def __init__(self, config: Config):
        """Initialize metadata handler.
        
        Args:
            config: Application configuration
        """
        self.config = config
    
    def copy_metadata(self, source: Path, destination: Path) -> None:
        """Copy metadata from FLAC to M4A.
        
        Args:
            source: Source FLAC file
            destination: Destination M4A file
        """
        try:
            # Read FLAC tags
            flac = FLAC(source)
            
            # Open M4A for writing
            m4a = MP4(destination)
            
            # Copy text tags
            self._copy_text_tags(flac, m4a)
            
            # Copy cover art if enabled
            if self.config.metadata.copy_artwork:
                self._copy_cover_art(flac, m4a)
            
            m4a.save()
            logger.debug(f"Copied metadata: {source.name} -> {destination.name}")
            
        except Exception as e:
            logger.error(f"Failed to copy metadata from {source.name}: {e}")
            raise
    
    def _copy_text_tags(self, flac: FLAC, m4a: MP4) -> None:
        """Copy text tags from FLAC to M4A.
        
        Args:
            flac: Source FLAC object
            m4a: Destination MP4 object
        """
        for vorbis_key, mp4_key in TAG_MAPPING.items():
            values = flac.get(vorbis_key, [])
            if not values:
                continue
            
            # Handle track/disc numbers specially (tuple format)
            if vorbis_key in ['tracknumber', 'discnumber']:
                try:
                    # Parse "track/total" or just "track"
                    parts = str(values[0]).split('/')
                    track_num = int(parts[0])
                    total = int(parts[1]) if len(parts) > 1 else 0
                    m4a[mp4_key] = [(track_num, total)]
                except (ValueError, IndexError):
                    logger.warning(f"Invalid {vorbis_key} format: {values[0]}")
            else:
                # Standard text tags
                m4a[mp4_key] = [str(v) for v in values]
    
    def _copy_cover_art(self, flac: FLAC, m4a: MP4) -> None:
        """Copy embedded cover art from FLAC to M4A.
        
        Args:
            flac: Source FLAC object
            m4a: Destination MP4 object
        """
        if not flac.pictures:
            logger.debug("No embedded cover art found")
            return
        
        # Find front cover (type 3) or use first picture
        cover = None
        for pic in flac.pictures:
            if pic.type == 3:  # Front cover
                cover = pic
                break
        
        if not cover and flac.pictures:
            cover = flac.pictures[0]
        
        if cover:
            # Determine format
            if cover.mime == 'image/jpeg':
                image_format = MP4Cover.FORMAT_JPEG
            elif cover.mime == 'image/png':
                image_format = MP4Cover.FORMAT_PNG
            else:
                logger.warning(f"Unsupported cover format: {cover.mime}")
                return
            
            m4a['covr'] = [MP4Cover(cover.data, imageformat=image_format)]
            logger.debug(f"Copied cover art ({cover.mime})")


class CoverManager:
    """Manages standalone cover art files."""
    
    def __init__(self, config: Config):
        """Initialize cover manager.
        
        Args:
            config: Application configuration
        """
        self.config = config
        self.cover_config = config.metadata.cover_file
    
    def handle_cover_file(self, source_album_dir: Path, dest_album_dir: Path) -> None:
        """Handle standalone cover file for an album.
        
        Args:
            source_album_dir: Source album directory
            dest_album_dir: Destination album directory
        """
        if not self.cover_config.enabled:
            return
        
        # Search for existing cover file
        cover_source = self._find_cover_file(source_album_dir)
        
        if not cover_source:
            # Extract from first FLAC file as fallback
            cover_source = self._extract_cover_from_flac(source_album_dir)
        
        if cover_source:
            self._copy_cover_file(cover_source, dest_album_dir)
    
    def _find_cover_file(self, directory: Path) -> Optional[Path]:
        """Search for cover file in directory.
        
        Args:
            directory: Directory to search
            
        Returns:
            Path to cover file if found
        """
        for filename in self.cover_config.search_names:
            cover_path = directory / filename
            if cover_path.exists():
                logger.debug(f"Found cover file: {cover_path.name}")
                return cover_path
        
        return None
    
    def _extract_cover_from_flac(self, directory: Path) -> Optional[Path]:
        """Extract cover from first FLAC file in directory.
        
        Args:
            directory: Directory containing FLAC files
            
        Returns:
            Path to extracted cover file
        """
        flac_files = list(directory.glob("*.flac")) + list(directory.glob("*.FLAC"))
        
        if not flac_files:
            return None
        
        try:
            flac = FLAC(flac_files[0])
            
            if not flac.pictures:
                return None
            
            # Get front cover
            cover = None
            for pic in flac.pictures:
                if pic.type == 3:
                    cover = pic
                    break
            
            if not cover and flac.pictures:
                cover = flac.pictures[0]
            
            if cover:
                # Save to temp file
                temp_cover = directory / self.cover_config.fallback_name
                with open(temp_cover, 'wb') as f:
                    f.write(cover.data)
                
                logger.debug(f"Extracted cover from {flac_files[0].name}")
                return temp_cover
        
        except Exception as e:
            logger.warning(f"Failed to extract cover: {e}")
        
        return None
    
    def _copy_cover_file(self, source: Path, dest_dir: Path) -> None:
        """Copy and optionally process cover file.
        
        Args:
            source: Source cover file
            dest_dir: Destination directory
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / source.name
        
        # Skip if exists and overwrite disabled
        if dest_path.exists() and not self.config.processing.overwrite_existing:
            logger.debug(f"Cover file already exists: {dest_path.name}")
            return
        
        # Process image if needed
        if Image and self.cover_config.max_size > 0:
            self._process_and_save(source, dest_path)
        else:
            shutil.copy2(source, dest_path)
        
        logger.info(f"Copied cover: {dest_path.name}")
    
    def _process_and_save(self, source: Path, dest: Path) -> None:
        """Process image (resize, convert) and save.
        
        Args:
            source: Source image file
            dest: Destination image file
        """
        try:
            with Image.open(source) as img:
                # Convert RGBA to RGB if needed
                if img.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                    img = background
                
                # Resize if needed
                if self.cover_config.max_size > 0:
                    img.thumbnail(
                        (self.cover_config.max_size, self.cover_config.max_size),
                        Image.Resampling.LANCZOS
                    )
                
                # Save as JPEG
                img.save(
                    dest,
                    'JPEG',
                    quality=self.cover_config.jpeg_quality,
                    optimize=True
                )
        
        except Exception as e:
            logger.warning(f"Failed to process image, copying as-is: {e}")
            shutil.copy2(source, dest)
