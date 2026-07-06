"""Transcriber wiring for audio / caption-less-video ingest (audit row 1b).

The production ingest path previously never passed a transcriber, so audio
uploads hit a bare KeyError and caption-less YouTube raised TranscriptUnavailable
in the worker. These tests exercise the wired path end-to-end with a FAKE
transcriber (never the real whisper model).
"""
from __future__ import annotations

import pytest

from mslearn.adapters import youtube as youtube_adapter
from mslearn.adapters.base import make_source_id
from mslearn.adapters.registry import load_source
from mslearn.opsdb import OpsDB
from mslearn.transcribe import TranscriptSegment
from mslearn.worker import tasks as worker_tasks
from mslearn.worker.app import app
from mslearn.worker.context import PipelineContext, set_context
from tests.fakes import InMemoryGraphStore, ScriptedRouter


class FakeTranscriber:
    def transcribe(self, audio_path):
        return [
            TranscriptSegment(0.0, 4.0, "Caching keeps hot data close to the reader."),
            TranscriptSegment(4.0, 8.0, "A TTL bounds how long a stale entry can survive."),
        ]


@pytest.fixture(autouse=True)
def eager_app():
    app.conf.task_always_eager = True
    yield
    app.conf.task_always_eager = False


def _ctx(tmp_path, *, transcriber):
    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    ctx = PipelineContext(
        settings=None, db=db, router=ScriptedRouter([]), graph=graph, transcriber=transcriber
    )
    set_context(ctx)
    return ctx


def _run_source(ctx, ref: str, source_type: str):
    source_id = make_source_id(ref)
    ctx.db.register_source(source_id, ref=ref, role="spine", total_chunks=0)
    ctx.db.set_source_status(source_id, "chunking")
    worker_tasks.chunk_source_task.delay(
        "default", source_id, ref, "spine", source_type, False
    ).get()
    return source_id


def test_audio_source_ingests_end_to_end_with_transcriber(tmp_path):
    ctx = _ctx(tmp_path, transcriber=FakeTranscriber())
    source_id = _run_source(ctx, str(tmp_path / "episode.mp3"), "audio")

    row = ctx.db.source_row(source_id)
    assert row["status"] == "running"
    assert row["total_chunks"] > 0
    assert ctx.graph.chunks  # chunks reached the graph


def test_captionless_youtube_ingests_via_transcriber(tmp_path, monkeypatch):
    # No captions -> fetch raises -> fall back to download + fake transcribe.
    monkeypatch.setattr(
        youtube_adapter, "_default_fetch",
        lambda video_id: (_ for _ in ()).throw(RuntimeError("captions disabled")),
    )

    def fake_download(url, out_dir):
        path = out_dir / "audio.m4a"
        path.write_bytes(b"\x00")
        return path

    monkeypatch.setattr(youtube_adapter, "_default_download_audio", fake_download)

    ctx = _ctx(tmp_path, transcriber=FakeTranscriber())
    source_id = _run_source(ctx, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube")

    row = ctx.db.source_row(source_id)
    assert row["status"] == "running"
    assert row["total_chunks"] > 0


def test_audio_without_transcriber_fails_clearly_not_keyerror(tmp_path):
    ctx = _ctx(tmp_path, transcriber=None)
    source_id = _run_source(ctx, str(tmp_path / "episode.mp3"), "audio")

    row = ctx.db.source_row(source_id)
    assert row["status"] == "failed"
    assert "transcribe" in row["error"].lower()
    assert "KeyError" not in row["error"]


def test_load_source_audio_without_transcriber_raises_readable_valueerror():
    with pytest.raises(ValueError, match="no transcriber available"):
        load_source("episode.mp3", source_type="audio")
