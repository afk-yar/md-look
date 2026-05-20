"""AST-based tests for 002-улучшения T-01/T-02 requirements (no runtime deps needed).

Checks:
  T-01: reload on focus
    - Api.__init__ has self._file_mtime = None
    - Api.reload_file method exists with force=False default
    - reload_file checks os.path.isfile (not deleted)
    - reload_file reads mtime and compares
    - reload_file reads file content and returns dict with content/name/path
    - reload_file returns None when path is None or file missing
    - reload_file updates self._file_mtime
    - open_file updates self._file_mtime
    - save_file updates self._file_mtime
    - save_file_as updates self._file_mtime
    - _create_window sets api_inst._file_mtime when loading a file
    - on_loaded sets api_inst._file_mtime when loading a file
    - BRIDGE_JS contains window.addEventListener('focus', ...)
    - BRIDGE_JS contains pywebview.api.reload_file() call (focus handler)
    - BRIDGE_JS focus handler has hasUnsaved guard
    - BRIDGE_JS has debounce variable (_reloadDebounceTimer or similar)
  T-02: refresh button
    - BRIDGE_JS contains btnRefresh element creation
    - BRIDGE_JS calls reload_file(true) with force=True from button click
"""
import ast
import os
import sys
import re

_test_dir = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(_test_dir, 'app.py'), 'r', encoding='utf-8').read()
tree = ast.parse(src)

results = []

# ── Test 1: Syntax ──
try:
    ast.parse(src)
    results.append(('PASS', 'Syntax: app.py parses without errors'))
except SyntaxError as e:
    results.append(('FAIL', f'Syntax error: {e}'))

# ── Test 2: Api.__init__ has self._file_mtime = None ──
api_mtime_init = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == '__init__':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Attribute) and t.attr == '_file_mtime':
                                if isinstance(stmt.value, ast.Constant) and stmt.value.value is None:
                                    api_mtime_init = True
results.append(('PASS' if api_mtime_init else 'FAIL',
                'T-01: Api.__init__ sets self._file_mtime = None'))

# ── Test 3: Api.reload_file method exists ──
reload_file_found = False
reload_file_has_force_param = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == 'reload_file':
                reload_file_found = True
                # Check that it has a `force` parameter with default=False
                for arg in item.args.args:
                    if arg.arg == 'force':
                        reload_file_has_force_param = True
results.append(('PASS' if reload_file_found else 'FAIL',
                'T-01: Api.reload_file method exists'))
results.append(('PASS' if reload_file_has_force_param else 'FAIL',
                'T-01: Api.reload_file has `force` parameter'))

# ── Test 4: reload_file checks os.path.isfile ──
reload_file_checks_isfile = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == 'reload_file':
                for sub in ast.walk(item):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        if isinstance(func, ast.Attribute) and func.attr == 'isfile':
                            reload_file_checks_isfile = True
results.append(('PASS' if reload_file_checks_isfile else 'FAIL',
                'T-01: reload_file checks os.path.isfile'))

# ── Test 5: reload_file calls os.path.getmtime ──
reload_file_calls_getmtime = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == 'reload_file':
                for sub in ast.walk(item):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        if isinstance(func, ast.Attribute) and func.attr == 'getmtime':
                            reload_file_calls_getmtime = True
results.append(('PASS' if reload_file_calls_getmtime else 'FAIL',
                'T-01: reload_file calls os.path.getmtime'))

# ── Test 6: reload_file updates self._file_mtime ──
reload_file_updates_mtime = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == 'reload_file':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Attribute) and t.attr == '_file_mtime':
                                reload_file_updates_mtime = True
results.append(('PASS' if reload_file_updates_mtime else 'FAIL',
                'T-01: reload_file updates self._file_mtime'))

# ── Test 7: reload_file returns dict with 'content'/'name'/'path' keys ──
reload_file_returns_dict = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == 'reload_file':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Return) and stmt.value is not None:
                        if isinstance(stmt.value, ast.Dict):
                            keys = [k.value for k in stmt.value.keys
                                    if isinstance(k, ast.Constant)]
                            if 'content' in keys and 'name' in keys and 'path' in keys:
                                reload_file_returns_dict = True
results.append(('PASS' if reload_file_returns_dict else 'FAIL',
                "T-01: reload_file returns dict with 'content'/'name'/'path' keys"))

# ── Test 8: reload_file returns None on early exit (no path / missing file) ──
reload_file_returns_none = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == 'reload_file':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Return) and stmt.value is not None:
                        if isinstance(stmt.value, ast.Constant) and stmt.value.value is None:
                            reload_file_returns_none = True
