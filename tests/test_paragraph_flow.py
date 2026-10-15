"""Paragraph lines wrapped in the source flow as one paragraph (CommonMark soft breaks),
dollar amounts stay text, and the reader column keeps its reading width."""
from pathlib import Path

import pytest

import app


DOC = """# Отчет

Первая строка абзаца, документы совпали с эталоном
по базе стенда, **жирный
через перенос** и дальше.

Жесткий разрыв двумя пробелами{two_spaces}
новая строка. Жесткий разрыв обратной чертой\\
еще строка.

Стоимость: $3,53 против $5,38 у Opus medium.

Формула $x^2$ остается формулой, как и $a+b$.

Абзац с `inline code` внутри.
""".replace('{two_spaces}', '  ')


@pytest.fixture(scope='module')
def page(tmp_path_factory):
    playwright = pytest.importorskip('playwright.sync_api')
    tmp_path = tmp_path_factory.mktemp('paraflow')
    html_path = app.build_html(DOC, 'para-probe.md', str(tmp_path))
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error as exc:
            if 'Executable doesn' in str(exc) or 'playwright install' in str(exc):
                pytest.skip('Playwright Chromium browser is not installed')
            raise
        pg = browser.new_page(viewport={'width': 1600, 'height': 900})
        pg.goto(Path(html_path).as_uri())
        pg.wait_for_selector('#readerContent p')
        yield pg
        browser.close()


def paras(page):
    return page.evaluate("[...document.querySelectorAll('#readerContent p')].map(p=>p.innerHTML)")


def test_soft_line_break_joins_with_space(page):
    first = paras(page)[0]
    assert '<br>' not in first
    assert 'с эталоном по базе стенда' in first


def test_emphasis_can_span_a_soft_break(page):
    assert '<strong>жирный через перенос</strong>' in paras(page)[0]


def test_hard_breaks_are_kept(page):
    hard = paras(page)[1]
    assert hard.count('<br>') == 2
    assert 'пробелами<br>новая' in hard
    assert 'чертой<br>еще' in hard


def test_dollar_amounts_stay_text(page):
    money = paras(page)[2]
    assert 'math-inline' not in money
    assert '$3,53 против $5,38' in money


def test_inline_math_still_renders(page):
    assert page.evaluate("document.querySelectorAll('#readerContent p .math-inline').length") == 2


def test_reader_column_width_and_font(page):
    m = page.evaluate("""(()=>{const p=document.querySelector('#readerContent p');const s=getComputedStyle(p);
      return {w:p.getBoundingClientRect().width,font:s.fontFamily,size:s.fontSize,wrap:s.textWrap||s.textWrapStyle}})()""")
    assert abs(m['w'] - 850) <= 1
    assert m['font'].startswith('Inter')
    assert m['size'] == '16.5px'
    assert 'pretty' in m['wrap']


def test_lone_title_section_does_not_fold(page):
    root = page.locator('#readerContent > .md-section')
    assert root.count() == 1
    assert 'md-section-root' in root.get_attribute('class')
    page.click('#readerContent > .md-section > .md-section-head')
    assert 'collapsed' not in root.get_attribute('class')


def test_reader_layer_is_opaque_for_cleartype(page):
    # Transparent composited layer -> WebView2 falls back to grayscale AA (blurry text)
    bg = page.evaluate("getComputedStyle(document.querySelector('#reader')).backgroundColor")
    assert bg not in ('rgba(0, 0, 0, 0)', 'transparent')


def test_inline_code_is_outlined_and_block_code_is_not(page):
    code = page.evaluate("""(()=>{const s=getComputedStyle(document.querySelector('#readerContent p code'));
      return {bw:s.borderTopWidth,bg:s.backgroundColor}})()""")
    assert code['bw'] == '1px'
    assert code['bg'] == 'rgba(0, 0, 0, 0)'
