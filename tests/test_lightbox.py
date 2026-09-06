"""Full-size viewer (lightbox) for images and mermaid diagrams."""
import base64
from pathlib import Path

import pytest

import app


PNG_2X2 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mP8z8BQz0AEYBxV'
    'SF+FABJADveWkH6oAAAAAElFTkSuQmCC'
)

DOC = '\n'.join([
    '# Lightbox probe',
    '',
    '![First](one.png)',
    '',
    '![Second](two.png)',
    '',
    '![Broken](missing.png)',
    '',
    '[![Linked](one.png)](https://example.com/)',
    '',
    '```mermaid',
    'graph TD',
    '    A[Start] --> B[End]',
    '```',
    '',
])


def test_template_defines_lightbox_markup_and_state():
    html = Path(app.TEMPLATE_PATH).read_text(encoding='utf-8')

    assert 'id="lightbox"' in html
    assert 'id="lbStage"' in html
    assert 'function lbCollect' in html
    assert 'window.mdlookCloseLightbox' in html
    # line-height:0 on the stage collapses mermaid foreignObject labels
    assert '.lb-content{position:absolute;top:0;left:0;transform-origin:0 0;will-change:transform}' in html


def test_html_export_drops_the_lightbox_overlay():
    """Export strips all <script>, so an overlay left in 'active' state would
    be stuck over the exported document with no way to close it."""
    assert '.auto-scroll-btn, .lightbox' in app.BRIDGE_JS


@pytest.fixture(scope='module')
def lightbox_page(tmp_path_factory):
    playwright = pytest.importorskip('playwright.sync_api')

    tmp_path = tmp_path_factory.mktemp('lightbox')
    for name in ('one.png', 'two.png'):
        (tmp_path / name).write_bytes(base64.b64decode(PNG_2X2))
    html_path = app.build_html(DOC, 'lightbox-probe.md', str(tmp_path))

    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error as exc:
            if 'Executable doesn' in str(exc) or 'playwright install' in str(exc):
                pytest.skip('Playwright Chromium browser is not installed')
            raise
        page = browser.new_page()
        page.goto(Path(html_path).as_uri())
        page.wait_for_selector('.mermaid-wrap svg')
        yield page
        browser.close()


def test_nested_image_link_renders_image_not_placeholder(lightbox_page):
    """[![alt](img)](url) must restore both HTML slots, not leave 'HTML0' text."""
    page = lightbox_page

    assert page.locator('#readerContent a img[alt="Linked"]').count() == 1
    reader_text = page.locator('#readerContent').inner_text()
    assert 'HTML0' not in reader_text


def test_collect_skips_broken_and_linked_images(lightbox_page):
    items = lightbox_page.evaluate(
        "lbCollect(document.querySelector('#readerContent'))"
        ".map(i => i.kind + ':' + (i.kind === 'img' ? i.node.alt : 'diagram'))"
    )

    assert items == ['img:First', 'img:Second', 'svg:diagram']


def test_click_opens_carousel_and_arrows_navigate(lightbox_page):
    page = lightbox_page

    page.locator('#readerContent img[alt="Second"]').click()
    assert page.evaluate('lbState.open') is True
    assert page.locator('#lbCounter').inner_text() == '2 / 3'
    assert page.locator('#lbCaption').inner_text() == 'Second'

    page.keyboard.press('ArrowRight')
    assert page.locator('#lbCounter').inner_text() == '3 / 3'
    assert page.evaluate("document.querySelector('#lbContent').firstElementChild.tagName") == 'svg'

    # wraps around
    page.keyboard.press('ArrowRight')
    assert page.locator('#lbCounter').inner_text() == '1 / 3'

    page.keyboard.press('ArrowLeft')
    assert page.locator('#lbCounter').inner_text() == '3 / 3'

    page.keyboard.press('Escape')
    assert page.evaluate('lbState.open') is False


def test_zoom_keys_and_actual_size(lightbox_page):
    page = lightbox_page

    page.locator('#readerContent img[alt="First"]').click()
    fit = page.evaluate('lbState.fit')

    page.keyboard.press('+')
    assert page.evaluate('lbState.scale') == pytest.approx(fit * 1.25)

    page.keyboard.press('0')
    assert page.evaluate('lbState.scale') == pytest.approx(fit)

    page.keyboard.press('1')
    assert page.evaluate('lbState.scale') == pytest.approx(1)

    page.keyboard.press('Escape')


def test_linked_image_click_does_not_open_lightbox(lightbox_page):
    page = lightbox_page
    page.evaluate(
        "document.addEventListener('click', e => {"
        "  const a = e.target.closest && e.target.closest('a[href]');"
        "  if (a) e.preventDefault();"
        "}, true)"
    )

    page.locator('#readerContent a img[alt="Linked"]').click()

    assert page.evaluate('lbState.open') is False


def test_mermaid_clone_keeps_label_typography(lightbox_page):
    """The clone must inherit the same line-height as the rendered diagram,
    otherwise foreignObject labels collapse and the text is clipped."""
    page = lightbox_page

    metrics = page.evaluate(
        """(() => {
          function read(svg){
            const fo = svg.querySelector('foreignObject');
            if(!fo) return null;
            const cs = getComputedStyle(fo.querySelector('div'));
            return [cs.lineHeight, cs.fontSize];
          }
          const before = read(document.querySelector('.mermaid-wrap svg'));
          document.querySelector('.mermaid-wrap').click();
          const after = read(document.querySelector('#lbContent svg'));
          return {before, after};
        })()"""
    )

    assert metrics['before'] is not None
    assert metrics['before'] == metrics['after']
    page.keyboard.press('Escape')


def test_backdrop_click_closes_but_content_click_does_not(lightbox_page):
    page = lightbox_page

    page.locator('#readerContent img[alt="First"]').click()
    assert page.evaluate('lbState.open') is True

    page.locator('#lbContent img').click()
    assert page.evaluate('lbState.open') is True

    stage = page.locator('#lbStage').bounding_box()
    page.mouse.click(stage['x'] + 8, stage['y'] + stage['height'] - 8)
    assert page.evaluate('lbState.open') is False


def test_mode_switch_closes_lightbox(lightbox_page):
    page = lightbox_page

    page.locator('#readerContent img[alt="First"]').click()
    assert page.evaluate('lbState.open') is True

    page.evaluate("setMode('edit')")
    assert page.evaluate('lbState.open') is False

    page.wait_for_selector('#preview img')
    page.locator('#preview img[alt="First"]').click()
    assert page.evaluate('lbState.open') is True

    page.keyboard.press('Escape')
    page.evaluate("setMode('read')")
    page.wait_for_selector('#readerContent img')