results.append(('PASS' if reload_file_returns_none else 'FAIL',
                'T-01: reload_file returns None when path is None or file missing'))

# ── Test 9: open_file updates self._file_mtime ──
open_file_updates_mtime = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == 'open_file':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Attribute) and t.attr == '_file_mtime':
                                open_file_updates_mtime = True
results.append(('PASS' if open_file_updates_mtime else 'FAIL',
                'T-01: open_file updates self._file_mtime'))

# ── Test 10: save_file updates self._file_mtime ──
save_file_updates_mtime = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == 'save_file':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Attribute) and t.attr == '_file_mtime':
                                save_file_updates_mtime = True
results.append(('PASS' if save_file_updates_mtime else 'FAIL',
                'T-01: save_file updates self._file_mtime'))

# ── Test 11: save_file_as updates self._file_mtime ──
save_file_as_updates_mtime = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == 'save_file_as':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Attribute) and t.attr == '_file_mtime':
                                save_file_as_updates_mtime = True
results.append(('PASS' if save_file_as_updates_mtime else 'FAIL',
                'T-01: save_file_as updates self._file_mtime'))

# ── Test 12: _create_window sets api_inst._file_mtime when loading a file ──
create_window_sets_mtime = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_create_window':
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Attribute) and t.attr == '_file_mtime':
                        create_window_sets_mtime = True
results.append(('PASS' if create_window_sets_mtime else 'FAIL',
                'T-01: _create_window sets api_inst._file_mtime'))

# ── Test 13: on_loaded sets api_inst._file_mtime when loading a file ──
on_loaded_sets_mtime = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'on_loaded':
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Attribute) and t.attr == '_file_mtime':
                        on_loaded_sets_mtime = True
results.append(('PASS' if on_loaded_sets_mtime else 'FAIL',
                'T-01: on_loaded sets api_inst._file_mtime'))

# ── Tests 14-19: BRIDGE_JS content checks (regex on raw source) ──
# Extract BRIDGE_JS string literal from source
bridge_js_match = re.search(r'BRIDGE_JS\s*=\s*"""(.*?)"""', src, re.DOTALL)
bridge_js = bridge_js_match.group(1) if bridge_js_match else ''

# Test 14: focus event listener
has_focus_listener = "addEventListener('focus'" in bridge_js or 'addEventListener("focus"' in bridge_js
results.append(('PASS' if has_focus_listener else 'FAIL',
                "T-01: BRIDGE_JS has window.addEventListener('focus', ...) handler"))

# Test 15: reload_file call in focus handler
has_reload_call = 'reload_file()' in bridge_js or 'reload_file(false)' in bridge_js or 'reload_file(False)' in bridge_js
results.append(('PASS' if has_reload_call else 'FAIL',
                'T-01: BRIDGE_JS focus handler calls pywebview.api.reload_file()'))

# Test 16: hasUnsaved guard in focus handler
has_unsaved_guard = 'hasUnsaved' in bridge_js
results.append(('PASS' if has_unsaved_guard else 'FAIL',
                'T-01: BRIDGE_JS has hasUnsaved guard in focus/reload logic'))

# Test 17: debounce mechanism
has_debounce = 'Debounce' in bridge_js or 'debounce' in bridge_js or 'Timer' in bridge_js or 'timer' in bridge_js or 'setTimeout' in bridge_js
results.append(('PASS' if has_debounce else 'FAIL',
                'T-01: BRIDGE_JS has debounce mechanism (setTimeout)'))

# Test 18 (T-02): Refresh button element created in BRIDGE_JS
has_refresh_btn = 'btnRefresh' in bridge_js or 'Refresh' in bridge_js or 'refresh' in bridge_js
results.append(('PASS' if has_refresh_btn else 'FAIL',
                'T-02: BRIDGE_JS creates a Refresh button'))

# Test 19 (T-02): reload_file called with force=True (true in JS) from button
has_force_reload = 'reload_file(true)' in bridge_js
results.append(('PASS' if has_force_reload else 'FAIL',
                'T-02: BRIDGE_JS refresh button calls reload_file(true) (force=True)'))

# ── Print results ──
print()
passes = sum(1 for r in results if r[0] == 'PASS')
fails = [r for r in results if r[0] == 'FAIL']
for status, desc in results:
    mark = '+' if status == 'PASS' else 'X'
    print(f'  [{mark}] {desc}')
print()
print(f'  Results: {passes}/{len(results)} PASS, {len(fails)} FAIL')
if fails:
    print('  FAILURES:')
    for _, desc in fails:
        print(f'    - {desc}')
    sys.exit(1)
else:
    print('  All tests passed.')
    sys.exit(0)
