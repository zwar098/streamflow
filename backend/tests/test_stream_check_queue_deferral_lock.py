"""The queue worker must not sleep while holding the queue lock.

Regression for docs/checker-queue-lock-stall.md: deferring a queued preflight
slept about 1 s under ``StreamCheckQueue.lock``, so status readers (widget
endpoint, queue endpoint, UI) stalled for tens of seconds.
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.stream.stream_checker_components import StreamCheckQueue  # noqa: E402

PREFLIGHT = {"source": "teamarr_preflight"}


def queue_with_preflight():
    check_queue = StreamCheckQueue(max_size=10)
    assert check_queue.add_channel(501, priority=100, stream_count=1, metadata=dict(PREFLIGHT))
    return check_queue


def record_waits(monkeypatch, check_queue):
    held = []
    monkeypatch.setattr(
        check_queue, "_wait_after_deferred_entry", lambda timeout: held.append(check_queue.lock.locked()),
    )
    return held


def test_excluded_source_backs_off_without_the_lock(monkeypatch):
    check_queue = queue_with_preflight()
    held = record_waits(monkeypatch, check_queue)

    entry = check_queue.get_next_entry(timeout=0.01, excluded_metadata_sources={"teamarr_preflight"})

    assert entry is None
    assert held == [False]
    assert check_queue.get_status()["queued"] == 1


def test_deferred_source_backs_off_without_the_lock(monkeypatch):
    check_queue = queue_with_preflight()
    check_queue.defer_metadata_sources({"teamarr_preflight"})
    held = record_waits(monkeypatch, check_queue)

    assert check_queue.get_next_entry(timeout=0.01) is None
    assert held == [False]
    assert check_queue.get_status()["queued"] == 1


def test_paused_queue_backs_off_without_the_lock(monkeypatch):
    check_queue = StreamCheckQueue(max_size=10)
    assert check_queue.add_channel(502, priority=10, stream_count=1)
    check_queue.set_paused(True)
    held = record_waits(monkeypatch, check_queue)

    assert check_queue.get_next_entry(timeout=0.01) is None
    assert held == [False]
    assert check_queue.get_status()["queued"] == 1
    assert check_queue.is_paused() is True


def test_normal_entry_is_still_activated_without_a_wait(monkeypatch):
    check_queue = StreamCheckQueue(max_size=10)
    assert check_queue.add_channel(503, priority=10, stream_count=1)
    held = record_waits(monkeypatch, check_queue)

    entry = check_queue.get_next_entry(timeout=0.01, excluded_metadata_sources={"teamarr_preflight"})

    assert entry["channel_id"] == 503
    assert held == []
    assert check_queue.get_status()["in_progress"] == 1


def test_stale_entry_returns_none_without_a_wait(monkeypatch):
    check_queue = StreamCheckQueue(max_size=10)
    assert check_queue.add_channel(504, priority=10, stream_count=1)
    check_queue.clear()
    held = record_waits(monkeypatch, check_queue)

    assert check_queue.get_next_entry(timeout=0.01) is None
    assert held == []


def test_readers_get_the_lock_while_a_worker_keeps_deferring():
    check_queue = queue_with_preflight()
    stop = threading.Event()
    cycles = []

    def worker():
        while not stop.is_set():
            check_queue.get_next_entry(timeout=1.0, excluded_metadata_sources={"teamarr_preflight"})
            cycles.append(1)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        time.sleep(0.05)
        for _ in range(20):
            assert check_queue.lock.acquire(timeout=0.5), "reader waited behind the deferring worker"
            check_queue.lock.release()
            time.sleep(0.05)
    finally:
        stop.set()
        thread.join(timeout=3)

    assert cycles, "worker never deferred"
    assert check_queue.get_status()["queued"] == 1
