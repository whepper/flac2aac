"""Audio encoding module using FFmpeg and libfdk_aac.

Handles FLAC to AAC conversion at VBR quality 5.
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)


class EncodingError(Exception):
    """Exception raised when encoding fails."""
    pass


class Encoder:
    """FFmpeg wrapper for FLAC to AAC encoding."""
    
    def __init__(self, config: Config):
        """Initialize encoder.
        
        Args:
            config: Application configuration
        """
        self.config = config
        self.ffmpeg_bin = config.paths.ffmpeg_bin
        self.vbr_quality = config.encoding.vbr_quality
    
    def encode(self, source: Path, destination: Path) -> None:
        """Encode FLAC file to AAC.
        
        Args:
            source: Input FLAC file path
            destination: Output M4A file path
            
        Raises:
            EncodingError: If encoding fails
        """
        # Create output directory if needed
        destination.parent.mkdir(parents=True, exist_ok=True)
        
        # Build FFmpeg command
        # Add explicit profile to ensure VBR compatibility and suppress warning note
        cmd = [
            self.ffmpeg_bin,
            '-i', str(source),
            '-c:a', 'libfdk_aac',
            '-profile:a', 'aac_low',  # Ensures VBR works with standard parameters
            '-vbr', str(self.vbr_quality),
            '-y',  # Overwrite output file
            str(destination)
        ]
        
        logger.debug(f"Encoding: {source.name} -> {destination.name}")
        logger.debug(f"Command: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"Encoded: {source.name}")
            
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to encode {source.name}: {e.stderr}"
            logger.error(error_msg)
            raise EncodingError(error_msg) from e
        except FileNotFoundError:
            error_msg = f"FFmpeg binary not found: {self.ffmpeg_bin}"
            logger.error(error_msg)
            raise EncodingError(error_msg)
    
    def verify_ffmpeg(self) -> bool:
        """Verify FFmpeg is available with libfdk_aac support.
        
        Returns:
            True if FFmpeg with libfdk_aac is available
        """
        try:
            result = subprocess.run(
                [self.ffmpeg_bin, '-codecs'],
                capture_output=True,
                text=True,
                check=True
            )
            
            has_libfdk_aac = 'libfdk_aac' in result.stdout
            
            if not has_libfdk_aac:
                logger.error(
                    "FFmpeg found but libfdk_aac codec is not available. "
                    "Please install FFmpeg with libfdk_aac support."
                )
            
            return has_libfdk_aac
            
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.error(f"FFmpeg not found at: {self.ffmpeg_bin}")
            return False
