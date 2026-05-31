"""Tests for the iTunNORM conversion in LoudnessProcessor.

We avoid instantiating a full ``Config`` here; the conversion is a
pure method on ``LoudnessProcessor`` and only touches ``self`` for
method resolution.
"""

import re

import pytest

from loudness import LoudnessProcessor


@pytest.fixture
def processor():
    # Bind the unbound method manually — no Config needed.
    class _Shim:
        _replaygain_to_soundcheck = LoudnessProcessor._replaygain_to_soundcheck
    return _Shim()


def _split(itunnorm: str):
    # iTunes-style iTunNORM begins with a single leading space.
    assert itunnorm.startswith(" "), "iTunNORM must start with a leading space"
    parts = itunnorm[1:].split(" ")
    assert len(parts) == 10, f"expected 10 slots, got {len(parts)}"
    for p in parts:
        assert re.fullmatch(r"[0-9A-F]{8}", p), f"bad slot: {p!r}"
    return parts


def test_zero_db_reference(processor):
    """At 0 dB the 1000-W ref is exactly 1000 and the 2500-W ref 2500."""
    out = processor._replaygain_to_soundcheck(0.0)
    parts = _split(out)
    assert parts[0] == parts[1] == "000003E8"   # 1000
    assert parts[2] == parts[3] == "000009C4"   # 2500
    # Standard iTunes filler in slots 5-10.
    assert parts[4:] == [
        "00024CA8", "00024CA8",
        "00007FFF", "00007FFF",
        "00024CA8", "00024CA8",
    ]


def test_minus_six_db(processor):
    out = processor._replaygain_to_soundcheck(-6.0)
    parts = _split(out)
    assert parts[0] == parts[1] == "00000F8D"
    assert parts[2] == parts[3] == "000026E1"


def test_minus_eighteen_db(processor):
    out = processor._replaygain_to_soundcheck(-18.0)
    parts = _split(out)
    assert parts[0] == parts[1] == "0000F678"
    assert parts[2] == parts[3] == "0002682B"


def test_positive_gain_collapses_toward_zero(processor):
    # +20 dB means the track was very loud; ratio = 0.01.
    # sc_1000 = round(0.01 * 1000) = 10 = 0x0A
    out = processor._replaygain_to_soundcheck(20.0)
    parts = _split(out)
    assert parts[0] == parts[1] == "0000000A"
    assert parts[2] == parts[3] == "00000019"   # 25


def test_extreme_negative_clamps(processor):
    # -70 dB would overflow even the 32-bit slot; must clamp to
    # 0xFFFFFFFE rather than silently wrapping or clipping at 65534.
    out = processor._replaygain_to_soundcheck(-70.0)
    parts = _split(out)
    assert parts[0] == parts[1] == "FFFFFFFE"
    assert parts[2] == parts[3] == "FFFFFFFE"


def test_fillers_are_fixed_across_gains(processor):
    # Regression guard: the iTunes-typical filler values must not
    # vary with gain. Only the first four slots depend on gain_db.
    ref = _split(processor._replaygain_to_soundcheck(0.0))[4:]
    for g in (-6.0, -18.0, -40.0, -70.0, 0.0, 10.0):
        assert _split(processor._replaygain_to_soundcheck(g))[4:] == ref


def test_default_reference_is_identity(processor):
    """reference_loudness=-18 (default) must produce the same output as
    calling without the argument."""
    assert (
        processor._replaygain_to_soundcheck(-7.0)
        == processor._replaygain_to_soundcheck(-7.0, -18.0)
    )


def test_reference_loudness_shifts_gain(processor):
    """Targeting -14 LUFS shifts the effective gain by +4 dB vs -18 LUFS.

    Higher sc value = louder track = less iTunes boost; lower sc = quieter track
    = more iTunes boost. Targeting -14 LUFS (louder playback level) means
    iTunes must boost more, so sc should be LOWER.
    """
    parts_18 = _split(processor._replaygain_to_soundcheck(0.0, -18.0))
    parts_14 = _split(processor._replaygain_to_soundcheck(0.0, -14.0))
    sc_18 = int(parts_18[0], 16)
    sc_14 = int(parts_14[0], 16)
    # Louder target → lower sc (iTunes applies more gain)
    assert sc_14 < sc_18


def test_reference_loudness_minus18_matches_legacy(processor):
    """-18 LUFS reference must reproduce the known -18 dB result."""
    out = processor._replaygain_to_soundcheck(-18.0, -18.0)
    parts = _split(out)
    assert parts[0] == parts[1] == "0000F678"
    assert parts[2] == parts[3] == "0002682B"
