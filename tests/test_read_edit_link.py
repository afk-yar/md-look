"""Ctrl+E keeps the place: rendered blocks carry their source line (data-line); a selection in Read
becomes the same selection in the editor, and the editor caret opens Read on its block."""
from pathlib import Path

import pytest

import app


FILLER = '\n\n'.join(f'Заполнитель {i}, длинный абзац, чтобы страница прокручивалась далеко вниз. ' * 3 for i in range(30))
DOC = f"""# Заголовок

Первый абзац со словом ключ и продолжением
на второй строке.

- пункт списка
- пункт со словом ключ

| Колонка | Число |
|---|---|
| строка ключ | 1 |

```python
print("ключ")
```

$$
a + b
$$

Абзац после формулы.

> Цитата первая
>
> Цитата вторая со словом **жирный ключ** внутри

## Середина

{FILLER}

## Конец раздела

Абзац в конце со словом ключ и ещё раз ключ здесь.

{FILLER}
"""
LINES = DOC.split('\n')


def line_of(text):
    return next(i for i, l in enumerate(LINES) if text in l)


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
def pg(browser, tmp_path):
    url = Path(app.build_html(DOC, 'link.md', str(tmp_path))).as_uri()
    page = browser.new_page(viewport={'width': 1400, 'height': 800})
    page.goto(url)
    page.wait_for_selector('#readerContent p')
    page.wait_for_timeout(300)
    return page


SELECT = """([sel,text,nth])=>{
  const el=[...document.querySelectorAll(sel)].find(e=>e.textContent.includes(text));
  const w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);let n,k=0;
  while((n=w.nextNode())){let p=-1;while((p=n.nodeValue.indexOf(text,p+1))>=0){if(k++===nth){
    const r=document.createRange();r.setStart(n,p);r.setEnd(n,p+text.length);
    const s=getSelection();s.removeAllRanges();s.addRange(r);return true}}}
  return false}"""
EDITOR_SEL = "(()=>{const e=document.getElementById('editor');return [e.selectionStart,e.selectionEnd,e.value.slice(e.selectionStart,e.selectionEnd)]})()"


def ctrl_e(pg):
    pg.keyboard.press('Control+KeyE')
    pg.wait_for_timeout(250)


def offset_of_line(n):
    return sum(len(l) + 1 for l in LINES[:n])


def test_blocks_carry_their_source_line(pg):
    got = pg.evaluate("""[...document.querySelectorAll('#readerContent [data-line]')].map(e=>[e.tagName,+e.dataset.line,e.textContent.trim().slice(0,22)])""")

    def by_text(prefix):
        return next((tag, n) for tag, n, t in got if t.startswith(prefix))
    assert by_text('Заголовок') == ('H1', line_of('# Заголовок'))
    assert by_text('Первый абзац со словом') == ('P', line_of('Первый абзац'))
    assert by_text('пункт со словом ключ') == ('LI', line_of('- пункт со словом'))
    assert ('TR', line_of('| строка ключ')) in [(tag, n) for tag, n, _ in got]
    assert ('PRE', line_of('```python')) in [(tag, n) for tag, n, _ in got]
    assert by_text('Абзац после формулы.') == ('P', line_of('Абзац после формулы'))
    assert by_text('Цитата вторая') == ('P', line_of('> Цитата вторая'))
    assert by_text('Конец раздела') == ('H2', line_of('## Конец раздела'))


def test_selection_in_read_becomes_selection_in_editor(pg):
    # the second "ключ" of the paragraph near the end, not the first one in the file
    pg.evaluate(SELECT, ['#readerContent p', 'Абзац в конце', 0])
    pg.evaluate(SELECT.replace('includes(text)', "includes('Абзац в конце')"), ['#readerContent p', 'ключ', 1])
    ctrl_e(pg)
    assert pg.evaluate('currentMode') == 'edit'
    s, e, text = pg.evaluate(EDITOR_SEL)
    assert text == 'ключ'
    line = LINES[line_of('Абзац в конце')]
    assert s == offset_of_line(line_of('Абзац в конце')) + line.index('ключ', line.index('ключ') + 1)
    assert pg.evaluate("document.getElementById('editor').scrollTop") > 0


def test_selection_across_soft_wrap_found(pg):
    pg.evaluate(SELECT, ['#readerContent p', 'продолжением на второй', 0])
    ctrl_e(pg)
    s, e, text = pg.evaluate(EDITOR_SEL)
    assert text == 'продолжением\nна второй'


def test_selection_across_bold_falls_back_to_block_start(pg):
    pg.evaluate("""(()=>{const p=[...document.querySelectorAll('#readerContent blockquote p')].find(e=>e.textContent.includes('жирный'));
      const t=p.firstChild,b=p.querySelector('strong').firstChild,r=document.createRange();
      r.setStart(t,t.nodeValue.indexOf('словом'));r.setEnd(b,6);const s=getSelection();s.removeAllRanges();s.addRange(r)})()""")
    assert pg.evaluate('getSelection().toString()') == 'словом жирный'
    ctrl_e(pg)
    s, e, _ = pg.evaluate(EDITOR_SEL)
    assert s == e == offset_of_line(line_of('> Цитата вторая'))


def test_no_selection_takes_block_at_top_of_screen(pg):
    pg.evaluate("""const r=document.querySelector('#reader'),h=[...document.querySelectorAll('#readerContent h2')].find(e=>e.textContent==='Конец раздела');
      r.scrollTop+=h.getBoundingClientRect().top-r.getBoundingClientRect().top""")
    pg.wait_for_timeout(100)
    ctrl_e(pg)
    s, e, _ = pg.evaluate(EDITOR_SEL)
    assert s == e == offset_of_line(line_of('## Конец раздела'))


def test_editor_caret_opens_read_on_its_block(pg):
    pg.evaluate("setMode('edit')")
    pg.wait_for_timeout(200)
    pos = offset_of_line(line_of('Абзац в конце')) + 5
    pg.evaluate(f"const e=document.getElementById('editor');e.focus();e.setSelectionRange({pos},{pos})")
    ctrl_e(pg)
    assert pg.evaluate('currentMode') == 'read'
    top = pg.evaluate("""(()=>{const r=document.querySelector('#reader').getBoundingClientRect().top;
      const p=[...document.querySelectorAll('#readerContent p')].find(e=>e.textContent.startsWith('Абзац в конце'));
      return p.getBoundingClientRect().top-r})()""")
    assert 0 <= top < 120


def test_tabs_and_source_mode_unchanged(pg):
    pg.evaluate(SELECT, ['#readerContent p', 'Абзац в конце', 0])
    pg.keyboard.press('Control+Digit2')
    pg.wait_for_timeout(200)
    assert pg.evaluate(EDITOR_SEL)[:2] == [0, 0]
