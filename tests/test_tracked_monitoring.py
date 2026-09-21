"""stuck detection, stats, query API, snapshot, dump, dump signal."""
import io
import json
import logging
import os
import signal
import threading

import pytest
from conftest import T, hold, wait_until, within

from tracked_executor import TrackedExecutor


# ---- stats ------------------------------------------------------------------
def test_stats_counters(ex):
    def boom():
        raise ValueError("x")

    ex.submit(lambda: 1, label="ok").result(T)
    with pytest.raises(ValueError):
        ex.submit(boom, label="bad").result(T)
    _, gate = hold(ex)
    ex.submit(lambda: 1, label="dropped")
    ex.cancel("dropped")
    gate.set()
    wait_until(lambda: ex.stats()["completed"] == 2)  # ok + blocker
    s = ex.stats()
    assert (s["submitted"], s["completed"], s["failed"], s["cancelled"]) == (4, 2, 1, 1)
    assert s["pending"] == 0 and s["running"] == 0


def test_stats_pending_and_running_are_live(ex):
    _, gate = hold(ex)
    ex.submit(lambda: None, label="q1")
    ex.submit(lambda: None, label="q2")
    s = ex.stats()
    assert (s["pending"], s["running"], s["submitted"]) == (2, 1, 3)
    gate.set()
    wait_until(lambda: ex.stats()["completed"] == 3)


def test_stats_returns_a_copy(ex):
    s = ex.stats()
    s["submitted"] = 99
    assert ex.stats()["submitted"] == 0


def test_counters_sum_invariant(make):
    e = make(max_workers=4)
    futs = [e.submit((lambda: 1 / 0) if i % 3 == 0 else (lambda: 1), label=f"t{i}") for i in range(60)]
    e.wait(T)
    wait_until(lambda: sum(e.stats()[k] for k in ("completed", "failed", "cancelled")) == 60)
    s = e.stats()
    assert s["failed"] == 20 and s["completed"] == 40 and s["submitted"] == 60
    assert all(f.done() for f in futs)


# ---- query ------------------------------------------------------------------
def test_tasks_filter_find_and_copy(ex):
    _, gate = hold(ex)
    ex.submit(lambda: None, label="q")
    assert {t.label for t in ex.tasks()} == {"blocker", "q"}
    assert [t.label for t in ex.tasks("running")] == [t.label for t in ex.running()] == ["blocker"]
    assert [t.label for t in ex.tasks("pending")] == [t.label for t in ex.pending()] == ["q"]
    assert ex.tasks("bogus") == []
    assert ex.find("q").label == "q" and ex.find("zzz") is None
    ex.tasks().clear()
    assert len(ex.tasks()) == 2  # mutating the returned list is harmless
    gate.set()


def test_find_none_after_finish(ex):
    ex.submit(lambda: 1, label="x").result(T)
    wait_until(lambda: ex.find("x") is None)
    assert ex.tasks() == []


# ---- stuck ------------------------------------------------------------------
def test_stuck_only_counts_running_time_not_queue_time(make):
    e = make(max_workers=1, stuck_after=3600)
    _, gate = hold(e)
    e.submit(lambda: None, label="queued", stuck_after=-1)
    assert e.stuck() == []  # pending never stuck, blocker under default limit
    assert [t.label for t in e.stuck(older_than=-1)] == ["blocker"]
    gate.set()


def test_stuck_excludes_finished(ex):
    ex.submit(lambda: 1, label="x", stuck_after=-1).result(T)
    assert ex.stuck() == []


def test_stuck_uses_default_then_updated_default(ex):
    _, gate = hold(ex)
    assert ex.stuck() == []
    ex.stuck_after = -1  # attribute is live
    assert [t.label for t in ex.stuck()] == ["blocker"]
    gate.set()


def test_per_task_zero_is_respected_not_treated_as_unset(make):
    e = make(max_workers=1, stuck_after=3600)
    _, gate = hold(e, stuck_after=0)
    wait_until(lambda: [t.label for t in e.stuck()] == ["blocker"])  # age > 0 eventually
    gate.set()


def test_older_than_overrides_per_task_and_default(make):
    e = make(max_workers=1, stuck_after=-1)
    _, gate = hold(e, stuck_after=-1)
    assert e.stuck(older_than=3600) == []
    assert len(e.stuck(older_than=-1)) == 1
    gate.set()


def test_stuck_fires_once_per_task_even_across_many_scans(make):
    calls = []
    e = make(max_workers=2, on_stuck=calls.append)
    _, g1 = hold(e, label="a", stuck_after=-1)
    _, g2 = hold(e, label="b", stuck_after=-1)
    for _ in range(5):
        e._check_stuck()
    assert sorted(t.label for t in calls) == ["a", "b"]
    assert all(t.alerted and t.state == "running" for t in calls)
    g1.set(), g2.set()


