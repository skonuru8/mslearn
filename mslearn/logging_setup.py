"""Terse terminal signal, independent of the framework log levels.

dev_up.sh runs celery at `-l warning` and uvicorn at `--log-level warning`
so `logging.getLogger(__name__).info(...)` calls scattered through the
codebase are silently swallowed — provider errors, chunk failures, and
synthesis results never reach the terminal even though they're the events
a developer watching `make run` actually cares about. The `"mslearn"`
logger configured here is separate from root/uvicorn/celery's logging
config: its own handler, its own level, `propagate=False` so it is never
gated by whatever the framework loggers are set to.
"""

import logging
import sys

_LOGGER_NAME = "mslearn"


def configure_event_log() -> logging.Logger:
    """Idempotent: safe to call from both server/app.py and worker/app.py
    (and repeatedly, e.g. across test app construction) without stacking
    duplicate handlers."""
    logger = logging.getLogger(_LOGGER_NAME)
    if not any(getattr(h, "_mslearn_event_log", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        handler._mslearn_event_log = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
