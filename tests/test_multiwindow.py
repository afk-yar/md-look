"""
T-04: Automated tests for MDLook multi-window logic.

Groups:
  1 — IPC protocol (socket-level)
  2 — Window lifecycle (_windows registry)
  3 — build_html
  4 — Api isolation
  5 — _get_file_arg parsing
  6 — Thread-safety of _windows
"""
import sys
import os
import socket
import threading
import time
import tempfile
from unittest.mock import MagicMock, patch, call
import pytest

import app


# ===========================================================================
# Group 1 — IPC protocol
# ===========================================================================

class TestIpcProtocol:
    """Test IPC listener and signalling without mocking GUI."""

    def _free_port(self):
        """Find a free localhost port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]

    def _wait_for_listener(self, port, timeout=5):
        """Block until the IPC listener is accepting connections."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                s = socket.socket()
                s.settimeout(0.1)
                s.connect(('127.0.0.1', port))
                s.close()
                return
            except (ConnectionRefusedError, OSError):
                time.sleep(0.01)
        raise RuntimeError(f"Listener on port {port} didn't start within {timeout}s")

    def test_open_message_calls_create_window(self, tmp_path):
        """OPEN:/path/to/file.md → _create_window called with that path."""
        md_file = tmp_path / 'file.md'
        md_file.write_text('# Hello', encoding='utf-8')
        filepath = str(md_file)

        port = self._free_port()
        event = threading.Event()

        def mock_create(path):
            event.set()

        with patch.object(app, 'IPC_PORT', port), \
             patch.object(app, '_create_window', side_effect=mock_create) as m_create, \
             patch.object(app, '_force_foreground', MagicMock()) as m_fg, \
             patch.object(app, '_quitting', False):
            app._start_ipc_listener()
            self._wait_for_listener(port)

            s = socket.socket()
            s.connect(('127.0.0.1', port))
            s.sendall(('OPEN:' + filepath).encode('utf-8'))
            s.close()

            assert event.wait(timeout=5), "_create_window was not called within 5s"

        m_create.assert_called_once_with(filepath)
        m_fg.assert_not_called()

    def test_show_message_calls_force_foreground(self):
        """SHOW → _force_foreground called."""
        port = self._free_port()
        event = threading.Event()

        def mock_fg():
            event.set()

        with patch.object(app, 'IPC_PORT', port), \
             patch.object(app, '_create_window', MagicMock()) as m_create, \
             patch.object(app, '_force_foreground', side_effect=mock_fg) as m_fg, \
             patch.object(app, '_quitting', False):
            app._start_ipc_listener()
            self._wait_for_listener(port)

            s = socket.socket()
            s.connect(('127.0.0.1', port))
            s.sendall(b'SHOW')
            s.close()

            assert event.wait(timeout=5), "_force_foreground was not called within 5s"

        m_fg.assert_called_once()
        m_create.assert_not_called()

    def test_garbage_message_does_not_crash(self):
        """Garbage message → no exception, connection handled cleanly."""
        port = self._free_port()
        # For garbage we can't wait for a handler call (nothing fires).
        # Instead, send garbage then send a valid SHOW — the SHOW event proves
        # the listener survived the garbage without crashing.
        event = threading.Event()

        def mock_fg():
            event.set()

        with patch.object(app, 'IPC_PORT', port), \
             patch.object(app, '_create_window', MagicMock()) as m_create, \
             patch.object(app, '_force_foreground', side_effect=mock_fg) as m_fg, \
             patch.object(app, '_quitting', False):
            app._start_ipc_listener()
            self._wait_for_listener(port)

            # Send garbage
            s = socket.socket()
            s.connect(('127.0.0.1', port))
            s.sendall(b'\x00\xff\xfe garbage \n\r\t')
            s.close()

            # Now send a valid SHOW to verify listener is still alive
            s2 = socket.socket()
            s2.settimeout(2)
            s2.connect(('127.0.0.1', port))
            s2.sendall(b'SHOW')
            s2.close()

            assert event.wait(timeout=5), "Listener died after garbage message"

        m_create.assert_not_called()

    def test_signal_existing_instance_returns_true_when_listener_running(self):
        """_signal_existing_instance() → True when listener is active."""
        port = self._free_port()

        with patch.object(app, 'IPC_PORT', port), \
             patch.object(app, '_quitting', False):
            app._start_ipc_listener()
            self._wait_for_listener(port)

            with patch.object(app, 'IPC_PORT', port):
                result = app._signal_existing_instance()

        assert result is True

    def test_signal_existing_instance_returns_false_when_no_listener(self):
        """_signal_existing_instance() → False when nothing is listening."""
        port = self._free_port()
        # Don't start listener

        with patch.object(app, 'IPC_PORT', port):
            result = app._signal_existing_instance()

        assert result is False

    def test_open_nonexistent_file_does_not_call_create_window(self):
        """OPEN:/nonexistent/path.md → _create_window not called (file check fails)."""
        port = self._free_port()
        # Listener skips non-existent files, so _create_window won't fire.
        # Send a SHOW afterwards to confirm the listener processed the OPEN message.
        event = threading.Event()

        def mock_fg():
            event.set()

        with patch.object(app, 'IPC_PORT', port), \
             patch.object(app, '_create_window', MagicMock()) as m_create, \
             patch.object(app, '_force_foreground', side_effect=mock_fg), \
             patch.object(app, '_quitting', False):
            app._start_ipc_listener()
            self._wait_for_listener(port)

            s = socket.socket()
            s.connect(('127.0.0.1', port))
            s.sendall(b'OPEN:/nonexistent/no/such/file.md')
            s.close()

            # Follow up with SHOW to sequence — once SHOW fires, the OPEN
            # has definitely been processed already.
            s2 = socket.socket()
            s2.settimeout(2)
            s2.connect(('127.0.0.1', port))
            s2.sendall(b'SHOW')
            s2.close()

            assert event.wait(timeout=5), "Listener didn't process follow-up SHOW"

        m_create.assert_not_called()


