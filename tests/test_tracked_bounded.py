"""max_queued / block / timeout behaviour and slot accounting."""
import threading

import pytest
from conftest import T, hold, wait_until, within

from tracked_executor import QueueFull


def slots(e):
    return e._slots._value


def test_unbounded_ignores_block_and_timeout(make):
    e = make(max_workers=1)
    _, gate = hold(e)
    for i in range(100):
        e.submit(lambda: None, label=f"q{i}", block=False, timeout=0)
    assert e.stats()["pending"] == 100
    assert e._slots is None
    gate.set()


def test_capacity_is_workers_plus_queued(make):
    e = make(max_workers=2, max_queued=1)
    assert e._capacity == 3 and slots(e) == 3


@pytest.mark.parametrize("workers,queued", [(1, 0), (1, 1), (2, 0), (2, 3), (4, 2)])
def test_exactly_capacity_tasks_fit(make, workers, queued):
    e = make(max_workers=workers, max_queued=queued)
    gates = []
    for i in range(workers + queued):
        _, g = hold(e, label=f"b{i}") if i < workers else (None, None)
        if g is None:
            e.submit(lambda: None, label=f"q{i}", block=False)
        else:
            gates.append(g)
    with pytest.raises(QueueFull):
        e.submit(lambda: None, label="over", block=False)
    for g in gates:
        g.set()


def test_queuefull_is_runtimeerror_with_helpful_message(make):
    e = make(max_workers=1, max_queued=0)
    _, gate = hold(e)
    with pytest.raises(RuntimeError) as ei:
        e.submit(lambda: None, label="mine", block=False)
    assert isinstance(ei.value, QueueFull)
    assert "capacity (1" in str(ei.value) and "'mine'" in str(ei.value)
    assert "within" not in str(ei.value)
    with pytest.raises(QueueFull, match="within 0.02s"):
        e.submit(lambda: None, label="mine", timeout=0.02)
    gate.set()


def test_rejected_submit_leaves_no_trace(make):
    e = make(max_workers=1, max_queued=0)
    _, gate = hold(e)
    before = e.stats()
    with pytest.raises(QueueFull):
        e.submit(lambda: None, label="ghost", block=False)
    assert e.stats() == before and e.find("ghost") is None and slots(e) == 0
    gate.set()


def test_timeout_zero_and_block_false_are_immediate(make):
    e = make(max_workers=1, max_queued=0)
    _, gate = hold(e)
    within(lambda: pytest.raises(QueueFull, e.submit, lambda: None, timeout=0), 2)
    within(lambda: pytest.raises(QueueFull, e.submit, lambda: None, block=False), 2)
    gate.set()


def test_negative_timeout_behaves_like_zero(make):
    e = make(max_workers=1, max_queued=0)
    e.submit(lambda: 1, label="fits", timeout=-1).result(T)  # slot free -> admitted
    wait_until(lambda: slots(e) == 1)
    _, gate = hold(e)
    with pytest.raises(QueueFull):
        within(lambda: e.submit(lambda: None, label="x", timeout=-1), 2)
    gate.set()


def test_block_false_ignores_timeout_argument(make):
    e = make(max_workers=1, max_queued=0)
    _, gate = hold(e)
    with pytest.raises(QueueFull):
        e.submit(lambda: None, block=False, timeout=5)  # must not wait 5s
    gate.set()


def test_blocked_submit_unblocks_when_task_finishes(make):
    e = make(max_workers=1, max_queued=0)
    _, gate = hold(e)
    box, done = {}, threading.Event()

    def producer():
        box["f"] = e.submit(lambda: 9, label="late", timeout=T)
        done.set()

    threading.Thread(target=producer, daemon=True).start()
    assert not done.wait(0.05)  # genuinely blocked
    gate.set()
    assert done.wait(T)
    assert box["f"].result(T) == 9


