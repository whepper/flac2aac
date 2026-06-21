# flac2aac

A fast, parallel FLAC-to-AAC converter with full metadata preservation,
cover art handling, ReplayGain 2.0 analysis, and iTunes SoundCheck tagging.

Optionally uses a **working directory** (e.g. a RAM disk) so that all
encoding, tagging, and loudness analysis are done in memory first;
finished albums are then moved to the final output directory in a single
operation — minimising writes to spinning disks, NAS shares, or SSDs.

---

## Features

- Parallel FLAC-to-AAC encoding via FFmpeg + libfdk_aac (VBR 1–5)
- Full FLAC metadata → M4A tag mapping (title, artist, album, year, track, disc, …)
- Extended tag pass-through: BPM, compilation, grouping, MusicBrainz IDs, ISRC, label, catalogue number, barcode
- Sort fields: title sort, artist sort, album sort, album artist sort, composer sort
- Embedded cover art copied to M4A files
- Standalone `cover.jpg` copied/extracted per album
- EBU R128 / ReplayGain 2.0 track & album gain tagging (via **rsgain**)
- iTunes SoundCheck (iTunNORM) tag generation with configurable loudness target
- **Working directory support** — encode to RAM disk, move to output when done
- Recursive directory scanning with mirrored output structure
- Configurable via a single `config.toml` file
- CLI override flags — `--input`, `--output`, `--workers`, `--log-level` for ad-hoc runs without editing the config

---

## Requirements

### Python

- Python 3.9+
- Dependencies: `pip install -r requirements.txt`

### FFmpeg with libfdk_aac

**macOS (Homebrew)**
```bash
brew tap homebrew-ffmpeg/ffmpeg
brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-fdk-aac
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

### rsgain

[rsgain](https://github.com/complexlogic/rsgain) is required for ReplayGain 2.0 analysis and tagging.
It is a compiled system binary, not a Python package.

**macOS (Homebrew)**
```bash
brew install rsgain
```

**Ubuntu / Debian**
```bash
sudo apt install rsgain
```

**Arch Linux**
```bash
sudo pacman -S rsgain
```

**Manual install** — download a pre-built binary from the
[rsgain releases page](https://github.com/complexlogic/rsgain/releases) and
place it on your `PATH`, or set `rsgain_bin` in `config.toml` to the full path.

> If you don't need ReplayGain or iTunNORM tagging, set
> `enable_replaygain = false` and `enable_itunes_soundcheck = false` in
> `[loudness]` and rsgain is not required.

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
rsgain_bin = "rsgain"
```

### With RAM disk working directory

Set `work_dir` to a RAM disk mountpoint. All processing happens in RAM;
only the final `shutil.move` writes to your output storage.

```toml
[paths]
input_dir  = "/music/flac"
output_dir = "/music/aac"
ffmpeg_bin = "ffmpeg"
rsgain_bin = "rsgain"
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

> In the GUI, clicking **Create** makes the RAM disk for you, and
> closing the app auto-ejects it. Uncheck **Auto-eject on exit** in the
> Folders panel if you want the mount to survive between sessions.

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

# Override config values on the command line
python main.py --input /music/flac --output /music/aac
python main.py --workers 8 --log-level DEBUG

# Print version and exit
python main.py --version
```

### CLI flags

| Flag | Overrides | Notes |
|---|---|---|
| `--config PATH` | — | Path to TOML file (default: `config.toml`) |
| `--dry-run` | — | Scan and report, no encoding |
| `--input DIR` | `[paths] input_dir` | |
| `--output DIR` | `[paths] output_dir` | |
| `--workers N` | `[processing] workers` | Must be ≥ 1 |
| `--log-level LEVEL` | `[processing] log_level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `--version` | — | Print version and exit |

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | All files converted successfully |
| `1` | One or more files/albums failed, or a fatal runtime error occurred |
| `2` | Configuration file not found, invalid, or a CLI override was rejected |
| `130` | Interrupted by Ctrl+C |

---

## Configuration Reference

### `[paths]`

| Key | Default | Description |
|---|---|---|
| `input_dir` | *(required)* | FLAC source directory (scanned recursively) |
| `output_dir` | *(required)* | AAC output root directory |
| `ffmpeg_bin` | `"ffmpeg"` | Path to FFmpeg binary |
| `rsgain_bin` | `"rsgain"` | Path to rsgain binary |
| `work_dir` | *(disabled)* | Working directory for intermediate files (RAM disk recommended) |

### `[encoding]`

| Key | Default | Description |
|---|---|---|
| `vbr_quality` | `5` | FDK-AAC VBR quality 1–5 (5 = highest) |
| `output_format` | `"m4a"` | Container: `"m4a"` or `"mp4"` |
| `encode_timeout` | `1800` | Seconds before FFmpeg is killed for a stalled file |

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
| `enable_replaygain` | `true` | Write ReplayGain 2.0 tags via rsgain |
| `enable_itunes_soundcheck` | `true` | Write iTunes SoundCheck (iTunNORM) tag |
| `reference_loudness` | `-18.0` | iTunNORM target in LUFS (does not affect ReplayGain tags, which always target −18 LUFS) |
| `reuse_existing_replaygain` | `false` | Skip rsgain analysis when the source FLAC already has ReplayGain tags |

> **Note on partial re-runs:** with `overwrite_existing = false`, a re-run
> encodes only the tracks missing from the output, and ReplayGain *album*
> gain is then computed over just those newly encoded tracks. If accurate
> album gain matters after a partial conversion, set
> `overwrite_existing = true` for that run (or delete the album's output
> folder) so the whole album is analysed together.

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
  ├─ 4. EBU R128 loudness analysis   (rsgain)
  ├─ 5. Write ReplayGain + iTunNORM  (mutagen)
  └─ 6. Move album → output_dir      (shutil.move — one write per file)
```

