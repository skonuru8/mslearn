from pathlib import Path

import pytest

from mslearn.adapters.youtube import TranscriptUnavailable, load_youtube, video_id_of
from mslearn.transcribe import TranscriptSegment

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_video_id_parsing():
    assert video_id_of(URL) == "dQw4w9WgXcQ"
    assert video_id_of("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    with pytest.raises(ValueError):
        video_id_of("https://example.com/nope")


def test_caption_path_builds_time_units():
    def fake_fetch(video_id):
        assert video_id == "dQw4w9WgXcQ"
        return [
            {"text": "Never gonna give", "start": 0.0, "duration": 2.5},
            {"text": "you up", "start": 2.5, "duration": 1.5},
        ]

    doc = load_youtube(URL, fetch_transcript=fake_fetch)
    assert doc.source_type == "youtube"
    assert len(doc.units) == 2
    loc = doc.units[0].locator
    assert loc.kind == "time" and loc.url == URL and loc.start_s == 0.0 and loc.end_s == 2.5


def test_fallback_uses_downloader_and_transcriber(tmp_path):
    def failing_fetch(video_id):
        raise RuntimeError("captions disabled")

    downloaded = []

    def fake_download(url, out_dir):
        downloaded.append(url)
        p = Path(out_dir) / "a.m4a"
        p.write_bytes(b"\x00")
        return p

    class FakeTranscriber:
        def transcribe(self, audio_path):
            return [TranscriptSegment(0.0, 3.0, "transcribed text")]

    doc = load_youtube(URL, fetch_transcript=failing_fetch, transcriber=FakeTranscriber(),
                       download_audio=fake_download, work_dir=tmp_path)
    assert downloaded == [URL]
    assert doc.units[0].text == "transcribed text"
    assert doc.units[0].locator.kind == "time"


def test_no_captions_no_transcriber_raises():
    def failing_fetch(video_id):
        raise RuntimeError("captions disabled")

    with pytest.raises(TranscriptUnavailable):
        load_youtube(URL, fetch_transcript=failing_fetch)


def test_heavy_imports_lazy():
    import sys

    import mslearn.adapters.youtube  # noqa: F401

    assert "youtube_transcript_api" not in sys.modules
    assert "yt_dlp" not in sys.modules
