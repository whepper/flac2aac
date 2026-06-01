# PyInstaller spec for flac2aac.app (macOS)
#
# Prerequisites:
#   1. pip install -r requirements.txt -r requirements-gui.txt
#   2. Place a libfdk_aac-enabled static FFmpeg build at vendor/ffmpeg (chmod +x)
#      Remove Gatekeeper quarantine: xattr -d com.apple.quarantine vendor/ffmpeg
#   3. Place a static rsgain build at vendor/rsgain (chmod +x)
#      Download from https://github.com/complexlogic/rsgain/releases
#      Remove Gatekeeper quarantine: xattr -d com.apple.quarantine vendor/rsgain
#   4. Optionally place a 1024x1024 PNG at assets/flac2aac.png and generate the
#      .icns file: see assets/README or run `make icon` if provided.
#
# Build:
#   pyinstaller flac2aac_gui.spec
#
# Output:
#   dist/flac2aac.app  —  double-click to run, no Python, FFmpeg or rsgain needed.

import os
from pathlib import Path

HERE = Path(SPECPATH)


def _vendor_binary(name):
    """Return a PyInstaller binaries tuple for a file in vendor/, or warn if absent."""
    src = HERE / "vendor" / name
    if src.exists():
        return (str(src), "bin")
    import warnings
    warnings.warn(
        f"vendor/{name} not found — the app will require {name} to be installed "
        f"separately (e.g. via Homebrew). Place a static build at vendor/{name} "
        f"to produce a fully self-contained bundle.",
        stacklevel=1,
    )
    return None


_binaries = [t for t in [_vendor_binary("ffmpeg"), _vendor_binary("rsgain")] if t]

from PyInstaller.utils.hooks import collect_all

mutagen_datas, mutagen_binaries, mutagen_hiddenimports = collect_all("mutagen")

a = Analysis(
    [str(HERE / "gui.py")],
    pathex=[str(HERE)],
    binaries=_binaries + mutagen_binaries,
    datas=mutagen_datas,
    hiddenimports=(
        mutagen_hiddenimports
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
