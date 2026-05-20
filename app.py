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
import ctypes
import ctypes.wintypes

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


# ── Single-instance IPC ──
SILENT_MODE = '--silent' in sys.argv
IPC_PORT = 52845


def _get_file_arg():
    """Get the file path from command line args (ignoring flags)."""
    for arg in sys.argv[1:]:
        if not arg.startswith('--') and arg != '':
            if os.path.isfile(arg):
                return os.path.abspath(arg)
    return None


def _signal_existing_instance():
    """Try to signal an already-running instance to show/open file. Returns True if successful."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(('127.0.0.1', IPC_PORT))
        filepath = _get_file_arg()
        if filepath:
            msg = 'OPEN:' + filepath
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
                    filepath = data[5:]
                    if os.path.isfile(filepath):
                        _create_window(filepath)
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
    for path in _temp_html:
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


class Api:
    """Python functions exposed to JavaScript via window.pywebview.api"""

    def __init__(self):
        self._current_path = None
        self._window = None

    @property
    def current_path(self):
        return self._current_path

    def _get_window(self):
        """Get the webview window reliably."""
        return self._window

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
        if(res.name) fileName = res.name;
        document.querySelector('#fn').textContent = fileName;
        document.title = fileName + ' \\u2014 MDLook';
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
        if(!res) return;
        rawMd = res.content;
        fileName = res.name;
        hasUnsaved = false;
        document.body.classList.remove('unsaved');
        document.querySelector('#fn').textContent = res.name;
        document.title = res.name + ' \\u2014 MDLook';
        var ed = document.querySelector('#editor');
        if(ed) ed.value = rawMd;
        clearAutoSave();
        setMode('read');
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
      var remove = clone.querySelectorAll('.bar, .editor-wrap, .outline-panel, .zen-exit, .modal, script, .teleprompter-bar, .auto-scroll-btn');
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
        _force_foreground()

    def on_startup(icon, item):
        _toggle_startup()

    def on_file_assoc(icon, item):
        _toggle_file_assoc()

    def on_quit(icon, item):
        global _quitting
        _quitting = True
        icon.stop()
        with _windows_lock:
            windows_to_destroy = [e['window'] for e in _windows]
        for w in windows_to_destroy:
            try:
                w.destroy()
            except Exception:
                pass

    menu = pystray.Menu(
        pystray.MenuItem('Show MDLook', on_show, default=True),
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


def _create_window(filepath=None):
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
    title = md_name + ' — MDLook' if md_name else 'MDLook'

    window = webview.create_window(
        title,
        url=file_url,
        js_api=api_inst,
        width=960,
        height=720,
        min_size=(600, 400),
    )
    api_inst._window = window

    entry = {'window': window, 'api': api_inst, 'temp_html': html_path}
    with _windows_lock:
        _windows.append(entry)

    window.events.closing += _make_on_closing(window)

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
        if arg and os.path.isfile(arg):
            with open(arg, 'r', encoding='utf-8') as f:
                md_content = f.read()
            md_name = os.path.basename(arg)
            md_folder = os.path.dirname(os.path.abspath(arg))
            api_inst._current_path = arg
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

        title = md_name + ' — ' + 'MDLook' if md_name else 'MDLook'

        w.load_url(file_url)
        w.set_title(title)

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
