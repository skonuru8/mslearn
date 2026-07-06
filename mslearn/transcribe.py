import threading
from contextlib import contextmanager
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


# One transcription at a time within a single worker process. The file lock
# below extends the same guarantee across processes; this keeps threads inside
# one process orderly and covers the case where flock is unavailable.
_PROCESS_LOCK = threading.Lock()


class SerializingTranscriber:
    """Wrap a Transcriber so only one .transcribe() runs at a time per machine.

    The ingest worker runs prefork `--concurrency=2` (two OS processes), so two
    sources could each load a whisper model simultaneously; on the 18 GB M3 that
    plus a resident Ollama model collides. An advisory file lock (`flock`) bounds
    transcription to one at a time across every ingest slot; an in-process
    threading lock serializes threads within a process and is the fallback where
    `flock` isn't available. Non-transcribing chunk work keeps its parallelism —
    only the `.transcribe()` critical section is serialized.
    """

    def __init__(self, inner: Transcriber, lock_path: Path | str):
        self._inner = inner
        self._lock_path = Path(lock_path)

    def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        with self._serialized():
            return self._inner.transcribe(audio_path)

    @contextmanager
    def _serialized(self):
        with _PROCESS_LOCK:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._lock_path, "w") as handle:
                try:
                    import fcntl  # POSIX only; darwin/linux have it

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except (ImportError, OSError):
                    pass  # threading lock above still serializes this process
                yield


class FasterWhisperTranscriber:
    # "small" + int8 keeps the resident footprint modest (~0.5 GB) so whisper
    # can coexist with a resident Ollama model on an 18 GB machine — see
    # SerializingTranscriber for why only one loads at a time.
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
