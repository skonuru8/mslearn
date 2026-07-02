import re
import tempfile
from pathlib import Path
from typing import Callable

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id
from mslearn.transcribe import Transcriber


class TranscriptUnavailable(Exception):
    """No captions available and no transcriber was provided."""


_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")


def video_id_of(url: str) -> str:
    match = _ID_RE.search(url)
    if not match:
        raise ValueError(f"cannot parse a YouTube video id from {url!r}")
    return match.group(1)


def _default_fetch(video_id: str) -> list[dict]:
    from youtube_transcript_api import YouTubeTranscriptApi  # lazy

    return YouTubeTranscriptApi().fetch(video_id).to_raw_data()


def _default_download_audio(url: str, out_dir: Path) -> Path:
    import yt_dlp  # lazy

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


def load_youtube(
    url: str,
    role: str = "supplement",
    *,
    fetch_transcript: Callable[[str], list[dict]] | None = None,
    transcriber: Transcriber | None = None,
    download_audio: Callable[[str, Path], Path] | None = None,
    work_dir: Path | None = None,
) -> SourceDocument:
    video_id = video_id_of(url)
    fetch = fetch_transcript or _default_fetch
    try:
        raw = fetch(video_id)
    except Exception:
        # Caption absence surfaces as library-specific exceptions; every
        # failure routes to the transcription fallback below.
        raw = None

    units: list[StructuralUnit] = []
    if raw is not None:
        for entry in raw:
            text = entry["text"].strip()
            if not text:
                continue
            start = float(entry["start"])
            units.append(
                StructuralUnit(
                    index=len(units), title="", text=text,
                    locator=Locator(kind="time", url=url, start_s=start,
                                    end_s=start + float(entry.get("duration", 0.0))),
                )
            )
    else:
        if transcriber is None:
            raise TranscriptUnavailable(
                f"no captions for {url!r} and no transcriber provided"
            )
        downloader = download_audio or _default_download_audio
        target_dir = work_dir or Path(tempfile.mkdtemp(prefix="mslearn-yt-"))
        audio_path = downloader(url, target_dir)
        for seg in transcriber.transcribe(audio_path):
            text = seg.text.strip()
            if not text:
                continue
            units.append(
                StructuralUnit(
                    index=len(units), title="", text=text,
                    locator=Locator(kind="time", url=url,
                                    start_s=seg.start_s, end_s=seg.end_s),
                )
            )
    return SourceDocument(
        source_id=make_source_id(url), source_type="youtube",
        role=role, title=url, units=units,
    )
