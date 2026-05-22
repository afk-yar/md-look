# Задачи: Inline code не экранирует HTML

**Проект:** MDLook
**Дата:** 2026-05-22

---

## Прогресс

- [x] **T-01** Экранировать HTML в inline code (`parseInline`) `[XS]`
- [x] **T-02** Экранировать HTML в block math data-attr `[XS]`
- [x] **T-03** Нейтрализовать RAWTEXT-теги в обычном тексте `[XS]`

---

## Подробно

### T-01. Экранировать HTML-теги в inline code `[XS]`

**Severity:** Critical (контент исчезает)

**Проблема:** `parseInline()` сохраняет содержимое бэктик-кода в слоты, но при восстановлении не экранирует HTML-сущности. Когда inline code содержит HTML-теги (`` `<style>` ``, `` `<script>` ``, `` `<textarea>` ``), браузер интерпретирует их как реальные HTML-элементы.

`<style>` особенно опасен: браузер переключается в RAWTEXT-режим и поглощает **весь последующий контент** как CSS до закрывающего `</style>`, который никогда не встречается. Результат: всё после `` `<style>` `` невидимо в режиме чтения.

**Воспроизведение:** открыть в MDLook файл `E:/_Проекты/ОБРП/lp-marketplace/tasks/003-аудит-безопасности/tasks.md`. В read mode контент обрезается после T-56 (строка 83 содержит `` `<style>` `` в бэктиках). В edit mode всё видно.

**Корень:** `MDLook-template-offline.html:4017`
```javascript
// Текущий код — НЕ экранирует:
t=t.replace(/ICODE(\d+)/g,function(_,idx){
  return '<code>'+codeSlots[parseInt(idx)]+'</code>';
});
```

**Фикс:**
```javascript
t=t.replace(/ICODE(\d+)/g,function(_,idx){
  return '<code>'+esc(codeSlots[parseInt(idx)])+'</code>';
});
```

**Definition of Done:**
- [x] `esc()` применяется к содержимому inline code при восстановлении из слотов
- [x] Markdown с `` `<style>` ``, `` `<script>` ``, `` `<div>` `` корректно отображается как текст в `<code>`
- [x] Файл-репродьюсер рендерится полностью (все задачи T-54..T-60 видны)

### T-02. Экранировать HTML в data-math атрибутах `[XS]`

**Severity:** Low (визуальный глитч, не потеря контента)

**Проблема:** Block math и inline math помещают выражения в `data-math` атрибут без полного экранирования — только `"` заменяется. Если math-выражение содержит `>` или `<`, атрибут может быть обрезан.

**Корень:**
- `MDLook-template-offline.html:4082` (block math): `.replace(/"/g,'&quot;')`
- `MDLook-template-offline.html:4028` (inline math): `.replace(/"/g,'&amp;quot;')`

**Фикс:** использовать `esc()` вместо ручной замены `"`.

**Definition of Done:**
- [x] Block math и inline math используют `esc()` для data-math атрибутов
- [x] Math-выражения с `<` и `>` (неравенства) рендерятся корректно

### T-03. Нейтрализовать RAWTEXT-теги в обычном тексте `[XS]`

**Severity:** Medium (контент исчезает если теги без бэктиков)

**Проблема:** `parseInline()` не экранирует HTML в обычном тексте. Если пользователь пишет `<style>` или `<script>` без бэктиков, браузер интерпретирует их как реальные теги и поглощает последующий контент.

**Стандарт:** GitHub Flavored Markdown использует whitelist разрешённых тегов и удаляет `<style>`, `<script>` и другие опасные теги. VS Code делает то же самое.

**Фикс:** добавить regex в `parseInline()` после извлечения code/math слотов, который экранирует угловые скобки RAWTEXT-тегов (`style`, `script`, `textarea`, `title`, `xmp`, `plaintext`):
```javascript
t=t.replace(/<(\/?)(style|script|textarea|title|xmp|plaintext)\b([^>]*)>/gi,
  function(_,sl,tag,rest){return '&lt;'+sl+tag+rest+'&gt;'});
```

**Definition of Done:**
- [x] RAWTEXT-теги в обычном тексте экранируются (отображаются как текст)
- [x] Безопасные теги (`<div>`, `<details>`, `<kbd>`, `<br>`) продолжают работать как raw HTML
- [x] Regex не матчит слот-маркеры (Unicode private-use chars), только реальные HTML-теги