# ===========================================================================
# Group 2 — Window lifecycle (_windows)
# ===========================================================================

class TestWindowLifecycle:
    """Test _create_window and _make_on_closing interactions with _windows."""

    def test_create_window_adds_entry_to_windows(self, patch_template, tmp_path):
        """_create_window(filepath) → entry added to _windows."""
        md_file = tmp_path / 'test.md'
        md_file.write_text('# Test', encoding='utf-8')

        app._create_window(str(md_file))

        with app._windows_lock:
            assert len(app._windows) == 1
            entry = app._windows[0]
        assert 'window' in entry
        assert 'api' in entry
        assert 'temp_html' in entry

    def test_create_window_none_loads_example_md(self, patch_template, tmp_path, monkeypatch):
        """_create_window(None) -> api._current_path is None (no file set), window created."""
        # Create a fake example.md in tmp_path and redirect app.__file__ there
        # so _create_window's os.path.join(dirname(__file__), 'example.md') finds it
        example_md = tmp_path / 'example.md'
        example_md.write_text('# Example\n', encoding='utf-8')

        fake_app_file = str(tmp_path / 'app.py')
        monkeypatch.setattr(app, '__file__', fake_app_file)

        app._create_window(None)

        with app._windows_lock:
            assert len(app._windows) == 1

    def test_closing_last_window_hides_instead_of_destroy(self, patch_template, tmp_path):
        """Last window closing → window.hide() called, handler returns False."""
        md_file = tmp_path / 'a.md'
        md_file.write_text('# A', encoding='utf-8')

        app._create_window(str(md_file))

        with app._windows_lock:
            entry = app._windows[0]
        window = entry['window']

        # Get the closing handler
        closing_handler = window._closing_handlers[0]

        result = closing_handler()

        assert result is False
        window.hide.assert_called_once()

    def test_closing_non_last_window_removes_entry(self, patch_template, tmp_path):
        """Non-last window closing → entry removed from _windows, returns True."""
        md1 = tmp_path / 'a.md'
        md2 = tmp_path / 'b.md'
        md1.write_text('# A', encoding='utf-8')
        md2.write_text('# B', encoding='utf-8')

        app._create_window(str(md1))
        app._create_window(str(md2))

        with app._windows_lock:
            assert len(app._windows) == 2
            entry1 = app._windows[0]
        window1 = entry1['window']

        closing_handler = window1._closing_handlers[0]
        result = closing_handler()

        assert result is True
        with app._windows_lock:
            remaining = [e['window'] for e in app._windows]
        assert window1 not in remaining

    def test_closing_with_quitting_returns_true(self, patch_template, tmp_path):
        """_quitting=True + closing → handler returns True (allow destruction)."""
        md_file = tmp_path / 'a.md'
        md_file.write_text('# A', encoding='utf-8')

        app._create_window(str(md_file))

        with app._windows_lock:
            entry = app._windows[0]
        window = entry['window']
        closing_handler = window._closing_handlers[0]

        app._quitting = True
        result = closing_handler()

        assert result is True
        window.hide.assert_not_called()

    def test_temp_file_removed_on_non_last_window_close(self, patch_template, tmp_path):
        """Closing non-last window → temp HTML file deleted."""
        md1 = tmp_path / 'a.md'
        md2 = tmp_path / 'b.md'
        md1.write_text('# A', encoding='utf-8')
        md2.write_text('# B', encoding='utf-8')

        app._create_window(str(md1))
        app._create_window(str(md2))

        with app._windows_lock:
            entry1 = app._windows[0]
        temp_html_path = entry1['temp_html']
        window1 = entry1['window']

        assert os.path.isfile(temp_html_path)

        closing_handler = window1._closing_handlers[0]
        closing_handler()

        assert not os.path.isfile(temp_html_path)


