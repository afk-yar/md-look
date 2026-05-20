"""AST-based tests for T-03 requirements (no runtime deps needed).

Checks:
- on_open_file exists in _setup_tray and calls _powershell_open_dialog + _create_window
- on_open_file is registered as 'Open File...' menu item
- on_show creates window when _windows is empty (_create_window(None))
- on_quit order: _quitting=True -> destroy all windows -> icon.stop()
- cleanup collects temp paths from _windows entries (entry.get('temp_html'))
"""
import ast
import os
import sys

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

# ── Test 2: on_open_file exists in _setup_tray ──
on_open_file_found = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef) and child.name == 'on_open_file':
                on_open_file_found = True
results.append(('PASS' if on_open_file_found else 'FAIL',
                'T-03: on_open_file function exists in _setup_tray'))

# ── Test 3: on_open_file calls _powershell_open_dialog ──
on_open_calls_dialog = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef) and child.name == 'on_open_file':
                for sub in ast.walk(child):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                        if sub.func.id == '_powershell_open_dialog':
                            on_open_calls_dialog = True
results.append(('PASS' if on_open_calls_dialog else 'FAIL',
                'T-03: on_open_file calls _powershell_open_dialog()'))

# ── Test 4: on_open_file calls _create_window (only if path is truthy) ──
on_open_calls_create_window = False
on_open_guarded = False  # _create_window is inside an if-path guard
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef) and child.name == 'on_open_file':
                # Walk body looking for: if path: _create_window(path)
                for stmt in ast.walk(child):
                    if isinstance(stmt, ast.If):
                        if isinstance(stmt.test, ast.Name) and stmt.test.id == 'path':
                            for s in ast.walk(stmt):
                                if isinstance(s, ast.Call) and isinstance(s.func, ast.Name):
                                    if s.func.id == '_create_window':
                                        on_open_calls_create_window = True
                                        on_open_guarded = True
results.append(('PASS' if on_open_calls_create_window else 'FAIL',
                'T-03: on_open_file calls _create_window()'))
results.append(('PASS' if on_open_guarded else 'FAIL',
                'T-03: on_open_file calls _create_window only when path is truthy'))

# ── Test 5: 'Open File...' MenuItem uses on_open_file as handler ──
menu_has_open_file = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Name) and t.id == 'menu':
                        # Walk menu call args looking for MenuItem('Open File...', on_open_file)
                        for sub in ast.walk(child.value):
                            if isinstance(sub, ast.Call):
                                func = sub.func
                                if (isinstance(func, ast.Attribute)
                                        and func.attr == 'MenuItem'):
                                    args = sub.args
                                    if len(args) >= 2:
                                        label_node = args[0]
                                        handler_node = args[1]
                                        if (isinstance(label_node, ast.Constant)
                                                and 'Open File' in str(label_node.value)
                                                and isinstance(handler_node, ast.Name)
                                                and handler_node.id == 'on_open_file'):
                                            menu_has_open_file = True
results.append(('PASS' if menu_has_open_file else 'FAIL',
                "T-03: tray menu has 'Open File...' item linked to on_open_file"))

# ── Test 6: on_show: if no windows -> calls _create_window(None) ──
on_show_creates_when_empty = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef) and child.name == 'on_show':
                # Look for the if/else: if has_windows -> _force_foreground
                #                       else -> _create_window(None)
                for stmt in ast.walk(child):
                    if isinstance(stmt, ast.If):
                        if isinstance(stmt.test, ast.Name) and stmt.test.id == 'has_windows':
                            # Check else branch for _create_window(None)
                            for else_stmt in stmt.orelse:
                                for s in ast.walk(else_stmt):
                                    if isinstance(s, ast.Call) and isinstance(s.func, ast.Name):
                                        if s.func.id == '_create_window':
                                            # Verify arg is None
                                            if (len(s.args) == 1
                                                    and isinstance(s.args[0], ast.Constant)
                                                    and s.args[0].value is None):
                                                on_show_creates_when_empty = True
results.append(('PASS' if on_show_creates_when_empty else 'FAIL',
                'T-03: on_show calls _create_window(None) when _windows is empty'))

# ── Test 7: on_show checks _windows with lock before deciding ──
on_show_uses_lock = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef) and child.name == 'on_show':
                for sub in ast.walk(child):
                    if isinstance(sub, ast.With):
                        for item in sub.items:
                            if (isinstance(item.context_expr, ast.Name)
                                    and item.context_expr.id == '_windows_lock'):
                                on_show_uses_lock = True
results.append(('PASS' if on_show_uses_lock else 'FAIL',
                'T-03: on_show reads _windows under _windows_lock'))

# ── Test 8: on_quit order: _quitting=True FIRST (line < loop) ──
on_quit_quitting_first = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef) and child.name == 'on_quit':
                quitting_assign_line = None
                destroy_loop_line = None
                icon_stop_line = None

                for stmt in child.body:
                    # _quitting = True assignment
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Name) and t.id == '_quitting':
                                if (isinstance(stmt.value, ast.Constant)
                                        and stmt.value.value is True):
                                    quitting_assign_line = stmt.lineno
                    # with _windows_lock: windows_to_destroy = ...
                    if isinstance(stmt, ast.With):
                        destroy_loop_line = stmt.lineno
                    # for w in windows_to_destroy: w.destroy()
                    if isinstance(stmt, ast.For):
                        destroy_loop_line = stmt.lineno
                    # icon.stop()
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                        f = stmt.value.func
                        if (isinstance(f, ast.Attribute) and f.attr == 'stop'
                                and isinstance(f.value, ast.Name) and f.value.id == 'icon'):
                            icon_stop_line = stmt.lineno

                if (quitting_assign_line is not None
                        and destroy_loop_line is not None
                        and icon_stop_line is not None):
                    if quitting_assign_line < destroy_loop_line < icon_stop_line:
                        on_quit_quitting_first = True

