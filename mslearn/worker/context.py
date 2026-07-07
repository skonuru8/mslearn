import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_context = None


@dataclass
class PipelineContext:
    settings: object
    db: object
    router: object
    graph: object
    memory: object | None = None
    # Shared whisper transcriber for audio / caption-less-video ingest. A
    # Transcriber wraps a faster_whisper model and is NOT serializable, so it
    # can't ride through Celery task args — it lives here and is constructed
    # once per worker process. Construction is cheap (the heavy faster_whisper
    # import + model load is deferred to the first .transcribe()); tests that
    # never touch audio can leave it None.
    transcriber: object | None = None
    # Compiled LangGraph extraction graph, built once per worker process
    # instead of once per chunk (build_extraction_graph re-reads every
    # extraction/trust tunable and recompiles the StateGraph — real overhead
    # at chunk volume). None is a valid value for tests/callers that never
    # touch extract_chunk_task; build_default_context always populates it, and
    # a worker restart re-reads the tunables.
    extraction_graph: object | None = None


def set_context(context: PipelineContext) -> None:
    global _context
    _context = context


def get_context() -> PipelineContext:
    if _context is None:
        raise RuntimeError("pipeline context not initialised (worker_process_init not run?)")
    return _context


def build_default_context() -> PipelineContext:
    from mslearn.graph.store import GraphStore
    from mslearn.opsdb import OpsDB
    from mslearn.pipeline.extraction_graph import build_extraction_graph
    from mslearn.profiles import load_profiles
    from mslearn.providers.router import ModelRouter
    from mslearn.settings import get_settings

    settings = get_settings()
    db = OpsDB(settings.ops_db)
    router = ModelRouter(load_profiles(settings.profiles_path), db, settings)
    graph = GraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    memory = None
    try:
        from mslearn.memory.sqlite_memory import SqliteMemory

        memory = SqliteMemory(db, router)
    except Exception as exc:
        logger.warning("learner memory disabled: %s", exc)
    transcriber = _build_transcriber(settings)
    # Built once here, at worker-process init, not per chunk — see
    # extraction_graph field docstring above.
    extraction_graph = build_extraction_graph(router, db)
    return PipelineContext(
        settings=settings, db=db, router=router, graph=graph,
        memory=memory, transcriber=transcriber, extraction_graph=extraction_graph,
    )


def _build_transcriber(settings) -> object:
    """Construct the shared whisper transcriber for this worker process.

    Cheap: FasterWhisperTranscriber defers both the `faster_whisper` import
    and the model download/load to the first `.transcribe()` call, so a worker
    that never ingests audio pays nothing. Wrapped in SerializingTranscriber so
    two ingest slots (`--concurrency=2`) never load two whisper models at once
    and blow the 18 GB memory budget.
    """
    from mslearn.transcribe import FasterWhisperTranscriber, SerializingTranscriber

    base = FasterWhisperTranscriber(model_name=settings.whisper_model)
    return SerializingTranscriber(base, settings.data_dir / "whisper.lock")
