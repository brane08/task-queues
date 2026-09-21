"""ThreadPoolExecutor wrapper: fixed worker count, optionally bounded queue,
per-task tracking, stuck detection, and a small query/control API.

Requires Python 3.13+.

Locking rule: ``self._lock`` is non-reentrant and is never held while running
user code, calling ``Future.cancel()`` or ``Future.add_done_callback()`` (both
may run done-callbacks inline on the calling thread).

Known limit: ``TaskInfo.stack()`` uses the recorded ``thread_id``. Just after a
task finishes that worker may already be running another task, so the stack can
briefly belong to the wrong task.
"""
import functools
import inspect
import itertools
import logging
import signal
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass, field
from typing import Any, Self, TextIO

log = logging.getLogger(__name__)


class QueueFull(RuntimeError):
    """submit() could not acquire a slot (bounded executor is at capacity)."""


@dataclass
class TaskInfo:
    label: str
    submitted: float
    future: Future | None = None
    started: float | None = None
    thread_id: int | None = None
    alerted: bool = False  # set once by the watchdog so on_stuck fires once
    cancel_event: threading.Event = field(default_factory=threading.Event)
    stuck_after: float | None = None  # per-task override of the executor default

    @property
    def state(self) -> str:
        f = self.future
        if f is not None and f.done():
            return "cancelled" if f.cancelled() else "done"
        return "running" if self.started is not None else "pending"

    def age(self, now: float | None = None) -> float:
        """Seconds running if started, else seconds waiting in the queue."""
        now = time.monotonic() if now is None else now
        return now - (self.started if self.started is not None else self.submitted)

    def stack(self) -> str:
        if self.state != "running" or self.thread_id is None:
            return "<not running>"
        frame = sys._current_frames().get(self.thread_id)
        return "".join(traceback.format_stack(frame)) if frame else "<no frame>"


def _accepts_cancel_event(fn: Callable) -> bool:
    """True if fn explicitly declares a keyword-passable ``cancel_event``
    parameter that is not already bound (e.g. via functools.partial)."""
    if isinstance(fn, functools.partial) and "cancel_event" in (fn.keywords or {}):
        return False
    try:
        param = inspect.signature(fn).parameters.get("cancel_event")
    except (TypeError, ValueError):  # some builtins have no signature
        return False
    return param is not None and param.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    )


def _should_inject(fn: Callable, args: tuple, kwargs: dict) -> bool:
    """Inject only if fn declares cancel_event and the caller didn't supply it
    (by keyword or by position)."""
    if not _accepts_cancel_event(fn) or "cancel_event" in kwargs:
        return False
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except TypeError:  # bad arguments: let the call itself raise the real error
        return False
    return "cancel_event" not in bound.arguments