def test_task_that_becomes_stuck_later_alerts_later(make):
    calls = []
    e = make(max_workers=2, stuck_after=3600, on_stuck=calls.append)
    _, g1 = hold(e, label="a")
    e._check_stuck()
    assert calls == []
    e.stuck_after = -1
    e._check_stuck()
    e._check_stuck()
    assert [t.label for t in calls] == ["a"]
    g1.set()


def test_pending_and_finished_tasks_never_alert(make):
    calls = []
    e = make(max_workers=1, on_stuck=calls.append)
    _, gate = hold(e, stuck_after=3600)
    e.submit(lambda: None, label="queued", stuck_after=-1)
    e._check_stuck()
    assert calls == []
    gate.set()
    e.submit(lambda: None, label="quick", stuck_after=-1).result(T)
    e._check_stuck()
    assert calls == []


def test_default_handler_logs_stuck_with_stack(make, caplog):
    e = make(max_workers=1)
    started, gate = threading.Event(), threading.Event()

    def stuck_marker_fn():
        started.set()
        gate.wait(T)

    e.submit(stuck_marker_fn, label="sm", stuck_after=-1)
    assert started.wait(T)
    with caplog.at_level(logging.ERROR, logger="tracked_executor"):
        e._check_stuck()
    text = caplog.text
    assert "STUCK sm" in text and "stuck_marker_fn" in text
    gate.set()


def test_failing_handler_is_logged_and_not_retried(make, caplog):
    calls = []

    def bad(t):
        calls.append(t.label)
        raise RuntimeError("handler broke")

    e = make(max_workers=1, on_stuck=bad)
    _, gate = hold(e, label="x", stuck_after=-1)
    with caplog.at_level(logging.ERROR, logger="tracked_executor"):
        e._check_stuck()
        e._check_stuck()
    assert calls == ["x"]
    assert "on_stuck handler failed for x" in caplog.text and "handler broke" in caplog.text
    gate.set()


def test_handler_failure_does_not_stop_other_tasks_in_same_scan(make):
    seen = []

    def handler(t):
        seen.append(t.label)
        if t.label == "a":
            raise RuntimeError

    e = make(max_workers=2, on_stuck=handler)
    _, g1 = hold(e, label="a", stuck_after=-1)
    _, g2 = hold(e, label="b", stuck_after=-1)
    e._check_stuck()
    assert sorted(seen) == ["a", "b"]
    g1.set(), g2.set()


def test_real_watchdog_survives_failing_handler():
    second = threading.Event()
    seen = []

    def handler(t):
        seen.append(t.label)
        if t.label == "first":
            raise RuntimeError("boom")
        second.set()

    with TrackedExecutor(max_workers=2, check_every=0.01, on_stuck=handler) as ex:
        gate = threading.Event()
        ex.submit(lambda: gate.wait(T), label="first", stuck_after=-1)
        wait_until(lambda: "first" in seen)
        ex.submit(lambda: gate.wait(T), label="second", stuck_after=-1)
        assert second.wait(T), "watchdog died after handler exception"
        gate.set()
    assert seen.count("first") == 1


def test_handler_may_use_executor_api_without_deadlock(make):
    out = {}

    def handler(t):
        out["stuck"] = [x.label for x in e.stuck()]
        out["stats"] = e.stats()
        e.dump(file=io.StringIO())
        out["cancel"] = e.cancel(t.label)

    e = make(max_workers=1, on_stuck=handler)
    hold(e, label="x", stuck_after=-1)
    within(e._check_stuck)
    assert out["stuck"] == ["x"] and out["cancel"] == "signalled"


# ---- snapshot ---------------------------------------------------------------
def test_snapshot_empty(ex):
    assert ex.snapshot() == []


def test_snapshot_content_json_and_order(ex):
    _, gate = hold(ex)
    ex.submit(lambda: None, label="waiting")
    snap = ex.snapshot()
    assert json.loads(json.dumps(snap)) == snap
    assert [s["label"] for s in snap] == ["blocker", "waiting"]  # oldest first
    by = {s["label"]: s for s in snap}
    assert set(by["blocker"]) == {"label", "state", "age", "thread_id"}
    assert by["blocker"]["state"] == "running" and isinstance(by["blocker"]["thread_id"], int)
    assert by["waiting"]["state"] == "pending" and by["waiting"]["thread_id"] is None
    assert all(isinstance(s["age"], float) and s["age"] >= 0 for s in snap)
    assert all(round(s["age"], 3) == s["age"] for s in snap)
    gate.set()


