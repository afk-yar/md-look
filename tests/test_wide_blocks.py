"""Cyrillic is drawn by the embedded fonts, and long tables / wide diagrams may grow
past the 850px reader column while short ones stay inside it."""
from pathlib import Path

import pytest

import app


LONG_CELL = ' '.join(['длинное содержимое ячейки с путем E:/tmp/данные-блоков/08/queries/ответ.json'] * 3)

DOC = f"""# Блоки

Русский текст и `код/с/кириллицей` в абзаце.

| Короткая | Таблица |
|---|---|
| да | нет |

| Механизм | По данным | След |
|---|---|---|
| {LONG_CELL} | {LONG_CELL} | {LONG_CELL} |

```mermaid
graph LR
    A[Узел] --> B[Узел]
```

# Второй раздел верхнего уровня

```mermaid
graph LR
    A[Расчетная система зарплаты] --> B[ОтражениеЗарплатыВФинансовомУчете] --> C[Прочие активы и пассивы] --> D[Международный учет МСФО] --> E[Оценочные обязательства] --> F[Еще один длинный узел]
```
"""


@pytest.fixture(scope='module')
def page(tmp_path_factory):
    playwright = pytest.importorskip('playwright.sync_api')
    tmp_path = tmp_path_factory.mktemp('wideblocks')
    html_path = app.build_html(DOC, 'wide-probe.md', str(tmp_path))
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error as exc:
            if 'Executable doesn' in str(exc) or 'playwright install' in str(exc):
                pytest.skip('Playwright Chromium browser is not installed')
            raise
        pg = browser.new_page(viewport={'width': 1600, 'height': 900})
        pg.goto(Path(html_path).as_uri())
        pg.wait_for_function("document.querySelectorAll('#readerContent .mermaid-wrap svg').length===2")
        pg.wait_for_timeout(300)
        yield pg
        browser.close()


def platform_fonts(page, selector):
    cdp = page.context.new_cdp_session(page)
    cdp.send('DOM.enable')
    cdp.send('CSS.enable')
    root = cdp.send('DOM.getDocument')['root']['nodeId']
    node = cdp.send('DOM.querySelector', {'nodeId': root, 'selector': selector})['nodeId']
    return {f['familyName'] for f in cdp.send('CSS.getPlatformFontsForNode', {'nodeId': node})['fonts']}


def test_cyrillic_uses_embedded_fonts(page):
    # the paragraph node includes its <code> child, hence both families
    assert platform_fonts(page, '#readerContent p') == {'Inter', 'JetBrains Mono'}
    assert platform_fonts(page, '#readerContent p code') == {'JetBrains Mono'}


def widths(page, selector):
    return page.evaluate(f"[...document.querySelectorAll('#readerContent {selector}')].map(e=>Math.round(e.getBoundingClientRect().width))")


def test_short_table_stays_in_column_long_table_grows(page):
    short, long = widths(page, 'table')
    column = widths(page, 'p')[0]
    assert short == column
    assert column < long <= 1100


def test_several_top_sections_stay_foldable(page):
    assert page.evaluate("document.querySelectorAll('#readerContent > .md-section').length") == 2
    assert page.evaluate("document.querySelectorAll('.md-section-root').length") == 0


def test_double_click_selects_heading_without_folding(page):
    head = '#readerContent > .md-section:nth-of-type(2) > .md-section-head'
    page.dblclick(head + ' h1')
    page.wait_for_timeout(400)
    assert page.evaluate("window.getSelection().toString().trim()") != ''
    assert 'collapsed' not in page.locator('#readerContent > .md-section:nth-of-type(2)').get_attribute('class')


def test_single_click_on_heading_still_folds(page):
    sec = page.locator('#readerContent > .md-section:nth-of-type(2)')
    page.evaluate("window.getSelection().removeAllRanges()")
    page.click('#readerContent > .md-section:nth-of-type(2) > .md-section-head h1')
    page.wait_for_timeout(400)
    assert 'collapsed' in sec.get_attribute('class')
    page.click('#readerContent > .md-section:nth-of-type(2) > .md-section-head h1')
    page.wait_for_timeout(400)
    assert 'collapsed' not in sec.get_attribute('class')


def test_only_wide_diagram_gets_wide_frame(page):
    flags = page.evaluate("[...document.querySelectorAll('#readerContent .mermaid-wrap')].map(w=>w.classList.contains('wide'))")
    assert flags == [False, True]
    small, wide = widths(page, '.mermaid-wrap')
    assert small == widths(page, 'p')[0]
    assert wide > small
