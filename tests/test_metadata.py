"""Tests for metadata tag-copy logic, especially the date/year collision
and tracktotal/totaltracks fallback branches.

The FLAC fixture is generated on-the-fly by mutagen.flac.FLAC writing
into a silent-WAV-shaped file; we don't need real audio bytes because
mutagen only looks at the STREAMINFO metadata block.
"""

import struct
from pathlib import Path

import pytest

mutagen = pytest.importorskip("mutagen")
from mutagen.flac import FLAC  # noqa: E402

from config import (  # noqa: E402
    Config,
    CoverFileConfig,
    EncodingConfig,
    LoudnessConfig,
    MetadataConfig,
    PathsConfig,
    ProcessingConfig,
)
from metadata import MetadataHandler  # noqa: E402


# Precomputed minimal FLAC: 4-byte "fLaC" signature followed by a
# single last-metadata-block STREAMINFO describing a 1-sample mono
# 44.1 kHz stream with an all-zero MD5. Enough for mutagen to read
# and write Vorbis comments.
def _write_empty_flac(path: Path) -> None:
    streaminfo = (
        struct.pack(">H", 4096)      # min_blocksize
        + struct.pack(">H", 4096)    # max_blocksize
        + b"\x00\x00\x00"            # min_framesize
        + b"\x00\x00\x00"            # max_framesize
        # sample_rate(20) | channels(3) | bits_per_sample(5) |
        # total_samples(36) = 64 bits. Build as a big-endian integer.
        + int(
            (44100 << 44) | (0 << 41) | (15 << 36) | 1
        ).to_bytes(8, "big")
        + b"\x00" * 16               # MD5
    )
    block_header = bytes([0x80 | 0]) + len(streaminfo).to_bytes(3, "big")
    path.write_bytes(b"fLaC" + block_header + streaminfo)


@pytest.fixture
def handler():
    cfg = Config(
        paths=PathsConfig(input_dir=Path("/tmp"), output_dir=Path("/tmp")),
        encoding=EncodingConfig(),
        metadata=MetadataConfig(cover_file=CoverFileConfig()),
        loudness=LoudnessConfig(),
        processing=ProcessingConfig(),
    )
    return MetadataHandler(cfg)


class _FakeM4A(dict):
    """Stand-in for a mutagen MP4 object good enough for
    _copy_text_tags — it only needs dict-style __setitem__."""


def test_date_wins_over_year_when_both_present(handler, tmp_path):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["date"] = ["1999-04-15"]
    flac["year"] = ["2020"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert m4a["©day"] == ["1999-04-15"]


def test_year_used_when_date_absent(handler, tmp_path):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["year"] = ["1987"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert m4a["©day"] == ["1987"]


def test_track_combined_form_still_works(handler, tmp_path):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["tracknumber"] = ["5/12"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert m4a["trkn"] == [(5, 12)]


def test_tracktotal_fallback(handler, tmp_path):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["tracknumber"] = ["5"]
    flac["tracktotal"] = ["12"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert m4a["trkn"] == [(5, 12)]


def test_totaltracks_alias_fallback(handler, tmp_path):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["tracknumber"] = ["3"]
    flac["totaltracks"] = ["10"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert m4a["trkn"] == [(3, 10)]


def test_disc_disctotal_fallback(handler, tmp_path):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["discnumber"] = ["1"]
    flac["disctotal"] = ["2"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert m4a["disk"] == [(1, 2)]


def test_track_without_total_defaults_to_zero(handler, tmp_path):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["tracknumber"] = ["7"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert m4a["trkn"] == [(7, 0)]


def test_invalid_tracknumber_is_skipped_not_raised(handler, tmp_path, caplog):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["tracknumber"] = ["not-a-number"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert "trkn" not in m4a


# --------------------------------------------------------------------
# Extra tag pass-through: BPM, compilation, grouping, MB IDs, ISRC, ...
# --------------------------------------------------------------------

def test_bpm_copied_as_integer(handler, tmp_path):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["bpm"] = ["128"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert m4a["tmpo"] == [128]


def test_bpm_accepts_float_string(handler, tmp_path):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["bpm"] = ["128.5"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert m4a["tmpo"] == [128]


def test_bpm_invalid_is_skipped(handler, tmp_path, caplog):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["bpm"] = ["not-a-number"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert "tmpo" not in m4a


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True), ("true", True), ("TRUE", True),
        ("yes", True), ("on", True),
        ("0", False), ("false", False), ("no", False), ("", False),
    ],
)
def test_compilation_parses_truthy_forms(handler, tmp_path, raw, expected):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["compilation"] = [raw]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert m4a["cpil"] is expected


def test_grouping_copied_as_text(handler, tmp_path):
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["grouping"] = ["Live Recordings"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    assert m4a["©grp"] == ["Live Recordings"]


def test_musicbrainz_and_isrc_copied_as_freeform(handler, tmp_path):
    from mutagen.mp4 import MP4FreeForm

    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)
    flac = FLAC(flac_path)
    flac["musicbrainz_trackid"] = ["abc-123"]
    flac["musicbrainz_albumid"] = ["def-456"]
    flac["isrc"] = ["USRC17607839"]
    flac["label"] = ["Acme Records"]
    flac["catalognumber"] = ["ACME-001"]
    flac.save()

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    for atom, expected in [
        ('----:com.apple.iTunes:MusicBrainz Track Id', 'abc-123'),
        ('----:com.apple.iTunes:MusicBrainz Album Id', 'def-456'),
        ('----:com.apple.iTunes:ISRC', 'USRC17607839'),
        ('----:com.apple.iTunes:LABEL', 'Acme Records'),
        ('----:com.apple.iTunes:CATALOGNUMBER', 'ACME-001'),
    ]:
        stored = m4a[atom]
        assert len(stored) == 1
        assert isinstance(stored[0], MP4FreeForm)
        assert bytes(stored[0]).decode('utf-8') == expected


def test_missing_extra_tags_leave_atoms_absent(handler, tmp_path):
    """No BPM/compilation/MB tags in source → no atoms in dest."""
    flac_path = tmp_path / "sample.flac"
    _write_empty_flac(flac_path)

    m4a = _FakeM4A()
    handler._copy_text_tags(FLAC(flac_path), m4a)
    for atom in [
        'tmpo', 'cpil', '©grp',
        '----:com.apple.iTunes:MusicBrainz Track Id',
        '----:com.apple.iTunes:ISRC',
    ]:
        assert atom not in m4a
