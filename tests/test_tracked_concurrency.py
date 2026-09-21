"""Races, stress, and seeded random operation mix."""
import random
import threading
import time

import pytest
from conftest import T, wait_until, within

from tracked_executor import QueueFull, TrackedExecutor


def run_threads(n, target):
    barrier = threading.Barrier(n, timeout=T)
    errs = []

    def wrap(i):
        try:
            barrier.wait()
            target(i)
        except BaseException as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=wrap, args=(i,), daemon=True) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(T * 2)
        assert not t.is_alive(), "thread hung"
    return errs


def test_duplicate_label_race_exactly_one_winner(make):
    e = make(max_workers=2)
    gate = threading.Event()
    wins, dups = [], []

    def go(i):
        try:
            wins.append(e.submit(lambda: gate.wait(T), label="same"))
        except ValueError:
            dups.append(i)

    assert run_threads(16, go) == []
    assert len(wins) == 1 and len(dups) == 15
    assert e.stats()["submitted"] == 1
    gate.set()


def test_concurrent_unique_submits_all_complete(make):
    e = make(max_workers=8)
    futs, lock = [], threading.Lock()

    def go(i):
        for j in range(50):
            f = e.submit(lambda i=i, j=j: i * 1000 + j, label=f"{i}-{j}")
            with lock:
                futs.append(f)

    assert run_threads(8, go) == []
    assert sorted(f.result(T) for f in futs) == sorted(i * 1000 + j for i in range(8) for j in range(50))
    wait_until(lambda: e.stats()["completed"] == 400)
    assert e.tasks() == []


def test_label_churn_same_label_sequentially_from_many_threads(make):
    e = make(max_workers=4)
    hits = []

    def go(i):
        for _ in range(30):
            while True:
                try:
                    hits.append(e.submit(lambda: 1, label="shared").result(T))
                    break
                except ValueError:  # another thread's task with this label is live
                    time.sleep(0.001)

    assert run_threads(4, go) == []
    assert len(hits) == 120
    wait_until(lambda: e.stats()["completed"] == 120 and e.tasks() == [])


def test_queries_concurrent_with_mutation_never_raise(make):
    e = make(max_workers=4)
    stop = threading.Event()
    errors = []

    def reader():
        try:
            while not stop.is_set():
                e.stats(), e.tasks(), e.pending(), e.running(), e.stuck(), e.snapshot()
                e.find("t5")
        except BaseException as err:  # noqa: BLE001
            errors.append(err)

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
    for r in readers:
        r.start()
    futs = [e.submit(lambda: time.sleep(0.0005), label=f"t{i}") for i in range(300)]
    for f in futs:
        f.result(T)
    stop.set()
    for r in readers:
        r.join(T)
    assert errors == []


def test_bounded_producers_respect_capacity_and_restore_slots(make):
    e = make(max_workers=4, max_queued=4)
    cap, peak, stop = 8, [0], threading.Event()

    def monitor():
        while not stop.is_set():
            peak[0] = max(peak[0], len(e.tasks()))

    m = threading.Thread(target=monitor, daemon=True)
    m.start()
    futs, lock = [], threading.Lock()

    def go(i):
        for j in range(40):
            f = e.submit(lambda: time.sleep(0.0005), label=f"{i}-{j}", timeout=T)
            with lock:
                futs.append(f)

    assert run_threads(8, go) == []
    for f in futs:
        f.result(T)
    stop.set()
    m.join(T)
    wait_until(lambda: e.stats()["completed"] == 320 and e._slots._value == cap)
    assert peak[0] <= cap


def test_bounded_nonblocking_producers_either_succeed_or_queuefull(make):
    e = make(max_workers=2, max_queued=2)
    ok, full, lock = [], [], threading.Lock()
    gate = threading.Event()

    def go(i):
        for j in range(20):
            try:
                f = e.submit(lambda: gate.wait(T), label=f"{i}-{j}", block=False)
                with lock:
                    ok.append(f)
            except QueueFull:
                with lock:
                    full.append(1)

    assert run_threads(8, go) == []
    assert len(ok) == 4 and len(full) == 156  # exactly capacity admitted while gated
    gate.set()
    for f in ok:
        f.result(T)
    wait_until(lambda: e._slots._value == 4)


