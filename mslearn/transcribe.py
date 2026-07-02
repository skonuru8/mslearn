from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> list[TranscriptSegment]: ...


class FasterWhisperTranscriber:
    def __init__(self, model_name: str = "small", device: str = "auto",
                 compute_type: str = "int8"):
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # heavy — lazy import

            self._model = WhisperModel(
                self._model_name, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        segments, _info = self._load().transcribe(str(audio_path))
        return [TranscriptSegment(s.start, s.end, s.text.strip()) for s in segments]
