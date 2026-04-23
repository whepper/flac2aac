"""Tests for the CLI layer: argparse wiring and `apply_cli_overrides`."""

import argparse
import textwrap
from pathlib import Path

import pytest

from config import (
    Config,
    ConfigError,
    CoverFileConfig,
    EncodingConfig,
    LoudnessConfig,
    MetadataConfig,
    PathsConfig,
    ProcessingConfig,
    load_config,
)
from main import (
    EXIT_CONFIG,
    apply_cli_overrides,
    build_parser,
    main,
)


@pytest.fixture
def config(tmp_path):
    return Config(
        paths=PathsConfig(
            input_dir=tmp_path / "in",
            output_dir=tmp_path / "out",
        ),
        encoding=EncodingConfig(),
        metadata=MetadataConfig(cover_file=CoverFileConfig()),
        loudness=LoudnessConfig(),
        processing=ProcessingConfig(
            workers=4, overwrite_existing=False, log_level="INFO"
        ),
    )


@pytest.fixture
def parser():
    return build_parser()


def test_version_exits_cleanly(parser, capsys):
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("flac2aac ")


def test_log_level_choices_are_enforced(parser, capsys):
    with pytest.raises(SystemExit):
        parser.parse_args(["--log-level", "TRACE"])
    err = capsys.readouterr().err
    assert "TRACE" in err


def test_parser_defaults(parser):
    args = parser.parse_args([])
    assert args.config == Path("config.toml")
    assert args.dry_run is False
    assert args.input is None
    assert args.output is None
    assert args.workers is None
    assert args.log_level is None


def test_apply_cli_overrides_no_op_without_flags(parser, config):
    args = parser.parse_args([])
    before = (
        config.paths.input_dir,
        config.paths.output_dir,
        config.processing.workers,
        config.processing.log_level,
    )
    apply_cli_overrides(config, args)
    after = (
        config.paths.input_dir,
        config.paths.output_dir,
        config.processing.workers,
        config.processing.log_level,
    )
    assert before == after


def test_apply_cli_overrides_input_and_output(parser, config, tmp_path):
    new_in = tmp_path / "override_in"
    new_out = tmp_path / "override_out"
    args = parser.parse_args(
        ["--input", str(new_in), "--output", str(new_out)]
    )
    apply_cli_overrides(config, args)
    assert config.paths.input_dir == new_in.resolve()
    assert config.paths.output_dir == new_out.resolve()


def test_apply_cli_overrides_workers(parser, config):
    args = parser.parse_args(["--workers", "12"])
    apply_cli_overrides(config, args)
    assert config.processing.workers == 12


def test_apply_cli_overrides_workers_rejects_zero(parser, config):
    args = parser.parse_args(["--workers", "0"])
    with pytest.raises(ConfigError, match="workers"):
        apply_cli_overrides(config, args)


def test_apply_cli_overrides_log_level(parser, config):
    args = parser.parse_args(["--log-level", "DEBUG"])
    apply_cli_overrides(config, args)
    assert config.processing.log_level == "DEBUG"


# --------------------------------------------------------------------
# Exit-code smoke tests for main() — these exercise the glue that ties
# argparse to config loading without actually running ffmpeg.
# --------------------------------------------------------------------

def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body))
    return path


def test_main_returns_exit_config_for_missing_config_file(tmp_path):
    # main() calls setup_logging(..., force=True) which replaces
    # caplog's handler; assertions rely on stderr via capsys instead.
    result = main(["--config", str(tmp_path / "nope.toml")])
    assert result == EXIT_CONFIG


def test_main_returns_exit_config_for_bad_override(tmp_path):
    cfg_path = _write_config(tmp_path, f"""
        [paths]
        input_dir = "{tmp_path / 'in'}"
        output_dir = "{tmp_path / 'out'}"
    """)
    result = main(["--config", str(cfg_path), "--workers", "0"])
    assert result == EXIT_CONFIG


def test_main_returns_exit_config_for_unknown_key(tmp_path, capsys):
    cfg_path = _write_config(tmp_path, f"""
        [paths]
        input_dir = "{tmp_path / 'in'}"
        output_dir = "{tmp_path / 'out'}"

        [encoding]
        vbr_qualtiy = 5
    """)
    result = main(["--config", str(cfg_path)])
    assert result == EXIT_CONFIG
    err = capsys.readouterr().err
    assert "vbr_qualtiy" in err
