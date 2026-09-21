"""cancel, cancel_pending, wait, shutdown, context manager."""
import threading
from concurrent.futures import Future

import pytest
from conftest import T, hold, wait_until, within

from tracked_executor import TaskInfo, TrackedExecutor


# ---- cancel -----------------------------------------------------------------
def test_cancel_missing(ex):
    assert ex.cancel("nope") == "missing"


def test_cancel_pending(ex):
    _, gate = hold(ex)
    fut = ex.submit(lambda: 1, label="p")
    assert ex.cancel("p") == "cancelled"
    assert fut.cancelled()
    assert ex.find("p") is None and ex.cancel("p") == "missing"
    gate.set()


def test_cancel_pending_sets_its_event(ex):
    _, gate = hold(ex)
    ex.submit(lambda cancel_event: 1, label="p")
    info = ex.find("p")
    assert ex.cancel("p") == "cancelled"
    assert info.cancel_event.is_set()
    gate.set()


def test_cancel_running_cooperative_task(ex):
    started = threading.Event()

    def coop(cancel_event):
        started.set()
        return cancel_event.wait(T)

    fut = ex.submit(coop, label="coop")
    assert started.wait(T)
    assert ex.cancel("coop") == "signalled"
    assert ex.cancel("coop") in ("signalled", "missing")  # idempotent; may already be done
    assert fut.result(T) is True and not fut.cancelled()
    wait_until(lambda: ex.stats()["completed"] == 1)


def test_cancel_running_uncooperative_task_keeps_running(ex):
    _, gate = hold(ex)  # does not look at any event
    assert ex.cancel("blocker") == "signalled"
    assert ex.find("blocker").cancel_event.is_set()
    assert ex.find("blocker").state == "running"
    gate.set()
    wait_until(lambda: ex.stats()["completed"] == 1)  # finished normally, not cancelled
    assert ex.stats()["cancelled"] == 0


def test_cancel_finished_but_not_yet_deregistered_is_missing(ex):
    done = Future()
    done.set_result(1)
    with ex._lock:  # white-box: the window between set_result and _on_done
        ex._tasks["ghost"] = TaskInfo("ghost", 0.0, future=done)
    assert ex.cancel("ghost") == "missing"
    with ex._lock:
        del ex._tasks["ghost"]


def test_cancel_after_completion_is_missing(ex):
    ex.submit(lambda: 1, label="x").result(T)
    wait_until(lambda: ex.find("x") is None)
    assert ex.cancel("x") == "missing"


def test_cancelled_pending_task_never_runs(ex):
    _, gate = hold(ex)
    ran = []
    ex.submit(ran.append, 1, label="p")
    ex.cancel("p")
    gate.set()
    ex.submit(lambda: None, label="after").result(T)  # queue drained past it
    assert ran == []


# ---- cancel_pending ---------------------------------------------------------
def test_cancel_pending_all_and_predicate(ex):
    _, gate = hold(ex)
    for lbl in ("a-1", "a-2", "b-1"):
        ex.submit(lambda: None, label=lbl)
    assert ex.cancel_pending(lambda t: t.label.startswith("a-")) == 2
    assert [t.label for t in ex.pending()] == ["b-1"]
    assert ex.cancel_pending() == 1
    assert ex.pending() == []
    assert ex.cancel_pending() == 0
    gate.set()


def test_cancel_pending_never_touches_running(ex):
    _, gate = hold(ex)
    assert ex.cancel_pending() == 0
    assert ex.find("blocker").state == "running"
    gate.set()


def test_cancel_pending_no_match_returns_zero(ex):
    _, gate = hold(ex)
    ex.submit(lambda: None, label="q")
    assert ex.cancel_pending(lambda t: False) == 0
    assert len(ex.pending()) == 1
    gate.set()


def test_cancel_pending_predicate_may_call_executor(ex):
    _, gate = hold(ex)
    ex.submit(lambda: None, label="q")

    def pred(t):
        ex.stats(), ex.tasks(), ex.find(t.label)  # would deadlock if lock were held
        return True

    assert within(lambda: ex.cancel_pending(pred)) == 1
    gate.set()


def test_cancel_pending_predicate_exception_propagates_executor_survives(ex):
    _, gate = hold(ex)
    ex.submit(lambda: None, label="q")

    def pred(t):
        raise KeyError("x")

    with pytest.raises(KeyError):
        ex.cancel_pending(pred)
    assert len(ex.pending()) == 1  # nothing was cancelled
    gate.set()
    assert ex.submit(lambda: 7, label="still-works").result(T) == 7


