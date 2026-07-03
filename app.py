"""
MDLook – Portable desktop app
Uses pywebview (WebView2 on Windows) to render the HTML template
and exposes native file I/O so Save writes directly to disk.
Minimizes to system tray on close for instant re-open.
Single-instance: second launch signals the first to show its window.
"""

import sys
import os
import json
import tempfile
import atexit
import subprocess
import threading
import socket
import hashlib
import ctypes
import ctypes.wintypes
import re
import urllib.parse
import urllib.request
import webbrowser

# ── Short path for pythonnet DLL ──
_base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
_buf = ctypes.create_unicode_buffer(512)
try:
    _pydll = os.path.join(_base, 'python312.dll')
    if os.path.isfile(_pydll):
        ctypes.windll.kernel32.GetShortPathNameW(_pydll, _buf, 512)
        os.environ['PYTHONNET_PYDLL'] = _buf.value
except Exception:
    pass

# ── Add pythonnet runtime to PATH ──
_pnrt = os.path.join(_base, 'pythonnet', 'runtime')
if os.path.isdir(_pnrt):
    os.environ['PATH'] = _pnrt + os.pathsep + os.environ.get('PATH', '')

# ── Remove Zone.Identifier ADS from DLLs/EXEs ──
for _root, _dirs, _files in os.walk(_base):
    for _fn in _files:
        if _fn.lower().endswith(('.dll', '.exe')):
            try:
                os.remove(os.path.join(_root, _fn + ':Zone.Identifier'))
            except OSError:
                pass

import webview


def _force_foreground():
    """Show all hidden MDLook windows, restore and force them to the foreground."""
    # Show all registered windows that are hidden
    with _windows_lock:
        entries = list(_windows)
    for entry in entries:
        try:
            entry['window'].show()
            entry['window'].restore()
        except Exception:
            pass

    import time
    time.sleep(0.15)

    user32 = ctypes.windll.user32
    SW_RESTORE = 9

    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    GetCurrentThreadId = ctypes.windll.kernel32.GetCurrentThreadId
    AttachThreadInput = user32.AttachThreadInput
    EnumWindows = user32.EnumWindows

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )

    def callback(h, _):
        cls = ctypes.create_unicode_buffer(256)
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, title, 256)
        user32.GetClassNameW(h, cls, 256)
        t = title.value
        if t == 'MDLook' or t.endswith('— MDLook'):
            fg_hwnd = user32.GetForegroundWindow()
            fg_tid = GetWindowThreadProcessId(fg_hwnd, None)
            our_tid = GetCurrentThreadId()
            AttachThreadInput(our_tid, fg_tid, True)
            user32.ShowWindow(h, SW_RESTORE)
            user32.SetForegroundWindow(h)
            user32.BringWindowToTop(h)
            AttachThreadInput(our_tid, fg_tid, False)
            # Continue enumeration — raise all MDLook windows, not just the first
        return True

    try:
        EnumWindows(WNDENUMPROC(callback), 0)
    except Exception:
        pass


def _force_foreground_window(window):
    """Restore and bring a specific pywebview window to the foreground.

    Shows the window if hidden (tray), then uses Win32 to set foreground
    by matching the window title.
    """
    try:
        window.show()
        window.restore()
    except Exception:
        pass

    import time
    time.sleep(0.15)

    # Retrieve the expected title from the window object
    try:
        target_title = window.title
    except Exception:
        target_title = None

    # Without a title we cannot reliably identify the HWND —
    # skip Win32 SetForegroundWindow to avoid raising a random MDLook window
    if not target_title:
        return

    user32 = ctypes.windll.user32
    SW_RESTORE = 9

    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    GetCurrentThreadId = ctypes.windll.kernel32.GetCurrentThreadId
    AttachThreadInput = user32.AttachThreadInput
    EnumWindows = user32.EnumWindows

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )

    def callback(h, _):
        title_buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, title_buf, 512)
        if title_buf.value == target_title:
            fg_hwnd = user32.GetForegroundWindow()
            fg_tid = GetWindowThreadProcessId(fg_hwnd, None)
            our_tid = GetCurrentThreadId()
            AttachThreadInput(our_tid, fg_tid, True)
            user32.ShowWindow(h, SW_RESTORE)
            user32.SetForegroundWindow(h)
            user32.BringWindowToTop(h)
            AttachThreadInput(our_tid, fg_tid, False)
            return False  # Stop enumeration — target window found
        return True

    try:
        EnumWindows(WNDENUMPROC(callback), 0)
    except Exception:
        pass


def _activate_window_async(window, delay=0.15):
    """Bring a pywebview window to the foreground after it has been created."""

    def _activate():
        try:
            import time
            time.sleep(delay)
            _force_foreground_window(window)
        except Exception:
            pass

    threading.Thread(target=_activate, daemon=True).start()


def _evaluate_goto_target_async(window, target, delay=0.1):
    """Run the page-level goto function with a short readiness retry."""
    if not target:
        return

    def _goto():
        import time
        script = (
            '(function(){'
            'if(!window.mdlookGotoTarget) return false;'
            'window.mdlookGotoTarget(%s);'
            'return true;'
            '})();'
        ) % json.dumps(str(target))
        for attempt in range(12):
            try:
                time.sleep(delay if attempt == 0 else 0.15)
                if window.evaluate_js(script):
                    return
            except Exception:
                pass

    threading.Thread(target=_goto, daemon=True).start()


def _goto_target_async(window, target, delay=0.1):
    """Ask the loaded page to scroll after the webview has loaded."""
    if not target:
        return

    events = getattr(window, 'events', None)
    loaded = getattr(events, 'loaded', None) if events is not None else None
    if loaded is None:
        _evaluate_goto_target_async(window, target, delay)
        return

    try:
        is_loaded = loaded.is_set()
    except Exception:
        is_loaded = None

    if is_loaded is True:
        _evaluate_goto_target_async(window, target, delay)
        return

    if is_loaded is False:
        def _on_loaded():
            try:
                loaded.__isub__(_on_loaded)
            except Exception:
                pass
            _evaluate_goto_target_async(window, target, delay)

        try:
            loaded += _on_loaded
            return
        except Exception:
            pass

    _evaluate_goto_target_async(window, target, delay)


# ── Single-instance IPC ──
SILENT_MODE = '--silent' in sys.argv
IPC_PORT_BASE = 49152
IPC_PORT_SPAN = 16000


