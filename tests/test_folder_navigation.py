import json
import os
from pathlib import Path

import pytest

import app


def test_list_sibling_files_returns_supported_files_with_current_marker(tmp_path):
    current = tmp_path / '2.md'
    current.write_text('# Current\n', encoding='utf-8')
    next_file = tmp_path / '10.md'
    next_file.write_text('# Next\n', encoding='utf-8')
    notes = tmp_path / 'notes.markdown'
    notes.write_text('# Notes\n', encoding='utf-8')
    readme = tmp_path / 'readme.txt'
    readme.write_text('Readme\n', encoding='utf-8')
    (tmp_path / 'image.png').write_bytes(b'not markdown')
    (tmp_path / 'subdir').mkdir()

    api = app.Api()
    api._current_path = str(current)

    result = api.list_sibling_files()

    assert result['ok'] is True
    assert result['folder'] == str(tmp_path)
    names = [item['name'] for item in result['files']]
    assert names == ['2.md', '10.md', 'notes.markdown', 'readme.txt']
    current_items = [item for item in result['files'] if item['is_current']]
    assert len(current_items) == 1
    assert current_items[0]['path'] == str(current)
    assert all('size' in item and 'mtime' in item for item in result['files'])


def test_list_sibling_files_without_current_file_returns_empty_state():
    api = app.Api()

    assert api.list_sibling_files() == {'ok': False, 'reason': 'no current file'}


def test_open_file_path_reads_supported_file_and_updates_current_path(tmp_path):
    target = tmp_path / 'chapter.md'
    target.write_text('# Chapter\n\nBody\n', encoding='utf-8')

    api = app.Api()
    result = api.open_file_path(str(target))

    assert result == {
        'ok': True,
        'content': '# Chapter\n\nBody\n',
        'name': 'chapter.md',
        'path': str(target),
    }
    assert api.current_path == str(target)
    assert api._file_mtime == os.path.getmtime(target)


def test_open_file_path_rejects_missing_and_unsupported_files(tmp_path):
    image = tmp_path / 'image.png'
    image.write_bytes(b'png')

    api = app.Api()

    assert api.open_file_path(str(tmp_path / 'missing.md')) == {
        'ok': False,
        'reason': 'not found',
        'path': str(tmp_path / 'missing.md'),
    }
    assert api.open_file_path(str(image)) == {
        'ok': False,
        'reason': 'unsupported file type',
        'path': str(image),
    }


def test_template_and_bridge_contain_folder_navigation_contract():
    html = Path(app.TEMPLATE_PATH).read_text(encoding='utf-8')

    assert 'id="btnFolderNav"' in html
    assert 'id="folderPanel"' in html
    assert 'id="folderFileList"' in html
    assert 'id="folderFileFilter"' in html
    assert 'list_sibling_files' in app.BRIDGE_JS
    assert 'open_file_path' in app.BRIDGE_JS
    assert '.folder-panel, .folder-panel-scrim' in app.BRIDGE_JS


def test_playwright_folder_panel_lists_and_opens_sibling_file(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')

    current = tmp_path / '0088-current.md'
    current.write_text('# Current\n\nCurrent body\n', encoding='utf-8')
    next_file = tmp_path / '0089-next.md'
    next_file.write_text('# Next\n\nOpened body\n', encoding='utf-8')
    (tmp_path / 'image.png').write_bytes(b'png')

    html_path = app.build_html(current.read_text(encoding='utf-8'), current.name, str(tmp_path))
    files_payload = [
        {
            'name': current.name,
            'path': str(current),
            'is_current': True,
            'size': current.stat().st_size,
            'mtime': current.stat().st_mtime,
        },
        {
            'name': next_file.name,
            'path': str(next_file),
            'is_current': False,
            'size': next_file.stat().st_size,
            'mtime': next_file.stat().st_mtime,
        },
    ]

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
            window.__openedFolderPath = null;
            window.pywebview = {
              api: {
                list_sibling_files: async function(){
                  return {ok: true, folder: %s, files: %s};
                },
                open_file_path: async function(path){
                  window.__openedFolderPath = path;
                  return {
                    ok: true,
                    content: '# Next\\n\\nOpened body\\n',
                    name: '0089-next.md',
                    path: path
                  };
                }
              }
            };
            """ % (json.dumps(str(tmp_path)), json.dumps(files_payload))
        )
        page.goto(Path(html_path).as_uri())
        page.wait_for_selector('#btnFolderNav')

        page.locator('#btnFolderNav').click()
        page.wait_for_selector('#folderPanel.open')
        page.locator('#folderFileFilter').fill('0089')

        assert page.locator('#folderFileList').get_by_text('0089-next.md').is_visible()
        assert page.locator('#folderFileList').get_by_text('0088-current.md').count() == 0

        page.locator('#folderFileList').get_by_text('0089-next.md').click()
        page.wait_for_function("document.querySelector('#fn').textContent.includes('0089-next.md')")
        page.wait_for_function("document.querySelector('#readerContent').textContent.includes('Opened body')")
        opened = page.evaluate('window.__openedFolderPath')
        browser.close()

    assert opened == str(next_file)