def test_blocked_submit_times_out(make):
    e = make(max_workers=1, max_queued=0)
    _, gate = hold(e)
    box, done = {}, threading.Event()

    def producer():
        try:
            e.submit(lambda: None, label="late", timeout=0.05)
        except QueueFull as err:
            box["err"] = err
        done.set()

    threading.Thread(target=producer, daemon=True).start()
    assert done.wait(T) and isinstance(box["err"], QueueFull)
    gate.set()


def test_blocked_submit_unblocks_on_cancel_pending(make):
    e = make(max_workers=1, max_queued=1)
    _, gate = hold(e)
    e.submit(lambda: None, label="q")  # full
    done, box = threading.Event(), {}

    def producer():
        box["f"] = e.submit(lambda: 1, label="waiter", timeout=T)
        done.set()

    threading.Thread(target=producer, daemon=True).start()
    assert not done.wait(0.05)
    assert e.cancel_pending(lambda t: t.label == "q") == 1
    assert done.wait(T)
    gate.set()


def test_blocked_submit_after_shutdown_gets_runtimeerror_and_frees_slot(make):
    e = make(max_workers=1, max_queued=1)
    _, gate = hold(e)
    e.submit(lambda: None, label="q")
    done, box = threading.Event(), {}

    def producer():
        try:
            e.submit(lambda: 1, label="waiter", timeout=T)
        except BaseException as err:  # noqa: BLE001
            box["err"] = err
        done.set()

    threading.Thread(target=producer, daemon=True).start()
    assert not done.wait(0.05)
    e.shutdown(wait=False, cancel_pending=True)  # cancels "q" -> frees slot -> waiter proceeds
    assert done.wait(T)
    assert isinstance(box["err"], RuntimeError) and not isinstance(box["err"], QueueFull)
    gate.set()
    wait_until(lambda: slots(e) == 2 and e.stats()["submitted"] == 2)  # waiter rolled back


def test_slot_released_on_success_failure_cancel(make):
    e = make(max_workers=1, max_queued=1)
    e.submit(lambda: 1, label="ok").result(T)

    def boom():
        raise ValueError

    with pytest.raises(ValueError):
        e.submit(boom, label="bad").result(T)
    _, gate = hold(e)
    e.submit(lambda: None, label="q")
    e.cancel("q")
    gate.set()
    wait_until(lambda: slots(e) == 2)


def test_slot_released_on_duplicate_label_and_submit_after_shutdown(make):
    e = make(max_workers=1, max_queued=2)
    _, gate = hold(e)  # 1/3
    e.submit(lambda: None, label="q")  # 2/3
    for _ in range(5):
        with pytest.raises(ValueError, match="duplicate"):
            e.submit(lambda: None, label="q", block=False)
    assert slots(e) == 1  # dup attempts leaked nothing
    e.submit(lambda: None, label="r", block=False)  # 3/3
    with pytest.raises(QueueFull):
        e.submit(lambda: None, block=False)
    assert e.cancel("r") == "cancelled"
    e.submit(lambda: None, label="s", block=False)
    gate.set()


def test_slot_released_after_shutdown_submit_failure(make):
    e = make(max_workers=1, max_queued=0)
    e.shutdown()
    for _ in range(3):
        with pytest.raises(RuntimeError) as ei:
            e.submit(lambda: None, block=False)
        assert not isinstance(ei.value, QueueFull)
    assert slots(e) == 1


def test_all_slots_restored_after_drain(make):
    e = make(max_workers=2, max_queued=3)
    futs = [e.submit(lambda: 1, label=f"t{i}") for i in range(20)]
    for f in futs:
        f.result(T)
    wait_until(lambda: slots(e) == 5)


def test_boundedsemaphore_never_over_released(make):
    """A double release would raise ValueError inside a callback; exercise all
    release paths and make sure the count ends exactly at capacity."""
    e = make(max_workers=2, max_queued=2)
    _, g = hold(e, label="h1")
    _, g2 = hold(e, label="h2")
    e.submit(lambda: None, label="a")
    e.submit(lambda: None, label="b")
    e.cancel_pending()
    g.set(), g2.set()
    wait_until(lambda: slots(e) == 4)
