"""Tests for rsgain integration in LoudnessProcessor."""

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
from loudness import LoudnessProcessor


def _make_processor(tmp_path: Path, rsgain_bin: str = "rsgain") -> LoudnessProcessor:
    cfg = Config(
        paths=PathsConfig(
            input_dir=tmp_path / "in",
            output_dir=tmp_path / "out",
            rsgain_bin=rsgain_bin,
        ),
        encoding=EncodingConfig(),
        metadata=MetadataConfig(cover_file=CoverFileConfig()),
        loudness=LoudnessConfig(),
        processing=ProcessingConfig(workers=1, log_level="INFO"),
    )
    return LoudnessProcessor(cfg)


def _ok(*args, **kwargs):
    return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")


# ── verify_rsgain ────────────────────────────────────────────────────────────

def test_verify_rsgain_returns_true_when_binary_exits_zero(tmp_path):
    proc = _make_processor(tmp_path)
    with patch("subprocess.run", side_effect=_ok):
        assert proc.verify_rsgain() is True


def test_verify_rsgain_returns_false_when_binary_missing(tmp_path):
    proc = _make_processor(tmp_path)
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert proc.verify_rsgain() is False


def test_verify_rsgain_returns_false_on_timeout(tmp_path):
    proc = _make_processor(tmp_path)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="rsgain", timeout=10)):
        assert proc.verify_rsgain() is False


def test_verify_rsgain_returns_false_when_exit_nonzero(tmp_path):
    proc = _make_processor(tmp_path)
    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")
    with patch("subprocess.run", return_value=failed):
        assert proc.verify_rsgain() is False


# ── _add_replaygain_tags ─────────────────────────────────────────────────────

def test_rsgain_called_with_album_flag(tmp_path):
    proc = _make_processor(tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    files = [tmp_path / "a.m4a", tmp_path / "b.m4a"]
    with patch("subprocess.run", side_effect=fake_run):
        with patch.object(proc, "_has_replaygain", return_value=True):
            proc._add_replaygain_tags(files)

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "rsgain"
    assert "custom" in cmd
    assert "--album" in cmd
    assert str(files[0]) in cmd
    assert str(files[1]) in cmd


def test_rsgain_timeout_scales_with_file_count(tmp_path):
    proc = _make_processor(tmp_path)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    files = [tmp_path / f"track{i}.m4a" for i in range(5)]
    with patch("subprocess.run", side_effect=fake_run):
        with patch.object(proc, "_has_replaygain", return_value=True):
            proc._add_replaygain_tags(files)

    assert captured["timeout"] == max(120, 5 * 60)


def test_rsgain_failure_is_logged_not_raised(tmp_path):
    proc = _make_processor(tmp_path)
    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "rsgain", stderr="boom"),
    ):
        # Should not raise; error is logged and the method returns early.
        proc._add_replaygain_tags([tmp_path / "a.m4a"])
