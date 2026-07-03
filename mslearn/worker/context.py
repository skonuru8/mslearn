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
    from mslearn.profiles import load_profiles
    from mslearn.providers.router import ModelRouter
    from mslearn.settings import get_settings

    settings = get_settings()
    db = OpsDB(settings.ops_db)
    router = ModelRouter(load_profiles(settings.profiles_path), db, settings)
    graph = GraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    memory = None
    try:
        from mslearn.memory.mem0_impl import Mem0Memory

        memory = Mem0Memory(settings, db)
    except ImportError:
        logger.warning("mem0 not installed; learner memory disabled")
    return PipelineContext(settings=settings, db=db, router=router, graph=graph, memory=memory)
