"""Code highlighting (Prism with the 1C grammar), number columns in tables, the current section
in the outline, the reading progress bar and the preview card for links to other Markdown files."""
from pathlib import Path

import pytest

import app


PARAS = '\n\n'.join(f'Абзац номер {i}, достаточно длинный, чтобы страница прокручивалась. ' * 3 for i in range(25))
DOC = f"""# Документ

Ссылка на [соседнюю заметку](note.md) и на [сайт](https://example.com).

```bsl
&НаСервере
Процедура Проверить(Параметр) Экспорт
    // комментарий
    Запрос = Новый Запрос;
    Запрос.Текст = "ВЫБРАТЬ ПЕРВЫЕ 1 Т.Ссылка ИЗ Справочник.Товары КАК Т ГДЕ Т.Код = &Код";
    Если Параметр = Неопределено Тогда
        Возврат;
    КонецЕсли;
КонецПроцедуры
```

```1c
Функция Ф() КонецФункции
```

```запрос
ВЫБРАТЬ СУММА(Т.Количество) ИЗ Т ГДЕ Т.Дата > &Дата
```

```python
def f(x):
    return "s"  # c
```

| Название | Сумма | Доля |
|---|---|---|
| Первый | 1 200,50 ₽ | 15 % |
| Второй | 300 ₽ | — |
| Третий | 12 ₽ | 3 % |

## Раздел второй

{PARAS}

## Раздел третий

{PARAS}
"""


@pytest.fixture()
def browser():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except playwright.Error as exc:
            if 'Executable doesn' in str(exc) or 'playwright install' in str(exc):
                pytest.skip('Playwright Chromium browser is not installed')
            raise
        yield b
        b.close()


@pytest.fixture()
def doc_url(tmp_path):
    (tmp_path / 'note.md').write_text('# Заметка\n\nПервая строка.\n', encoding='utf-8')
    return Path(app.build_html(DOC, 'code.md', str(tmp_path))).as_uri()


def open_page(browser, url):
    pg = browser.new_page(viewport={'width': 1400, 'height': 800})
    pg.goto(url)
    pg.wait_for_selector('#readerContent p')
    pg.wait_for_timeout(300)
    return pg


def color(pg, selector, text=None):
    return pg.evaluate("""([s,t])=>{const el=[...document.querySelectorAll(s)].find(e=>t===null||e.textContent===t);
      return el?getComputedStyle(el).color:null}""", [selector, text])


def test_bsl_highlighted_with_query_inside_string(browser, doc_url):
    pg = open_page(browser, doc_url)
    code = '#readerContent pre code.language-bsl'
    assert color(pg, code + ' .token.keyword', 'Процедура') == 'rgb(209, 36, 47)'
    assert color(pg, code + ' .token.boolean', 'Неопределено') == 'rgb(209, 36, 47)'
    assert color(pg, code + ' .token.comment', '// комментарий') == 'rgb(47, 125, 50)'
    assert color(pg, code + ' .token.annotation', '&НаСервере') == 'rgb(138, 90, 31)'
    assert color(pg, code + ' .token.identifier', 'Параметр') == 'rgb(45, 79, 184)'
    q = code + ' .token.query-string'
    assert color(pg, q + ' .token.keyword', 'ВЫБРАТЬ') == 'rgb(45, 79, 184)'
    assert color(pg, q + ' .token.parameter', '&Код') == 'rgb(15, 127, 134)'
    assert pg.evaluate("getComputedStyle(document.querySelector('#readerContent pre code')).backgroundColor") == 'rgb(248, 247, 244)'


def test_aliases_and_other_languages(browser, doc_url):
    pg = open_page(browser, doc_url)
    labels = pg.evaluate("[...document.querySelectorAll('#readerContent pre')].map(p=>[p.querySelector('.ch span').textContent,p.querySelector('code').className])")
    assert labels[1] == ['1c', 'language-bsl']
    assert labels[2] == ['запрос', 'language-sdbl']
    assert color(pg, '#readerContent code.language-sdbl .token.function', 'СУММА') == 'rgb(200, 50, 58)'
    assert color(pg, '#readerContent code.language-python .token.keyword', 'def') == 'rgb(207, 34, 46)'
    assert color(pg, '#readerContent code.language-python .token.string', '"s"') == 'rgb(10, 48, 105)'
    # Copy still takes the plain text of the block
    assert pg.evaluate("document.querySelector('#readerContent code.language-python').textContent").startswith('def f(x):')


