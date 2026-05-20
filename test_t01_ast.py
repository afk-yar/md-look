"""AST-based tests for T-01 requirements (no runtime deps needed)."""
import ast
import sys

src = open('E:/_Проекты/pet/md-look/app.py', 'r', encoding='utf-8').read()
tree = ast.parse(src)

results = []

# ── Test 1: Syntax ──
try:
    ast.parse(src)
    results.append(('PASS', 'Syntax: app.py parses without errors'))
except SyntaxError as e:
    results.append(('FAIL', f'Syntax error: {e}'))

# ── Test 2: _windows and _windows_lock module-level ──
_windows_found = False
_windows_lock_found = False
for node in ast.walk(tree):
    if isinstance(node, ast.Module):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AnnAssign):
                if isinstance(child.target, ast.Name) and child.target.id == '_windows':
                    _windows_found = True
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Name) and t.id == '_windows_lock':
                        _windows_lock_found = True
results.append(('PASS' if _windows_found else 'FAIL', '_windows: list[dict] module-level declaration'))
results.append(('PASS' if _windows_lock_found else 'FAIL', '_windows_lock = threading.Lock() module-level'))

# ── Test 3: Api.__init__ has self._window = None ──
api_window_init = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == '__init__':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Attribute) and t.attr == '_window':
                                if isinstance(stmt.value, ast.Constant) and stmt.value.value is None:
                                    api_window_init = True
results.append(('PASS' if api_window_init else 'FAIL', 'Api.__init__: self._window = None'))

# ── Test 4: Api._get_window returns self._window ──
get_window_ok = False
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == 'Api':
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == '_get_window':
                for stmt in item.body:
                    if isinstance(stmt, ast.Return):
                        if isinstance(stmt.value, ast.Attribute) and stmt.value.attr == '_window':
                            get_window_ok = True
results.append(('PASS' if get_window_ok else 'FAIL', 'Api._get_window: returns self._window'))

# ── Test 5: _create_window exists and correct structure ──
create_window_found = False
create_window_api_window = False
create_window_append = False
create_window_lock = False
create_window_webview = False
create_window_closing_factory = False
create_order = []
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_create_window':
        create_window_found = True
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Attribute):
                    if (child.func.attr == 'create_window'
                            and isinstance(child.func.value, ast.Name)
                            and child.func.value.id == 'webview'):
                        create_window_webview = True
                    if (child.func.attr == 'append'
                            and isinstance(child.func.value, ast.Name)
                            and child.func.value.id == '_windows'):
                        create_window_append = True
                        create_order.append(('append', child.lineno))
                if isinstance(child.func, ast.Name) and child.func.id == '_make_on_closing':
                    create_window_closing_factory = True
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Attribute) and t.attr == '_window':
                        create_window_api_window = True
                        create_order.append(('_window', child.lineno))
            if isinstance(child, ast.With):
                for item in child.items:
                    if (isinstance(item.context_expr, ast.Name)
                            and item.context_expr.id == '_windows_lock'):
                        create_window_lock = True

results.append(('PASS' if create_window_found else 'FAIL', '_create_window function exists'))
results.append(('PASS' if create_window_webview else 'FAIL', '_create_window: calls webview.create_window()'))
results.append(('PASS' if create_window_api_window else 'FAIL', '_create_window: sets api_inst._window = window'))
results.append(('PASS' if create_window_append else 'FAIL', '_create_window: appends to _windows'))
results.append(('PASS' if create_window_lock else 'FAIL', '_create_window: uses _windows_lock'))
results.append(('PASS' if create_window_closing_factory else 'FAIL', '_create_window: uses _make_on_closing factory'))

create_order.sort(key=lambda x: x[1])
order_labels = [x[0] for x in create_order]
try:
    ok = order_labels.index('_window') < order_labels.index('append')
    results.append(('PASS' if ok else 'FAIL', '_create_window: _window set before _windows.append'))
except ValueError:
    results.append(('FAIL', '_create_window: _window or append not found'))

# ── Test 6 (T-02): _load_file_in_window removed ──
load_file_removed = True
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_load_file_in_window':
        load_file_removed = False
results.append(('PASS' if load_file_removed else 'FAIL',
                'T-02: _load_file_in_window removed'))

