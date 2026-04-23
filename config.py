"""Configuration module for FLAC to AAC converter.

Loads and validates TOML configuration files.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        raise ImportError(
            "Python < 3.11 requires 'tomli' package. Install with: pip install tomli"
        )


# Validation bounds. Kept as module-level constants so the limits are
# discoverable and adjustable in one place.
VBR_QUALITY_MIN = 1
VBR_QUALITY_MAX = 5
JPEG_QUALITY_MIN = 1
JPEG_QUALITY_MAX = 95
REFERENCE_LUFS_MIN = -30.0
REFERENCE_LUFS_MAX = 0.0
VALID_OUTPUT_FORMATS = ("m4a", "mp4")
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass
class PathsConfig:
    """Path configuration."""
    input_dir: Path
    output_dir: Path
    ffmpeg_bin: str = "ffmpeg"
    work_dir: Optional[Path] = None

    def __post_init__(self):
        self.input_dir = Path(self.input_dir).expanduser().resolve()
        self.output_dir = Path(self.output_dir).expanduser().resolve()
        if self.work_dir is not None:
            self.work_dir = Path(self.work_dir).expanduser().resolve()


@dataclass
class EncodingConfig:
    """Encoding configuration."""
    vbr_quality: int = 5
    output_format: str = "m4a"
    encode_timeout: int = 1800

    def __post_init__(self):
        if not VBR_QUALITY_MIN <= self.vbr_quality <= VBR_QUALITY_MAX:
            raise ValueError(
                f"vbr_quality must be between {VBR_QUALITY_MIN} and {VBR_QUALITY_MAX}"
            )
        if self.output_format not in VALID_OUTPUT_FORMATS:
            raise ValueError(
                f"output_format must be one of {VALID_OUTPUT_FORMATS}"
            )
        if self.encode_timeout <= 0:
            raise ValueError("encode_timeout must be > 0 (seconds)")


@dataclass
class CoverFileConfig:
    """Standalone cover file configuration."""
    enabled: bool = True
    search_names: List[str] = field(default_factory=lambda: [
        "cover.jpg", "folder.jpg", "front.jpg", "Cover.jpg"
    ])
    fallback_name: str = "cover.jpg"
    max_size: int = 2000
    jpeg_quality: int = 95

    def __post_init__(self):
        if self.max_size < 0:
            raise ValueError("max_size must be >= 0")
        if not JPEG_QUALITY_MIN <= self.jpeg_quality <= JPEG_QUALITY_MAX:
            raise ValueError(
                f"jpeg_quality must be between {JPEG_QUALITY_MIN} and {JPEG_QUALITY_MAX}"
            )


@dataclass
class MetadataConfig:
    """Metadata configuration."""
    copy_artwork: bool = True
    cover_file: CoverFileConfig = field(default_factory=CoverFileConfig)


@dataclass
class LoudnessConfig:
    """Loudness tagging configuration."""
    enable_replaygain: bool = True
    enable_itunes_soundcheck: bool = True
    reference_loudness: float = -18.0

    def __post_init__(self):
        if not REFERENCE_LUFS_MIN <= self.reference_loudness <= REFERENCE_LUFS_MAX:
            raise ValueError(
                f"reference_loudness must be between {REFERENCE_LUFS_MIN} "
                f"and {REFERENCE_LUFS_MAX} LUFS"
            )


@dataclass
class ProcessingConfig:
    """Processing configuration."""
    workers: int = 4
    overwrite_existing: bool = False
    log_level: str = "INFO"

    def __post_init__(self):
        if self.workers < 1:
            raise ValueError("workers must be >= 1")
        if self.log_level.upper() not in VALID_LOG_LEVELS:
            raise ValueError(
                f"log_level must be one of {VALID_LOG_LEVELS}"
            )
        self.log_level = self.log_level.upper()


@dataclass
class Config:
    """Main configuration object."""
    paths: PathsConfig
    encoding: EncodingConfig
    metadata: MetadataConfig
    loudness: LoudnessConfig
    processing: ProcessingConfig


def load_config(config_path: Path) -> Config:
    """Load and validate configuration from TOML file.

    Args:
        config_path: Path to config.toml file

    Returns:
        Validated Config object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If configuration is invalid
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, 'rb') as f:
        data = tomllib.load(f)

    # Parse nested structures
    cover_file_data = data.get('metadata', {}).get('cover_file', {})
    cover_file = CoverFileConfig(**cover_file_data)

    metadata_data = {k: v for k, v in data.get('metadata', {}).items() if k != 'cover_file'}
    metadata_data['cover_file'] = cover_file

    return Config(
        paths=PathsConfig(**data['paths']),
        encoding=EncodingConfig(**data.get('encoding', {})),
        metadata=MetadataConfig(**metadata_data),
        loudness=LoudnessConfig(**data.get('loudness', {})),
        processing=ProcessingConfig(**data.get('processing', {}))
    )
