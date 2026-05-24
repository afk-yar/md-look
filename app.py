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
                            # Run in a separate thread to avoid blocking the IPC listener
                            threading.Thread(
                                target=_force_foreground_window,
                                args=(existing['window'],),
                                daemon=True,
                            ).start()
                        else:
                            # New file — open and bring to front after load
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
            return {'content': content, 'name': os.path.basename(path), 'path': path}
        except Exception:
            return None

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
        if(window._mdlookResetSearch) window._mdlookResetSearch();
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
      if(!res) return;
      rawMd = res.content;
      fileName = res.name;
      var ed = document.querySelector('#editor');
      if(ed) ed.value = rawMd;
      hasUnsaved = false;
      document.body.classList.remove('unsaved');
      document.querySelector('#fn').textContent = res.name;
      document.title = res.name + ' \\u2014 MDLook';
      if(window._mdlookResetSearch) window._mdlookResetSearch();
      setMode('read');
    });

    // ── Reload on focus: re-read file when window regains focus ──
    var _reloadDebounceTimer = null;
    window.addEventListener('focus', function(){
      if(_reloadDebounceTimer) return;
      _reloadDebounceTimer = setTimeout(function(){ _reloadDebounceTimer = null; }, 1000);
      if(hasUnsaved) return;
      window.pywebview.api.reload_file().then(function(res){
        if(!res) return;
        rawMd = res.content;
        fileName = res.name;
        var ed = document.querySelector('#editor');
        if(ed) ed.value = rawMd;
        document.querySelector('#fn').textContent = res.name;
        document.title = res.name + ' \\u2014 MDLook';
        if(window._mdlookResetSearch) window._mdlookResetSearch();
        setMode('read');
      });
    });

    // ── In-page search (Ctrl+F) ──
    (function(){
      var bar = null, input = null, counter = null, matches = [], matchIdx = -1;
      var MARK_CLASS = 'mdlook-search-hl';
      var MARK_CUR   = 'mdlook-search-cur';

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
            '.' + MARK_CUR   + '{background:#ff9800!important;color:#fff!important;}'
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

      function getSearchRoot(){
        var reader = document.querySelector('.reader');
        return (reader && reader.offsetHeight > 0) ? reader : document.body;
      }

      function clearHighlights(){
        var marks = document.querySelectorAll('.' + MARK_CLASS);
        marks.forEach(function(m){
          var parent = m.parentNode;
          if(!parent) return;
          parent.replaceChild(document.createTextNode(m.textContent), m);
          parent.normalize();
        });
        matches = [];
        matchIdx = -1;
      }

      function doSearch(q){
        clearHighlights();
        if(!q){ counter.textContent = ''; return; }

        var root = getSearchRoot();
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

      function highlightCurrent(){
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

      function openSearch(){
        createBar();
        bar.style.display = 'flex';
        input.focus();
        input.select();
        if(input.value) doSearch(input.value);
      }

      function closeSearch(){
        clearHighlights();
        if(input) input.value = '';
        if(bar) bar.style.display = 'none';
        if(counter) counter.textContent = '';
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
          openSearch();
          return;
        }
        if(e.key === 'F3'){
          e.preventDefault();
          e.stopPropagation();
          if(bar && bar.style.display !== 'none' && matches.length > 0){
            if(e.shiftKey) navigatePrev(); else navigateNext();
          } else {
            openSearch();
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
            _create_window(None)

    def on_open_file(icon, item):
        path = _powershell_open_dialog()
        if path:
            _create_window(path)

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
    title = md_name + ' — MDLook' if md_name else 'MDLook'

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