---

## macOS GUI App

A standalone double-click `.app` for macOS can be built with PyInstaller.
No Python, FFmpeg, or rsgain installation is required on the target machine —
everything is bundled inside `flac2aac.app`.

### Runtime UI

During a run the GUI shows three signals:

- a **progress bar** that tracks the total number of encoded files.
  It only ever moves right — long-running phases like ReplayGain
  analysis and album moves do not reset the bar to zero.
- an **activity label** below the bar with the current phase
  (`Encoding album 3 / 12 — track 5 / 10`,
  `Analysing loudness — album 3 / 12`, `Moving album to output`).
- a **log** of every pipeline message, capped at the most recent
  5000 lines so a long run cannot grow it without bound.

The RAM disk create/eject buttons use the indeterminate bar style
because their duration is unknown up front.

### 1 — Clone the repository

```bash
git clone https://github.com/whepper/flac2aac.git
cd flac2aac
```

### 2 — Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-gui.txt
```

### 3 — Get FFmpeg with libfdk_aac

Homebrew's default FFmpeg omits libfdk_aac for licensing reasons.
Install it from the community tap (compiles from source, takes a few minutes):

```bash
brew tap homebrew-ffmpeg/ffmpeg
brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-fdk-aac
```

### 4 — Copy FFmpeg into the `vendor/` directory

```bash
mkdir -p vendor
cp "$(brew --prefix homebrew-ffmpeg/ffmpeg/ffmpeg)/bin/ffmpeg" vendor/ffmpeg
xattr -d com.apple.quarantine vendor/ffmpeg 2>/dev/null || true
```

### 5 — Get rsgain and copy it into `vendor/`

```bash
brew install rsgain
mkdir -p vendor
cp "$(brew --prefix rsgain)/bin/rsgain" vendor/rsgain
xattr -d com.apple.quarantine vendor/rsgain 2>/dev/null || true
```

Alternatively, download a pre-built macOS binary directly from the
[rsgain releases page](https://github.com/complexlogic/rsgain/releases).

### 6 — Build the app

```bash
pyinstaller flac2aac_gui.spec
```

The finished app is at `dist/flac2aac.app`. Drag it to `/Applications` or
double-click it directly — no terminal needed.

> **Note:** The `.venv` must be active when running `pyinstaller` so it can
> find all installed packages. If you open a new terminal session, run
> `source .venv/bin/activate` again before building.
>
> **Tkinter not found?** Homebrew Python requires a separate package for the GUI toolkit.
> Install it matching your Python version, then rebuild:
> ```bash
> brew install python-tk@3.14   # adjust to match: python3 --version
> rm -rf dist build && pyinstaller flac2aac_gui.spec
> ```

### Running without building

You can also run the GUI directly from the project directory (no PyInstaller needed):

```bash
source .venv/bin/activate
python gui.py
```

FFmpeg and rsgain must both be on your `PATH` (e.g. installed via Homebrew),
or you can place custom builds at `vendor/ffmpeg` and `vendor/rsgain` and
the GUI will use them automatically.

---

## Note on AAC Patents

This tool uses fdk-aac for encoding. fdk-aac is covered by patents related to
AAC audio encoding. Distributing compiled binaries that include fdk-aac may
require a patent license in some jurisdictions. Use at your own risk.

---

## License

MIT — see [LICENSE](LICENSE).
