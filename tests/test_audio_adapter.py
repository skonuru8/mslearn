from pathlib import Path

from mslearn.adapters.audio import load_audio
from mslearn.transcribe import TranscriptSegment


class FakeTranscriber:
    def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        return [
            TranscriptSegment(0.0, 4.2, "Welcome to the show."),
            TranscriptSegment(4.2, 9.8, "Today we discuss caching."),
            TranscriptSegment(9.8, 10.0, "   "),  # whitespace-only -> dropped
        ]


def test_load_audio_units_and_time_locators(tmp_path):
    audio = tmp_path / "episode.mp3"
    audio.write_bytes(b"\x00")  # adapter never reads it; transcriber is faked
    doc = load_audio(audio, transcriber=FakeTranscriber())
    assert doc.source_type == "audio" and doc.title == "episode"
    assert len(doc.units) == 2
    loc = doc.units[1].locator
    assert loc.kind == "time" and loc.start_s == 4.2 and loc.end_s == 9.8


def test_heavy_import_is_lazy():
    import sys

    import mslearn.transcribe  # noqa: F401

    assert "faster_whisper" not in sys.modules
