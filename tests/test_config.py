"""Tests for configuration validation and typo-friendly diagnostics."""

import textwrap
from pathlib import Path

import pytest

from config import (
    ConfigError,
    CoverFileConfig,
    EncodingConfig,
    LoudnessConfig,
    ProcessingConfig,
    load_config,
)


# --------------------------------------------------------------------
# Individual dataclass validation
# --------------------------------------------------------------------

def test_vbr_quality_bounds():
    EncodingConfig(vbr_quality=1)
    EncodingConfig(vbr_quality=5)
    with pytest.raises(ValueError, match="vbr_quality"):
        EncodingConfig(vbr_quality=0)
    with pytest.raises(ValueError, match="vbr_quality"):
        EncodingConfig(vbr_quality=6)


def test_output_format_whitelist():
    EncodingConfig(output_format="m4a")
    EncodingConfig(output_format="mp4")
    with pytest.raises(ValueError, match="output_format"):
        EncodingConfig(output_format="ogg")


def test_encode_timeout_positive():
    EncodingConfig(encode_timeout=1)
    with pytest.raises(ValueError, match="encode_timeout"):
        EncodingConfig(encode_timeout=0)
    with pytest.raises(ValueError, match="encode_timeout"):
        EncodingConfig(encode_timeout=-5)


def test_jpeg_quality_bounds():
    CoverFileConfig(jpeg_quality=1)
    CoverFileConfig(jpeg_quality=95)
    with pytest.raises(ValueError, match="jpeg_quality"):
        CoverFileConfig(jpeg_quality=0)
    with pytest.raises(ValueError, match="jpeg_quality"):
        CoverFileConfig(jpeg_quality=96)


def test_reference_loudness_bounds():
    LoudnessConfig(reference_loudness=0.0)
    LoudnessConfig(reference_loudness=-30.0)
    with pytest.raises(ValueError, match="reference_loudness"):
        LoudnessConfig(reference_loudness=0.1)
    with pytest.raises(ValueError, match="reference_loudness"):
        LoudnessConfig(reference_loudness=-30.1)


def test_log_level_whitelist_and_normalisation():
    cfg = ProcessingConfig(log_level="debug")
    assert cfg.log_level == "DEBUG"
    with pytest.raises(ValueError, match="log_level"):
        ProcessingConfig(log_level="TRACE")


def test_workers_positive():
    ProcessingConfig(workers=1)
    with pytest.raises(ValueError, match="workers"):
        ProcessingConfig(workers=0)


# --------------------------------------------------------------------
# load_config: end-to-end round-trip + unknown-key detection
# --------------------------------------------------------------------

def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body))
    return path


def test_minimal_config_roundtrip(tmp_path):
    cfg_path = _write_config(tmp_path, """
        [paths]
        input_dir = "{inp}"
        output_dir = "{out}"
    """.format(inp=tmp_path / "in", out=tmp_path / "out"))
    config = load_config(cfg_path)
    assert config.paths.input_dir.name == "in"
    assert config.encoding.vbr_quality == 5   # default
    assert config.loudness.reuse_existing_replaygain is False  # default


def test_unknown_key_in_section(tmp_path):
    cfg_path = _write_config(tmp_path, """
        [paths]
        input_dir = "/tmp/in"
        output_dir = "/tmp/out"

        [encoding]
        vbr_qualtiy = 5
    """)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_path)
    msg = str(excinfo.value)
    assert "encoding" in msg
    assert "vbr_qualtiy" in msg
    # The error should also hint at the valid keys.
    assert "vbr_quality" in msg


def test_unknown_key_in_nested_cover_file(tmp_path):
    cfg_path = _write_config(tmp_path, """
        [paths]
        input_dir = "/tmp/in"
        output_dir = "/tmp/out"

        [metadata.cover_file]
        maximum_size = 1000
    """)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_path)
    assert "metadata.cover_file" in str(excinfo.value)
    assert "maximum_size" in str(excinfo.value)


def test_invalid_value_is_wrapped_with_section(tmp_path):
    cfg_path = _write_config(tmp_path, """
        [paths]
        input_dir = "/tmp/in"
        output_dir = "/tmp/out"

        [encoding]
        vbr_quality = 9
    """)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_path)
    assert "[encoding]" in str(excinfo.value)
    assert "vbr_quality" in str(excinfo.value)


def test_missing_paths_section_is_rejected(tmp_path):
    cfg_path = _write_config(tmp_path, """
        [encoding]
        vbr_quality = 5
    """)
    with pytest.raises(ConfigError, match=r"\[paths\]"):
        load_config(cfg_path)


def test_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_reuse_existing_replaygain_is_opt_in(tmp_path):
    cfg_path = _write_config(tmp_path, """
        [paths]
        input_dir = "/tmp/in"
        output_dir = "/tmp/out"

        [loudness]
        reuse_existing_replaygain = true
    """)
    config = load_config(cfg_path)
    assert config.loudness.reuse_existing_replaygain is True