def test_dark_theme_and_edit_preview_highlighted(browser, doc_url):
    pg = open_page(browser, doc_url)
    pg.click('#thm')
    pg.wait_for_timeout(300)
    assert color(pg, '#readerContent code.language-bsl .token.keyword', 'Процедура') == 'rgb(203, 166, 247)'
    assert color(pg, '#readerContent code.language-bsl .token.query-string .token.keyword', 'ВЫБРАТЬ') == 'rgb(137, 220, 235)'
    assert pg.evaluate("getComputedStyle(document.querySelector('#readerContent pre code')).backgroundColor") == 'rgb(22, 22, 30)'
    pg.evaluate("setMode('edit')")
    pg.wait_for_timeout(300)
    assert pg.evaluate("document.querySelectorAll('#preview code.language-bsl .token.keyword').length") > 3
    assert pg.evaluate("document.querySelector('#preview td:nth-child(2)').style.textAlign") == 'right'


def test_number_columns_aligned_right_with_muted_units(browser, doc_url):
    pg = open_page(browser, doc_url)
    cols = pg.evaluate("""[...document.querySelectorAll('#readerContent table tr')].map(r=>[...r.children]
      .map(c=>getComputedStyle(c).textAlign))""")
    assert cols[0] == ['left', 'right', 'right']
    assert all(r == ['left', 'right', 'right'] for r in cols[1:])
    assert pg.evaluate("getComputedStyle(document.querySelector('#readerContent td:nth-child(2)')).fontVariantNumeric") == 'tabular-nums'
    muted = pg.evaluate("[...document.querySelectorAll('#readerContent td .md-num-mute')].map(e=>e.textContent)")
    assert sorted(muted) == sorted(['₽', '%', '₽', '—', '₽', '%'])
    tm = pg.evaluate("getComputedStyle(document.body).getPropertyValue('--tm').trim()")
    assert tm == '#8a8a8a'
    assert color(pg, '#readerContent td .md-num-mute', '—') == 'rgb(138, 138, 138)'
    assert pg.evaluate("document.querySelector('#readerContent td:nth-child(2)').textContent") == '1 200,50 ₽'


def test_outline_marks_current_section_and_progress_follows_scroll(browser, doc_url):
    pg = open_page(browser, doc_url)
    progress = "getComputedStyle(document.getElementById('mdProgress')).transform"
    assert pg.evaluate(progress) in ('matrix(0, 0, 0, 1, 0, 0)', 'none')
    pg.evaluate("""const r=document.querySelector('#reader'),h=document.querySelectorAll('#readerContent h2')[1];
      r.scrollTop+=h.getBoundingClientRect().top-r.getBoundingClientRect().top+40""")
    pg.wait_for_timeout(300)
    assert pg.evaluate("[...document.querySelectorAll('.outline-item.md-cur')].map(b=>b.textContent)") == ['Раздел третий']
    pg.evaluate("const r=document.querySelector('#reader');r.scrollTop=r.scrollHeight")
    pg.wait_for_timeout(300)
    assert pg.evaluate(progress) == 'matrix(1, 0, 0, 1, 0, 0)'
    assert pg.evaluate("getComputedStyle(document.getElementById('mdProgress')).height") == '2px'
    pg.evaluate("document.querySelector('#reader').scrollTop=0")
    pg.wait_for_timeout(300)
    assert pg.evaluate("[...document.querySelectorAll('.outline-item.md-cur')].map(b=>b.textContent)") == ['Документ']


