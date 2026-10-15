"""KaTeX and Mermaid are compiled only when the document has formulas or diagrams."""
from pathlib import Path

import pytest

import app

PLAIN = '# Plain\n\nJust text.\n'
RICH = '# Rich\n\nInline $a^2+b^2$ math.\n\n$$\\int_0^1 x\\,dx$$\n\n```mermaid\ngraph TD\n  A-->B\n```\n'


def _browser(p):
    try:
        return p.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        if 'Executable doesn' in str(exc) or 'playwright install' in str(exc):
            pytest.skip('Playwright Chromium browser is not installed')
        raise


def test_libraries_are_not_executed_at_page_load():
    html = Path(app.TEMPLATE_PATH).read_text(encoding='utf-8')
    assert '<script type="text/plain" id="embedded-katex-js">' in html
    assert '<script type="text/plain" id="embedded-mermaid-js">' in html
    assert html.count('<style id="embedded-katex-css">') == 1
    assert html.count('<style id="embedded-fonts">') == 1


def test_plain_document_does_not_load_katex_or_mermaid(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    url = Path(app.build_html(PLAIN, 'plain.md', str(tmp_path))).as_uri()
    with playwright.sync_playwright() as p:
        browser = _browser(p)
        try:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_function("document.querySelector('#readerContent h1') !== null")
            page.wait_for_timeout(300)
            state = page.evaluate("() => ({katex: typeof katex, mermaid: typeof mermaid})")
        finally:
            browser.close()
    assert state == {'katex': 'undefined', 'mermaid': 'undefined'}


def test_rich_document_renders_math_and_diagram(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    url = Path(app.build_html(RICH, 'rich.md', str(tmp_path))).as_uri()
    with playwright.sync_playwright() as p:
        browser = _browser(p)
        try:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_function("document.querySelector('#readerContent pre.mermaid svg') !== null", timeout=15000)
            page.wait_for_function("document.querySelectorAll('#readerContent .katex').length >= 2")
            state = page.evaluate(
                """() => ({
                  katex: typeof katex, mermaid: typeof mermaid,
                  displayMath: document.querySelectorAll('#readerContent .katex-display').length,
                  libText: document.getElementById('embedded-mermaid-js').textContent.length
                })"""
            )
            # Switching modes re-renders without loading the libraries again
            page.evaluate("() => { setMode('edit'); setMode('read'); }")
            page.wait_for_function("document.querySelector('#readerContent pre.mermaid svg') !== null", timeout=15000)
            scripts = page.evaluate("() => document.head.querySelectorAll('script:not([type])').length")
        finally:
            browser.close()
    assert state['katex'] == 'object' and state['mermaid'] == 'object'
    assert state['displayMath'] == 1
    assert state['libText'] == 0
    assert scripts == 2  # one injected KaTeX + one injected Mermaid


def test_template_export_embeds_fonts_and_katex_css(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    url = Path(app.build_html(RICH, 'rich.md', str(tmp_path))).as_uri()
    with playwright.sync_playwright() as p:
        browser = _browser(p)
        try:
            page = browser.new_page(accept_downloads=True)
            page.goto(url)
            page.wait_for_function("document.querySelector('#readerContent h1') !== null")
            with page.expect_download() as dl:
                page.evaluate("() => document.querySelector('#exportHTML').click()")
            exported = Path(dl.value.path()).read_text(encoding='utf-8')
        finally:
            browser.close()
    assert '<style id="embedded-fonts">' in exported
    assert '<style id="embedded-katex-css">' in exported and 'KaTeX_AMS' in exported
    assert "/* cyrillic subset */" in exported
