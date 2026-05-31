# PyInstaller spec for flac2aac.app (macOS)
#
# Prerequisites:
#   1. pip install -r requirements.txt -r requirements-gui.txt
#   2. Place a libfdk_aac-enabled static FFmpeg build at vendor/ffmpeg (chmod +x)
#      Remove Gatekeeper quarantine: xattr -d com.apple.quarantine vendor/ffmpeg
#   3. Optionally place a 1024x1024 PNG at assets/flac2aac.png and generate the
#      .icns file: see assets/README or run `make icon` if provided.
#
# Build:
#   pyinstaller flac2aac_gui.spec
#
# Output:
#   dist/flac2aac.app  —  double-click to run, no Python or FFmpeg needed.

import os
from pathlib import Path

HERE = Path(SPECPATH)

# Bundle FFmpeg binary if present at vendor/ffmpeg; otherwise warn.
_ffmpeg_src = HERE / "vendor" / "ffmpeg"
_binaries = []
if _ffmpeg_src.exists():
    _binaries = [(str(_ffmpeg_src), "bin")]  # must not be "." — conflicts with ffmpeg Python package
else:
    import warnings
    warnings.warn(
        "vendor/ffmpeg not found — the app will require FFmpeg to be installed "
        "separately (e.g. via Homebrew). Place a libfdk_aac-enabled static build "
        "at vendor/ffmpeg to produce a fully self-contained bundle.",
        stacklevel=1,
    )

from PyInstaller.utils.hooks import collect_all, collect_submodules

r128gain_datas, r128gain_binaries, r128gain_hiddenimports = collect_all("r128gain")
mutagen_datas, mutagen_binaries, mutagen_hiddenimports = collect_all("mutagen")
tqdm_datas, tqdm_binaries, tqdm_hiddenimports = collect_all("tqdm")
platformdirs_datas, platformdirs_binaries, platformdirs_hiddenimports = collect_all("platformdirs")

a = Analysis(
    [str(HERE / "gui.py")],
    pathex=[str(HERE)],
    binaries=_binaries + r128gain_binaries + mutagen_binaries + tqdm_binaries + platformdirs_binaries,
    datas=r128gain_datas + mutagen_datas + tqdm_datas + platformdirs_datas,
    hiddenimports=(
        r128gain_hiddenimports
        + mutagen_hiddenimports
        + tqdm_hiddenimports
        + platformdirs_hiddenimports
        + [
            "PIL",
            "PIL.Image",
            "PIL.JpegImagePlugin",
            "PIL.PngImagePlugin",
            "tomli",
            # tkinter and its submodules are not always auto-detected on macOS
            "tkinter",
            "tkinter.ttk",
            "tkinter.filedialog",
            "tkinter.messagebox",
            "tkinter.scrolledtext",
            "_tkinter",
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="flac2aac",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # no terminal window on macOS
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="flac2aac",
)

app = BUNDLE(
    coll,
    name="flac2aac.app",
    icon=str(HERE / "assets" / "flac2aac.icns") if (HERE / "assets" / "flac2aac.icns").exists() else None,
    bundle_identifier="nl.whepper.flac2aac",
    info_plist={
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSHumanReadableCopyright": "MIT",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "NSRequiresAquaSystemAppearance": False,  # support Dark Mode
    },
)