def _instance_identity():
    """Return the path that scopes single-instance IPC for this app copy."""
    if getattr(sys, 'frozen', False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(__file__)


def _derive_ipc_port(identity=None):
    """Derive a stable localhost port for the current executable/script path."""
    seed = os.path.normcase(os.path.abspath(identity or _instance_identity()))
    digest = hashlib.blake2s(seed.encode('utf-8'), digest_size=4).digest()
    return IPC_PORT_BASE + (int.from_bytes(digest, 'big') % IPC_PORT_SPAN)


IPC_PORT = _derive_ipc_port()


def _get_goto_arg():
    """Get the external scroll target from --goto target or --goto=target."""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == '--goto' and i + 1 < len(args):
            return args[i + 1] or None
        if arg.startswith('--goto='):
            return arg.split('=', 1)[1] or None
    return None


def _get_file_arg():
    """Get the file path from command line args (ignoring flags)."""
    skip_next = False
    for arg in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg == '--goto':
            skip_next = True
            continue
        if arg.startswith('--goto='):
            continue
        if not arg.startswith('--') and arg != '':
            if os.path.isfile(arg):
                return os.path.abspath(arg)
    return None


def _build_open_message(filepath, target=None):
    msg = 'OPEN:' + filepath
    if target:
        msg += '\t' + str(target)
    return msg


def _parse_open_payload(payload):
    if '\t' not in payload:
        return payload, None
    filepath, target = payload.split('\t', 1)
    return filepath, target or None


def _parent_folder_for_title(folder):
    """Return the immediate parent folder name for the native window title."""
    if not folder:
        return ''

    folder = os.path.normpath(folder)
    parent = os.path.basename(folder)
    if parent:
        return parent

    drive, _ = os.path.splitdrive(folder)
    return drive or folder


def _format_window_title(filepath=None, filename='', folder='', dirty=False):
    """Build a compact, path-aware MDLook window title."""
    if filepath:
        filename = os.path.basename(filepath)
        folder = os.path.dirname(os.path.abspath(filepath))

    if not filename:
        return 'MDLook'

    compact_folder = _parent_folder_for_title(folder)
    core = filename
    if compact_folder:
        core += ' — ' + compact_folder

    prefix = '● ' if dirty else ''
    return prefix + core + ' — MDLook'


def _signal_existing_instance():
    """Try to signal an already-running instance to show/open file. Returns True if successful."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(('127.0.0.1', IPC_PORT))
        filepath = _get_file_arg()
        if filepath:
            msg = _build_open_message(filepath, _get_goto_arg())
        else:
            msg = 'SHOW'
        s.sendall(msg.encode('utf-8'))
        s.close()
        return True
    except (ConnectionRefusedError, OSError, socket.timeout):
        return False


def _start_ipc_listener():
    """Listen for signals from new instances."""

    def _listen():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(('127.0.0.1', IPC_PORT))
        except OSError:
            return
        srv.listen(1)
        srv.settimeout(1)
        while not _quitting:
            try:
                conn, _ = srv.accept()
                data = conn.recv(8192).decode('utf-8', errors='replace')
                conn.close()
                if data.startswith('OPEN:'):
                    filepath, target = _parse_open_payload(data[5:])
                    if os.path.isfile(filepath):
                        norm = os.path.normcase(filepath)
                        existing = None
                        with _windows_lock:
                            for entry in _windows:
                                cp = entry['api'].current_path
                                if cp and os.path.normcase(cp) == norm:
                                    existing = entry
                                    break
                        if existing is not None:
                            # File already open — activate the existing window
                            _activate_window_async(existing['window'], delay=0.05)
                            _goto_target_async(existing['window'], target, delay=0.2)
                        else:
                            # New file — open and bring to front after load
                            if target:
                                window = _create_window(filepath, target)
                            else:
                                window = _create_window(filepath)
                            _activate_window_async(window)
                elif data == 'SHOW':
                    _force_foreground()
            except socket.timeout:
                pass
            except Exception:
                pass

    t = threading.Thread(target=_listen, daemon=True)
    t.start()


# ── Paths ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, 'MDLook-template-offline.html')
ICON_PATH = os.path.join(BASE_DIR, 'MDLook.ico')
DOC_ICON_PATH = os.path.join(BASE_DIR, 'MDLook-doc.ico')
_temp_html = []


def cleanup():
    # Collect all known temp paths: global list + per-window entries
    paths = set(_temp_html)
    with _windows_lock:
        for entry in _windows:
            tmp = entry.get('temp_html')
            if tmp:
                paths.add(tmp)
    for path in paths:
        if os.path.isfile(path):
            try:
                os.unlink(path)
            except Exception:
                pass


atexit.register(cleanup)


def _powershell_open_dialog():
    """Show a native Windows Open File dialog via PowerShell subprocess.
    Works from any thread — no deadlock risk."""
    try:
        result = subprocess.run(
            (
                'powershell', '-NoProfile', '-Command',
                '[System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms") | Out-Null; '
                '$f = New-Object System.Windows.Forms.OpenFileDialog; '
                '$f.Filter = "Markdown files (*.md;*.txt)|*.md;*.markdown;*.txt|All files (*.*)|*.*"; '
                '$f.Title = "Open Markdown file"; '
                'if($f.ShowDialog() -eq "OK"){$f.FileName}'
            ),
            capture_output=True, text=True, timeout=120, creationflags=0x08000000,
        )
        path = result.stdout.strip()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass
    return None


def _is_markdown_document(path):
    return os.path.splitext(path)[1].lower() in ('.md', '.markdown', '.txt')


def _natural_sort_key(name):
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r'(\d+)', str(name))
    ]


def _is_windows_absolute_path(value):
    return (
        len(value) >= 3
        and value[1] == ':'
        and value[2] in ('\\', '/')
        and value[0].isalpha()
    ) or value.startswith('\\\\')


def _file_url_to_path(url):
    parsed = urllib.parse.urlparse(url)
    path = urllib.request.url2pathname(parsed.path)
    if parsed.netloc:
        path = '//' + parsed.netloc + path
    if len(path) >= 3 and path[0] == '/' and path[2] == ':':
        path = path[1:]
    return os.path.abspath(path)


class Api:
    """Python functions exposed to JavaScript via window.pywebview.api"""

    def __init__(self):
        self._current_path = None
        self._window = None
        self._file_mtime = None

    @property
    def current_path(self):
        return self._current_path

    def _get_window(self):
        """Get the webview window reliably."""
        return self._window

    def _update_window_title(self):
        w = self._get_window()
        if not w or not self._current_path:
            return
        try:
            w.set_title(_format_window_title(filepath=self._current_path))
        except Exception:
            pass

    def save_file(self, content, filename):
        """Save directly to the original file path, or ask where."""
        path = self._current_path
        if path and os.path.isfile(path):
            pass  # use existing path
        else:
            w = self._get_window()
            if not w:
                return {'ok': False, 'reason': 'no window'}
            result = w.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=filename or 'document.md',
                file_types=('Markdown files (*.md)',),
            )
            if not result or (isinstance(result, str) and not result):
                return {'ok': False, 'reason': 'cancelled'}
            path = result if isinstance(result, str) else result[0]

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            self._current_path = path
            self._file_mtime = os.path.getmtime(path)
            self._update_window_title()
            return {'ok': True, 'path': path, 'name': os.path.basename(path)}
        except Exception as e:
            return {'ok': False, 'reason': str(e)}

    def save_file_as(self, content, filename):
        """Always ask where to save."""
        w = self._get_window()
        if not w:
            return {'ok': False, 'reason': 'no window'}
        result = w.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=filename or 'document.md',
            file_types=('Markdown files (*.md)',),
        )
        if not result or (isinstance(result, str) and not result):
            return {'ok': False, 'reason': 'cancelled'}
        path = result if isinstance(result, str) else result[0]

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            self._current_path = path
            self._file_mtime = os.path.getmtime(path)
            self._update_window_title()
            return {'ok': True, 'path': path, 'name': os.path.basename(path)}
        except Exception as e:
            return {'ok': False, 'reason': str(e)}

    def open_file(self):
        """Show native Open dialog using Win32 API (works from any thread)."""
        path = _powershell_open_dialog()
        if not path:
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            self._current_path = path
            self._file_mtime = os.path.getmtime(path)
            self._update_window_title()
            return {'content': content, 'name': os.path.basename(path), 'path': path}
        except Exception:
            return None

    def list_sibling_files(self):
        """Return Markdown-like files from the current document folder."""
        path = self._current_path
        if not path:
            return {'ok': False, 'reason': 'no current file'}

        folder = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(folder):
            return {'ok': False, 'reason': 'folder not found', 'folder': folder}

        current_norm = os.path.normcase(os.path.abspath(path))
        files = []
        try:
            names = sorted(os.listdir(folder), key=_natural_sort_key)
            for name in names:
                item_path = os.path.join(folder, name)
                if not os.path.isfile(item_path) or not _is_markdown_document(item_path):
                    continue
                stat = os.stat(item_path)
                files.append({
                    'name': name,
                    'path': item_path,
                    'is_current': os.path.normcase(os.path.abspath(item_path)) == current_norm,
                    'size': stat.st_size,
                    'mtime': stat.st_mtime,
                })
        except Exception as e:
            return {'ok': False, 'reason': str(e), 'folder': folder}

        return {'ok': True, 'folder': folder, 'files': files}

    def open_file_path(self, path):
        """Open a Markdown-like file path in the current window."""
        target = os.path.abspath(str(path or ''))
        if not os.path.isfile(target):
            return {'ok': False, 'reason': 'not found', 'path': target}
        if not _is_markdown_document(target):
            return {'ok': False, 'reason': 'unsupported file type', 'path': target}

        try:
            with open(target, 'r', encoding='utf-8') as f:
                content = f.read()
            self._current_path = target
            self._file_mtime = os.path.getmtime(target)
            self._update_window_title()
            return {
                'ok': True,
                'content': content,
                'name': os.path.basename(target),
                'path': target,
            }
        except Exception as e:
            return {'ok': False, 'reason': str(e), 'path': target}

    def open_url(self, url):
        """Open a non-hash Markdown link via the appropriate native action."""
        raw = str(url or '').strip()
        if not raw or raw.startswith('#'):
            return {'ok': False, 'reason': 'empty'}

        parsed = urllib.parse.urlparse(raw)
        scheme = parsed.scheme.lower()

        if scheme in ('http', 'https', 'mailto'):
            ok = bool(webbrowser.open(raw))
            return {'ok': ok, 'action': 'external', 'url': raw}

        if scheme and scheme != 'file':
            return {'ok': False, 'reason': 'blocked scheme'}

        target = ''
        if scheme == 'file':
            path = _file_url_to_path(raw)
            target = urllib.parse.unquote(parsed.fragment or '')
        else:
            path_part, sep, target_part = raw.partition('#')
            if not (os.path.isabs(path_part) or _is_windows_absolute_path(path_part)):
                return {'ok': False, 'reason': 'blocked scheme'}
            path = os.path.abspath(path_part)
            target = urllib.parse.unquote(target_part) if sep else ''

        if not os.path.isfile(path):
            return {'ok': False, 'reason': 'not found', 'path': path}

        if _is_markdown_document(path):
            _create_window(path, target or None)
            return {'ok': True, 'action': 'mdlook', 'path': path, 'target': target}

        try:
            if hasattr(os, 'startfile'):
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
            return {'ok': True, 'action': 'system', 'path': path}
        except Exception as e:
            return {'ok': False, 'reason': str(e), 'path': path}

    def reload_file(self, force=False):
        """Re-read the current file from disk if it changed (or always if force=True).

        Returns {'content': ..., 'name': ..., 'path': ...} on success, None otherwise.
        """
        path = self._current_path
        if not path or not os.path.isfile(path):
            return None
        try:
            mtime = os.path.getmtime(path)
            if not force and self._file_mtime is not None and mtime == self._file_mtime:
                return None
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            self._file_mtime = mtime
            self._update_window_title()
            return {'content': content, 'name': os.path.basename(path), 'path': path}
        except Exception:
            return None

    def save_html(self, html_content, filename):
        """Save exported HTML via native dialog."""
        w = self._get_window()
        if not w:
            return {'ok': False}
        result = w.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=filename or 'document.html',
            file_types=('HTML files (*.html)',),
        )
        if not result or (isinstance(result, str) and not result):
            return {'ok': False, 'reason': 'cancelled'}
        path = result if isinstance(result, str) else result[0]

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            return {'ok': True, 'path': path}
        except Exception as e:
            return {'ok': False, 'reason': str(e)}


BRIDGE_JS = """
<script>
(function(){
  function initBridge(){
    var folderNavCache = null;
    var folderNavCachePath = null;

    function updateFileChrome(name, path){
      var previousPath = filePath;
      if(name) fileName = name;
      if(typeof path === 'string') filePath = path;
      if(filePath !== previousPath){
        folderNavCache = null;
        folderNavCachePath = null;
      }
      var fn = document.querySelector('#fn');
      if(fn){
        fn.textContent = fileName || 'New document';
        if(filePath) fn.setAttribute('title', filePath);
        else fn.removeAttribute('title');
      }
      if(window.mdlookTitleForFile) document.title = window.mdlookTitleForFile(fileName, filePath, hasUnsaved);
      else document.title = (hasUnsaved ? '\\u25cf ' : '') + (fileName || 'MDLook') + ' \\u2014 MDLook';
    }

    function applyDocumentResult(res){
      if(!res || res.ok === false || typeof res.content !== 'string') return false;
      rawMd = typeof res.content === 'string' ? res.content : '';
      hasUnsaved = false;
      document.body.classList.remove('unsaved');
      updateFileChrome(res.name || fileName, res.path || '');
      var ed = document.querySelector('#editor');
      if(ed) ed.value = rawMd;
      clearAutoSave();
      if(window._mdlookResetSearch) window._mdlookResetSearch();
      setMode('read');
      return true;
    }

    // ── Override Save: write to disk instead of Blob download ──
    window.saveFile = async function(){
      var editor = document.querySelector('#editor');
      if(editor) rawMd = editor.value;
      var content = rawMd || '';
      var fn = fileName || 'document.md';
      var res = await window.pywebview.api.save_file(content, fn);
      if(res && res.ok){
        hasUnsaved = false;
        document.body.classList.remove('unsaved');
        updateFileChrome(res.name || fileName, res.path || filePath);
        var btn = document.querySelector('#btnSave');
        var orig = btn.innerHTML;
        btn.classList.add('saved');
        btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:15px;height:15px"><polyline points="20 6 9 17 4 12"/></svg>';
        setTimeout(function(){ btn.innerHTML = orig; btn.classList.remove('saved'); }, 1500);
      }
    };
    document.querySelector('#btnSave').onclick = saveFile;

    // ── Override Open: use pywebview native file dialog ──
    // WebView2 blocks programmatic input.click() on file inputs.
    // Replace #btnOpen entirely to kill all old event listeners.
    var oldBtn = document.querySelector('#btnOpen');
    var newBtn = document.createElement('button');
    newBtn.className = oldBtn.className;
    newBtn.id = oldBtn.id;
    newBtn.innerHTML = oldBtn.innerHTML;
    if(oldBtn.dataset.tip) newBtn.setAttribute('data-tip', oldBtn.dataset.tip);
    oldBtn.parentNode.replaceChild(newBtn, oldBtn);
    newBtn.addEventListener('click', async function(e){
      e.preventDefault();
      e.stopPropagation();
      if(hasUnsaved && !await appConfirm('Unsaved changes. Open another file?')) return;
      window.pywebview.api.open_file().then(function(res){
        applyDocumentResult(res);
      }).catch(function(err){ alert('Open failed: ' + err); });
    });

    // ── Override Export HTML: replace Blob download with Python save ──
    var oldExport = document.querySelector('#exportHTML');
    var newExport = oldExport.cloneNode(true);
    oldExport.parentNode.replaceChild(newExport, oldExport);
    newExport.addEventListener('click', function(){
      var menu = document.querySelector('#exportMenu');
      if(menu) menu.classList.remove('open');
      var fn = (fileName || 'document').replace(/\\.md$/i, '') + '.html';
      // Use the original template's exportHTML function logic
      // Get the full rendered page HTML from the document
      var clone = document.documentElement.cloneNode(true);
      // Remove UI elements not needed in export
      var remove = clone.querySelectorAll('.bar, .editor-wrap, .outline-panel, .folder-panel, .folder-panel-scrim, .zen-exit, .modal, script, .teleprompter-bar, .auto-scroll-btn');
      for(var i=0;i<remove.length;i++) remove[i].remove();
      // Clean up body classes
      var body = clone.querySelector('body');
      if(body){ body.className = ''; body.setAttribute('data-theme', document.body.getAttribute('data-theme')||''); }
      // Fix reader padding
      var reader = clone.querySelector('.reader');
      if(reader) reader.style.padding = '32px 48px 80px 32px';
      var html = '<!DOCTYPE html>\\n' + clone.outerHTML;
      window.pywebview.api.save_html(html, fn);
    });

    // ── Refresh button: force-reload from disk ──
    var btnOpen2 = document.querySelector('#btnOpen');
    var btnRefresh = document.createElement('button');
    btnRefresh.id = 'btnRefresh';
    btnRefresh.className = btnOpen2 ? btnOpen2.className : '';
    btnRefresh.setAttribute('data-tip', 'Reload file');
    btnRefresh.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" style="width:15px;height:15px"><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>';
    if(btnOpen2 && btnOpen2.parentNode) btnOpen2.parentNode.insertBefore(btnRefresh, btnOpen2.nextSibling);
    btnRefresh.addEventListener('click', async function(){
      if(hasUnsaved && !await appConfirm('Unsaved changes. Reload and discard?')) return;
      var res = await window.pywebview.api.reload_file(true);
      applyDocumentResult(res);
    });

    // ── Reload on focus: re-read file when window regains focus ──
    var _reloadDebounceTimer = null;
    window.addEventListener('focus', function(){
      if(_reloadDebounceTimer) return;
      _reloadDebounceTimer = setTimeout(function(){ _reloadDebounceTimer = null; }, 1000);
      if(hasUnsaved) return;
      window.pywebview.api.reload_file().then(function(res){
        applyDocumentResult(res);
      });
    });

    // ── Folder navigation: sibling Markdown files in the current directory ──
    (function(){
      var btn = document.querySelector('#btnFolderNav');
      var panel = document.querySelector('#folderPanel');
      var scrim = document.querySelector('#folderPanelScrim');
      var closeBtn = document.querySelector('#folderPanelClose');
      var filter = document.querySelector('#folderFileFilter');
      var list = document.querySelector('#folderFileList');
      var pathLabel = document.querySelector('#folderPanelPath');
      if(!btn || !panel || !scrim || !filter || !list) return;

      var folderFiles = [];
      var folderOpen = false;

      function setFolderOpen(value){
        folderOpen = !!value;
        panel.classList.toggle('open', folderOpen);
        scrim.classList.toggle('open', folderOpen);
        btn.classList.toggle('active', folderOpen);
        btn.setAttribute('aria-expanded', folderOpen ? 'true' : 'false');
        if(folderOpen){
          loadFolderFiles(false);
          setTimeout(function(){ try{ filter.focus(); filter.select(); } catch(_){} }, 30);
        }
      }

      function renderFolderMessage(text, cls){
        list.innerHTML = '';
        var el = document.createElement('div');
        el.className = cls || 'folder-file-empty';
        el.textContent = text;
        list.appendChild(el);
      }

      function renderFolderFiles(){
        list.innerHTML = '';
        var q = (filter.value || '').trim().toLowerCase();
        var shown = folderFiles.filter(function(file){
          return !q || String(file.name || '').toLowerCase().indexOf(q) !== -1;
        });
        if(!shown.length){
          renderFolderMessage(q ? 'No matching files' : 'No Markdown files in this folder', 'folder-file-empty');
          return;
        }
        shown.forEach(function(file){
          var item = document.createElement('button');
          item.type = 'button';
          item.className = 'folder-file-item' + (file.is_current ? ' current' : '');
          item.title = file.path || file.name || '';
          item.dataset.path = file.path || '';

          var name = document.createElement('span');
          name.className = 'folder-file-item-name';
          name.textContent = file.name || file.path || '';
          item.appendChild(name);

          item.addEventListener('click', function(){
            openFolderFile(file);
          });
          list.appendChild(item);
        });
      }

      async function loadFolderFiles(force){
        var api = window.pywebview && window.pywebview.api;
        if(!api || !api.list_sibling_files){
          renderFolderMessage('Folder navigation is unavailable', 'folder-file-error');
          return;
        }
        if(!force && folderNavCache && folderNavCachePath === filePath){
          folderFiles = folderNavCache.files || [];
          if(pathLabel) pathLabel.textContent = folderNavCache.folder || folderFromPath(filePath) || '';
          renderFolderFiles();
          return;
        }

        renderFolderMessage('Loading files...', 'folder-file-loading');
        try{
          var res = await api.list_sibling_files();
          if(!res || !res.ok){
            folderFiles = [];
            if(pathLabel) pathLabel.textContent = folderFromPath(filePath) || '';
            renderFolderMessage((res && res.reason) || 'No current file', 'folder-file-empty');
            return;
          }
          folderNavCache = res;
          folderNavCachePath = filePath;
          folderFiles = Array.isArray(res.files) ? res.files : [];
          if(pathLabel) pathLabel.textContent = res.folder || folderFromPath(filePath) || '';
          renderFolderFiles();
        } catch(err){
          renderFolderMessage('Failed to read folder', 'folder-file-error');
        }
      }

      async function openFolderFile(file){
        if(!file || file.is_current || !file.path) return;
        var api = window.pywebview && window.pywebview.api;
        if(!api || !api.open_file_path){
          renderFolderMessage('Open file API is unavailable', 'folder-file-error');
          return;
        }
        if(hasUnsaved && !await appConfirm('Unsaved changes. Open another file?')) return;
        try{
          var res = await api.open_file_path(file.path);
          if(!res || !res.ok){
            renderFolderMessage((res && res.reason) || 'Open failed', 'folder-file-error');
            return;
          }
          if(applyDocumentResult(res)) setFolderOpen(false);
        } catch(err){
          renderFolderMessage('Open failed', 'folder-file-error');
        }
      }

      btn.addEventListener('click', function(e){
        e.preventDefault();
        e.stopPropagation();
        setFolderOpen(!folderOpen);
      });
      scrim.addEventListener('click', function(){ setFolderOpen(false); });
      if(closeBtn) closeBtn.addEventListener('click', function(){ setFolderOpen(false); });
      filter.addEventListener('input', renderFolderFiles);
      document.addEventListener('keydown', function(e){
        if(e.key === 'Escape' && folderOpen){
          e.preventDefault();
          e.stopPropagation();
          setFolderOpen(false);
        }
      }, true);
    })();

    function mdlookExpandForTarget(target){
      var el = target && target.closest ? target.closest('.md-section') : null;
      var toExpand = [];
      while(el){
        if(el.classList.contains('collapsed')) toExpand.push(el);
        el = el.parentElement && el.parentElement.closest ? el.parentElement.closest('.md-section') : null;
      }
      toExpand.reverse().forEach(function(s){ s.classList.remove('collapsed'); });
    }

    function mdlookScrollElement(target, behavior){
      if(!target) return false;
      mdlookExpandForTarget(target);
      target.scrollIntoView({behavior:behavior || 'smooth', block:'start'});
      return true;
    }

    function mdlookFindTextTarget(query){
      query = String(query || '');
      if(!query) return null;
      var needle = query.toLowerCase();
      var roots = [
        document.querySelector('#readerContent'),
        document.querySelector('#reader'),
        document.querySelector('#preview'),
        document.body
      ].filter(Boolean);
      var seen = [];
      for(var r=0; r<roots.length; r++){
        var root = roots[r];
        if(seen.indexOf(root) !== -1) continue;
        seen.push(root);
        var walker = document.createTreeWalker(
          root,
          NodeFilter.SHOW_TEXT,
          {
            acceptNode: function(node){
              var p = node.parentNode;
              if(!p) return NodeFilter.FILTER_REJECT;
              var tag = p.tagName ? p.tagName.toLowerCase() : '';
              if(tag === 'script' || tag === 'style') return NodeFilter.FILTER_REJECT;
              if(p.closest && (p.closest('.bar') || p.closest('#mdlook-search-bar'))) return NodeFilter.FILTER_REJECT;
              return node.textContent.toLowerCase().indexOf(needle) !== -1
                ? NodeFilter.FILTER_ACCEPT
                : NodeFilter.FILTER_SKIP;
            }
          }
        );
        var node = walker.nextNode();
        if(node) return node.parentElement || node.parentNode;
      }
      return null;
    }

    function mdlookGotoTargetNow(target){
      target = String(target || '').trim();
      if(!target) return false;
      if(target.charAt(0) === '#') target = target.slice(1);
      try{ target = decodeURIComponent(target); } catch(_){}
      var lower = target.toLowerCase();
      var upper = target.toUpperCase();
      var anchor = document.getElementById(target) || document.getElementsByName(target)[0]
        || document.getElementById(lower) || document.getElementsByName(lower)[0]
        || document.getElementById(upper) || document.getElementsByName(upper)[0];
      if(anchor) return mdlookScrollElement(anchor, 'smooth');
      return mdlookScrollElement(mdlookFindTextTarget(target), 'smooth');
    }

    window.mdlookGotoTarget = function(target){
      var attemptsLeft = 10;
      function attempt(){
        if(mdlookGotoTargetNow(target)) return;
        attemptsLeft -= 1;
        if(attemptsLeft > 0) setTimeout(attempt, 120);
      }
      attempt();
    };

    // ── Internal anchor links: keep #... navigation inside WebView ──
    document.addEventListener('click', function(e){
      var a = e.target && e.target.closest ? e.target.closest('a[href^="#"]') : null;
      if(!a) return;
      var href = a.getAttribute('href') || '';
      if(href === '#') return;
      e.preventDefault();
      e.stopPropagation();
      var id = href.slice(1);
      try{ id = decodeURIComponent(id); } catch(_){}
      var target = document.getElementById(id) || document.getElementsByName(id)[0];
      if(!mdlookScrollElement(target, 'smooth')) return;
      try{ if(history && history.replaceState) history.replaceState(null, '', href); } catch(_){}
    }, true);

    // ── Non-hash links: delegate external files/URLs to Python ──
    document.addEventListener('click', function(e){
      var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
      if(!a) return;
      var rawHref = a.getAttribute('href') || '';
      if(!rawHref || rawHref.charAt(0) === '#') return;
      var api = window.pywebview && window.pywebview.api;
      if(!api || !api.open_url) return;
      e.preventDefault();
      e.stopPropagation();
      var href = a.href || rawHref;
      Promise.resolve(api.open_url(href)).catch(function(err){
        try{ console.warn('MDLook link open failed', err); } catch(_){}
      });
    }, true);

    // ── In-page search (Ctrl+F) ──
    (function(){
      var bar = null, input = null, counter = null, matches = [], matchIdx = -1, searchMode = 'dom';
      var searchTarget = null, lastSearchTarget = 'reader';
      var MARK_CLASS = 'mdlook-search-hl';
      var MARK_CUR   = 'mdlook-search-cur';

      function searchTargetFromNode(node){
        if(!node || !node.closest) return null;
        if(node.closest('#mdlook-search-bar')) return null;
        if(node.closest('#editor')) return 'editor';
        if(node.closest('#previewPane') || node.closest('#preview')) return 'preview';
        if(node.closest('#reader') || node.closest('#readerContent')) return 'reader';
        if(node.closest('#rawSource')) return 'source';
        return null;
      }

      function rememberSearchTarget(node){
        var target = searchTargetFromNode(node);
        if(target) lastSearchTarget = target;
        return target;
      }

      function currentSearchTarget(origin){
        if(searchTarget) return searchTarget;
        var target = rememberSearchTarget(origin) || searchTargetFromNode(document.activeElement);
        if(target) return target;
        var ed = document.querySelector('#editor');
        var wrap = document.querySelector('#editorWrap');
        if(ed && wrap && wrap.classList.contains('active')){
          if(lastSearchTarget === 'preview' || lastSearchTarget === 'editor') return lastSearchTarget;
          return 'editor';
        }
        return lastSearchTarget || 'reader';
      }

      document.addEventListener('focusin', function(e){ rememberSearchTarget(e.target); }, true);
      document.addEventListener('pointerdown', function(e){ rememberSearchTarget(e.target); }, true);

      function createBar(){
        if(bar) return;
        bar = document.createElement('div');
        bar.id = 'mdlook-search-bar';
        bar.style.cssText = [
          'position:fixed','top:8px','right:16px','z-index:99999',
          'display:flex','align-items:center','gap:6px',
          'background:var(--bg,#fff)','border:1px solid var(--border,#ccc)',
          'border-radius:6px','padding:5px 8px',
          'box-shadow:0 2px 10px rgba(0,0,0,.18)',
          'font-family:Inter,system-ui,sans-serif','font-size:13px'
        ].join(';');

        input = document.createElement('input');
        input.type = 'text';
        input.placeholder = 'Search…';
        input.style.cssText = 'border:none;outline:none;width:180px;background:transparent;color:inherit;font-size:13px;';

        counter = document.createElement('span');
        counter.style.cssText = 'min-width:56px;color:#888;font-size:12px;white-space:nowrap;';
        counter.textContent = '';

        var btnPrev = document.createElement('button');
        btnPrev.innerHTML = '&#x25B2;';
        btnPrev.title = 'Previous (Shift+Enter)';
        btnPrev.style.cssText = 'border:none;background:none;cursor:pointer;padding:0 3px;font-size:13px;';

        var btnNext = document.createElement('button');
        btnNext.innerHTML = '&#x25BC;';
        btnNext.title = 'Next (Enter)';
        btnNext.style.cssText = 'border:none;background:none;cursor:pointer;padding:0 3px;font-size:13px;';

        var btnClose = document.createElement('button');
        btnClose.innerHTML = '\\u00D7';
        btnClose.title = 'Close (Esc)';
        btnClose.style.cssText = 'border:none;background:none;cursor:pointer;padding:0 3px;font-size:16px;line-height:1;';

        bar.appendChild(input);
        bar.appendChild(counter);
        bar.appendChild(btnPrev);
        bar.appendChild(btnNext);
        bar.appendChild(btnClose);
        document.body.appendChild(bar);

        // Style for highlights (injected once)
        if(!document.getElementById('mdlook-search-style')){
          var st = document.createElement('style');
          st.id = 'mdlook-search-style';
          st.textContent = [
            '.' + MARK_CLASS + '{background:#ffe066;color:inherit;border-radius:2px;}',
            '.' + MARK_CUR   + '{background:#ff9800!important;color:#fff!important;}',
            '#mdlook-editor-search-overlay .' + MARK_CLASS + '{color:transparent!important;background:rgba(255,224,102,.65)!important;}',
            '#mdlook-editor-search-overlay .' + MARK_CUR + '{color:transparent!important;background:rgba(255,152,0,.9)!important;}'
          ].join('\\n');
          document.head.appendChild(st);
        }

        input.addEventListener('input', function(){ doSearch(input.value); });
        input.addEventListener('keydown', function(e){
          if(e.key === 'Enter'){
            e.preventDefault();
            if(e.shiftKey) navigatePrev(); else navigateNext();
          } else if(e.key === 'Escape'){
            closeSearch();
          }
        });
        btnPrev.addEventListener('click', navigatePrev);
        btnNext.addEventListener('click', navigateNext);
        btnClose.addEventListener('click', closeSearch);
      }

      function getSearchRoot(target){
        if(target === 'preview'){
          var preview = document.querySelector('#preview');
          if(preview) return preview;
        }
        if(target === 'source'){
          var raw = document.querySelector('#rawSource');
          if(raw) return raw;
        }
        if(target === 'reader'){
          var readerContent = document.querySelector('#readerContent');
          var reader = document.querySelector('#reader');
          return readerContent || reader || document.body;
        }

        var reader = document.querySelector('#reader');
        var readerContent = document.querySelector('#readerContent');
        if(reader && reader.offsetHeight > 0) return readerContent || reader;

        var raw = document.querySelector('#rawSource');
        if(raw && raw.classList.contains('v')) return raw;

        var preview = document.querySelector('#preview');
        var previewPane = document.querySelector('#previewPane');
        if(preview && previewPane && previewPane.offsetHeight > 0) return preview;

        return document.body;
      }

      function clearHighlights(){
        var marks = document.querySelectorAll('.' + MARK_CLASS);
        marks.forEach(function(m){
          var parent = m.parentNode;
          if(!parent) return;
          parent.replaceChild(document.createTextNode(m.textContent), m);
          parent.normalize();
        });
        clearEditorOverlay();
        matches = [];
        matchIdx = -1;
      }

      function clearEditorOverlay(){
        var overlay = document.querySelector('#mdlook-editor-search-overlay');
        if(overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
      }

      function ensureEditorOverlay(ed){
        var overlay = document.querySelector('#mdlook-editor-search-overlay');
        if(!overlay){
          overlay = document.createElement('div');
          overlay.id = 'mdlook-editor-search-overlay';
          overlay.style.cssText = [
            'position:absolute','inset:0','z-index:5','pointer-events:none',
            'overflow:hidden','white-space:pre-wrap','word-wrap:break-word',
            'box-sizing:border-box','color:transparent','background:transparent'
          ].join(';');
          ed.parentNode.appendChild(overlay);
          ed.addEventListener('scroll', function(){ syncEditorOverlay(ed); });
        }

        var cs = getComputedStyle(ed);
        overlay.style.font = cs.font;
        overlay.style.lineHeight = cs.lineHeight;
        overlay.style.padding = cs.padding;
        overlay.style.border = cs.border;
        overlay.style.letterSpacing = cs.letterSpacing;
        overlay.style.tabSize = cs.tabSize;
        overlay.style.width = ed.clientWidth + 'px';
        overlay.style.height = ed.clientHeight + 'px';
        return overlay;
      }

      function syncEditorOverlay(ed){
        var overlay = document.querySelector('#mdlook-editor-search-overlay');
        if(!overlay) return;
        overlay.scrollTop = ed.scrollTop;
        overlay.scrollLeft = ed.scrollLeft;
      }

      function renderEditorOverlay(ed){
        if(searchMode !== 'editor' || !input || !input.value || !matches.length){
          clearEditorOverlay();
          return;
        }

        var overlay = ensureEditorOverlay(ed);
        overlay.textContent = '';

        var text = ed.value || '';
        var frag = document.createDocumentFragment();
        var last = 0;
        matches.forEach(function(match, idx){
          if(match.start > last) frag.appendChild(document.createTextNode(text.slice(last, match.start)));
          var mark = document.createElement('mark');
          mark.className = MARK_CLASS + (idx === matchIdx ? ' ' + MARK_CUR : '');
          mark.textContent = text.slice(match.start, match.end);
          frag.appendChild(mark);
          last = match.end;
        });
        if(last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
        overlay.appendChild(frag);
        syncEditorOverlay(ed);
      }

      function scrollEditorToMatch(ed, start){
        var targetY = 0;
        try{
          if(typeof editorCharToScroll === 'function'){
            targetY = editorCharToScroll(start);
          } else {
            var cs = getComputedStyle(ed);
            var mirror = document.createElement('div');
            mirror.style.cssText = 'position:absolute;visibility:hidden;white-space:pre-wrap;word-wrap:break-word;overflow:hidden;';
            mirror.style.width = cs.width;
            mirror.style.font = cs.font;
            mirror.style.lineHeight = cs.lineHeight;
            mirror.style.padding = cs.padding;
            mirror.style.tabSize = cs.tabSize;
            mirror.textContent = ed.value.substring(0, start);
            ed.parentNode.appendChild(mirror);
            targetY = mirror.scrollHeight;
            mirror.remove();
          }
        } catch(e){
          targetY = (start / Math.max(1, ed.value.length)) * (ed.scrollHeight - ed.clientHeight);
        }
        ed.scrollTop = Math.max(0, targetY - ed.clientHeight / 3);
      }

      function doEditorSearch(q){
        searchMode = 'editor';
        var ed = document.querySelector('#editor');
        if(!ed) return;

        var text = ed.value || '';
        var lt = text.toLowerCase();
        var lq = q.toLowerCase();
        var last = 0, idx;
        while((idx = lt.indexOf(lq, last)) !== -1){
          matches.push({start: idx, end: idx + q.length});
          last = idx + q.length;
        }

        if(matches.length > 0){
          matchIdx = 0;
          highlightCurrent();
        } else {
          renderEditorOverlay(ed);
        }
        updateCounter();
      }

      function doDomSearch(q, target){
        searchMode = 'dom';
        var root = getSearchRoot(target);
        var lq = q.toLowerCase();

        // Walk text nodes
        var walker = document.createTreeWalker(
          root,
          NodeFilter.SHOW_TEXT,
          {
            acceptNode: function(node){
              var p = node.parentNode;
              if(!p) return NodeFilter.FILTER_REJECT;
              // Skip script/style/search bar itself
              var tag = p.tagName ? p.tagName.toLowerCase() : '';
              if(tag === 'script' || tag === 'style') return NodeFilter.FILTER_REJECT;
              if(p.closest && p.closest('#mdlook-search-bar')) return NodeFilter.FILTER_REJECT;
              if(node.textContent.toLowerCase().indexOf(lq) === -1) return NodeFilter.FILTER_SKIP;
              return NodeFilter.FILTER_ACCEPT;
            }
          }
        );

        var nodes = [];
        var n;
        while((n = walker.nextNode())) nodes.push(n);

        nodes.forEach(function(textNode){
          var text = textNode.textContent;
          var lt = text.toLowerCase();
          var parent = textNode.parentNode;
          if(!parent) return;
          var frag = document.createDocumentFragment();
          var last = 0, idx;
          while((idx = lt.indexOf(lq, last)) !== -1){
            if(idx > last) frag.appendChild(document.createTextNode(text.slice(last, idx)));
            var mark = document.createElement('mark');
            mark.className = MARK_CLASS;
            mark.textContent = text.slice(idx, idx + q.length);
            frag.appendChild(mark);
            matches.push(mark);
            last = idx + q.length;
          }
          if(last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
          parent.replaceChild(frag, textNode);
        });

        if(matches.length > 0){
          matchIdx = 0;
          highlightCurrent();
        }
        updateCounter();
      }

      function doSearch(q){
        clearHighlights();
        if(!q){ counter.textContent = ''; return; }

        if(currentSearchTarget() === 'editor') doEditorSearch(q);
        else doDomSearch(q, currentSearchTarget());
      }

      function highlightCurrent(){
        if(searchMode === 'editor'){
          var ed = document.querySelector('#editor');
          var match = matches[matchIdx];
          if(ed) renderEditorOverlay(ed);
          if(ed && match){
            try{ ed.setSelectionRange(match.start, match.end); } catch(e){}
            scrollEditorToMatch(ed, match.start);
          }
          updateCounter();
          return;
        }

        matches.forEach(function(m){ m.classList.remove(MARK_CUR); });
        if(matchIdx >= 0 && matchIdx < matches.length){
          matches[matchIdx].classList.add(MARK_CUR);
          matches[matchIdx].scrollIntoView({block:'nearest', behavior:'smooth'});
        }
        updateCounter();
      }

      function navigateNext(){
        if(!matches.length) return;
        matchIdx = (matchIdx + 1) % matches.length;
        highlightCurrent();
      }

      function navigatePrev(){
        if(!matches.length) return;
        matchIdx = (matchIdx - 1 + matches.length) % matches.length;
        highlightCurrent();
      }

      function updateCounter(){
        if(!matches.length){ counter.textContent = input && input.value ? 'No results' : ''; return; }
        counter.textContent = (matchIdx + 1) + ' / ' + matches.length;
      }

      function openSearch(origin){
        createBar();
        searchTarget = currentSearchTarget(origin);
        bar.style.display = 'flex';
        input.focus();
        input.select();
        if(input.value) doSearch(input.value);
      }

      function closeSearch(){
        var editorMatch = (searchMode === 'editor' && matchIdx >= 0 && matchIdx < matches.length) ? matches[matchIdx] : null;
        clearHighlights();
        if(input) input.value = '';
        if(bar) bar.style.display = 'none';
        if(counter) counter.textContent = '';
        searchTarget = null;
        if(editorMatch){
          var ed = document.querySelector('#editor');
          if(ed){
            try{ ed.focus({preventScroll:true}); } catch(e){ ed.focus(); }
            try{ ed.setSelectionRange(editorMatch.start, editorMatch.end); } catch(e){}
            scrollEditorToMatch(ed, editorMatch.start);
          }
        }
      }

      var editorForSearch = document.querySelector('#editor');
      if(editorForSearch){
        editorForSearch.addEventListener('input', function(){
          if(bar && bar.style.display !== 'none' && searchTarget === 'editor' && input && input.value){
            doSearch(input.value);
          }
        });
      }

      // Expose reset so Open/Refresh/Reload handlers can invalidate stale matches
      window._mdlookResetSearch = function(){
        closeSearch();
      };

      // Intercept Ctrl+F — suppress native WebView2 find bar, show ours instead
      // F3 / Shift+F3 — navigate matches or open search
      document.addEventListener('keydown', function(e){
        if((e.ctrlKey || e.metaKey) && (e.key === 'f' || e.code === 'KeyF')){
          e.preventDefault();
          e.stopPropagation();
          openSearch(e.target);
          return;
        }
        if(e.key === 'F3'){
          e.preventDefault();
          e.stopPropagation();
          if(bar && bar.style.display !== 'none' && matches.length > 0){
            if(e.shiftKey) navigatePrev(); else navigateNext();
          } else {
            openSearch(e.target);
          }
        }
      }, true);
    })();
  }

  if(window.pywebview && window.pywebview.api) initBridge();
  else window.addEventListener('pywebviewready', initBridge);
})();
</script>
"""

LOADING_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>MDLook</title>
<style>
body{margin:0;display:flex;align-items:center;justify-content:center;height:100vh;
background:#f5f0eb;font-family:Inter,system-ui,sans-serif;color:#494849}
.loader{text-align:center}
.spinner{width:32px;height:32px;border:3px solid #e0d6cc;border-top:3px solid #c57147;
border-radius:50%;animation:spin .7s linear infinite;margin:0 auto 16px}
@keyframes spin{to{transform:rotate(360deg)}}
p{font-size:.9rem;opacity:.6}
</style></head><body><div class="loader"><div class="spinner"></div><p>Loading…</p></div></body></html>"""


def build_html(md_content, md_name, md_folder):
    """Read the HTML template, inject content, write to temp file, return path."""
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    json_content = json.dumps(md_content)
    # Escape </script> tags inside JSON content
    json_content = json_content.replace('</script>', '<\\/script>')
    json_content = json_content.replace('</Script>', '<\\/Script>')
    json_content = json_content.replace('</SCRIPT>', '<\\/SCRIPT>')

    html = html.replace('MDCONTENT_PLACEHOLDER', json_content)
    html = html.replace('MDNAME_PLACEHOLDER', json.dumps(md_name))
    html = html.replace('MDFOLDER_PLACEHOLDER', json.dumps(md_folder))

    # Switch default mode based on content
    if md_content:
        html = html.replace("setMode('edit');\n}\n</script>\n</body>",
                             "setMode('read');\n}\n</script>\n</body>")

    # Inject bridge JS before </body>
    last_body = html.rfind('</body>')
    if last_body != -1:
        html = html[:last_body] + '\n' + BRIDGE_JS + '\n</body>' + html[last_body + 7:]

    fd, path = tempfile.mkstemp(suffix='.html', prefix='mdlook_')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(html)
    _temp_html.append(path)
    return path


_tray = None
_quitting = False

_windows: list[dict] = []  # {'window': Window, 'api': Api, 'temp_html': str}
_windows_lock = threading.Lock()


def _get_exe_path():
    """Get the path to the current executable."""
    if getattr(sys, '_MEIPASS', None):
        return sys.executable
    return os.path.abspath(sys.argv[0])


def _is_startup_enabled():
    """Check if MDLook is in Windows startup."""
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r'Software\Microsoft\Windows\CurrentVersion\Run',
                             0, winreg.KEY_READ)
        winreg.QueryValueEx(key, 'MDLook')
        winreg.CloseKey(key)
        return True
    except (FileNotFoundError, OSError):
        return False


def _toggle_startup():
    """Add or remove MDLook from Windows startup."""
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r'Software\Microsoft\Windows\CurrentVersion\Run',
                         0, winreg.KEY_SET_VALUE)
    if _is_startup_enabled():
        try:
            winreg.DeleteValue(key, 'MDLook')
        except OSError:
            pass
    else:
        exe = _get_exe_path()
        winreg.SetValueEx(key, 'MDLook', 0, winreg.REG_SZ, '"' + exe + '" --silent')
    winreg.CloseKey(key)


def _is_file_assoc_enabled():
    """Check if .md files are associated with MDLook."""
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r'Software\Classes\.md',
                             0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, '')
        winreg.CloseKey(key)
        return val == 'MDLook.md'
    except (FileNotFoundError, OSError):
        return False


def _toggle_file_assoc():
    """Associate or disassociate .md files with MDLook."""
    import winreg
    exe = _get_exe_path()

    if _is_file_assoc_enabled():
        # Remove association
        for subkey in (
            r'Software\Classes\MDLook.md\shell\open\command',
            r'Software\Classes\MDLook.md\shell\open',
            r'Software\Classes\MDLook.md\shell',
            r'Software\Classes\MDLook.md\DefaultIcon',
            r'Software\Classes\MDLook.md',
        ):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
            except OSError:
                pass
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r'Software\Classes\.md',
                                 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, '')
            winreg.CloseKey(key)
        except OSError:
            pass
    else:
        # Create association
        doc_ico = os.path.join(BASE_DIR, 'MDLook-doc.ico')

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\.md')
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, 'MDLook.md')
        winreg.CloseKey(key)

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\MDLook.md')
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, 'MDLook Markdown Document')
        winreg.CloseKey(key)

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\MDLook.md\DefaultIcon')
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, doc_ico)
        winreg.CloseKey(key)

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\MDLook.md\shell\open\command')
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, '"' + exe + '" "%1"')
        winreg.CloseKey(key)

    # Notify shell of association change
    try:
        from ctypes import windll
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0
        windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass


def _setup_tray():
    """Create system tray icon with menu."""
    global _tray
    import pystray
    from PIL import Image

    icon_img = Image.open(ICON_PATH)

    def on_show(icon, item):
        with _windows_lock:
            has_windows = bool(_windows)
        if has_windows:
            _force_foreground()
        else:
            window = _create_window(None)
            _activate_window_async(window)

    def on_open_file(icon, item):
        path = _powershell_open_dialog()
        if path:
            window = _create_window(path)
            _activate_window_async(window)

    def on_startup(icon, item):
        _toggle_startup()

    def on_file_assoc(icon, item):
        _toggle_file_assoc()

    def on_quit(icon, item):
        global _quitting
        _quitting = True
        with _windows_lock:
            windows_to_destroy = [e['window'] for e in _windows]
        for w in windows_to_destroy:
            try:
                w.destroy()
            except Exception:
                pass
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem('Show MDLook', on_show, default=True),
        pystray.MenuItem('Open File…', on_open_file),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Start with Windows', on_startup,
                         checked=lambda item: _is_startup_enabled()),
        pystray.MenuItem('Associate .md files', on_file_assoc,
                         checked=lambda item: _is_file_assoc_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Quit', on_quit),
    )

    _tray = pystray.Icon('MDLook', icon_img, 'MDLook', menu)
    _tray.run_detached()