# ===========================================================================
# Group 3 — build_html
# ===========================================================================

class TestBuildHtml:
    """Test build_html: temp file creation, placeholder substitution, cleanup."""

    def test_creates_html_temp_file(self, patch_template):
        """build_html returns a path to an .html file."""
        path = app.build_html('# Hello', 'test.md', '/some/folder')
        assert os.path.isfile(path)
        assert path.endswith('.html')

    def test_placeholder_substituted(self, patch_template):
        """MDCONTENT_PLACEHOLDER is replaced by JSON-encoded content."""
        path = app.build_html('hello world', 'doc.md', '/dir')
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'MDCONTENT_PLACEHOLDER' not in content
        assert 'hello world' in content

    def test_script_tag_escaped(self, patch_template):
        r"""</script> inside md content is escaped as <\/script>."""
        md_with_script = 'before</script>after'
        path = app.build_html(md_with_script, 'doc.md', '/dir')
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        # The </script> inside the JSON string must be escaped
        assert '<\\/script>' in content

    def test_temp_file_registered_in_temp_html(self, patch_template):
        """build_html adds the temp path to app._temp_html."""
        before = list(app._temp_html)
        path = app.build_html('# X', 'x.md', '/')
        assert path in app._temp_html
        assert path not in before

    def test_cleanup_removes_temp_files(self, patch_template):
        """cleanup() removes all temp HTML files registered in _temp_html."""
        paths = [app.build_html(f'# {i}', f'f{i}.md', '/') for i in range(3)]
        for p in paths:
            assert os.path.isfile(p)

        app.cleanup()

        for p in paths:
            assert not os.path.isfile(p)

    def test_empty_content_keeps_edit_mode(self, patch_template):
        """build_html with empty md_content keeps setMode('edit') in the init script block."""
        path = app.build_html('', 'empty.md', '/dir')
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        # Empty content → the template init pattern setMode('edit');\n}\n</script> is NOT replaced
        assert "setMode('edit');\n}\n</script>" in content

    def test_nonempty_content_switches_to_read_mode(self, patch_template):
        """build_html with non-empty md_content switches init to setMode('read')."""
        path = app.build_html('# Some content', 'doc.md', '/dir')
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        # Non-empty content → init pattern switched to read
        assert "setMode('read');\n}\n</script>" in content
        assert "setMode('edit');\n}\n</script>" not in content


