# flac2aac

A fast, parallel FLAC-to-AAC converter with full metadata preservation,
cover art handling, ReplayGain 2.0 analysis, and iTunes SoundCheck tagging.

Optionally uses a **working directory** (e.g. a RAM disk) so that all
encoding, tagging, and loudness analysis are done in memory first;
finished albums are then moved to the final output directory in a single
operation — minimising writes to spinning disks, NAS shares, or SSDs.

---

## Features

- Parallel FLAC-to-AAC encoding via FFmpeg + libfdk_aac (VBR 1-5)
- Full FLAC metadata → M4A tag mapping (title, artist, album, year, track, disc, …)
- Embedded cover art copied to M4A files
- Standalone `cover.jpg` copied/extracted per album
- EBU R128 / ReplayGain 2.0 track & album gain tagging (via r128gain)
- iTunes SoundCheck (iTunNORM) tag generation
- **Working directory support** — encode to RAM disk, move to output when done
- Recursive directory scanning with mirrored output structure
- Configurable via a single `config.toml` file

---

## Requirements

### Python

- Python 3.9+
- Dependencies: `pip install -r requirements.txt`

### FFmpeg with libfdk_aac

**macOS (Homebrew)**
```bash
brew install ffmpeg
```

**Ubuntu / Debian**

libfdk_aac is non-free and must be compiled from source or sourced from a
third-party repo:
```bash
# Option 1: nonfree PPA (Ubuntu)
sudo add-apt-repository ppa:savoury1/ffmpeg4
sudo apt update && sudo apt install ffmpeg

# Option 2: compile FFmpeg + fdk-aac from source (see EXAMPLES.md)
```

**Arch Linux**
```bash
yay -S ffmpeg-libfdk_aac
```

---

## Installation

```bash
git clone https://github.com/whepper/flac2aac.git
cd flac2aac
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Configuration

Copy and edit `config.toml`:

```bash
cp config.toml my_config.toml
```

### Minimal configuration

```toml
[paths]
input_dir  = "/music/flac"
output_dir = "/music/aac"
ffmpeg_bin = "ffmpeg"
```

### With RAM disk working directory

Set `work_dir` to a RAM disk mountpoint. All processing happens in RAM;
only the final `shutil.move` writes to your output storage.

```toml
[paths]
input_dir  = "/music/flac"
output_dir = "/music/aac"
ffmpeg_bin = "ffmpeg"
work_dir   = "/Volumes/RAMDisk"   # macOS example
# work_dir = "/mnt/ramdisk"       # Linux example
```

Leave `work_dir` commented out (or omit it) to write directly to
`output_dir` as before.

> **RAM disk sizing**: the working directory only needs to hold **one
> album at a time**. ~500 MB is sufficient for typical albums at VBR 5.

#### Creating a RAM disk

**macOS** — create a 1 GB RAM disk:
```bash
diskutil erasevolume HFS+ RAMDisk $(hdiutil attach -nomount ram://2097152)
# Mountpoint: /Volumes/RAMDisk
# Remove when done:
# hdiutil detach /Volumes/RAMDisk
```

**Linux** — mount a tmpfs:
```bash
sudo mkdir -p /mnt/ramdisk
sudo mount -t tmpfs -o size=1G tmpfs /mnt/ramdisk
# Persist across reboots by adding to /etc/fstab:
# tmpfs  /mnt/ramdisk  tmpfs  defaults,size=1G  0  0
```

---

## Usage

```bash
# Default config
python main.py

# Custom config file
python main.py --config my_config.toml

# Dry run — scan and report without encoding
python main.py --dry-run
```

---

## Configuration Reference

### `[paths]`

| Key | Default | Description |
|---|---|---|
| `input_dir` | *(required)* | FLAC source directory (scanned recursively) |
| `output_dir` | *(required)* | AAC output root directory |
| `ffmpeg_bin` | `"ffmpeg"` | Path to FFmpeg binary |
| `work_dir` | *(disabled)* | Working directory for intermediate files (RAM disk recommended) |

### `[encoding]`

| Key | Default | Description |
|---|---|---|
| `vbr_quality` | `5` | FDK-AAC VBR quality 1–5 (5 = highest) |
| `output_format` | `"m4a"` | Container: `"m4a"` or `"mp4"` |

### `[metadata]`

| Key | Default | Description |
|---|---|---|
| `copy_artwork` | `true` | Embed cover art in M4A files |
| `cover_file.enabled` | `true` | Copy standalone cover file per album |
| `cover_file.search_names` | `["cover.jpg", …]` | Cover filenames to look for |
| `cover_file.max_size` | `2000` | Max cover dimension in pixels (0 = no resize) |
| `cover_file.jpeg_quality` | `95` | JPEG quality for resized covers |

### `[loudness]`

| Key | Default | Description |
|---|---|---|
| `enable_replaygain` | `true` | Write ReplayGain 2.0 tags |
| `enable_itunes_soundcheck` | `true` | Write iTunes SoundCheck (iTunNORM) tag |
| `reference_loudness` | `-18.0` | Target in LUFS (informational; r128gain uses −18 LUFS fixed) |

### `[processing]`

| Key | Default | Description |
|---|---|---|
| `workers` | `4` | Parallel encoding threads per album |
| `overwrite_existing` | `false` | Re-encode files that already exist in output |
| `log_level` | `"INFO"` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

---

## Output structure

The input directory tree is mirrored exactly:

```
input_dir/
  Artist/
    Artist - Album/
      01 - Track.flac
      cover.jpg

output_dir/
  Artist/
    Artist - Album/
      01 - Track.m4a   ← encoded + fully tagged
      cover.jpg         ← copied standalone cover
```

---

## Pipeline overview

```
For each album:
  ┌─ work_dir/album/    (RAM disk)    ─── or ─── output_dir/album/
  │
  ├─ 1. Encode FLAC → M4A            (parallel, FFmpeg + libfdk_aac)
  ├─ 2. Copy FLAC metadata → M4A     (mutagen)
  ├─ 3. Copy / extract cover art     (Pillow)
  ├─ 4. EBU R128 loudness analysis   (r128gain)
  ├─ 5. Write ReplayGain + iTunNORM  (mutagen)
  └─ 6. Move album → output_dir      (shutil.move — one write per file)
```

---

## License

MIT — see [LICENSE](LICENSE).
