"""Tests for Pipeline internals: _move_album_to_output overwrite guard."""

import threading
from pathlib import Path

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


def _make_pipeline(tmp_path: Path, overwrite: bool = False) -> Pipeline:
    cfg = Config(
        paths=PathsConfig(
            input_dir=tmp_path / "in",
            output_dir=tmp_path / "out",
        ),
        encoding=EncodingConfig(),
        metadata=MetadataConfig(cover_file=CoverFileConfig()),
        loudness=LoudnessConfig(),
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
