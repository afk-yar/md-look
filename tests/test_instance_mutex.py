"""Single-instance detection via a named Win32 mutex.

The old implementation probed the IPC port with a blocking connect(); on this
machine a closed localhost port does not answer with RST, so every launch
without a running instance burned the full socket timeout (~1 s). Detection now
goes through a named mutex and the socket carries commands only.
"""
import time
import uuid

import pytest

import app


@pytest.fixture()
def mutex_name():
    """A mutex name no other process on this machine can be holding."""
    return 'Local\\MDLook-test-' + uuid.uuid4().hex


@pytest.fixture()
def handles():
    """Collect Win32 handles opened by a test and close them afterwards."""
    opened = []
    yield opened
    for handle in opened:
        app._close_handle(handle)


def test_fresh_name_is_not_already_existing(mutex_name, handles):
    handle, already_exists = app._acquire_instance_mutex(mutex_name)
    handles.append(handle)

    assert handle, 'CreateMutexW returned a null handle'
    assert already_exists is False


def test_second_acquire_of_same_name_reports_existing(mutex_name, handles):
    first, first_exists = app._acquire_instance_mutex(mutex_name)
    handles.append(first)
    second, second_exists = app._acquire_instance_mutex(mutex_name)
    handles.append(second)

    assert first_exists is False
    assert second_exists is True
    assert second, 'CreateMutexW returned a null handle for the second caller'


def test_name_is_free_again_after_all_handles_closed(mutex_name):
    first, _ = app._acquire_instance_mutex(mutex_name)
    second, _ = app._acquire_instance_mutex(mutex_name)
    app._close_handle(first)
    app._close_handle(second)

    third, third_exists = app._acquire_instance_mutex(mutex_name)
    try:
        assert third_exists is False, 'mutex outlived every open handle'
    finally:
        app._close_handle(third)


def test_mutex_name_is_derived_from_ipc_port(monkeypatch):
    monkeypatch.setattr(app, 'IPC_PORT', 50123)

    assert app._instance_mutex_name() == 'Local\\MDLook-50123'


def test_signal_existing_instance_returns_fast_when_no_instance(monkeypatch, mutex_name):
    """No running instance -> False without ever touching the socket."""
    # A port the app never derives, so a stray dev instance cannot answer.
    monkeypatch.setattr(app, 'IPC_PORT', 65500)
    monkeypatch.setattr(app, '_instance_mutex_name', lambda port=None: mutex_name)
    monkeypatch.setattr(app, '_instance_mutex_handle', None)

    def _fail_on_socket(*args, **kwargs):
        raise AssertionError('the fast path must not open a socket')

    monkeypatch.setattr(app, '_send_ipc_message', _fail_on_socket)

    started = time.perf_counter()
    try:
        result = app._signal_existing_instance()
        elapsed = time.perf_counter() - started

        assert result is False
        assert elapsed < 0.3, 'took %.0f ms, expected under 300 ms' % (elapsed * 1000)
        assert app._instance_mutex_handle, 'first instance must keep the mutex handle'
    finally:
        app._close_handle(app._instance_mutex_handle)
        app._instance_mutex_handle = None


def test_signal_existing_instance_sends_command_when_instance_exists(monkeypatch, mutex_name, handles):
    """Mutex already taken -> the launch hands its command over the socket."""
    holder, _ = app._acquire_instance_mutex(mutex_name)
    handles.append(holder)

    monkeypatch.setattr(app, 'IPC_PORT', 65500)
    monkeypatch.setattr(app, '_instance_mutex_name', lambda port=None: mutex_name)
    monkeypatch.setattr(app, '_instance_mutex_handle', None)
    monkeypatch.setattr(app.sys, 'argv', ['MDLook.exe'])

    sent = []

    def _capture(msg, **kwargs):
        sent.append(msg)
        return True

    monkeypatch.setattr(app, '_send_ipc_message', _capture)

    assert app._signal_existing_instance() is True
    assert sent == ['SHOW']
    assert app._instance_mutex_handle is None, 'second instance must not keep a handle'


def test_signal_message_carries_file_and_goto(monkeypatch, tmp_path):
    note = tmp_path / 'note.md'
    note.write_text('# note\n', encoding='utf-8')
    monkeypatch.setattr(app.sys, 'argv', ['MDLook.exe', str(note), '--goto', 'anchor'])

    assert app._signal_message() == 'OPEN:' + str(note) + '\tanchor'
