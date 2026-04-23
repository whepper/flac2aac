"""Audio encoding module using FFmpeg and libfdk_aac.

Handles FLAC to AAC conversion at VBR quality 5.
"""

import logging
import re
import shlex
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
        self.encode_timeout = config.encoding.encode_timeout
    
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
        # -vn: Skip video streams (embedded cover art will be handled by metadata.py)
        # -profile:a aac_low: Ensures VBR compatibility
        cmd = [
            self.ffmpeg_bin,
            '-i', str(source),
            '-vn',  # Ignore video/image streams (cover art)
            '-c:a', 'libfdk_aac',
            '-profile:a', 'aac_low',
            '-vbr', str(self.vbr_quality),
            '-y',  # Overwrite output file
            str(destination)
        ]
        
        logger.debug(f"Encoding: {source.name} -> {destination.name}")
        logger.debug(f"Command: {shlex.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=self.encode_timeout,
            )
            logger.info(f"Encoded: {source.name}")

        except subprocess.TimeoutExpired as e:
            # Remove any partial output ffmpeg may have left behind.
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            error_msg = (
                f"Encoding timed out after {self.encode_timeout}s: {source.name}"
            )
            logger.error(error_msg)
            raise EncodingError(error_msg) from e
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

        Queries ``ffmpeg -encoders`` and matches ``libfdk_aac`` in the
        encoder-name column so that an unrelated mention of the string
        (e.g. in another encoder's description) cannot produce a false
        positive.

        Returns:
            True if FFmpeg with libfdk_aac is available.
        """
        try:
            result = subprocess.run(
                [self.ffmpeg_bin, '-encoders'],
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.error(f"FFmpeg not found at: {self.ffmpeg_bin}")
            return False

        # ffmpeg -encoders prints lines like:
        #   " A....D libfdk_aac           Fraunhofer FDK AAC (codec aac)"
        # The encoder name sits in the second column after the flags.
        pattern = re.compile(r'^\s*[A-Z.]+\s+libfdk_aac\b', re.MULTILINE)
        if pattern.search(result.stdout):
            return True

        logger.error(
            "FFmpeg found but libfdk_aac encoder is not available. "
            "Please install FFmpeg with libfdk_aac support."
        )
        return False
