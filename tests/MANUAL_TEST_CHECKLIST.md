# MDLook Manual Test Checklist (T-05)

Сценарии ручного тестирования multi-window функциональности.

**Подготовка:** запустить `python app.py` из директории `_internal` (там все зависимости pywebview/pystray/PIL) или через собранный `MDLook.exe`.

Для тестирования через `python app.py` нужны установленные зависимости:
```
pip install pywebview pystray pillow
```

---

## Сценарии

### 1. Запуск без аргументов → окно с example.md
- [ ] Запустить: `python app.py`
- [ ] Ожидается: одно окно, заголовок `example.md — MDLook`, содержимое — example.md
- [ ] Tray-иконка MDLook присутствует

### 2. Запуск с файлом → окно с этим файлом
- [ ] Подготовить тестовый файл: `echo "# Test" > test.md`
- [ ] Запустить: `python app.py test.md`
- [ ] Ожидается: одно окно с заголовком `test.md — MDLook`

### 3. Повторный запуск с другим файлом → второе окно
- [ ] При уже запущенном экземпляре (из сц. 2): `python app.py other.md`
- [ ] Ожидается: второй процесс завершается, в первом процессе появляется второе окно с `other.md`
- [ ] Два окна работают независимо (разные заголовки)

### 4. Повторный запуск без файла → SHOW
- [ ] При запущенных окнах (из сц. 3): `python app.py`
- [ ] Ожидается: второй процесс завершается, существующие окна появляются/поднимаются на передний план

### 5. Закрыть одно окно → второе продолжает работать
- [ ] При двух открытых окнах: закрыть крестиком первое
- [ ] Ожидается: первое окно закрывается, второе продолжает работать
- [ ] Процесс не завершается, tray-иконка остаётся

### 6. Закрыть все окна → приложение в трее
- [ ] Закрыть последнее окно крестиком
- [ ] Ожидается: окно скрывается (не завершается), tray-иконка остаётся
- [ ] Задача в диспетчере задач: процесс `python.exe` / `MDLook.exe` жив

### 7. Tray → «Open File…» → новое окно
- [ ] Правый клик на tray-иконке → «Open File…»
- [ ] Откроется диалог выбора файла
- [ ] Выбрать .md файл → новое окно с этим файлом

### 8. Tray → «Quit» → всё закрывается
- [ ] Правый клик → «Quit»
- [ ] Ожидается: все окна закрываются, tray-иконка исчезает
- [ ] Процесс завершается (нет zombie в диспетчере задач)
- [ ] Нет temp-файлов `mdlook_*.html` в `%TEMP%`

### 9. Редактирование и сохранение независимо
- [ ] Открыть два разных .md файла (сц. 2 + сц. 3)
- [ ] В первом окне: отредактировать текст → Ctrl+S → убедиться, что сохранено в исходный файл
- [ ] Во втором окне: отредактировать текст → Ctrl+S → убедиться, что сохранено в свой файл
- [ ] Файлы не смешиваются (каждый Api знает свой путь)

### 10. --silent → скрытый старт
- [ ] Запустить: `python app.py --silent`
- [ ] Ожидается: нет видимых окон, появляется только tray-иконка
- [ ] Двойной клик по tray-иконке (или Show MDLook) → окно появляется

### 11. Ссылки и картинки из Markdown
- [ ] Закрыть старые экземпляры MDLook или проверить, что активный процесс запущен из тестируемой сборки: `Get-Process MDLook | Select-Object Id,Path,StartTime`
- [ ] Запустить тестируемую сборку с файлом `docs/links-images-repro.md`
- [ ] Ожидается: картинка `Whiteboard` отображается, `Missing image` показывает fallback
- [ ] Клик по `Linked note` открывает `docs/linked-note.md` в MDLook и переходит к `#target`
- [ ] Клик по `Back to target` скроллит внутри текущего окна
- [ ] Клик по `External site` открывает системный браузер
- [ ] Если открылся браузер с `%TEMP%/linked-note.md`, проверяется не та сборка или используется старый шаблон

### 12. Навигация по соседним файлам книги
- [ ] Закрыть старые экземпляры MDLook или проверить, что активный процесс запущен из тестируемой сборки: `Get-Process MDLook | Select-Object Id,Path,StartTime`
- [ ] Открыть файл `E:/_AI/_Доки/1С/ERP-WE/ИТС-книги/erpwedoc/markdown/0147_2.11._Планирование_сборки_и_разборки.md`
- [ ] Нажать кнопку `This folder` в toolbar
- [ ] Ожидается: слева открывается панель со списком соседних `.md` файлов из `E:/_AI/_Доки/1С/ERP-WE/ИТС-книги/erpwedoc/markdown`
- [ ] Ввести `0097` в фильтр
- [ ] Ожидается: в списке остается `0097_2.3._Настройки_формирования_планов.md`
- [ ] Кликнуть по `0097_2.3._Настройки_формирования_планов.md`
- [ ] Ожидается: документ меняется в том же окне, заголовок окна и плашка имени файла показывают `0097_2.3._Настройки_формирования_планов.md`
- [ ] Снова открыть панель, найти `0147`, вернуться к `0147_2.11._Планирование_сборки_и_разборки.md`
- [ ] Если открылось новое окно или список пустой при наличии файлов, проверяется не та сборка или bridge не получил `filePath`

---

## Проверка отсутствия утечек

После Quit:
- [ ] `%TEMP%\mdlook_*.html` — нет файлов
- [ ] Диспетчер задач — нет `python.exe` / `MDLook.exe` от MDLook

---

## Автоматизированные эквиваленты

Следующие аспекты покрыты автотестами в `tests/test_multiwindow.py`:

| Сценарий | Автотест |
|----------|----------|
| Запуск с файлом | `TestWindowLifecycle::test_create_window_adds_entry_to_windows` |
| Запуск без файла | `TestWindowLifecycle::test_create_window_none_loads_example_md` |
| Закрыть одно из нескольких | `TestWindowLifecycle::test_closing_non_last_window_removes_entry` |
| Закрыть последнее → трей | `TestWindowLifecycle::test_closing_last_window_hides_instead_of_destroy` |
| Quit + closing | `TestWindowLifecycle::test_closing_with_quitting_returns_true` |
| IPC OPEN: | `TestIpcProtocol::test_open_message_calls_create_window` |
| IPC SHOW | `TestIpcProtocol::test_show_message_calls_force_foreground` |
| IPC порт scoped по exe/dev path | `TestIpcProtocol::test_ipc_port_is_scoped_by_executable_identity` |
| Второй экземпляр → True | `TestIpcProtocol::test_signal_existing_instance_returns_true_when_listener_running` |
| Нет первого → False | `TestIpcProtocol::test_signal_existing_instance_returns_false_when_no_listener` |
| Независимые Api | `TestApiIsolation::test_save_file_writes_to_own_current_path` |
| --silent парсинг | `TestGetFileArg::test_argv_silent_only_returns_none` |
| Temp-файл при quit | `TestBuildHtml::test_cleanup_removes_temp_files` |
| Markdown ссылки и картинки | `tests/test_links_images.py` |
| Навигация по соседним файлам | `tests/test_folder_navigation.py` |
