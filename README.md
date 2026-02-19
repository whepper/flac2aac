# flac2aac

A Python application that converts FLAC audio files to AAC format using the high-quality FDK-AAC encoder. The converter preserves all metadata, maintains directory structure, and adds both ReplayGain 2.0 and iTunes SoundCheck loudness tags.

## Features

- **High-Quality Encoding**: Uses FDK-AAC VBR mode 5 (highest quality, ~256 kbps)
- **Complete Metadata Preservation**: Copies all tags from FLAC to M4A format
- **Cover Art Handling**:
  - Embeds cover art in M4A files
  - Copies/extracts standalone cover files (cover.jpg, folder.jpg)
  - Optional resizing and PNG to JPEG conversion
- **Loudness Normalization**:
  - ReplayGain 2.0 (EBU R128) track and album tags
  - iTunes SoundCheck (iTunNORM) tag generation
- **Directory Structure**: Mirrors source folder hierarchy
- **Parallel Processing**: Multi-threaded encoding for speed
- **Configurable**: All settings in a well-documented TOML file
- **Modular Design**: Clean, maintainable codebase

## Requirements

### System Dependencies

- **Python 3.9+** (3.11+ recommended for built-in TOML support)
- **FFmpeg** with **libfdk_aac** codec support

### Installing FFmpeg with libfdk_aac

**macOS (Homebrew):**
```bash
brew install ffmpeg --with-fdk-aac
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ffmpeg
```

**Windows:**
Download FFmpeg builds with libfdk_aac from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) or compile from source.

**Verify libfdk_aac support:**
```bash
ffmpeg -codecs | grep libfdk_aac
```

### Python Dependencies

Install required packages:

```bash
pip install -r requirements.txt
```

Core dependencies:
- `mutagen` - Audio metadata handling
- `r128gain` - ReplayGain 2.0 / EBU R128 analysis
- `Pillow` - Image processing (optional)
- `tomli` - TOML parsing (Python < 3.11)

## Installation

1. Clone the repository:
```bash
git clone https://github.com/whepper/flac2aac.git
cd flac2aac
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Copy and edit configuration:
```bash
cp config.toml my_config.toml
# Edit my_config.toml with your paths and preferences
```

## Configuration

All settings are configured in `config.toml`. Key sections:

### Paths
```toml
[paths]
input_dir = "/music/flac"     # Source FLAC directory
output_dir = "/music/aac"     # Destination AAC directory
ffmpeg_bin = "ffmpeg"         # FFmpeg binary path
```

### Encoding
```toml
[encoding]
vbr_quality = 5               # FDK-AAC VBR (1-5, 5=highest)
output_format = "m4a"         # Container format
```

### Metadata & Cover Art
```toml
[metadata]
copy_artwork = true

[metadata.cover_file]
enabled = true
search_names = ["cover.jpg", "folder.jpg", "front.jpg"]
fallback_name = "cover.jpg"
max_size = 2000               # Resize to max dimension (0=disable)
jpeg_quality = 95
```

### Loudness
```toml
[loudness]
enable_replaygain = true
enable_itunes_soundcheck = true
reference_loudness = -18.0    # LUFS target
```

### Processing
```toml
[processing]
workers = 4                   # Parallel encoding threads
overwrite_existing = false    # Skip existing files
log_level = "INFO"
```

## Usage

### Basic Usage

```bash
python main.py
```

This uses `config.toml` in the current directory.

### Custom Configuration

```bash
python main.py --config /path/to/my_config.toml
```

### Dry Run

See what would be processed without actually encoding:

```bash
python main.py --dry-run
```

### Help

```bash
python main.py --help
```

## Module Overview

- **`main.py`** - Entry point and CLI interface
- **`config.py`** - Configuration loading and validation
- **`scanner.py`** - File discovery and path mapping
- **`encoder.py`** - FFmpeg wrapper for FLAC→AAC encoding
- **`metadata.py`** - Tag mapping and cover art handling
- **`loudness.py`** - ReplayGain and iTunes SoundCheck tagging
- **`pipeline.py`** - Orchestrates the complete workflow

## Processing Workflow

1. **Scan** - Discover all FLAC files recursively
2. **Group** - Organize files by album directory
3. **Encode** (parallel) - Convert FLAC to AAC with metadata
4. **Cover Art** - Copy/extract standalone cover files
5. **Loudness** - Analyze and tag with ReplayGain + iTunNORM

## Tag Mapping

| FLAC (Vorbis Comment) | M4A (MP4 Atom) |
|---|---|
| TITLE | ©nam |
| ARTIST | ©ART |
| ALBUMARTIST | aART |
| ALBUM | ©alb |
| DATE / YEAR | ©day |
| TRACKNUMBER | trkn |
| DISCNUMBER | disk |
| GENRE | ©gen |
| COMMENT | ©cmt |
| COMPOSER | ©wrt |
| Cover Art | covr |

Additional tags added:
- `REPLAYGAIN_TRACK_GAIN` / `REPLAYGAIN_TRACK_PEAK`
- `REPLAYGAIN_ALBUM_GAIN` / `REPLAYGAIN_ALBUM_PEAK`
- `iTunNORM` (iTunes SoundCheck)

## Example Output

```
2026-02-19 19:42:15 - __main__ - INFO - FLAC to AAC Converter starting
2026-02-19 19:42:15 - __main__ - INFO - Input: /music/flac
2026-02-19 19:42:15 - __main__ - INFO - Output: /music/aac
2026-02-19 19:42:15 - scanner - INFO - Found 47 FLAC file(s)
2026-02-19 19:42:15 - pipeline - INFO - Organized into 5 album(s)

2026-02-19 19:42:15 - pipeline - INFO - Processing album: /music/flac/Artist/Album
2026-02-19 19:42:15 - pipeline - INFO - Encoding 10 track(s)...
2026-02-19 19:42:47 - pipeline - INFO - Processing cover art...
2026-02-19 19:42:47 - pipeline - INFO - Analyzing loudness and adding tags...

============================================================
Conversion Summary
============================================================
Total files processed: 47
Successful: 47
Failed: 0
Skipped: 0
Albums processed: 5
============================================================
```

## Troubleshooting

### FFmpeg not found

Error: `FFmpeg binary not found: ffmpeg`

**Solution**: Install FFmpeg or provide full path in `config.toml`:
```toml
ffmpeg_bin = "/usr/local/bin/ffmpeg"
```

### libfdk_aac not available

Error: `FFmpeg found but libfdk_aac codec is not available`

**Solution**: Reinstall FFmpeg with libfdk_aac support (see Requirements)

### r128gain warnings

Warning: `r128gain not installed. ReplayGain tagging disabled.`

**Solution**: Install r128gain:
```bash
pip install r128gain
```

### Pillow warnings

Warning: `Pillow not installed. PNG to JPEG conversion disabled.`

**Solution**: Install Pillow:
```bash
pip install Pillow
```

## Performance Tips

- Set `workers` to match your CPU core count
- Use SSD storage for both input and output
- Disable `overwrite_existing` for incremental conversions
- Set `log_level = "WARNING"` for faster processing (less I/O)

## License

MIT License - see LICENSE file for details

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## Author

Created by Jeroen (whepper)

## Acknowledgments

- FFmpeg project and libfdk_aac developers
- mutagen audio metadata library
- r128gain for EBU R128 implementation
- Community feedback and testing
