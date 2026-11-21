"""GET /api/debug/threads (docs/checker-queue-lock-stall.md §8.5)."""

import threading

import pytest

import apps.api.web_api as web_api

URL = "/api/debug/threads"


@pytest.fixture
def client():
    return web_api.app.test_client()


def test_returns_every_thread_as_plain_text(client, monkeypatch):
    monkeypatch.setenv("STREAMFLOW_DEBUG_THREADS", "true")
    marker = threading.Event()
    worker = threading.Thread(target=marker.wait, args=(5,), name="dump-test-worker", daemon=True)
    worker.start()
    try:
        response = client.get(URL)
    finally:
        marker.set()
        worker.join(2)

    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_data(as_text=True)
    assert body.startswith("Thread dump at ")
    assert f"--- {threading.current_thread().name} (" in body
    assert "--- dump-test-worker (" in body
    assert "test_debug_threads.py" in body


def test_off_by_default(client, monkeypatch):
    monkeypatch.delenv("STREAMFLOW_DEBUG_THREADS", raising=False)

    assert client.get(URL).status_code == 404


@pytest.mark.parametrize("value", ["false", "0", "off", "no"])
def test_switched_off_gives_a_404(client, monkeypatch, value):
    monkeypatch.setenv("STREAMFLOW_DEBUG_THREADS", value)

    assert client.get(URL).status_code == 404


def test_answers_while_the_checker_queue_lock_is_held(client, monkeypatch, tmp_path):
    from apps.stream import stream_checker_service as scs

    monkeypatch.setattr(scs, "CONFIG_DIR", tmp_path)
    checker = scs.StreamCheckerService()
    monkeypatch.setenv("STREAMFLOW_DEBUG_THREADS", "true")
    result = {}

    with checker.check_queue.lock, checker.lock:
        thread = threading.Thread(target=lambda: result.update(response=client.get(URL)), daemon=True)
        thread.start()
        thread.join(5)

    assert not thread.is_alive(), "thread dump waited on a checker lock"
    assert result["response"].status_code == 200
