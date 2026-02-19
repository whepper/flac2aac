"""Configuration module for FLAC to AAC converter.

Loads and validates TOML configuration files.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        raise ImportError(
            "Python < 3.11 requires 'tomli' package. Install with: pip install tomli"
        )


@dataclass
class PathsConfig:
    """Path configuration."""
    input_dir: Path
    output_dir: Path
    ffmpeg_bin: str = "ffmpeg"
    
    def __post_init__(self):
        self.input_dir = Path(self.input_dir).expanduser().resolve()
        self.output_dir = Path(self.output_dir).expanduser().resolve()


@dataclass
class EncodingConfig:
    """Encoding configuration."""
    vbr_quality: int = 5
    output_format: str = "m4a"
    
    def __post_init__(self):
        if not 1 <= self.vbr_quality <= 5:
            raise ValueError("vbr_quality must be between 1 and 5")
        if self.output_format not in ["m4a", "mp4"]:
            raise ValueError("output_format must be 'm4a' or 'mp4'")


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
        if not 1 <= self.jpeg_quality <= 95:
            raise ValueError("jpeg_quality must be between 1 and 95")


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
        if not -30.0 <= self.reference_loudness <= 0.0:
            raise ValueError("reference_loudness must be between -30.0 and 0.0 LUFS")


@dataclass
class ProcessingConfig:
    """Processing configuration."""
    workers: int = 4
    overwrite_existing: bool = False
    log_level: str = "INFO"
    
    def __post_init__(self):
        if self.workers < 1:
            raise ValueError("workers must be >= 1")
        if self.log_level.upper() not in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            raise ValueError("log_level must be DEBUG, INFO, WARNING, or ERROR")
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
    
    metadata_data = data.get('metadata', {})
    metadata_data['cover_file'] = cover_file
    
    return Config(
        paths=PathsConfig(**data['paths']),
        encoding=EncodingConfig(**data.get('encoding', {})),
        metadata=MetadataConfig(**metadata_data),
        loudness=LoudnessConfig(**data.get('loudness', {})),
        processing=ProcessingConfig(**data.get('processing', {}))
    )
