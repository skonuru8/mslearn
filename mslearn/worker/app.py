from celery import Celery
from celery.signals import worker_process_init

from mslearn.settings import get_settings

app = Celery("mslearn", broker=get_settings().redis_url)
app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=False,
    result_backend="cache+memory://",  # eager tests read results; prod workers don't rely on it
    broker_connection_retry_on_startup=True,
    task_routes={
        "mslearn.worker.tasks.extract_chunk_task": {"queue": "ingest"},
    },
)
app.autodiscover_tasks(["mslearn.worker"])


@worker_process_init.connect
def _init_worker_process(**_kwargs):
    from mslearn.worker.context import build_default_context, set_context

    set_context(build_default_context())
