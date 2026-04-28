# flac2aac — Examples

## Table of Contents

1. [Quick start](#quick-start)
2. [RAM disk workflow](#ram-disk-workflow)
3. [NAS / external drive workflow](#nas--external-drive-workflow)
4. [Multiple quality profiles](#multiple-quality-profiles)
5. [Ad-hoc overrides](#ad-hoc-overrides)
6. [Linux FFmpeg build with libfdk_aac](#linux-ffmpeg-build-with-libfdk_aac)
7. [Automation with cron / launchd](#automation)

---

## Quick start

```toml
# config.toml — simplest possible setup
[paths]
input_dir  = "/Users/you/Music/FLAC"
output_dir = "/Users/you/Music/AAC"
ffmpeg_bin = "ffmpeg"
```

```bash
python main.py
```

---

## RAM disk workflow

Using a RAM disk as `work_dir` means the entire encode → tag → loudness
analysis cycle happens in RAM. Only the final `shutil.move` per album
writes to your destination storage.

**Benefits:**
- Dramatically faster on HDD or NAS destinations
- Reduces write amplification on SSDs
- Clean output: half-finished albums never appear in `output_dir`
- On failure the RAM disk temp directory is cleaned up automatically

### macOS — 1 GB RAM disk

```bash
# Create
diskutil erasevolume HFS+ RAMDisk $(hdiutil attach -nomount ram://2097152)

# Verify
df -h /Volumes/RAMDisk

# Remove when done
hdiutil detach /Volumes/RAMDisk
```

```toml
[paths]
input_dir  = "/Users/you/Music/FLAC"
output_dir = "/Users/you/Music/AAC"
ffmpeg_bin = "ffmpeg"
work_dir   = "/Volumes/RAMDisk"
```

### Linux — tmpfs RAM disk

```bash
# Create
sudo mkdir -p /mnt/ramdisk
sudo mount -t tmpfs -o size=1G tmpfs /mnt/ramdisk

# Verify
df -h /mnt/ramdisk

# Persist across reboots (/etc/fstab entry):
# tmpfs  /mnt/ramdisk  tmpfs  defaults,size=1G  0  0
```

```toml
[paths]
input_dir  = "/mnt/flac"
output_dir = "/mnt/nas/aac"
ffmpeg_bin = "ffmpeg"
work_dir   = "/mnt/ramdisk"
```

### Sizing the RAM disk

| Album type | Typical AAC size at VBR 5 | Recommended work_dir size |
|---|---|---|
| Standard album (10–15 tracks) | 80–150 MB | 512 MB |
| Long album / live recording | 150–400 MB | 1 GB |
| Large classical set | 400–800 MB | 2 GB |

Only **one album at a time** occupies the working directory, so you
generally do not need to size for your whole library.

---

## NAS / external drive workflow

Writing directly to a NAS or slow external drive causes every encode,
tag edit, and ReplayGain write to go over a slow bus. With `work_dir`
set to a local SSD or RAM disk the NAS only sees the final move:

```toml
[paths]
input_dir  = "/Volumes/NAS/Music/FLAC"
output_dir = "/Volumes/NAS/Music/AAC"
ffmpeg_bin = "ffmpeg"
work_dir   = "/tmp/flac2aac_work"   # local SSD temp dir

[processing]
workers = 4
```

---

## Multiple quality profiles

Create separate config files for different use cases:

```bash
# High quality for home listening
cp config.toml config_hifi.toml
# Set vbr_quality = 5 in config_hifi.toml

# Smaller files for mobile / syncing
cp config.toml config_mobile.toml
# Set vbr_quality = 3 in config_mobile.toml
```

```bash
python main.py --config config_hifi.toml
python main.py --config config_mobile.toml
```

---

## Ad-hoc overrides

CLI flags override the corresponding config-file values, so you can do
one-off runs without editing `config.toml`:

```bash
# Use a different source/destination just this once
python main.py --input /Volumes/NAS/NewAlbums --output /Volumes/NAS/Music/AAC

# Throttle to 2 workers and see verbose output
python main.py --workers 2 --log-level DEBUG

# Combine a custom config file with a worker override
python main.py --config config_hifi.toml --workers 8
```

---

## Linux FFmpeg build with libfdk_aac

libfdk_aac is non-free and excluded from most Linux distribution FFmpeg
packages. Build it yourself:

```bash
# 1. Install build dependencies
sudo apt update
sudo apt install -y build-essential autoconf automake libtool pkg-config \
    nasm yasm libass-dev libfreetype6-dev libsdl2-dev libtool \
    libva-dev libvdpau-dev libvorbis-dev libxcb-dev libxcb-shm0-dev \
    libxcb-xfixes0-dev texinfo wget

# 2. Build fdk-aac
cd /tmp
git clone https://github.com/mstorsjo/fdk-aac.git
cd fdk-aac && autoreconf -fiv && ./configure && make -j$(nproc)
sudo make install && sudo ldconfig

# 3. Build FFmpeg with fdk-aac
cd /tmp
git clone https://git.ffmpeg.org/ffmpeg.git --depth=1
cd ffmpeg
./configure --enable-libfdk-aac --enable-nonfree --enable-gpl
make -j$(nproc)
sudo make install

# 4. Verify
ffmpeg -codecs 2>/dev/null | grep fdk
# Should show: DEA... aac_fdk
```

---

## Automation

### macOS launchd (run nightly at 02:00)

Create `~/Library/LaunchAgents/com.flac2aac.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.flac2aac</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/flac2aac/.venv/bin/python</string>
    <string>/Users/you/flac2aac/main.py</string>
    <string>--config</string>
    <string>/Users/you/flac2aac/config.toml</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/tmp/flac2aac.log</string>
  <key>StandardErrorPath</key><string>/tmp/flac2aac.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.flac2aac.plist
```

### Linux cron (run nightly at 02:00)

```bash
crontab -e
# Add:
0 2 * * * /home/you/flac2aac/.venv/bin/python /home/you/flac2aac/main.py \
  --config /home/you/flac2aac/config.toml >> /var/log/flac2aac.log 2>&1
```
