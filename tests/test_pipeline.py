"""Tests for Pipeline internals: _move_album_to_output overwrite guard,
and rsgain startup verification."""

import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from config import (
    Config,
    CoverFileConfig,
    EncodingConfig,
    LoudnessConfig,
    MetadataConfig,
    PathsConfig,
    ProcessingConfig,
)
from pipeline import Pipeline


def _make_pipeline(
    tmp_path: Path,
    overwrite: bool = False,
    reuse_rg: bool = False,
) -> Pipeline:
    cfg = Config(
        paths=PathsConfig(
            input_dir=tmp_path / "in",
            output_dir=tmp_path / "out",
        ),
        encoding=EncodingConfig(),
        metadata=MetadataConfig(cover_file=CoverFileConfig()),
        loudness=LoudnessConfig(reuse_existing_replaygain=reuse_rg),
        processing=ProcessingConfig(workers=1, overwrite_existing=overwrite, log_level="INFO"),
    )
    return Pipeline(cfg)


def test_move_album_skips_existing_when_overwrite_disabled(tmp_path):
    work_dir = tmp_path / "work" / "album"
    final_dir = tmp_path / "out" / "album"
    work_dir.mkdir(parents=True)
    final_dir.mkdir(parents=True)

    work_file = work_dir / "track.m4a"
    work_file.write_bytes(b"work_content")
    existing = final_dir / "track.m4a"
    existing.write_bytes(b"original_content")

    pipeline = _make_pipeline(tmp_path, overwrite=False)
    pipeline._move_album_to_output(work_dir, final_dir)

    # Existing file must not have been overwritten.
    assert existing.read_bytes() == b"original_content"
    # Work file should have been discarded.
    assert not work_file.exists()


def test_move_album_overwrites_when_overwrite_enabled(tmp_path):
    work_dir = tmp_path / "work" / "album"
    final_dir = tmp_path / "out" / "album"
    work_dir.mkdir(parents=True)
    final_dir.mkdir(parents=True)

    work_file = work_dir / "track.m4a"
    work_file.write_bytes(b"new_content")
    existing = final_dir / "track.m4a"
    existing.write_bytes(b"original_content")

    pipeline = _make_pipeline(tmp_path, overwrite=True)
    pipeline._move_album_to_output(work_dir, final_dir)

    assert existing.read_bytes() == b"new_content"


# ── rsgain startup guard ──────────────────────────────────────────────────────

def test_rsgain_verified_even_when_reuse_replaygain_enabled(tmp_path):
    """reuse_existing_replaygain is a best-effort fast path that falls back to
    rsgain for untagged files, so the binary must be verified at startup
    regardless of that setting."""
    pipeline = _make_pipeline(tmp_path, reuse_rg=True)
    (tmp_path / "in").mkdir(parents=True)

    with patch.object(pipeline.encoder, "verify_ffmpeg", return_value=True):
        with patch.object(
            pipeline.loudness_processor, "verify_rsgain", return_value=False
        ):
            with pytest.raises(RuntimeError, match="rsgain"):
                pipeline.run()


def test_rsgain_not_verified_when_replaygain_disabled(tmp_path):
    """When enable_replaygain=false rsgain is never needed."""
    cfg = Config(
        paths=PathsConfig(input_dir=tmp_path / "in", output_dir=tmp_path / "out"),
        encoding=EncodingConfig(),
        metadata=MetadataConfig(cover_file=CoverFileConfig()),
        loudness=LoudnessConfig(enable_replaygain=False, enable_itunes_soundcheck=False),
        processing=ProcessingConfig(workers=1, log_level="INFO"),
    )
    pipeline = Pipeline(cfg)
    (tmp_path / "in").mkdir(parents=True)

    verify_called = []
    with patch.object(pipeline.encoder, "verify_ffmpeg", return_value=True):
        with patch.object(
            pipeline.loudness_processor,
            "verify_rsgain",
            side_effect=lambda: verify_called.append(True) or False,
        ):
            pipeline.run()  # no FLAC files → returns early, but should not raise

    assert verify_called == [], "verify_rsgain should not be called when ReplayGain is disabled"