results.append(('PASS' if on_quit_quitting_first else 'FAIL',
                'T-03: on_quit order: _quitting=True → destroy windows → icon.stop()'))

# ── Test 9: on_quit calls w.destroy() for each window ──
on_quit_calls_destroy = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef) and child.name == 'on_quit':
                for sub in ast.walk(child):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                        if sub.func.attr == 'destroy':
                            on_quit_calls_destroy = True
results.append(('PASS' if on_quit_calls_destroy else 'FAIL',
                'T-03: on_quit calls .destroy() on windows'))

# ── Test 10: on_quit calls icon.stop() ──
on_quit_calls_icon_stop = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef) and child.name == 'on_quit':
                for sub in ast.walk(child):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                        if (sub.func.attr == 'stop'
                                and isinstance(sub.func.value, ast.Name)
                                and sub.func.value.id == 'icon'):
                            on_quit_calls_icon_stop = True
results.append(('PASS' if on_quit_calls_icon_stop else 'FAIL',
                'T-03: on_quit calls icon.stop()'))

# ── Test 11: cleanup uses _windows_lock to iterate entries ──
cleanup_uses_lock = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'cleanup':
        for sub in ast.walk(node):
            if isinstance(sub, ast.With):
                for item in sub.items:
                    if (isinstance(item.context_expr, ast.Name)
                            and item.context_expr.id == '_windows_lock'):
                        cleanup_uses_lock = True
results.append(('PASS' if cleanup_uses_lock else 'FAIL',
                'T-03: cleanup() uses _windows_lock when reading _windows'))

# ── Test 12: cleanup collects temp_html from each _windows entry ──
cleanup_collects_entry_temp = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'cleanup':
        # Look for entry.get('temp_html') or similar attribute access
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Attribute) and func.attr == 'get':
                    # Check argument is 'temp_html'
                    if (len(sub.args) >= 1
                            and isinstance(sub.args[0], ast.Constant)
                            and sub.args[0].value == 'temp_html'):
                        cleanup_collects_entry_temp = True
results.append(('PASS' if cleanup_collects_entry_temp else 'FAIL',
                "T-03: cleanup() reads entry.get('temp_html') from _windows entries"))

# ── Test 13: cleanup also starts with _temp_html global list ──
cleanup_uses_global_temp = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'cleanup':
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id == '_temp_html':
                cleanup_uses_global_temp = True
results.append(('PASS' if cleanup_uses_global_temp else 'FAIL',
                'T-03: cleanup() also includes _temp_html global list'))

# ── Test 14: cleanup adds paths to a set (deduplication) ──
cleanup_uses_set = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'cleanup':
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                # set(_temp_html) at top
                if isinstance(func, ast.Name) and func.id == 'set':
                    cleanup_uses_set = True
                # paths.add(tmp)
                if isinstance(func, ast.Attribute) and func.attr == 'add':
                    cleanup_uses_set = True
results.append(('PASS' if cleanup_uses_set else 'FAIL',
                'T-03: cleanup() uses a set for deduplication of temp paths'))

# ── Test 15: cleanup unlinks only existing files (os.path.isfile guard) ──
cleanup_checks_isfile = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'cleanup':
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Attribute) and func.attr == 'isfile':
                    cleanup_checks_isfile = True
results.append(('PASS' if cleanup_checks_isfile else 'FAIL',
                'T-03: cleanup() checks os.path.isfile before unlinking'))

# ── Test 16: 'Show MDLook' menu item is the default item ──
show_mdlook_is_default = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Name) and t.id == 'menu':
                        for sub in ast.walk(child.value):
                            if isinstance(sub, ast.Call):
                                func = sub.func
                                if (isinstance(func, ast.Attribute)
                                        and func.attr == 'MenuItem'):
                                    args = sub.args
                                    keywords = sub.keywords
                                    if args and isinstance(args[0], ast.Constant):
                                        if 'Show MDLook' in str(args[0].value):
                                            # Check default=True keyword
                                            for kw in keywords:
                                                if (kw.arg == 'default'
                                                        and isinstance(kw.value, ast.Constant)
                                                        and kw.value.value is True):
                                                    show_mdlook_is_default = True
results.append(('PASS' if show_mdlook_is_default else 'FAIL',
                "T-03: 'Show MDLook' menu item has default=True"))

# ── Test 17: 'Quit' MenuItem uses on_quit handler ──
menu_has_quit = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Name) and t.id == 'menu':
                        for sub in ast.walk(child.value):
                            if isinstance(sub, ast.Call):
                                func = sub.func
                                if (isinstance(func, ast.Attribute)
                                        and func.attr == 'MenuItem'):
                                    args = sub.args
                                    if len(args) >= 2:
                                        if (isinstance(args[0], ast.Constant)
                                                and args[0].value == 'Quit'
                                                and isinstance(args[1], ast.Name)
                                                and args[1].id == 'on_quit'):
                                            menu_has_quit = True
results.append(('PASS' if menu_has_quit else 'FAIL',
                "T-03: tray menu has 'Quit' item linked to on_quit"))

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