def test_done_callback_running_inline_in_cancel_does_not_deadlock(ex):
    _, gate = hold(ex)
    fut = ex.submit(lambda: 1, label="p")
    fut.add_done_callback(lambda f: (ex.stats(), ex.tasks(), ex.find("p")))
    assert within(lambda: ex.cancel("p")) == "cancelled"
    gate.set()


def test_done_callback_on_already_done_future_does_not_deadlock(ex):
    fut = ex.submit(lambda: 1, label="x")
    fut.result(T)
    within(lambda: fut.add_done_callback(lambda f: ex.submit(lambda: 2, label="y")))


def test_callback_can_resubmit_same_label(ex):
    """User callbacks run after the executor's own, so the label is free."""
    box = []
    fut = ex.submit(lambda: 1, label="loop")
    fut.add_done_callback(lambda f: box.append(ex.submit(lambda: 2, label="loop")))
    fut.result(T)
    wait_until(lambda: len(box) == 1)
    assert box[0].result(T) == 2


def test_nested_submit_from_task(make):
    e = make(max_workers=2)

    def outer():
        return e.submit(lambda: 5, label="inner").result(T)

    assert e.submit(outer, label="outer").result(T) == 5


# ---- wait -------------------------------------------------------------------
def test_wait_empty(ex):
    done, not_done = ex.wait(timeout=0)
    assert done == set() and not_done == set()


def test_wait_timeout_then_completion(ex):
    _, gate = hold(ex)
    pend = ex.submit(lambda: 1, label="q")
    done, not_done = ex.wait(timeout=0.05)
    assert done == set() and pend in not_done and len(not_done) == 2
    gate.set()
    done, not_done = ex.wait(timeout=T)
    assert not_done == set()


# ---- shutdown ---------------------------------------------------------------
def test_shutdown_wait_blocks_until_running_done(ex):
    _, gate = hold(ex)
    finished = threading.Event()
    threading.Thread(target=lambda: (ex.shutdown(), finished.set())).start()
    assert not finished.wait(0.05)
    gate.set()
    assert finished.wait(T)


def test_shutdown_nowait_returns_immediately(ex):
    _, gate = hold(ex)
    within(lambda: ex.shutdown(wait=False))
    assert ex.find("blocker").state == "running"
    gate.set()


def test_shutdown_lets_queued_tasks_run_by_default(ex):
    _, gate = hold(ex)
    futs = [ex.submit(lambda i=i: i, label=f"q{i}") for i in range(3)]
    t = threading.Thread(target=ex.shutdown)
    t.start()
    gate.set()
    t.join(T)
    assert [f.result(T) for f in futs] == [0, 1, 2]
    wait_until(lambda: ex.stats()["completed"] == 4)


def test_shutdown_cancel_pending_true(ex):
    _, gate = hold(ex)
    futs = [ex.submit(lambda: None, label=f"q{i}") for i in range(3)]
    t = threading.Thread(target=ex.shutdown, kwargs={"cancel_pending": True})
    t.start()
    wait_until(lambda: all(f.cancelled() for f in futs))
    gate.set()
    t.join(T)
    assert not t.is_alive()
    wait_until(lambda: ex.stats()["cancelled"] == 3 and ex.stats()["completed"] == 1)
    assert ex.tasks() == []


def test_shutdown_idempotent(ex):
    ex.shutdown()
    ex.shutdown()
    ex.shutdown(wait=False, cancel_pending=True)


def test_submit_after_shutdown_rolls_back_everything(ex):
    ex.shutdown()
    for _ in range(3):
        with pytest.raises(RuntimeError):
            ex.submit(lambda: None, label="late")
    assert ex.stats()["submitted"] == 0 and ex.find("late") is None and ex.tasks() == []


def test_queries_still_work_after_shutdown(ex):
    ex.submit(lambda: 1, label="x").result(T)
    ex.shutdown()
    wait_until(lambda: ex.stats()["completed"] == 1)
    assert ex.snapshot() == [] and ex.cancel("x") == "missing" and ex.stuck() == []
    assert ex.wait(timeout=0) == (set(), set())


# ---- context manager --------------------------------------------------------
def test_context_manager_returns_self_and_waits():
    e = TrackedExecutor(max_workers=2, check_every=3600)
    with e as got:
        assert got is e
        futs = [e.submit(lambda i=i: i, label=f"c{i}") for i in range(5)]
    assert all(f.done() for f in futs)
    with pytest.raises(RuntimeError):
        e.submit(lambda: None)


def test_context_manager_does_not_swallow_exceptions():
    with pytest.raises(KeyError), TrackedExecutor(max_workers=1, check_every=3600):
        raise KeyError("x")
