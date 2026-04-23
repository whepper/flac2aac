"""Loudness analysis and tagging module.

Handles ReplayGain 2.0 (EBU R128) and iTunes SoundCheck tag generation.
"""

import logging
import math
from pathlib import Path
from typing import List, Dict, Optional

try:
    from mutagen.mp4 import MP4, MP4FreeForm
except ImportError:
    raise ImportError(
        "mutagen package required. Install with: pip install mutagen"
    )

try:
    import r128gain
except ImportError:
    r128gain = None
    logging.warning(
        "r128gain not installed. ReplayGain tagging disabled. "
        "Install with: pip install r128gain"
    )

from config import Config

logger = logging.getLogger(__name__)


class LoudnessProcessor:
    """Handles loudness analysis and tag writing."""
    
    def __init__(self, config: Config):
        """Initialize loudness processor.
        
        Args:
            config: Application configuration
        """
        self.config = config
        self.loudness_config = config.loudness
        self.reference = self.loudness_config.reference_loudness
    
    def process_album(self, m4a_files: List[Path]) -> None:
        """Process loudness for an album.
        
        Args:
            m4a_files: List of M4A files in the album
        """
        if not m4a_files:
            return
        
        logger.info(f"Processing loudness for {len(m4a_files)} track(s)")
        
        # Calculate ReplayGain if enabled
        if self.loudness_config.enable_replaygain:
            if r128gain is None:
                logger.warning("r128gain not available, skipping ReplayGain")
            else:
                self._add_replaygain_tags(m4a_files)
        
        # Add iTunes SoundCheck if enabled
        if self.loudness_config.enable_itunes_soundcheck:
            self._add_itunes_soundcheck(m4a_files)
    
    # Possible locations r128gain (and earlier versions) may store ReplayGain
    # track gain under. Used both for iTunNORM lookup and for post-processing
    # verification that tags were actually written.
    _REPLAYGAIN_KEYS = (
        '----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN',
        '----:com.apple.iTunes:replaygain_track_gain',
        'REPLAYGAIN_TRACK_GAIN',
        'replaygain_track_gain',
    )

    def _add_replaygain_tags(self, m4a_files: List[Path]) -> None:
        """Add ReplayGain 2.0 tags using r128gain.

        Note: r128gain uses a fixed reference of -18 LUFS (ReplayGain 2.0 standard).
        The target_loudness config option is only used for iTunNORM calculation.

        Args:
            m4a_files: List of M4A files
        """
        file_paths = [str(f) for f in m4a_files]
        logger.debug(f"Running R128 analysis on {len(file_paths)} file(s)")

        try:
            # r128gain uses fixed -18 LUFS reference (ReplayGain 2.0 standard)
            # It does not accept target_loudness parameter
            r128gain.process(
                file_paths,
                album_gain=True,
                skip_tagged=False,
                opus_output_gain=False
            )
        except Exception as e:
            logger.error(f"Failed to add ReplayGain tags: {e}")
            return

        # r128gain swallows many errors internally and returns without raising,
        # so verify tags were actually written by re-reading the files.
        missing = [f for f in m4a_files if not self._has_replaygain(f)]
        if missing:
            logger.error(
                f"ReplayGain tags missing on {len(missing)}/{len(m4a_files)} "
                f"file(s) after r128gain run; first offender: {missing[0].name}"
            )
        else:
            logger.info(f"Added ReplayGain tags to {len(m4a_files)} file(s)")

    def _has_replaygain(self, m4a_file: Path) -> bool:
        """Return True if the M4A file has a ReplayGain track-gain tag."""
        try:
            m4a = MP4(m4a_file)
        except Exception as e:  # mutagen raises various subclasses of MutagenError
            logger.warning(f"Could not read {m4a_file.name} to verify tags: {e}")
            return False
        return any(key in m4a for key in self._REPLAYGAIN_KEYS)
    
    def _add_itunes_soundcheck(self, m4a_files: List[Path]) -> None:
        """Add iTunes SoundCheck (iTunNORM) tags.
        
        Args:
            m4a_files: List of M4A files
        """
        for m4a_file in m4a_files:
            try:
                m4a = MP4(m4a_file)
                
                logger.debug(f"Available keys in {m4a_file.name}: {list(m4a.keys())}")

                rg_gain = None
                for key in self._REPLAYGAIN_KEYS:
                    if key in m4a:
                        rg_gain = self._get_replaygain_value(m4a, key)
                        if rg_gain is not None:
                            logger.debug(f"Found ReplayGain at key '{key}': {rg_gain} dB")
                            break
                
                if rg_gain is not None:
                    # Convert to iTunNORM format
                    itunnorm = self._replaygain_to_soundcheck(rg_gain)
                    
                    # Write iTunNORM tag as MP4FreeForm
                    itunnorm_key = '----:com.apple.iTunes:iTunNORM'
                    m4a[itunnorm_key] = [MP4FreeForm(itunnorm.encode('utf-8'))]
                    m4a.save()
                    
                    logger.info(f"Added iTunNORM to {m4a_file.name} (gain: {rg_gain} dB)")
                else:
                    logger.warning(
                        f"No ReplayGain data found for {m4a_file.name}, "
                        "skipping iTunNORM"
                    )
            
            except Exception as e:
                logger.error(f"Failed to add iTunNORM to {m4a_file.name}: {e}")
    
    def _get_replaygain_value(self, m4a: MP4, key: str) -> Optional[float]:
        """Extract ReplayGain value from M4A freeform tags.
        
        r128gain writes ReplayGain tags as MP4FreeForm objects.
        
        Args:
            m4a: MP4 file object
            key: ReplayGain tag key
            
        Returns:
            Gain value in dB or None
        """
        if key not in m4a:
            return None
        
        try:
            # r128gain stores values as MP4FreeForm bytes
            value = m4a[key][0]
            
            # MP4FreeForm objects store data as bytes
            if isinstance(value, MP4FreeForm):
                value_bytes = bytes(value)
            elif isinstance(value, bytes):
                value_bytes = value
            else:
                value_bytes = str(value).encode('utf-8')
            
            # Decode to string
            value_str = value_bytes.decode('utf-8').strip()
            
            # Parse "+X.XX dB" or "-X.XX dB" format
            # r128gain format example: "-7.23 dB"
            gain_str = value_str.replace(' dB', '').replace('dB', '').strip()
            return float(gain_str)
        
        except (ValueError, IndexError, AttributeError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to parse ReplayGain value from {key}: {e}")
            return None
    
    def _replaygain_to_soundcheck(self, gain_db: float) -> str:
        """Convert ReplayGain dB to iTunes SoundCheck hex format.
        
        Based on the algorithm from:
        https://gist.github.com/daveisadork/4717535
        
        Args:
            gain_db: ReplayGain gain in dB
            
        Returns:
            iTunNORM hex string
        """
        # Convert dB to linear scale (inverse of 10^(gain/10))
        # SoundCheck uses milliwatt reference
        linear_gain = 10 ** (-gain_db / 10.0)
        
        # Scale to SoundCheck range and clamp
        sc_value = int(round(linear_gain * 1000))
        sc_value = min(sc_value, 65534)  # Max value
        
        # Format as 8-character hex (padded with spaces)
        hex_value = f"{sc_value:08X}"
        
        # iTunNORM format: 10 hex values (5 stereo pairs)
        # Format: [left] [right] [?] [?] [?] [?] [?] [?] [left] [right]
        # We use the same value for left and right channels
        itunnorm_parts = [
            hex_value,  # Left
            hex_value,  # Right
            "00000000",  # Unknown/reserved
            "00000000",
            "00000000",
            "00000000",
            "00000000",
            "00000000",
            hex_value,  # Left (repeated)
            hex_value   # Right (repeated)
        ]
        
        return ' '.join(itunnorm_parts)