def test_submit_racing_shutdown_accounting_is_exact():
    e = TrackedExecutor(max_workers=4, max_queued=8, check_every=3600)
    ok, rejected, lock = [], [], threading.Lock()

    def go(i):
        for j in range(100):
            try:
                f = e.submit(lambda: 1, label=f"{i}-{j}", timeout=T)
                with lock:
                    ok.append(f)
            except RuntimeError:
                with lock:
                    rejected.append(1)
                return

    ts = [threading.Thread(target=go, args=(i,), daemon=True) for i in range(6)]
    for t in ts:
        t.start()
    time.sleep(0.005)
    e.shutdown(wait=True)
    for t in ts:
        t.join(T)
        assert not t.is_alive()
    wait_until(lambda: e.stats()["submitted"] == len(ok) == e.stats()["completed"])
    assert e.tasks() == [] and e._slots._value == 12


def test_cancel_racing_completion_never_corrupts_counts(make):
    e = make(max_workers=4)
    futs = [e.submit(lambda: 1, label=f"t{i}") for i in range(300)]
    results = []

    def go(i):
        for k in range(i, 300, 4):
            results.append(e.cancel(f"t{k}"))

    assert run_threads(4, go) == []
    assert set(results) <= {"cancelled", "signalled", "missing"}
    e.wait(T)
    wait_until(lambda: sum(e.stats()[k] for k in ("completed", "cancelled", "failed")) == 300)
    assert e.stats()["cancelled"] == sum(f.cancelled() for f in futs)


@pytest.mark.parametrize("seed", range(5))
def test_random_operation_mix_keeps_invariants(make, seed):
    rnd = random.Random(seed)
    e = make(max_workers=3, max_queued=6, stuck_after=0.001)
    futs, lock = [], threading.Lock()
    plan = [rnd.random() for _ in range(6 * 60)]

    def coop(cancel_event):
        cancel_event.wait(0.003)

    def fail():
        raise RuntimeError("planned")

    def go(i):
        for j in range(60):
            r = plan[i * 60 + j]
            label = f"{i}-{j}"
            if r < 0.45:
                fn = coop
            elif r < 0.6:
                fn = fail
            else:
                fn = time.sleep
            try:
                f = e.submit(fn, *(() if fn is not time.sleep else (0.0005,)), label=label, timeout=T)
            except (QueueFull, ValueError):
                continue
            with lock:
                futs.append(f)
            if r > 0.9:
                e.cancel(label)
            elif r > 0.85:
                e.cancel_pending()
            elif r > 0.8:
                e.snapshot(), e.stats(), e.stuck()
            elif r > 0.78:
                e._check_stuck()

    assert run_threads(6, go) == []
    e.wait(T)
    wait_until(lambda: e.tasks() == [] and e._slots._value == 9)
    s = e.stats()
    assert s["submitted"] == len(futs)
    assert s["submitted"] == s["completed"] + s["failed"] + s["cancelled"]
    assert s["cancelled"] == sum(f.cancelled() for f in futs)
    assert s["failed"] == sum(1 for f in futs if not f.cancelled() and f.exception() is not None)
    assert s["pending"] == s["running"] == 0


def test_user_hooks_calling_back_into_executor_under_load_do_not_deadlock(make):
    e = make(max_workers=4)

    def cb(f):
        e.stats(), e.tasks(), e.find("nope"), e.snapshot()

    def go():
        futs = []
        for i in range(200):
            f = e.submit(lambda: 1, label=f"t{i}")
            f.add_done_callback(cb)
            futs.append(f)
        for f in futs:
            f.result(T)

    within(go, T * 2)
