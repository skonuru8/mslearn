"""SerializingTranscriber bounds concurrent whisper transcription (audit row 8)."""
from __future__ import annotations

import threading
import time

from mslearn.transcribe import SerializingTranscriber, TranscriptSegment


class RecordingTranscriber:
    """Inner transcriber that records how many transcriptions overlap."""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def transcribe(self, audio_path):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.05)
        with self._lock:
            self.active -= 1
        return [TranscriptSegment(0.0, 1.0, f"seg for {audio_path}")]


def test_delegates_to_inner(tmp_path):
    inner = RecordingTranscriber()
    wrapped = SerializingTranscriber(inner, tmp_path / "whisper.lock")
    segs = wrapped.transcribe(tmp_path / "a.mp3")
    assert segs[0].text.endswith("a.mp3")


def test_transcriptions_never_overlap(tmp_path):
    inner = RecordingTranscriber()
    wrapped = SerializingTranscriber(inner, tmp_path / "whisper.lock")

    threads = [
        threading.Thread(target=wrapped.transcribe, args=(tmp_path / f"{i}.mp3",))
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert inner.max_active == 1  # serialized: never two at once
