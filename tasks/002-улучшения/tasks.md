# Задачи: Улучшения MDLook

**Проект:** MDLook (форк/патч)
**Дата:** 2026-05-20

---

## Прогресс

- [ ] **T-01** Reload on focus: перечитать файл при получении фокуса `[S]`
- [ ] **T-02** Кнопка «Обновить» в toolbar `[XS]`

---

## Подробно

### T-01. Reload on focus `[S]`

**Зависимости:** нет

**Проблема:** Когда внешний процесс (Claude Code, VS Code, любой редактор) меняет .md файл, MDLook показывает старое содержимое. Нужно закрыть/открыть файл чтобы увидеть изменения.

**Что сделать:**

1. **`Api.reload_file()`** — новый метод. Если `self._current_path` существует и файл изменился (проверка по mtime):
   - Прочитать файл заново
   - Вернуть `{'content': ..., 'name': ..., 'path': ...}` (тот же формат, что `open_file`)
   - Если файл не изменился → вернуть `None` (JS не перерисовывает)
   - Хранить `self._file_mtime` — mtime последней загрузки. Обновлять при reload, open_file, save_file.

2. **JS: `document.addEventListener('focus', ...)`** в BRIDGE_JS — при получении фокуса окном вызвать `window.pywebview.api.reload_file()`. Если результат не null — обновить контент:
   ```js
   rawMd = res.content;
   fileName = res.name;
   var ed = document.querySelector('#editor');
   if(ed) ed.value = rawMd;
   setMode('read');
   document.querySelector('#fn').textContent = res.name;
   document.title = res.name + ' — MDLook';
   ```
   Debounce: не чаще 1 раза в секунду (предотвратить шквал при быстром alt-tab).

3. **Обновить mtime** в `open_file()`, `save_file()`, `on_loaded()`, `_create_window()` — везде, где загружается или сохраняется файл. `api._file_mtime = os.path.getmtime(path)`.

**Ключевой момент:** НЕ перезагружать всю страницу (load_url). Обновлять контент in-place через JS (как делает open_file в BRIDGE_JS). Это быстро и без мерцания.

**Edge cases:**
- Файл удалён → reload_file возвращает None, ничего не делаем
- _current_path = None (новый документ) → reload_file возвращает None
- Несохранённые изменения (hasUnsaved) → НЕ перезагружать (потеря правок). Показать визуальный индикатор «файл изменён снаружи» или просто пропустить.

**Файлы:** `app.py`

**Definition of Done:**
- При изменении файла внешним процессом → переключение на MDLook показывает актуальный контент
- Если файл не менялся → ничего не происходит (нет мерцания)
- Несохранённые правки не теряются при внешнем изменении
- Debounce работает (быстрый alt-tab не создаёт шквал запросов)

---

### T-02. Кнопка «Обновить» в toolbar `[XS]`

**Зависимости:** T-01

**Что сделать:**

1. **JS: кнопка Refresh** в BRIDGE_JS — добавить кнопку рядом с #btnOpen. Иконка: круговая стрелка (↻). Tooltip: «Reload file (re-read from disk)».

2. **Onclick:** принудительный reload — вызвать `reload_file()` с параметром `force=True` (игнорировать mtime, перечитать всегда). Тот же JS-код обновления контента что в T-01. Единственное отличие: force=True обходит проверку mtime.

3. **`Api.reload_file(force=False)`** — добавить параметр. `force=True` → пропустить проверку mtime, перечитать безусловно.

**Файлы:** `app.py`

**Definition of Done:**
- Кнопка видна в toolbar, визуально сочетается с остальными
- Клик перечитывает файл с диска безусловно
- Работает и в read, и в edit режиме