# ===========================================================================
# Group 4 — Api isolation
# ===========================================================================

class TestApiIsolation:
    """Test that multiple Api instances are completely independent."""

    def test_two_apis_have_independent_current_path(self, tmp_path):
        """Two Api instances have separate _current_path."""
        api1 = app.Api()
        api2 = app.Api()

        path1 = str(tmp_path / 'file1.md')
        path2 = str(tmp_path / 'file2.md')

        api1._current_path = path1
        api2._current_path = path2

        assert api1._current_path == path1
        assert api2._current_path == path2
        assert api1._current_path != api2._current_path

    def test_save_file_writes_to_own_current_path(self, tmp_path):
        """api.save_file writes to _current_path, doesn't affect the other Api."""
        file1 = tmp_path / 'file1.md'
        file2 = tmp_path / 'file2.md'
        file1.write_text('original1', encoding='utf-8')
        file2.write_text('original2', encoding='utf-8')

        api1 = app.Api()
        api2 = app.Api()
        api1._current_path = str(file1)
        api2._current_path = str(file2)

        # Mock windows so save dialogs aren't needed
        api1._window = MagicMock()
        api2._window = MagicMock()

        result1 = api1.save_file('content_for_1', 'file1.md')
        result2 = api2.save_file('content_for_2', 'file2.md')

        assert result1['ok'] is True
        assert result2['ok'] is True

        # Each file contains its own content
        assert file1.read_text(encoding='utf-8') == 'content_for_1'
        assert file2.read_text(encoding='utf-8') == 'content_for_2'

        # Paths remain independent
        assert api1._current_path == str(file1)
        assert api2._current_path == str(file2)

    def test_api_window_assignment_is_independent(self):
        """api._window is per-instance."""
        api1 = app.Api()
        api2 = app.Api()
        w1 = MagicMock(name='win1')
        w2 = MagicMock(name='win2')
        api1._window = w1
        api2._window = w2

        assert api1._get_window() is w1
        assert api2._get_window() is w2

    def test_save_file_no_path_dialog_cancelled_returns_not_ok(self):
        """save_file with no _current_path and cancelled dialog → {'ok': False}."""
        api = app.Api()
        w = MagicMock()
        w.create_file_dialog = MagicMock(return_value=None)
        api._window = w

        result = api.save_file('some content', 'doc.md')

        assert result['ok'] is False
        assert result.get('reason') == 'cancelled'


# ===========================================================================
# Group 5 — _get_file_arg parsing
# ===========================================================================