# ── Test 7 (T-02): IPC OPEN uses _create_window, not _load_file_in_window ──
ipc_open_uses_create_window = False
ipc_open_no_load_file = True
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_start_ipc_listener':
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name) and func.id == '_create_window':
                    ipc_open_uses_create_window = True
                if isinstance(func, ast.Name) and func.id == '_load_file_in_window':
                    ipc_open_no_load_file = False
results.append(('PASS' if ipc_open_uses_create_window else 'FAIL',
                'T-02: IPC OPEN calls _create_window()'))
results.append(('PASS' if ipc_open_no_load_file else 'FAIL',
                'T-02: IPC OPEN does NOT call _load_file_in_window'))

# ── Test 8 (T-02): _force_foreground has no webview.windows[0] hardcode ──
force_fg_no_hardcode = True
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_force_foreground':
        for child in ast.walk(node):
            if isinstance(child, ast.Subscript):
                val = child.value
                if (isinstance(val, ast.Attribute) and val.attr == 'windows'
                        and isinstance(val.value, ast.Name) and val.value.id == 'webview'):
                    force_fg_no_hardcode = False
results.append(('PASS' if force_fg_no_hardcode else 'FAIL',
                'T-02: _force_foreground has no webview.windows[0] hardcode'))

# ── Test 9 (T-02): _force_foreground iterates _windows registry ──
force_fg_uses_registry = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_force_foreground':
        for child in ast.walk(node):
            if isinstance(child, ast.With):
                for item in child.items:
                    if (isinstance(item.context_expr, ast.Name)
                            and item.context_expr.id == '_windows_lock'):
                        force_fg_uses_registry = True
results.append(('PASS' if force_fg_uses_registry else 'FAIL',
                'T-02: _force_foreground uses _windows_lock'))

# ── Test 10 (T-02): on_quit in _setup_tray has no webview.windows[0] ──
on_quit_no_hardcode = True
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_setup_tray':
        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef) and child.name == 'on_quit':
                for subchild in ast.walk(child):
                    if isinstance(subchild, ast.Subscript):
                        val = subchild.value
                        if (isinstance(val, ast.Attribute) and val.attr == 'windows'
                                and isinstance(val.value, ast.Name) and val.value.id == 'webview'):
                            on_quit_no_hardcode = False
results.append(('PASS' if on_quit_no_hardcode else 'FAIL',
                'T-02: on_quit has no webview.windows[0] hardcode'))

# ── Test 12: if __name__ == '__main__' guard ──
guard_found = False
for node in ast.walk(tree):
    if isinstance(node, ast.If):
        test = node.test
        if (isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name) and test.left.id == '__name__'
                and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == '__main__'):
            guard_found = True
results.append(('PASS' if guard_found else 'FAIL', 'main() guard: if __name__ == "__main__"'))

# ── Test 13: main() order api._window → _windows.append → webview.start ──
main_order = []
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'main':
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Attribute) and t.attr == '_window':
                        main_order.append(('_window', child.lineno))
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Attribute):
                    if (func.attr == 'append'
                            and isinstance(func.value, ast.Name)
                            and func.value.id == '_windows'):
                        main_order.append(('append', child.lineno))
                    if (func.attr == 'start'
                            and isinstance(func.value, ast.Name)
                            and func.value.id == 'webview'):
                        main_order.append(('webview.start', child.lineno))

main_order.sort(key=lambda x: x[1])
labels = [x[0] for x in main_order]
try:
    ok = labels.index('_window') < labels.index('append') < labels.index('webview.start')
    results.append(('PASS' if ok else 'FAIL',
                    'main(): order api._window → _windows.append → webview.start'))
except ValueError:
    results.append(('FAIL', 'main(): missing one of _window/_windows.append/webview.start'))

# ── Test 14: on_loaded uses _windows registry, no bare global api ──
on_loaded_no_global_api = True
on_loaded_uses_registry = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'on_loaded':
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id == 'api':
                # This is a bare Name 'api' usage — in on_loaded context only globals count
                # Check it is not inside an attribute (like api_inst.xxx)
                on_loaded_no_global_api = False
            if isinstance(child, ast.Subscript):
                val = child.value
                if isinstance(val, ast.Name) and val.id == '_windows':
                    on_loaded_uses_registry = True
results.append(('PASS' if on_loaded_no_global_api else 'FAIL',
                'on_loaded: no bare global api reference'))
results.append(('PASS' if on_loaded_uses_registry else 'FAIL',
                'on_loaded: uses _windows registry'))

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
