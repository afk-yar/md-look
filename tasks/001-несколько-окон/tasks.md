# Задачи: Поддержка нескольких окон в MDLook

**Проект:** MDLook (форк/патч)
**Дата:** 2026-05-20
**Источники:** [Реверс app.py](../../app.py) | [pywebview multi-window](https://pywebview.flowrl.com/3.7/guide/usage) | [js_api per-window](https://pywebview.flowrl.com/guide/interdomain.html)

**Контекст:** MDLook — pywebview + WebView2, single-instance через TCP-сокет на порту 52845. Второй запуск отправляет `OPEN:path` / `SHOW` первому экземпляру и завершается. Файл загружается в единственное окно через JS-инъекцию. Цель — открытие нескольких файлов в отдельных окнах одного процесса.

---

## Прогресс

### Блок 1: Multi-window ядро
- [x] **T-01** Фабрика окон: вынести создание окна в переиспользуемую функцию `[M]`
- [x] **T-02** IPC + жизненный цикл: новое окно вместо замены содержимого + корректное закрытие `[M]`

### Блок 2: Интеграция
- [x] **T-03** Tray-меню, горячие клавиши, полировка `[S]`

### Блок 3: Тесты и проверка
- [x] **T-04** Автотесты: IPC, жизненный цикл окон, build_html, Api `[M]`
- [x] **T-05** Сборка и ручной тест `[S]`

---

## Подробно

### T-01. Фабрика окон `[M]`

**Блок:** Multi-window ядро
**Зависимости:** нет

Сейчас создание окна — линейный код в конце модуля: `Api()` → `tempfile.mkstemp` (loading HTML) → `webview.create_window()` → подписка на `closing` → `webview.start()`. Это работает для одного окна, но не переиспользуется.

**Что сделать:**

1. **`def main()` guard** — обернуть module-level код (строки 663-695: `_signal_existing_instance`, `_start_ipc_listener`, `_setup_tray`, `webview.create_window`, `webview.start`) в `def main():` + `if __name__ == '__main__': main()`. Без этого `import app` в тестах запускает GUI, сокеты и tray — T-04 нереализуем.

2. **Глобальный список `_windows`** с `threading.Lock`:
   ```python
   _windows: list[dict] = []  # {'window': Window, 'api': Api, 'temp_html': str}
   _windows_lock = threading.Lock()
   ```
   Все операции с `_windows` (append, remove, iterate) — через `with _windows_lock`. IPC-поток и GUI-поток (closing handler) обращаются к списку конкурентно.

3. **`Api._window` — привязка к своему окну.** Сейчас `Api._get_window()` возвращает `webview.windows[0]` — при multi-window `save_file`/`save_file_as`/`save_html` откроют диалог в чужом окне. Исправить: после `create_window()` выполнить `api._window = window`. Метод `_get_window()` → `return self._window`.

4. **Два раздельных flow:**
   - **Первое окно:** создаётся в `main()` с loading HTML. Сразу после `create_window` (до `webview.start()`) — создать `Api`, привязать `api._window = window`, зарегистрировать в `_windows`. Затем `on_loaded()` вызывает `build_html` + `window.load_url(file_url)` и обновляет `api._current_path`. `on_loaded` НЕ использует `_create_window` — у неё уже есть существующее окно, она берёт его из `_windows[0]`.
   - **Новые окна (из IPC / tray):** `_create_window(filepath=None)` — создаёт `Api()`, определяет контент (если `filepath` — читает файл, иначе `example.md`), вызывает `build_html()`, `webview.create_window()` с финальным HTML сразу (без loading screen), привязывает `api._window = window`, подписывает closing, добавляет в `_windows`. pywebview `create_window()` после `start()` thread-safe — внутренне ставит задачу в очередь GUI-потока ([пример из доки](https://pywebview.flowrl.com/3.7/guide/usage): "Windows created after the GUI loop is started are shown immediately").

5. **`on_loaded()`** — убрать ссылку на глобальный `api`. Работать с `Api` инстансом из `_windows[0]` (первое окно, зарегистрированное в `main()`).

**Ключевой момент:** pywebview привязывает `js_api` per-window при `create_window()` (https://pywebview.flowrl.com/guide/interdomain.html). BRIDGE_JS обращается к `window.pywebview.api` — это API конкретного окна. Менять JS не нужно.

**Файлы:** `app.py`

**Temp-файлы:** `build_html` по-прежнему добавляет в глобальный `_temp_html` (primary source для `atexit cleanup`). `_windows[i]['temp_html']` — дублирующая ссылка для per-window cleanup в `_on_closing`. При удалении окна `_on_closing` удаляет temp через `_windows` entry; `cleanup()` при выходе безопасно пытается удалить всё из `_temp_html` (idempotent, `try/except`).

**Definition of Done:**
- `_create_window(filepath)` создаёт полноценное окно с собственным `Api` (+ `api._window`) и temp HTML
- `_create_window(None)` загружает `example.md`
- Первое окно зарегистрировано в `_windows` до вызова `webview.start()`
- `_windows` защищён `threading.Lock`
- Module-level side effects убраны в `main()`
- Первое окно работает как раньше (loading screen → шаблон)
- Второе окно при вызове `_create_window("test.md")` появляется и работает независимо (save/open диалоги в правильном окне)

---

### T-02. IPC + жизненный цикл окон `[M]`

**Блок:** Multi-window ядро
**Зависимости:** T-01

Сейчас IPC-listener при `OPEN:path` вызывает `_load_file_in_window()` — заменяет контент в единственном окне через JS-инъекцию. При `SHOW` — вызывает `_force_foreground()` для единственного окна.

**Что сделать:**

1. **IPC `OPEN:path`** — вместо `_load_file_in_window(filepath)` вызывать `_create_window(filepath)`. Новый файл → новое окно.

2. **IPC `SHOW`** — показать все скрытые окна, или поднять последнее активное. `_force_foreground()` нужно обобщить (см. п.4).

3. **`_on_closing(window)`** — per-window обработчик через замыкание. Логика возвратов:
   - `_quitting == True` → `return True` (разрешить закрытие, приложение завершается).
   - Не-последнее окно (`len(_windows) > 1`) → удалить из `_windows`, подчистить temp HTML, `return True` (уничтожить окно).
   - Последнее окно (`len(_windows) == 1`) → `window.hide()`, `return False` (скрыть в трей, окно живо, восстанавливается через tray).

   Все операции с `_windows` — через `_windows_lock` (из T-01).

4. **`_force_foreground()`** — убрать хардкод `webview.windows[0]`. EnumWindows callback сейчас возвращает `False` (стоп) после первого окна с заголовком `MDLook` — убрать этот early return, чтобы поднимались все окна MDLook.

5. Удалить `_load_file_in_window()` — больше не нужна.

**Файлы:** `app.py`

**Definition of Done:**
- `MDLook.exe file1.md` + `MDLook.exe file2.md` → два окна в одном процессе
- Закрытие одного окна не убивает второе
- Закрытие последнего окна → скрыть в трей (double-click на tray icon восстанавливает)
- `SHOW` из второго экземпляра поднимает существующие окна

---

### T-03. Tray-меню, горячие клавиши, полировка `[S]`

**Блок:** Интеграция
**Зависимости:** T-02

**Что сделать:**

1. **Tray menu** — добавить пункт «New Window» (или «Open File…»), который вызывает `_powershell_open_dialog()` → `_create_window(path)`. Разместить после «Show MDLook».

2. **«Show MDLook»** — показать/поднять все окна. Если окон нет — создать пустое.

3. **«Quit»** — `_quitting = True`, уничтожить **все** окна: `for entry in list(_windows): entry['window'].destroy()` (копия списка, т.к. `_on_closing` модифицирует `_windows` при каждом destroy), затем `icon.stop()`. Без уничтожения всех окон `webview.start()` не вернёт управление и процесс повиснет.

4. **`cleanup()`** — подчистить temp HTML из всех записей `_windows`, не только из `_temp_html`.

**Файлы:** `app.py`

**Definition of Done:**
- Пункт «New Window» / «Open File…» в tray-меню создаёт новое окно
- «Show MDLook» поднимает все окна
- «Quit» корректно завершает все окна и процесс
- Temp-файлы не остаются после выхода

---

### T-04. Автотесты `[M]`

**Блок:** Тесты и проверка
**Зависимости:** T-02 (код, который тестируем, должен существовать)

Автотесты на pytest. GUI не поднимаем — мокаем `webview` через `unittest.mock`. Тестируем логику, не рендеринг.

**Файл:** `tests/test_multiwindow.py`

**Группа 1 — IPC-протокол (socket-level, без мока GUI):**
- Поднять `_start_ipc_listener()` на реальном сокете (localhost, рандомный порт через параметризацию `IPC_PORT`).
- Отправить `OPEN:/path/to/file.md` → проверить, что вызвалась `_create_window` с правильным путём.
- Отправить `SHOW` → проверить, что вызвалась `_force_foreground`.
- Отправить мусор → ничего не упало, соединение закрыто.
- `_signal_existing_instance()` при запущенном listener → возвращает `True`.
- `_signal_existing_instance()` без listener → возвращает `False` (ConnectionRefused).

**Группа 2 — Жизненный цикл `_windows`:**
- `_create_window(filepath)` → добавляет запись в `_windows` с правильными полями.
- `_create_window(None)` → загружает `example.md`.
- Закрытие окна → запись удалена из `_windows`, temp-файл удалён.
- Закрытие последнего окна → `_windows` пуст, приложение не упало.
- `_quitting=True` + closing → обработчик возвращает `True`.
- `_quitting=False`, не-последнее окно → удаляет из `_windows`, возвращает `True`.
- `_quitting=False`, последнее окно → hide, возвращает `False`.

**Группа 3 — `build_html`:**
- Создаёт temp-файл с расширением `.html`.
- Контент подставлен вместо `MDCONTENT_PLACEHOLDER`.
- `</script>` в md-контенте экранирован как `<\/script>`.
- Файл добавлен в `_temp_html`.
- `cleanup()` удаляет все temp-файлы.

**Группа 4 — `Api` изоляция:**
- Два инстанса `Api` имеют независимые `_current_path`.
- `api1.save_file(content, name)` с замоканным `webview` → пишет в `_current_path` первого.
- `api2.save_file(content, name)` → пишет в `_current_path` второго, не затрагивает первый.

**Группа 5 — `_get_file_arg` парсинг:**
- `argv = ['app.py', 'file.md']` → возвращает абсолютный путь (если файл существует).
- `argv = ['app.py', '--silent', 'file.md']` → пропускает флаг, возвращает файл.
- `argv = ['app.py', '--silent']` → возвращает `None`.
- `argv = ['app.py']` → возвращает `None`.

**Группа 6 — Thread-safety `_windows`:**
- Два потока одновременно: один добавляет окно (`_create_window`), другой удаляет (`_on_closing`) → нет corrupted state, нет исключений.
- Итерация по `_windows` (в `_force_foreground`) конкурентно с модификацией → lock не пропускает.

**Мокинг:** `webview.create_window` → возвращает `MagicMock` с `.events.closing`, `.load_url()`, `.destroy()`, `.hide()`. Мок `TEMPLATE_PATH` → маленький HTML-файл без 7MB библиотек. `import app` безопасен благодаря `def main()` guard (T-01).

**Файлы:** `tests/test_multiwindow.py`, `tests/conftest.py` (фикстуры)

**Definition of Done:**
- `pytest tests/` проходит зелёным
- Покрыты все 6 групп
- Тесты не требуют GUI, запускаются в CI-окружении
- Время выполнения < 5 секунд

---

### T-05. Сборка и ручной тест `[S]`

**Блок:** Тесты и проверка
**Зависимости:** T-01–T-04

**Сценарии проверки:**

1. Запуск без аргументов → одно окно с `example.md`.
2. Запуск с файлом → окно с этим файлом.
3. Повторный запуск с другим файлом → второе окно в том же процессе.
4. Повторный запуск без файла → поднять существующие окна (SHOW).
5. Закрыть одно окно → второе продолжает работать.
6. Закрыть все окна → приложение в трее.
7. Tray → «Open File…» → новое окно с выбранным файлом.
8. Tray → «Quit» → всё закрывается, процесс завершается.
9. Редактирование и сохранение в каждом окне независимо.
10. `--silent` режим → скрытый старт, tray icon есть.

**Способ тестирования:** запустить `python app.py` из `_internal` директории MDLook (там все зависимости). Либо — собрать standalone через PyInstaller.

**Definition of Done:**
- Все 10 сценариев проходят
- Нет утечек temp-файлов
- Нет zombie-процессов после Quit
