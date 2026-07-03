import base64
import os
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import pytest

import app


PNG_1X1 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9Q'
    'DwADhgGAWjR9awAAAABJRU5ErkJggg=='
)


def test_template_uses_md_folder_url_resolver_for_links_and_images():
    html = Path(app.TEMPLATE_PATH).read_text(encoding='utf-8')

    assert 'function resolveMdUrl' in html
    assert "resolveMdUrl(src,'image')" in html
    assert "resolveMdUrl(href,'link')" in html
    assert '<img src="$2" alt="$1">' not in html
    assert "target=\"_blank\" rel=\"noopener\">'+label" not in html
    assert '$1<a href="$2" target="_blank" rel="noopener">$2</a>' not in html


def test_bridge_routes_non_hash_links_to_python_api():
    assert 'api.open_url' in app.BRIDGE_JS
    assert "closest('a[href]')" in app.BRIDGE_JS
    assert "a[href^=\"#\"]" in app.BRIDGE_JS


def test_open_url_opens_markdown_file_in_mdlook_window(tmp_path):
    note = tmp_path / 'linked-note.md'
    note.write_text('# Linked\n\n## Target\n', encoding='utf-8')
    url = note.as_uri() + '#target'

    api = app.Api()
    with patch.object(app, '_create_window') as create_window:
        result = api.open_url(url)

    assert result == {'ok': True, 'action': 'mdlook', 'path': str(note), 'target': 'target'}
    create_window.assert_called_once_with(str(note), 'target')


def test_open_url_rejects_blocked_scheme():
    api = app.Api()

    assert api.open_url('javascript:alert(1)') == {'ok': False, 'reason': 'blocked scheme'}


def test_open_url_routes_external_url_to_system_browser():
    api = app.Api()

    with patch('webbrowser.open', return_value=True) as open_browser:
        result = api.open_url('https://example.com/docs')

    assert result == {'ok': True, 'action': 'external', 'url': 'https://example.com/docs'}
    open_browser.assert_called_once_with('https://example.com/docs')


def test_playwright_generated_html_resolves_local_md_link_and_image(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')

    (tmp_path / 'linked-note.md').write_text('# Linked\n\n## Target\n', encoding='utf-8')
    image = tmp_path / 'local image.png'
    image.write_bytes(base64.b64decode(PNG_1X1))

    md = '\n'.join([
        '# Repro',
        '',
        '[Linked note](linked-note.md#target)',
        '',
        '![Local image](local image.png)',
    ])
    html_path = app.build_html(md, 'links-images-repro.md', str(tmp_path))

    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error as exc:
            if 'Executable doesn' in str(exc) or 'playwright install' in str(exc):
                pytest.skip('Playwright Chromium browser is not installed')
            raise
        page = browser.new_page()
        page.add_init_script(
            """
            window.__mdlookOpened = [];
            window.pywebview = {
              api: {
                open_url: async function(url){
                  window.__mdlookOpened.push(url);
                  return {ok: true, url: url};
                }
              }
            };
            """
        )
        page.goto(Path(html_path).as_uri())
        page.wait_for_selector('#readerContent img')

        link_href = page.locator('a', has_text='Linked note').get_attribute('href')
        img_src = page.locator('#readerContent img').get_attribute('src')
        natural_width = page.locator('#readerContent img').evaluate('img => img.naturalWidth')
        windows_image_url = page.evaluate(
            "window.mdlookResolveMdUrl('C:\\\\Temp\\\\file name.png', 'image')"
        )

        page.locator('a', has_text='Linked note').click()
        page.wait_for_function('window.__mdlookOpened.length === 1')
        opened = page.evaluate('window.__mdlookOpened[0]')
        browser.close()

    expected_link = (tmp_path / 'linked-note.md').as_uri() + '#target'
    expected_image = image.as_uri()

    assert urllib.parse.unquote(link_href) == urllib.parse.unquote(expected_link)
    assert urllib.parse.unquote(img_src) == urllib.parse.unquote(expected_image)
    assert natural_width > 0
    assert windows_image_url == 'file:///C:/Temp/file%20name.png'
    assert urllib.parse.unquote(opened) == urllib.parse.unquote(expected_link)