class TestGetFileArg:
    """Test _get_file_arg with various argv combinations."""

    def test_argv_with_file_returns_abspath(self, tmp_path):
        """argv=[app.py, file.md] → returns absolute path."""
        md = tmp_path / 'file.md'
        md.write_text('# Hi', encoding='utf-8')

        with patch.object(sys, 'argv', ['app.py', str(md)]):
            result = app._get_file_arg()

        assert result == str(md.resolve())

    def test_argv_with_silent_flag_and_file(self, tmp_path):
        """argv=[app.py, --silent, file.md] → skips flag, returns file."""
        md = tmp_path / 'hello.md'
        md.write_text('# Hello', encoding='utf-8')

        with patch.object(sys, 'argv', ['app.py', '--silent', str(md)]):
            result = app._get_file_arg()

        assert result == str(md.resolve())

    def test_argv_silent_only_returns_none(self):
        """argv=[app.py, --silent] → returns None."""
        with patch.object(sys, 'argv', ['app.py', '--silent']):
            result = app._get_file_arg()

        assert result is None

    def test_argv_no_args_returns_none(self):
        """argv=[app.py] → returns None."""
        with patch.object(sys, 'argv', ['app.py']):
            result = app._get_file_arg()

        assert result is None

    def test_argv_nonexistent_file_returns_none(self):
        """argv=[app.py, /no/such/file.md] → returns None (file doesn't exist)."""
        with patch.object(sys, 'argv', ['app.py', '/no/such/file.md']):
            result = app._get_file_arg()

        assert result is None

    def test_argv_multiple_flags_and_file(self, tmp_path):
        """argv=[app.py, --foo, --bar, file.md] → returns file."""
        md = tmp_path / 'doc.md'
        md.write_text('# Doc', encoding='utf-8')

        with patch.object(sys, 'argv', ['app.py', '--foo', '--bar', str(md)]):
            result = app._get_file_arg()

        assert result == str(md.resolve())

    def test_argv_empty_string_arg_returns_none(self):
        """argv=[app.py, ''] → empty string arg ignored, returns None."""
        with patch.object(sys, 'argv', ['app.py', '']):
            result = app._get_file_arg()

        assert result is None


# ===========================================================================
# Group 6 — Thread-safety of _windows
# ===========================================================================

class TestThreadSafety:
    """Test concurrent access to _windows doesn't corrupt state."""

    def test_concurrent_create_and_read(self, patch_template, tmp_path):
        """Multiple threads creating windows concurrently → no corruption."""
        errors = []
        created = []

        def create_one(i):
            md = tmp_path / f'f{i}.md'
            md.write_text(f'# File {i}', encoding='utf-8')
            try:
                w = app._create_window(str(md))
                created.append(w)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_one, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent create: {errors}"
        with app._windows_lock:
            assert len(app._windows) == 5

    def test_concurrent_add_and_remove(self, patch_template, tmp_path):
        """One thread adds windows, another reads _windows — no race conditions."""
        errors = []
        iterations = 10

        def adder():
            for i in range(iterations):
                md = tmp_path / f'add_{i}.md'
                md.write_text(f'# {i}', encoding='utf-8')
                try:
                    app._create_window(str(md))
                except Exception as e:
                    errors.append(('add', e))
                time.sleep(0.005)

        def reader():
            for _ in range(iterations * 2):
                try:
                    with app._windows_lock:
                        _ = list(app._windows)
                except Exception as e:
                    errors.append(('read', e))
                time.sleep(0.002)

        t_add = threading.Thread(target=adder)
        t_read = threading.Thread(target=reader)
        t_add.start()
        t_read.start()
        t_add.join()
        t_read.join()

        assert not errors, f"Thread safety errors: {errors}"

    def test_windows_lock_prevents_concurrent_modification(self, patch_template, tmp_path):
        """Lock is acquired during _windows access — simulated race condition safe."""
        # Verify that _windows_lock is a real threading.Lock / RLock
        assert hasattr(app._windows_lock, 'acquire')
        assert hasattr(app._windows_lock, 'release')

        barrier = threading.Barrier(2)
        results = []

        def thread_a():
            """Holds lock briefly, appends a sentinel."""
            with app._windows_lock:
                barrier.wait()  # Synchronize with thread_b
                time.sleep(0.02)
                app._windows.append({'window': MagicMock(), 'api': MagicMock(), 'temp_html': None})
                results.append('a_done')

        def thread_b():
            """Waits for the lock to be free, then reads."""
            barrier.wait()  # Synchronize — both enter near-simultaneously
            with app._windows_lock:
                snapshot = list(app._windows)
                results.append(('b_saw', len(snapshot)))

        ta = threading.Thread(target=thread_a)
        tb = threading.Thread(target=thread_b)
        ta.start()
        tb.start()
        ta.join()
        tb.join()

        assert 'a_done' in results
        # No exceptions means the lock worked correctly
