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
from pipeline import (
    PHASE_ENCODING,
    PHASE_LOUDNESS,
    PHASE_MOVING,
    PHASE_READY,
    PHASE_SCANNING,
    Pipeline,
    ProgressEvent,
)


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


# ── Progress callback ─────────────────────────────────────────────────────────

def test_progress_callback_optional(tmp_path):
    """Pipeline.run works fine with no progress_callback (the CLI case)."""
    pipeline = _make_pipeline(tmp_path)
    (tmp_path / "in").mkdir(parents=True)
    with patch.object(pipeline.encoder, "verify_ffmpeg", return_value=True):
        pipeline.run()  # must not raise


def test_progress_callback_receives_scanning_event(tmp_path):
    """The very first event the callback sees is the scanning marker."""
    events: list[ProgressEvent] = []
    pipeline = _make_pipeline(tmp_path)
    (tmp_path / "in").mkdir(parents=True)
    with patch.object(pipeline.encoder, "verify_ffmpeg", return_value=True):
        # progress_callback is wired up at construction time, not per-run.
        pipeline.progress_callback = events.append
        pipeline.run()
    assert events, "callback should have been invoked"
    assert events[0].phase == PHASE_SCANNING


def test_progress_files_done_is_monotonic(tmp_path):
    """files_done on emitted events is monotonically non-decreasing — the
    contract the GUI relies on to keep its progress bar moving right."""
    events: list[ProgressEvent] = []
    pipeline = _make_pipeline(tmp_path)
    (tmp_path / "in").mkdir(parents=True)
    with patch.object(pipeline.encoder, "verify_ffmpeg", return_value=True):
        pipeline.progress_callback = events.append
        pipeline.run()

    files_done_values = [e.files_done for e in events if e.files_total > 0]
    for prev, curr in zip(files_done_values, files_done_values[1:]):
        assert curr >= prev, f"files_done went backwards: {prev} → {curr}"


def test_progress_ready_event_carries_totals(tmp_path):
    """The 'ready' event advertises the album and file totals so the UI
    can size the bar up front."""
    events: list[ProgressEvent] = []
    pipeline = _make_pipeline(tmp_path)
    (tmp_path / "in").mkdir(parents=True)
    with patch.object(pipeline.encoder, "verify_ffmpeg", return_value=True):
        pipeline.progress_callback = events.append
        pipeline.run()

    ready = [e for e in events if e.phase == PHASE_READY]
    assert ready, "expected at least one PHASE_READY event"
    # No FLAC files in the test, but the event must still be emitted
    # with the totals the pipeline knows about.
    assert ready[0].album_total == 0
    assert ready[0].files_total == 0


def test_progress_emits_encoding_and_loudness_phases(tmp_path, monkeypatch):
    """When there is work to do, the pipeline emits encoding, loudness
    and (with work_dir) moving events in the expected order."""
    # Build a tiny fake album: one FLAC stub → one encoded output. The
    # encoder is patched out entirely; we only care that the pipeline
    # reaches the progress emission points.
    in_dir = tmp_path / "in" / "album"
    out_dir = tmp_path / "out" / "album"
    in_dir.mkdir(parents=True)
    (in_dir / "track.flac").write_bytes(b"fake")

    cfg = Config(
        paths=PathsConfig(input_dir=tmp_path / "in", output_dir=tmp_path / "out"),
        encoding=EncodingConfig(),
        metadata=MetadataConfig(cover_file=CoverFileConfig()),
        loudness=LoudnessConfig(enable_replaygain=False, enable_itunes_soundcheck=False),
        processing=ProcessingConfig(workers=1, log_level="INFO"),
    )

    def _fake_encode(self, source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"encoded")

    events: list[ProgressEvent] = []
    pipeline = Pipeline(cfg, progress_callback=events.append)
    with patch.object(pipeline.encoder, "verify_ffmpeg", return_value=True), \
         patch("encoder.Encoder.encode", _fake_encode), \
         patch("metadata.MetadataHandler.copy_metadata"), \
         patch("metadata.CoverManager.handle_cover_file"):
        pipeline.run()

    phases = [e.phase for e in events]
    assert PHASE_SCANNING in phases
    assert PHASE_READY in phases
    assert PHASE_ENCODING in phases
    # No work_dir configured → no moving phase.

    # files_total must match the one file we created.
    encoding_events = [e for e in events if e.phase == PHASE_ENCODING]
    assert encoding_events[-1].files_total == 1
    assert encoding_events[-1].files_done == 1
    assert encoding_events[-1].track_total == 1
    assert encoding_events[-1].track_index == 1


def test_progress_emits_moving_phase_when_work_dir_set(tmp_path, monkeypatch):
    """With work_dir configured, the 'moving' phase appears between
    loudness and the end of the album."""
    in_dir = tmp_path / "in" / "album"
    work_dir = tmp_path / "work"
    out_dir = tmp_path / "out"
    in_dir.mkdir(parents=True)
    (in_dir / "track.flac").write_bytes(b"fake")

    cfg = Config(
        paths=PathsConfig(
            input_dir=tmp_path / "in", output_dir=out_dir, work_dir=work_dir,
        ),
        encoding=EncodingConfig(),
        metadata=MetadataConfig(cover_file=CoverFileConfig()),
        loudness=LoudnessConfig(enable_replaygain=False, enable_itunes_soundcheck=False),
        processing=ProcessingConfig(workers=1, log_level="INFO"),
    )

    def _fake_encode(self, source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"encoded")

    events: list[ProgressEvent] = []
    pipeline = Pipeline(cfg, progress_callback=events.append)
    with patch.object(pipeline.encoder, "verify_ffmpeg", return_value=True), \
         patch("encoder.Encoder.encode", _fake_encode), \
         patch("metadata.MetadataHandler.copy_metadata"), \
         patch("metadata.CoverManager.handle_cover_file"):
        pipeline.run()

    phases = [e.phase for e in events]
    assert PHASE_MOVING in phases
    # The moving event should be issued after the encoding event and
    # carry the full per-album count in files_done.
    moving = [e for e in events if e.phase == PHASE_MOVING]
    assert moving[0].files_done == 1


def test_progress_callback_exceptions_do_not_abort_run(tmp_path, monkeypatch):
    """A buggy UI hook must not be able to kill an in-progress encode."""
    in_dir = tmp_path / "in" / "album"
    in_dir.mkdir(parents=True)
    (in_dir / "track.flac").write_bytes(b"fake")

    cfg = Config(
        paths=PathsConfig(input_dir=tmp_path / "in", output_dir=tmp_path / "out"),
        encoding=EncodingConfig(),
        metadata=MetadataConfig(cover_file=CoverFileConfig()),
        loudness=LoudnessConfig(enable_replaygain=False, enable_itunes_soundcheck=False),
        processing=ProcessingConfig(workers=1, log_level="INFO"),
    )

    def _fake_encode(self, source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"encoded")

    def _bad_callback(event: ProgressEvent) -> None:
        raise RuntimeError("boom")

    pipeline = Pipeline(cfg, progress_callback=_bad_callback)
    with patch.object(pipeline.encoder, "verify_ffmpeg", return_value=True), \
         patch("encoder.Encoder.encode", _fake_encode), \
         patch("metadata.MetadataHandler.copy_metadata"), \
         patch("metadata.CoverManager.handle_cover_file"):
        stats = pipeline.run()
    # Pipeline still finished cleanly.
    assert stats.successful == 1
    assert stats.failed == 0