def test_snapshot_is_a_fresh_list_and_drops_finished(ex):
    ex.submit(lambda: 1, label="x").result(T)
    wait_until(lambda: ex.snapshot() == [])
    a = ex.snapshot()
    a.append({"junk": 1})
    assert ex.snapshot() == []


# ---- dump -------------------------------------------------------------------
def test_dump_header_lines_and_order(ex):
    _, gate = hold(ex)
    ex.submit(lambda: None, label="waiting")
    buf = io.StringIO()
    ex.dump(file=buf, stacks=False)
    lines = buf.getvalue().splitlines()
    assert lines[0].startswith("== executor {") and "'pending': 1" in lines[0]
    assert "running" in lines[1] and "blocker" in lines[1]  # oldest first
    assert "pending" in lines[2] and "waiting" in lines[2]
    assert len(lines) == 3
    gate.set()


def test_dump_defaults_to_stderr_resolved_at_call_time(ex, capsys):
    ex.dump()
    assert "== executor" in capsys.readouterr().err


def test_dump_stacks_only_for_stuck_running_tasks(make):
    e = make(max_workers=2, stuck_after=3600)
    started, gate = threading.Event(), threading.Event()

    def stuck_marker_dump():
        started.set()
        gate.wait(T)

    e.submit(stuck_marker_dump, label="stuck", stuck_after=-1)
    _, g2 = hold(e, label="fine")
    assert started.wait(T)

    with_stacks, without = io.StringIO(), io.StringIO()
    e.dump(file=with_stacks)
    e.dump(file=without, stacks=False)
    assert "stuck_marker_dump" in with_stacks.getvalue()
    assert "_hold" not in with_stacks.getvalue()  # 'fine' isn't stuck -> no stack
    assert "stuck_marker_dump" not in without.getvalue()
    gate.set(), g2.set()


# ---- dump signal ------------------------------------------------------------
@pytest.fixture
def restore_signals():
    saved = {s: signal.getsignal(s) for s in (signal.SIGUSR1, signal.SIGUSR2)}
    yield
    for s, h in saved.items():
        signal.signal(s, h)


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="POSIX only")
class TestDumpSignal:
    def test_default_signal_triggers_dump(self, ex, monkeypatch, restore_signals):
        called = threading.Event()
        monkeypatch.setattr(ex, "dump", lambda *a, **k: called.set())
        assert ex.install_dump_signal() is True
        os.kill(os.getpid(), signal.SIGUSR1)
        assert called.wait(T)

    def test_custom_signal(self, ex, monkeypatch, restore_signals):
        called = threading.Event()
        monkeypatch.setattr(ex, "dump", lambda *a, **k: called.set())
        assert ex.install_dump_signal(signal.SIGUSR2) is True
        os.kill(os.getpid(), signal.SIGUSR2)
        assert called.wait(T)

    def test_dump_runs_off_the_main_thread(self, ex, monkeypatch, restore_signals):
        box = {}
        done = threading.Event()

        def fake_dump(*a, **k):
            box["thread"] = threading.current_thread()
            done.set()

        monkeypatch.setattr(ex, "dump", fake_dump)
        ex.install_dump_signal()
        os.kill(os.getpid(), signal.SIGUSR1)
        assert done.wait(T)
        assert box["thread"] is not threading.main_thread()

    def test_signal_while_lock_held_does_not_deadlock(self, ex, monkeypatch, restore_signals):
        buf, done = io.StringIO(), threading.Event()
        real_dump = ex.dump

        def dump(*a, **k):
            real_dump(file=buf, stacks=False)
            done.set()

        monkeypatch.setattr(ex, "dump", dump)
        ex.install_dump_signal()
        ex.submit(lambda: 1, label="x").result(T)
        with ex._lock:  # main thread holds the non-reentrant lock when the signal lands
            os.kill(os.getpid(), signal.SIGUSR1)
            assert not done.wait(0.05)  # dump thread is parked on the lock; main is not
        assert done.wait(T)
        assert "== executor" in buf.getvalue()

    def test_reinstall_replaces_handler(self, ex, restore_signals):
        assert ex.install_dump_signal() and ex.install_dump_signal()

    def test_off_main_thread_is_noop(self, ex, restore_signals):
        before = signal.getsignal(signal.SIGUSR1)
        out = []
        t = threading.Thread(target=lambda: out.append(ex.install_dump_signal()))
        t.start()
        t.join(T)
        assert out == [False] and signal.getsignal(signal.SIGUSR1) is before


def test_missing_sigusr1_is_noop(ex, monkeypatch):
    monkeypatch.delattr(signal, "SIGUSR1", raising=False)  # emulate Windows
    assert ex.install_dump_signal() is False
