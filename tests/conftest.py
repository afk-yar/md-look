"""
Shared fixtures and webview/pystray mocking for MDLook tests.

webview, pystray, PIL must be mocked BEFORE `import app` because
app.py does `import webview` at module level.
"""
import sys
import os
import types
import tempfile
import threading
from unittest.mock import MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# Stub modules that may not be installed (pywebview, pystray, PIL)
# ---------------------------------------------------------------------------

def _make_webview_stub():
    """Return a minimal webview stub module."""
    mod = types.ModuleType('webview')
    mod.SAVE_DIALOG = 10
    mod.OPEN_DIALOG = 20

    # create_window returns a MagicMock window
    def _create_window(title='', url='', js_api=None, **kwargs):
        w = MagicMock(name='WebviewWindow')
        w.title = title
        # events.closing behaves like a list of callables
        closing_handlers = []
        w.events = MagicMock()
        w.events.closing = MagicMock()
        w.events.closing.__iadd__ = lambda self_, fn: closing_handlers.append(fn) or self_
        w._closing_handlers = closing_handlers
        w.hide = MagicMock()
        w.show = MagicMock()
        w.restore = MagicMock()
        w.destroy = MagicMock()
        w.load_url = MagicMock()
        w.set_title = MagicMock()
        w.create_file_dialog = MagicMock(return_value=None)
        return w

    mod.create_window = _create_window
    mod.start = MagicMock()
    mod.windows = []
    return mod


def _make_pystray_stub():
    mod = types.ModuleType('pystray')
    mod.Icon = MagicMock()
    mod.Menu = MagicMock()
    mod.Menu.SEPARATOR = MagicMock()
    mod.MenuItem = MagicMock()
    return mod


def _make_pil_stub():
    pil = types.ModuleType('PIL')
    img_mod = types.ModuleType('PIL.Image')
    img_mod.open = MagicMock(return_value=MagicMock())
    pil.Image = img_mod
    return pil, img_mod


# Install stubs into sys.modules if real packages not importable
def _install_stubs():
    if 'webview' not in sys.modules:
        sys.modules['webview'] = _make_webview_stub()
    if 'pystray' not in sys.modules:
        sys.modules['pystray'] = _make_pystray_stub()
    if 'PIL' not in sys.modules:
        pil, img_mod = _make_pil_stub()
        sys.modules['PIL'] = pil
        sys.modules['PIL.Image'] = img_mod


_install_stubs()


# ---------------------------------------------------------------------------
# Import app (safe because of main() guard)
# ---------------------------------------------------------------------------
# We need app on the module level so fixtures can reference it
import importlib

# Make sure app is not cached with wrong mocks
if 'app' in sys.modules:
    del sys.modules['app']

# Patch TEMPLATE_PATH before app reads it at module level (it's set at import time)
# Actually TEMPLATE_PATH is set at module level but used inside build_html — we can patch there.

import app  # noqa: E402  (after stubs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_app_state():
    """Reset all mutable global state in app between tests."""
    # Save originals
    orig_windows = list(app._windows)
    orig_quitting = app._quitting
    orig_temp_html = list(app._temp_html)

    # Clear state
    with app._windows_lock:
        app._windows.clear()
    app._quitting = False
    app._temp_html.clear()

    yield

    # Restore / clean up
    with app._windows_lock:
        app._windows.clear()
    app._quitting = False
    app._temp_html.clear()


@pytest.fixture()
def tiny_template(tmp_path):
    """Create a minimal HTML template file with all required placeholders."""
    tpl = tmp_path / 'template.html'
    tpl.write_text(
        '<html><body>'
        '<script>var md=MDCONTENT_PLACEHOLDER; var name=MDNAME_PLACEHOLDER; var folder=MDFOLDER_PLACEHOLDER;</script>'
        "<script>setMode('edit');\n}\n</script>\n</body></html>",
        encoding='utf-8',
    )
    return str(tpl)


@pytest.fixture()
def patch_template(tiny_template):
    """Patch app.TEMPLATE_PATH to the tiny template."""
    with patch.object(app, 'TEMPLATE_PATH', tiny_template):
        yield tiny_template


@pytest.fixture()
def mock_webview_create_window():
    """Patch webview.create_window so it uses the stub (returns MagicMock window)."""
    webview_mod = sys.modules['webview']
    original = webview_mod.create_window
    # Re-use the stub's create_window (already in place)
    yield webview_mod.create_window
    webview_mod.create_window = original