def test_link_preview_only_inside_the_app(browser, doc_url):
    pg = open_page(browser, doc_url)
    link = "#readerContent a:text('соседнюю заметку')"
    pg.hover(link)
    pg.wait_for_timeout(700)
    assert pg.evaluate("!!document.querySelector('.md-linkcard.show')") is False
    pg.evaluate("""window.mdPreviewCalls=[];window.pywebview={api:{md_preview:async(u)=>{mdPreviewCalls.push(u);
      return {ok:true,title:'Заметка',lines:['Первая строка.','Вторая']}}}}""")
    pg.mouse.move(5, 300)
    pg.hover(link)
    pg.wait_for_timeout(150)
    assert pg.evaluate("!!document.querySelector('.md-linkcard.show')") is False  # the delay holds it back
    pg.wait_for_timeout(500)
    assert pg.evaluate("document.querySelector('.md-linkcard.show .md-linkcard-t').textContent") == 'Заметка'
    assert pg.evaluate("mdPreviewCalls[0]").endswith('/note.md')
    pg.mouse.move(5, 300)
    pg.wait_for_timeout(200)
    assert pg.evaluate("!!document.querySelector('.md-linkcard.show')") is False
    # external links get no card
    pg.hover("#readerContent a:text('сайт')")
    pg.wait_for_timeout(600)
    assert pg.evaluate("mdPreviewCalls.length") == 1


def test_shortcuts_work_on_russian_layout(browser, doc_url):
    pg = open_page(browser, doc_url)
    # Russian layout: the physical keys F and W give 'а' and 'ц', the code stays KeyF / KeyW
    press = """([t,k,c,s])=>document.querySelector(t).dispatchEvent(new KeyboardEvent('keydown',
      {key:k,code:c,ctrlKey:true,shiftKey:s,bubbles:true,cancelable:true}))"""
    pg.evaluate(press, ['#reader', 'а', 'KeyF', False])
    assert pg.evaluate("document.getElementById('findBar').classList.contains('open')") is True
    pg.keyboard.press('Escape')
    pg.evaluate(press, ['#reader', 'Ц', 'KeyW', True])
    assert pg.evaluate("document.body.classList.contains('wide')") is True
    pg.evaluate("setMode('edit')")
    pg.evaluate("const t=document.getElementById('editor');t.focus();t.setSelectionRange(2,10)")
    pg.evaluate(press, ['#editor', 'и', 'KeyB', False])
    assert '**' in pg.evaluate("document.getElementById('editor').value")


def test_ctrl_e_toggles_edit_and_editor_is_not_bright_white(browser, doc_url):
    pg = open_page(browser, doc_url)
    press = """k=>document.querySelector('#reader').dispatchEvent(new KeyboardEvent('keydown',
      {key:k,code:'KeyE',ctrlKey:true,bubbles:true,cancelable:true}))"""
    pg.evaluate(press, 'у')  # Russian layout
    assert pg.evaluate('currentMode') == 'edit'
    bg = "getComputedStyle(document.getElementById('editor')).backgroundColor"
    assert pg.evaluate(bg) == 'rgb(250, 249, 246)'
    assert pg.evaluate("getComputedStyle(document.querySelector('.fmt-bar')).backgroundColor") == 'rgb(250, 249, 246)'
    pg.evaluate("document.getElementById('editor').dispatchEvent(new KeyboardEvent('keydown',{key:'e',code:'KeyE',ctrlKey:true,bubbles:true,cancelable:true}))")
    assert pg.evaluate('currentMode') == 'read'


def test_md_preview_reads_title_and_first_lines(tmp_path):
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'n.md').write_text(
        '﻿---\ntitle: x\n---\n\n# Заголовок\n\nСтрока 1\n\n---\n\n- [пункт](a.md) `2`\nСтрока 3\n> цитата 4\nСтрока 5\n', encoding='utf-8')
    (tmp_path / 'plain.md').write_text('Без заголовка\nвторая\n', encoding='utf-8')
    (tmp_path / 'run.bat').write_text('echo', encoding='utf-8')
    api = app.Api()
    res = api.md_preview('sub/n.md', str(tmp_path))
    assert res == {'ok': True, 'title': 'Заголовок', 'lines': ['Строка 1', 'пункт 2', 'Строка 3', 'цитата 4'],
                   'path': str(tmp_path / 'sub' / 'n.md')}
    assert api.md_preview((tmp_path / 'sub' / 'n.md').as_uri() + '#x')['title'] == 'Заголовок'
    assert api.md_preview('plain.md', str(tmp_path))['title'] == 'plain.md'
    assert api.md_preview('run.bat', str(tmp_path))['ok'] is False
    assert api.md_preview('missing.md', str(tmp_path))['ok'] is False
