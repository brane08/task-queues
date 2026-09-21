"""Construction, TaskInfo, submit(), map(), cancel_event injection."""
import functools
import sys
import threading
import time
from concurrent.futures import Future

import pytest
from conftest import T, hold, live_watchdogs, wait_until

from tracked_executor import (
    TaskInfo,
    TrackedExecutor,
    _accepts_cancel_event,
    _should_inject,
)


def test_python_version():
    assert sys.version_info >= (3, 13)


# ---- construction -----------------------------------------------------------
def test_defaults(make):
    e = make()
    assert e.stuck_after == 600 and e.on_stuck is None
    assert e.stats() == {
        "submitted": 0, "completed": 0, "failed": 0, "cancelled": 0,
        "pending": 0, "running": 0,
    }  # fmt: skip


@pytest.mark.parametrize("kw", [{"max_queued": -1}, {"max_workers": 0}, {"max_workers": -3}])
def test_invalid_args_raise_and_start_no_watchdog(kw):
    before = set(live_watchdogs())
    with pytest.raises(ValueError):
        TrackedExecutor(check_every=3600, **kw)
    assert set(live_watchdogs()) <= before


def test_watchdog_thread_is_daemon_and_stops_on_shutdown():
    before = set(live_watchdogs())
    e = TrackedExecutor(max_workers=1, check_every=3600)
    (wd,) = set(live_watchdogs()) - before
    assert wd.daemon
    e.shutdown()
    wait_until(lambda: not wd.is_alive())


def test_executors_are_independent(make):
    a, b = make(max_workers=1), make(max_workers=1)
    a.submit(lambda: 1, label="same").result(T)
    b.submit(lambda: 2, label="same").result(T)  # same label, different executor
    wait_until(lambda: a.stats()["completed"] == 1 and b.stats()["completed"] == 1)


# ---- TaskInfo ---------------------------------------------------------------
def test_taskinfo_defaults_and_positional_compat():
    a, b = TaskInfo("a", 1.0), TaskInfo("b", 2.0)
    assert (a.future, a.started, a.thread_id, a.alerted, a.stuck_after) == (
        None, None, None, False, None,
    )  # fmt: skip
    assert a.cancel_event is not b.cancel_event
    assert not a.cancel_event.is_set()


def test_taskinfo_state_machine():
    t = TaskInfo("a", 0.0)
    assert t.state == "pending"  # no future yet
    t.future = Future()
    assert t.state == "pending"
    t.started = 1.0
    assert t.state == "running"
    t.future.set_result(1)
    assert t.state == "done"

    c = TaskInfo("c", 0.0, future=Future())
    assert c.future.cancel()
    assert c.state == "cancelled"

    failed = TaskInfo("f", 0.0, future=Future())
    failed.future.set_exception(ValueError())
    assert failed.state == "done"  # failure is still "done"


def test_taskinfo_age():
    assert TaskInfo("a", submitted=10.0).age(now=15.0) == 5.0  # queued
    assert TaskInfo("a", submitted=10.0, started=12.0).age(now=15.0) == 3.0  # running
    now = time.monotonic()
    assert TaskInfo("a", submitted=now - 1).age() >= 1.0


def test_taskinfo_stack_variants():
    t = TaskInfo("a", 0.0)
    assert t.stack() == "<not running>"  # pending
    t.started, t.thread_id = 1.0, -1
    assert t.state == "running" and t.stack() == "<no frame>"  # unknown thread
    t.thread_id = None
    assert t.stack() == "<not running>"
    t.thread_id, t.future = threading.get_ident(), Future()
    t.future.set_result(1)
    assert t.stack() == "<not running>"  # done


def test_taskinfo_stack_of_real_running_task(ex):
    started, gate = threading.Event(), threading.Event()

    def marker_inner():
        gate.wait(T)

    def marker_task():
        started.set()
        marker_inner()

    ex.submit(marker_task, label="m")
    assert started.wait(T)
    stack = ex.find("m").stack()
    assert "marker_inner" in stack and "marker_task" in stack
    gate.set()


# ---- submit basics ----------------------------------------------------------
def test_submit_result_args_kwargs(ex):
    assert ex.submit(lambda a, b=0: a + b, 1, b=2, label="s").result(T) == 3


def test_submit_exception_propagates_same_object_and_counts(ex):
    err = ValueError("boom")

    def boom():
        raise err

    fut = ex.submit(boom, label="b")
    assert fut.exception(T) is err
    wait_until(lambda: ex.stats()["failed"] == 1)
    assert ex.stats()["completed"] == 0


def test_submit_non_callable_fails_in_future_not_in_submit(ex):
    fut = ex.submit(None, label="nc")  # type: ignore[arg-type]
    assert isinstance(fut.exception(T), TypeError)
    wait_until(lambda: ex.stats()["failed"] == 1)
    assert ex.tasks() == []


