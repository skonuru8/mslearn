from dataclasses import dataclass

_context = None


@dataclass
class PipelineContext:
    settings: object
    db: object
    router: object
    graph: object


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
    return PipelineContext(settings=settings, db=db, router=router, graph=graph)