class TrackedExecutor:
    def __init__(
        self,
        max_workers: int = 50,
        stuck_after: float = 600,
        check_every: float = 30,
        on_stuck: Callable[[TaskInfo], None] | None = None,
        max_queued: int | None = None,
    ):
        """max_queued=None -> unbounded queue. Otherwise submit() blocks (or
        raises QueueFull) once max_workers + max_queued tasks are live."""
        if max_queued is not None and max_queued < 0:
            raise ValueError("max_queued must be >= 0 or None")
        self._ex = ThreadPoolExecutor(max_workers, thread_name_prefix="tracked")
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskInfo] = {}  # live tasks by unique label
        self._seq = itertools.count()
        self._counts = {"submitted": 0, "completed": 0, "failed": 0, "cancelled": 0}
        self._capacity = None if max_queued is None else max_workers + max_queued
        self._slots = (
            None if self._capacity is None else threading.BoundedSemaphore(self._capacity)
        )
        self.stuck_after = stuck_after
        self.on_stuck = on_stuck
        self._stop = threading.Event()
        threading.Thread(
            target=self._watch, args=(check_every,), daemon=True, name="tracked-watchdog"
        ).start()

    # ---- submit -------------------------------------------------------
    def submit(
        self,
        fn: Callable,
        *args: Any,
        label: str | None = None,
        stuck_after: float | None = None,
        block: bool = True,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Future:
        """Submit fn(*args, **kwargs).

        Reserved keyword names (consumed here, never forwarded to fn):
        ``label``, ``stuck_after``, ``block``, ``timeout``.

        - ``label``: unique among live tasks (ValueError otherwise). A label
          becomes reusable as soon as its future is done.
        - ``stuck_after``: per-task override of the executor default (seconds
          running before the task is considered stuck).
        - ``block``/``timeout``: only meaningful when max_queued was set; raise
          QueueFull if a slot can't be acquired (immediately if block=False,
          after ``timeout`` seconds otherwise).
        - If fn declares a ``cancel_event`` parameter, a threading.Event is
          injected (unless it is already bound/passed).
        """
        label = label or f"{getattr(fn, '__name__', 'task')}#{next(self._seq)}"
        info = TaskInfo(label=label, submitted=time.monotonic(), stuck_after=stuck_after)
        inject = _should_inject(fn, args, kwargs)

        def run():
            info.started = time.monotonic()
            info.thread_id = threading.get_ident()
            call_kwargs = {**kwargs, "cancel_event": info.cancel_event} if inject else kwargs
            return fn(*args, **call_kwargs)

        if self._slots is not None:
            got = (
                self._slots.acquire(True, timeout)
                if block
                else self._slots.acquire(False)
            )
            if not got:
                raise QueueFull(
                    f"executor at capacity ({self._capacity} live tasks); "
                    f"could not submit {label!r}"
                    + (f" within {timeout}s" if block and timeout is not None else "")
                )
        try:
            fut = self._register_and_submit(info, run)
        except BaseException:
            if self._slots is not None:
                self._slots.release()
            raise
        info.future = fut
        # Registered outside the lock: runs inline if fut is already done.
        fut.add_done_callback(lambda f: self._on_done(info, f))
        return fut

    def _register_and_submit(self, info: TaskInfo, run: Callable) -> Future:
        label = info.label
        with self._lock:
            old = self._tasks.get(label)
            # A done future whose callback hasn't run yet no longer owns the label.
            if old is not None and not (old.future is not None and old.future.done()):
                raise ValueError(f"duplicate live task label: {label!r}")
            self._tasks[label] = info
            self._counts["submitted"] += 1
        try:
            return self._ex.submit(run)
        except BaseException:  # e.g. RuntimeError after shutdown
            with self._lock:
                if self._tasks.get(label) is info:
                    del self._tasks[label]
                self._counts["submitted"] -= 1
            raise

    def map(self, fn: Callable, items: Iterable, label_fn: Callable = str) -> list[Future]:
        return [self.submit(fn, x, label=label_fn(x)) for x in items]

    # ---- query --------------------------------------------------------
    def tasks(self, state: str | None = None) -> list[TaskInfo]:
        with self._lock:
            infos = list(self._tasks.values())
        return [t for t in infos if state is None or t.state == state]

    def pending(self) -> list[TaskInfo]:
        return self.tasks("pending")

    def running(self) -> list[TaskInfo]:
        return self.tasks("running")

    def _limit(self, t: TaskInfo) -> float:
        return t.stuck_after if t.stuck_after is not None else self.stuck_after

    def stuck(self, older_than: float | None = None) -> list[TaskInfo]:
        """Running tasks older than their limit (``older_than`` overrides every
        per-task and default limit when given)."""
        now = time.monotonic()
        return [
            t
            for t in self.running()
            if t.age(now) > (older_than if older_than is not None else self._limit(t))
        ]

    def find(self, label: str) -> TaskInfo | None:
        with self._lock:
            return self._tasks.get(label)

    def stats(self) -> dict:
        infos = self.tasks()
        with self._lock:
            counts = dict(self._counts)
        counts["pending"] = sum(t.state == "pending" for t in infos)
        counts["running"] = sum(t.state == "running" for t in infos)
        return counts

    def snapshot(self) -> list[dict]:
        """JSON-serializable view of live tasks (oldest first), e.g. for /health."""
        now = time.monotonic()
        infos = sorted(self.tasks(), key=lambda t: -t.age(now))
        return [
            {
                "label": t.label,
                "state": t.state,
                "age": round(t.age(now), 3),
                "thread_id": t.thread_id,
            }
            for t in infos
        ]

    def dump(self, file: TextIO | None = None, stacks: bool = True) -> None:
        file = sys.stderr if file is None else file
        now = time.monotonic()
        print(f"== executor {self.stats()}", file=file)
        for t in sorted(self.tasks(), key=lambda t: -t.age(now)):
            print(f"{t.state:9} {t.age(now):8.1f}s  {t.label}", file=file)
            if stacks and t.state == "running" and t.age(now) > self._limit(t):
                print(t.stack(), file=file)

    def install_dump_signal(self, sig: int | None = None) -> bool:
        """Dump executor state to stderr on ``sig`` (default SIGUSR1).

        Returns True if installed. No-op (False) on platforms without the
        signal (Windows) or when not called from the main thread. The dump runs
        on a helper thread: a handler runs on the main thread and would
        deadlock if that thread was holding the non-reentrant lock.
        """
        if sig is None:
            sig = getattr(signal, "SIGUSR1", None)  # type: ignore[assignment]
        if sig is None or threading.current_thread() is not threading.main_thread():
            return False

        def handler(signum, frame):
            threading.Thread(target=self.dump, daemon=True, name="tracked-dump").start()

        signal.signal(sig, handler)
        return True

    # ---- control ------------------------------------------------------
    def cancel(self, label: str) -> str:
        """'cancelled' (was pending), 'signalled' (running; cancel_event set,
        task must cooperate), or 'missing' (unknown label or already finished)."""
        t = self.find(label)
        if t is None or (t.future is not None and t.future.done()):
            return "missing"
        t.cancel_event.set()
        if t.future is not None and t.future.cancel():
            return "cancelled"
        return "signalled"

    def cancel_pending(self, pred: Callable[[TaskInfo], bool] = lambda t: True) -> int:
        return sum(1 for t in self.pending() if pred(t) and t.future and t.future.cancel())

    def wait(self, timeout: float | None = None):
        futs = [t.future for t in self.tasks() if t.future is not None]
        return futures_wait(futs, timeout=timeout)

    def shutdown(self, wait: bool = True, cancel_pending: bool = False) -> None:
        self._stop.set()
        self._ex.shutdown(wait=wait, cancel_futures=cancel_pending)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()

    # ---- internals ----------------------------------------------------
    def _on_done(self, info: TaskInfo, fut: Future) -> None:
        with self._lock:
            if self._tasks.get(info.label) is info:
                del self._tasks[info.label]
            if fut.cancelled():
                self._counts["cancelled"] += 1
            elif fut.exception() is not None:
                self._counts["failed"] += 1
            else:
                self._counts["completed"] += 1
        if self._slots is not None:
            self._slots.release()

    def _log_stuck(self, t: TaskInfo) -> None:
        log.error("STUCK %s running %.0fs\n%s", t.label, t.age(), t.stack())

    def _check_stuck(self) -> None:
        for t in self.stuck():
            if t.alerted:
                continue
            t.alerted = True
            try:
                (self.on_stuck or self._log_stuck)(t)
            except Exception:
                log.exception("on_stuck handler failed for %s", t.label)

    def _watch(self, interval: float) -> None:
        while not self._stop.wait(interval):
            self._check_stuck()
