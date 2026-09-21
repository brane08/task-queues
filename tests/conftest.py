import logging
import threading
import time

import pytest

from tracked_executor import TrackedExecutor

T = 10.0  # upper bound for every wait; only reached when a test is about to fail


def wait_until(pred, timeout=T):
    """Poll pred() until true. Future.result() can return before the executor's
    own done-callback (bookkeeping) has run, so counters/removal need polling."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.001)
    raise AssertionError("condition not reached before timeout")


def within(fn, timeout=T):
    """Run fn on a thread; fail (instead of hanging the suite) on deadlock."""
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "deadlock: call did not return"
    if "error" in box:
        raise box["error"]
    return box.get("value")


@pytest.fixture(autouse=True)
def _no_callback_errors():
    """Future swallows exceptions raised by done-callbacks and only logs them
    ('exception calling callback'); surface those as test failures."""
    records: list[logging.LogRecord] = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record)

    h, lg = H(level=logging.ERROR), logging.getLogger("concurrent.futures")
    lg.addHandler(h)
    yield
    lg.removeHandler(h)
    assert not records, [r.getMessage() for r in records]


_gates: list[threading.Event] = []


@pytest.fixture(autouse=True)
def _release_gates():
    yield
    for g in _gates:
        g.set()
    _gates.clear()


def hold(ex, label="blocker", **kw):
    """Occupy one worker until the returned gate is set -> (future, gate)."""
    started, gate = threading.Event(), threading.Event()
    _gates.append(gate)

    def _hold():
        started.set()
        gate.wait(T)

    fut = ex.submit(_hold, label=label, **kw)
    assert started.wait(T)
    return fut, gate


def live_watchdogs():
    return [t for t in threading.enumerate() if t.name == "tracked-watchdog"]


@pytest.fixture
def ex():
    """Single worker, watchdog effectively off."""
    e = TrackedExecutor(max_workers=1, check_every=3600)
    yield e
    e.shutdown(wait=False, cancel_pending=True)


@pytest.fixture
def make():
    """Factory for executors that are always cleaned up."""
    made = []

    def factory(**kw):
        kw.setdefault("check_every", 3600)
        e = TrackedExecutor(**kw)
        made.append(e)
        return e

    yield factory
    for e in made:
        e.shutdown(wait=False, cancel_pending=True)
