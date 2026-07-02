from pathlib import Path

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id
from mslearn.transcribe import Transcriber


def load_audio(path: Path | str, transcriber: Transcriber,
               role: str = "supplement") -> SourceDocument:
    path = Path(path)
    units: list[StructuralUnit] = []
    for seg in transcriber.transcribe(path):
        text = seg.text.strip()
        if not text:
            continue
        units.append(
            StructuralUnit(
                index=len(units), title="", text=text,
                locator=Locator(kind="time", start_s=seg.start_s, end_s=seg.end_s),
            )
        )
    return SourceDocument(
        source_id=make_source_id(str(path)), source_type="audio",
        role=role, title=path.stem, units=units,
    )
