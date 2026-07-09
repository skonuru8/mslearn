import threading
import time

import pytest

import mslearn.worker.context as context_module
from mslearn.worker.context import get_context, set_context


@pytest.fixture(autouse=True)
def _reset_context():
    """These tests mutate the module-global `_context`; save/restore it so
    the suite stays order-independent regardless of what ran before/after."""
    saved = context_module._context
    context_module._context = None
    yield
    context_module._context = saved


def test_get_context_builds_lazily_when_no_signal_fired(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(context_module, "build_default_context", lambda: sentinel)

    result = get_context()

    assert result is sentinel


def test_get_context_builds_exactly_once_under_thread_race(monkeypatch):
    build_count = 0
    lock = threading.Lock()

    def fake_build():
        nonlocal build_count
        with lock:
            build_count += 1
        time.sleep(0.05)
        return object()

    monkeypatch.setattr(context_module, "build_default_context", fake_build)

    results = []
    results_lock = threading.Lock()

    def worker():
        ctx = get_context()
        with results_lock:
            results.append(ctx)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert build_count == 1
    assert len(results) == 8
    assert all(r is results[0] for r in results)


def test_set_context_overrides_lazy_build(monkeypatch):
    def _raise():
        raise AssertionError("build_default_context should not be called when context is set")

    monkeypatch.setattr(context_module, "build_default_context", _raise)

    fake = object()
    set_context(fake)

    assert get_context() is fake