def test_reserved_kwargs_are_not_forwarded(ex):
    def echo(**kw):
        return kw

    fut = ex.submit(echo, other=1, label="x", stuck_after=5, block=True, timeout=1)
    assert fut.result(T) == {"other": 1}


def named_callable():
    return 1


class Callable_:
    def __call__(self):
        return 1


@pytest.mark.parametrize(
    "fn, prefix",
    [
        (named_callable, "named_callable#"),
        (lambda: 1, "<lambda>#"),
        (functools.partial(named_callable), "task#"),
        (Callable_(), "task#"),
    ],
)
def test_auto_labels(ex, fn, prefix):
    _, gate = hold(ex)
    ex.submit(fn)
    assert next(t.label for t in ex.pending()).startswith(prefix)
    gate.set()


@pytest.mark.parametrize("empty", [None, ""])
def test_auto_label_when_label_falsy_and_unique(ex, empty):
    _, gate = hold(ex)
    ex.submit(lambda: 1, label=empty)
    ex.submit(lambda: 1, label=empty)
    labels = [t.label for t in ex.pending()]
    assert len(labels) == 2 and len(set(labels)) == 2
    assert all(lbl.startswith("<lambda>#") for lbl in labels)
    gate.set()


def test_unicode_and_odd_labels(ex):
    for lbl in ("täsk-✓", " ", "a/b:c", "x" * 500):
        assert ex.submit(lambda: 1, label=lbl).result(T) == 1


def test_fifo_order_single_worker(ex):
    _, gate = hold(ex)
    order = []
    futs = [ex.submit(order.append, i, label=f"t{i}") for i in range(20)]
    gate.set()
    for f in futs:
        f.result(T)
    assert order == list(range(20))


def test_workers_run_concurrently_up_to_max(make):
    e = make(max_workers=3)
    barrier = threading.Barrier(3, timeout=T)
    futs = [e.submit(barrier.wait, label=f"b{i}") for i in range(3)]
    for f in futs:
        f.result(T)  # would BrokenBarrier/timeout unless all 3 ran at once


def test_never_exceeds_max_workers(make):
    e = make(max_workers=3)
    lock, state = threading.Lock(), {"now": 0, "max": 0}

    def work():
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.005)
        with lock:
            state["now"] -= 1

    for f in [e.submit(work, label=f"w{i}") for i in range(30)]:
        f.result(T)
    assert 1 <= state["max"] <= 3


def test_task_thread_identity_and_running_info(make):
    e = make(max_workers=1)
    seen = {}

    def probe():
        info = e.find("probe")
        seen.update(
            tid=threading.get_ident(),
            name=threading.current_thread().name,
            info_tid=info.thread_id,
            state=info.state,
            started=info.started,
        )

    e.submit(probe, label="probe").result(T)
    assert seen["name"].startswith("tracked")
    assert seen["info_tid"] == seen["tid"]
    assert seen["state"] == "running" and seen["started"] is not None


def test_large_fanout(make):
    e = make(max_workers=8)
    futs = [e.submit(lambda i=i: i * i, label=f"n{i}") for i in range(500)]
    assert [f.result(T) for f in futs] == [i * i for i in range(500)]
    wait_until(lambda: e.stats()["completed"] == 500)
    assert e.tasks() == []
    s = e.stats()
    assert s["submitted"] == 500 and s["pending"] == s["running"] == 0


# ---- labels -----------------------------------------------------------------
def test_duplicate_live_label_rejected_pending_and_running(ex):
    _, gate = hold(ex)
    ex.submit(lambda: None, label="queued")
    for lbl in ("blocker", "queued"):
        with pytest.raises(ValueError, match="duplicate live task label"):
            ex.submit(lambda: None, label=lbl)
    assert ex.stats()["submitted"] == 2  # rejected submits are not counted
    gate.set()


def test_label_reusable_after_success_failure_and_cancel(ex):
    ex.submit(lambda: 1, label="x").result(T)
    assert ex.submit(lambda: 2, label="x").result(T) == 2  # immediately, no polling

    def boom():
        raise RuntimeError

    with pytest.raises(RuntimeError):
        ex.submit(boom, label="x").result(T)
    assert ex.submit(lambda: 3, label="x").result(T) == 3

    _, gate = hold(ex)
    ex.submit(lambda: 4, label="x")
    assert ex.cancel("x") == "cancelled"
    assert ex.submit(lambda: 5, label="x")  # reusable after cancel
    gate.set()


def test_reused_label_is_not_removed_by_old_callback(ex):
    """Old task's done-callback must not delete the new task registered under
    the same label."""
    ex.submit(lambda: 1, label="x").result(T)
    _, gate = hold(ex)
    new = ex.submit(lambda: 2, label="x")
    wait_until(lambda: ex.stats()["completed"] >= 1)
    assert ex.find("x").future is new
    gate.set()
    assert new.result(T) == 2


