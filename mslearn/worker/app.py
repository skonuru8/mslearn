from celery import Celery
from celery.signals import worker_process_init

from mslearn.logging_setup import configure_event_log
from mslearn.settings import get_settings

configure_event_log()

app = Celery("mslearn", broker=get_settings().redis_url)
app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=False,
    result_backend="cache+memory://",  # eager tests read results; prod workers don't rely on it
    broker_connection_retry_on_startup=True,
    # Every task MUST be routed to a queue the worker consumes (ingest, judge)
    # — an unrouted task lands in the default "celery" queue, which nothing
    # consumes, and its source sits in "Preparing…" forever. Guarded by
    # test_all_tasks_routed_to_consumed_queues.
    task_routes={
        "mslearn.worker.tasks.chunk_source_task": {"queue": "ingest"},
        "mslearn.worker.tasks.extract_chunk_task": {"queue": "ingest"},
        "mslearn.worker.tasks.synthesize_task": {"queue": "judge"},
    },
)
app.autodiscover_tasks(["mslearn.worker"])


@worker_process_init.connect
def _init_worker_process(**_kwargs):
    from mslearn.worker.context import build_default_context, set_context

    set_context(build_default_context())


def worker_online(timeout: float = 1.0) -> bool:
    """Best-effort check for a live Celery worker (used by /api/admin/health
    and the synthesize/synthesis-status endpoints so the UI can say plainly
    "worker offline" instead of silently no-op'ing an enqueue)."""
    try:
        pong = app.control.ping(timeout=timeout)
    except Exception:
        return False
    return bool(pong)
