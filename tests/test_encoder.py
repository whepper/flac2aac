"""Tests for Encoder: verify_ffmpeg timeout and missing-binary handling."""

import subprocess
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
from encoder import Encoder


def _make_encoder(tmp_path: Path, ffmpeg_bin: str = "ffmpeg") -> Encoder:
    cfg = Config(
        paths=PathsConfig(
            input_dir=tmp_path / "in",
            output_dir=tmp_path / "out",
            ffmpeg_bin=ffmpeg_bin,
        ),
        encoding=EncodingConfig(),
        metadata=MetadataConfig(cover_file=CoverFileConfig()),
        loudness=LoudnessConfig(),
        processing=ProcessingConfig(workers=1, log_level="INFO"),
    )
    return Encoder(cfg)


def test_verify_ffmpeg_returns_false_on_timeout(tmp_path):
    enc = _make_encoder(tmp_path)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10)):
        assert enc.verify_ffmpeg() is False


def test_verify_ffmpeg_returns_false_when_binary_missing(tmp_path):
    enc = _make_encoder(tmp_path)
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert enc.verify_ffmpeg() is False


def test_verify_ffmpeg_passes_timeout_to_subprocess(tmp_path):
    """subprocess.run must be called with timeout=10."""
    enc = _make_encoder(tmp_path)
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        result = subprocess.CompletedProcess(args=[], returncode=0)
        result.stdout = " A....D libfdk_aac  Fraunhofer FDK AAC\n"
        result.stderr = ""
        return result

    with patch("subprocess.run", side_effect=fake_run):
        enc.verify_ffmpeg()

    assert captured.get("timeout") == 10
