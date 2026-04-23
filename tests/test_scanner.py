"""Tests for Scanner: case-insensitive walk, dedup, overwrite behaviour."""

from dataclasses import dataclass
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
from scanner import Scanner


def _make_config(
    input_dir: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> Config:
    return Config(
        paths=PathsConfig(input_dir=input_dir, output_dir=output_dir),
        encoding=EncodingConfig(),
        metadata=MetadataConfig(cover_file=CoverFileConfig()),
        loudness=LoudnessConfig(),
        processing=ProcessingConfig(
            workers=1, overwrite_existing=overwrite, log_level="INFO"
        ),
    )


def test_matches_both_cases(tmp_path):
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "lower.flac").write_bytes(b"")
    (input_dir / "UPPER.FLAC").write_bytes(b"")
    (input_dir / "ignore.wav").write_bytes(b"")

    cfg = _make_config(input_dir, output_dir)
    pairs = list(Scanner(cfg).scan())
    names = sorted(src.name for src, _ in pairs)
    assert names == ["UPPER.FLAC", "lower.flac"]


def test_does_not_double_count_via_symlink_alias(tmp_path):
    """When the same physical file is reachable under two names that
    differ only in extension case (the bug that bit case-insensitive
    filesystems), Scanner must yield it exactly once."""
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    real = input_dir / "song.flac"
    real.write_bytes(b"")
    alias = input_dir / "song.FLAC"
    try:
        alias.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("filesystem does not support symlinks")

    cfg = _make_config(input_dir, output_dir)
    pairs = list(Scanner(cfg).scan())
    resolved = {src.resolve() for src, _ in pairs}
    assert len(resolved) == 1
    assert len(pairs) == 1


def test_preserves_directory_structure(tmp_path):
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    (input_dir / "Artist" / "Album").mkdir(parents=True)
    src = input_dir / "Artist" / "Album" / "01.flac"
    src.write_bytes(b"")

    cfg = _make_config(input_dir, output_dir)
    pairs = list(Scanner(cfg).scan())
    assert len(pairs) == 1
    _, dest = pairs[0]
    assert dest == output_dir / "Artist" / "Album" / "01.m4a"


def test_skips_existing_destination_when_overwrite_disabled(tmp_path):
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "song.flac").write_bytes(b"")
    dest = output_dir / "song.m4a"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"")

    cfg = _make_config(input_dir, output_dir, overwrite=False)
    assert list(Scanner(cfg).scan()) == []


def test_reencodes_when_overwrite_enabled(tmp_path):
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "song.flac").write_bytes(b"")
    dest = output_dir / "song.m4a"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"")

    cfg = _make_config(input_dir, output_dir, overwrite=True)
    pairs = list(Scanner(cfg).scan())
    assert len(pairs) == 1


def test_missing_input_dir_yields_nothing(tmp_path):
    """Scanner itself returns silently (we validate input_dir in
    Pipeline); the iterator simply produces no files."""
    cfg = _make_config(tmp_path / "missing", tmp_path / "out")
    assert list(Scanner(cfg).scan()) == []
