"""Loudness analysis and tagging module.

Handles ReplayGain 2.0 (EBU R128) and iTunes SoundCheck tag generation.
"""

import logging
import math
from pathlib import Path
from typing import List, Dict, Iterable, Optional, Tuple

try:
    from mutagen import MutagenError
    from mutagen.flac import FLAC
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
    
    def process_album(
        self,
        m4a_files: List[Path],
        source_pairs: Optional[List[Tuple[Path, Path]]] = None,
    ) -> None:
        """Process loudness for an album.

        Args:
            m4a_files: List of M4A files in the album.
            source_pairs: Optional list of (source_flac, dest_m4a) pairs
                used for the reuse_existing_replaygain fast path.
        """
        if not m4a_files:
            return

        logger.info(f"Processing loudness for {len(m4a_files)} track(s)")

        if self.loudness_config.enable_replaygain:
            reused = False
            if self.loudness_config.reuse_existing_replaygain and source_pairs:
                reused = self._reuse_source_replaygain(source_pairs)
            if not reused:
                if r128gain is None:
                    logger.warning("r128gain not available, skipping ReplayGain")
                else:
                    self._add_replaygain_tags(m4a_files)

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

    # Vorbis keys carrying existing ReplayGain data on a source FLAC,
    # mapped to the MP4 freeform atom we write on the destination.
    _SOURCE_REPLAYGAIN_ATOMS = {
        'replaygain_track_gain': '----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN',
        'replaygain_track_peak': '----:com.apple.iTunes:REPLAYGAIN_TRACK_PEAK',
        'replaygain_album_gain': '----:com.apple.iTunes:REPLAYGAIN_ALBUM_GAIN',
        'replaygain_album_peak': '----:com.apple.iTunes:REPLAYGAIN_ALBUM_PEAK',
    }

    def _reuse_source_replaygain(
        self, pairs: Iterable[Tuple[Path, Path]]
    ) -> bool:
        """Copy pre-computed ReplayGain tags from each source FLAC.

        Runs only when every source carries at least a
        ``replaygain_track_gain`` value — partial coverage falls back
        to a full r128 pass for predictability.

        Returns:
            True when RG tags were reused for the whole album, False
            when the caller should fall back to r128gain.
        """
        pair_list = list(pairs)
        extracted: List[Tuple[Path, Dict[str, str]]] = []
        for source, dest in pair_list:
            try:
                flac = FLAC(source)
            except (MutagenError, OSError) as e:
                logger.debug(
                    f"Cannot reuse RG, failed to read {source.name}: {e}"
                )
                return False
            if 'replaygain_track_gain' not in flac:
                logger.debug(
                    f"Cannot reuse RG, {source.name} has no replaygain_track_gain"
                )
                return False
            tags: Dict[str, str] = {}
            for vorbis_key, atom in self._SOURCE_REPLAYGAIN_ATOMS.items():
                values = flac.get(vorbis_key)
                if values:
                    tags[atom] = str(values[0])
            extracted.append((dest, tags))

        for dest, tags in extracted:
            try:
                m4a = MP4(dest)
                for atom, value in tags.items():
                    m4a[atom] = [MP4FreeForm(value.encode('utf-8'))]
                m4a.save()
            except (MutagenError, OSError) as e:
                logger.error(
                    f"Failed to copy ReplayGain tags to {dest.name}: {e}"
                )
                return False

        logger.info(
            f"Reused existing ReplayGain tags for {len(extracted)} track(s)"
        )
        return True

    def _has_replaygain(self, m4a_file: Path) -> bool:
        """Return True if the M4A file has a ReplayGain track-gain tag."""
        try:
            m4a = MP4(m4a_file)
        except (MutagenError, OSError) as e:
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

                if rg_gain is None:
                    logger.warning(
                        f"No ReplayGain data found for {m4a_file.name}, "
                        "skipping iTunNORM"
                    )
                    continue

                itunnorm = self._replaygain_to_soundcheck(rg_gain)
                itunnorm_key = '----:com.apple.iTunes:iTunNORM'
                m4a[itunnorm_key] = [MP4FreeForm(itunnorm.encode('utf-8'))]
                m4a.save()
                logger.info(f"Added iTunNORM to {m4a_file.name} (gain: {rg_gain} dB)")

            except (MutagenError, OSError) as e:
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
        """Convert ReplayGain dB to iTunes SoundCheck (iTunNORM) format.

        The string format expected by iTunes/Music.app:
        a leading space, then ten uppercase 8-character hex values
        separated by single spaces. Slots 1-2 encode the gain
        relative to a 1/1000 W reference, slots 3-4 relative to a
        2.5e-7 W reference (× 2500), and slots 5-10 carry
        standard filler values that iTunes itself writes. The
        per-slot field is 32-bit, so values clamp at 0xFFFFFFFE.

        This matches what Mp3tag and foobar2000 produce when
        converting ReplayGain → iTunNORM.

        Args:
            gain_db: ReplayGain gain in dB.

        Returns:
            iTunNORM hex string (with leading space).
        """
        ratio = 10 ** (-gain_db / 10.0)
        sc_1000 = max(0, min(int(round(ratio * 1000)), 0xFFFFFFFE))
        sc_2500 = max(0, min(int(round(ratio * 2500)), 0xFFFFFFFE))
        return (
            f" {sc_1000:08X} {sc_1000:08X}"
            f" {sc_2500:08X} {sc_2500:08X}"
            f" 00024CA8 00024CA8 00007FFF 00007FFF 00024CA8 00024CA8"
        )