# ---- map --------------------------------------------------------------------
def test_map_results_order_and_labels(ex):
    _, gate = hold(ex)
    futs = ex.map(lambda x: x * 2, [3, 1, 2], label_fn=lambda x: f"item-{x}")
    assert [t.label for t in ex.pending()] == ["item-3", "item-1", "item-2"]
    gate.set()
    assert [f.result(T) for f in futs] == [6, 2, 4]


def test_map_default_label_is_str_and_empty_ok(ex):
    assert ex.map(lambda x: x, []) == []
    _, gate = hold(ex)
    ex.map(lambda x: x, [10, 20])
    assert {t.label for t in ex.pending()} == {"10", "20"}
    gate.set()


def test_map_duplicate_labels_raise_after_earlier_items_submitted(ex):
    _, gate = hold(ex)
    with pytest.raises(ValueError):
        ex.map(lambda x: x, [1, 2, 1])
    assert {t.label for t in ex.pending()} == {"1", "2"}  # documented partial submit
    gate.set()


# ---- cancel_event detection / injection --------------------------------------
def _pos_or_kw(cancel_event):
    return cancel_event


def _kw_only(*, cancel_event):
    return cancel_event


def _pos_only(cancel_event, /):
    return cancel_event


def _var_kw(**kwargs):
    return kwargs


def _var_pos(*args):
    return args


def _other(x):
    return x


class _Obj:
    def method(self, cancel_event):
        return cancel_event

    def plain(self):
        return None

    def __call__(self, cancel_event):
        return cancel_event


@pytest.mark.parametrize(
    "fn, expected",
    [
        (_pos_or_kw, True),
        (_kw_only, True),
        (_pos_only, False),
        (_var_kw, False),
        (_var_pos, False),
        (_other, False),
        (_Obj().method, True),
        (_Obj().plain, False),
        (_Obj(), True),
        (functools.partial(_pos_or_kw), True),
        (functools.partial(_pos_or_kw, cancel_event=1), False),
        (functools.partial(functools.partial(_pos_or_kw, cancel_event=1)), False),
        (functools.partial(_other, 1), False),
        (len, False),
        (time.sleep, False),
        (print, False),
        (None, False),
        (42, False),
    ],
)
def test_accepts_cancel_event(fn, expected):
    assert _accepts_cancel_event(fn) is expected


def test_should_inject_respects_supplied_values():
    ev = threading.Event()
    assert _should_inject(_pos_or_kw, (), {})
    assert not _should_inject(_pos_or_kw, (ev,), {})  # positional
    assert not _should_inject(_pos_or_kw, (), {"cancel_event": ev})  # keyword
    assert not _should_inject(_pos_or_kw, (1, 2), {})  # bad arity -> let call fail
    assert _should_inject(_kw_only, (), {})


def test_injected_event_is_the_tasks_cancel_event(ex):
    seen = {}

    def task(cancel_event):
        seen["ev"] = cancel_event
        seen["info_ev"] = ex.find("t").cancel_event

    ex.submit(task, label="t").result(T)
    assert seen["ev"] is seen["info_ev"] and not seen["ev"].is_set()


def test_each_task_gets_its_own_event(make):
    e = make(max_workers=2)
    evs = []
    barrier = threading.Barrier(2, timeout=T)

    def task(cancel_event):
        evs.append(cancel_event)
        barrier.wait()

    for f in [e.submit(task, label=f"t{i}") for i in range(2)]:
        f.result(T)
    assert evs[0] is not evs[1]


def test_supplied_positional_and_keyword_events_win(ex):
    mine = threading.Event()
    assert ex.submit(_pos_or_kw, mine, label="p").result(T) is mine
    assert ex.submit(_pos_or_kw, cancel_event=mine, label="k").result(T) is mine
    sentinel = object()
    p = functools.partial(_pos_or_kw, cancel_event=sentinel)
    assert ex.submit(p, label="b").result(T) is sentinel


def test_keyword_only_bound_method_and_callable_object_get_event(ex):
    assert isinstance(ex.submit(_kw_only, label="a").result(T), threading.Event)
    assert isinstance(ex.submit(_Obj().method, label="b").result(T), threading.Event)
    assert isinstance(ex.submit(_Obj(), label="c").result(T), threading.Event)


def test_positional_only_param_is_not_injected(ex):
    fut = ex.submit(_pos_only, label="po")
    assert isinstance(fut.exception(T), TypeError)  # missing arg surfaced, not masked


def test_no_injection_for_builtins_and_var_kw(ex):
    assert ex.submit(len, [1, 2], label="len").result(T) == 2
    assert ex.submit(_var_kw, label="vk").result(T) == {}
    assert ex.submit(_var_pos, 1, label="vp").result(T) == (1,)


def test_bad_arity_raises_natural_typeerror(ex):
    fut = ex.submit(_pos_or_kw, 1, 2, label="bad")
    exc = fut.exception(T)
    assert isinstance(exc, TypeError)