def _make_on_closing(window):
    """Factory: return a per-window closing handler."""
    def _on_closing():
        if _quitting:
            return True
        with _windows_lock:
            if len(_windows) > 1:
                # Non-last window: remove from registry, allow destruction
                for i, entry in enumerate(_windows):
                    if entry['window'] is window:
                        _windows.pop(i)
                        # Clean up temp HTML for this window
                        tmp = entry.get('temp_html')
                        if tmp and os.path.isfile(tmp):
                            try:
                                os.unlink(tmp)
                            except Exception:
                                pass
                        break
                return True
            else:
                # Last window: hide to tray
                window.hide()
                return False
    return _on_closing


def _create_window(filepath=None, target=None):
    """Create a new MDLook window with its own Api instance.

    If filepath is given, load that file; otherwise load example.md.
    Can be called from any thread after webview.start() is running —
    pywebview queues the create_window call to the GUI thread internally.
    """
    api_inst = Api()

    md_content = ''
    md_name = ''
    md_folder = ''

    if filepath and os.path.isfile(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                md_content = f.read()
            md_name = os.path.basename(filepath)
            md_folder = os.path.dirname(os.path.abspath(filepath))
            api_inst._current_path = filepath
            api_inst._file_mtime = os.path.getmtime(filepath)
        except Exception:
            pass
    else:
        _example = os.path.join(
            getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
            'example.md'
        )
        if os.path.isfile(_example):
            try:
                with open(_example, 'r', encoding='utf-8') as f:
                    md_content = f.read()
                md_name = 'example.md'
            except Exception:
                pass

    html_path = build_html(md_content, md_name, md_folder)
    file_url = 'file:///' + html_path.replace('\\', '/')
    title = _format_window_title(filename=md_name, folder=md_folder)

    window = webview.create_window(
        title,
        url=file_url,
        js_api=api_inst,
        width=960,
        height=720,
        min_size=(600, 400),
        text_select=True,
    )
    api_inst._window = window

    entry = {'window': window, 'api': api_inst, 'temp_html': html_path}
    with _windows_lock:
        _windows.append(entry)

    window.events.closing += _make_on_closing(window)
    _goto_target_async(window, target)

    return window


def on_loaded():
    """Called after the window is shown — build the heavy template and navigate."""

    def _load():
        with _windows_lock:
            first_entry = _windows[0] if _windows else None
        if first_entry is None:
            return
        w = first_entry['window']
        api_inst = first_entry['api']

        if SILENT_MODE:
            import time
            time.sleep(0.3)
            try:
                w.hide()
            except Exception:
                pass

        md_content = ''
        md_name = ''
        md_folder = ''

        arg = _get_file_arg()
        target = _get_goto_arg()
        if arg and os.path.isfile(arg):
            with open(arg, 'r', encoding='utf-8') as f:
                md_content = f.read()
            md_name = os.path.basename(arg)
            md_folder = os.path.dirname(os.path.abspath(arg))
            api_inst._current_path = arg
            api_inst._file_mtime = os.path.getmtime(arg)
        else:
            # Load example.md
            _example = os.path.join(
                getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
                'example.md'
            )
            if os.path.isfile(_example):
                with open(_example, 'r', encoding='utf-8') as f:
                    md_content = f.read()
                md_name = 'example.md'

        html_path = build_html(md_content, md_name, md_folder)
        # Update temp_html reference for this window's entry
        with _windows_lock:
            if _windows and _windows[0]['window'] is w:
                _windows[0]['temp_html'] = html_path
        file_url = 'file:///' + html_path.replace('\\', '/')

        title = _format_window_title(filename=md_name, folder=md_folder)

        w.load_url(file_url)
        w.set_title(title)
        _goto_target_async(w, target)

    t = threading.Thread(target=_load, daemon=True)
    t.start()


def main():
    # ── Main ──
    if _signal_existing_instance():
        sys.exit(0)

    _start_ipc_listener()
    _setup_tray()

    api = Api()

    _loading_fd, _loading_path = tempfile.mkstemp(suffix='.html', prefix='mdlook_load_')
    with os.fdopen(_loading_fd, 'w', encoding='utf-8') as f:
        f.write(LOADING_HTML)
    loading_url = 'file:///' + _loading_path.replace('\\', '/')

    window = webview.create_window(
        'MDLook',
        url=loading_url,
        js_api=api,
        width=960,
        height=720,
        min_size=(600, 400),
        text_select=True,
        minimized=SILENT_MODE,
        hidden=SILENT_MODE,
    )
    api._window = window

    entry = {'window': window, 'api': api, 'temp_html': _loading_path}
    with _windows_lock:
        _windows.append(entry)

    window.events.closing += _make_on_closing(window)

    webview.start(func=on_loaded, gui='edgechromium', debug=False)

    _tray.stop()
    try:
        os.unlink(_loading_path)
    except Exception:
        pass


if __name__ == '__main__':
    main()
