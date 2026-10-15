"""Host page: one shared template page per version, the document comes from Api.initial_document."""
import json
from pathlib import Path

import pytest

import app


def _open_host_page(p, doc):
    try:
        browser = p.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        if 'Executable doesn' in str(exc) or 'playwright install' in str(exc):
            pytest.skip('Playwright Chromium browser is not installed')
        raise
    page = browser.new_page()
    page.add_init_script(
        """
        localStorage.setItem('__probe_draft_marker', '1');
        window.__initialCalls = 0;
        var impl = {
          initial_document: async function(){ window.__initialCalls++; return %s; }
        };
        window.pywebview = {api: new Proxy(impl, {
          get: function(t, k){ return k in t ? t[k] : async function(){ return null; }; }
        })};
        """ % json.dumps(doc)
    )
    page.goto(Path(app._host_page_path()).as_uri())
    page.wait_for_function('window.__mdlookDocumentRequested === true')
    page.wait_for_function("document.querySelector('#readerContent h1') !== null")
    return browser, page


def test_host_page_renders_document_from_api(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    doc_path = tmp_path / 'Глава.md'
    doc = {
        'ok': True,
        'content': '# Заголовок\n\nТекст с </script> внутри.\n',
        'name': 'Глава.md',
        'path': str(doc_path),
    }
    with playwright.sync_playwright() as p:
        browser, page = _open_host_page(p, doc)
        try:
            state = page.evaluate(
                """() => ({
                  h1: document.querySelector('#readerContent h1').textContent,
                  body: document.querySelector('#readerContent').textContent,
                  mode: currentMode,
                  name: fileName,
                  path: filePath,
                  editor: document.querySelector('#editor').value,
                  folder: document.querySelector('#folderPath').textContent,
                  folderShown: document.querySelector('#folderSection').style.display !== 'none',
                  calls: window.__initialCalls,
                  marker: localStorage.getItem('__probe_draft_marker')
                })"""
            )
        finally:
            browser.close()

    assert state['h1'] == 'Заголовок'
    assert 'Текст с </script> внутри.' in state['body']
    assert state['mode'] == 'read'
    assert state['name'] == 'Глава.md'
    assert state['path'] == str(doc_path)
    assert state['editor'] == doc['content']
    assert state['folder'] == str(tmp_path)
    assert state['folderShown'] is True
    assert state['calls'] == 1
    assert state['marker'] == '1'


def test_host_page_example_document_hides_folder_section():
    playwright = pytest.importorskip('playwright.sync_api')
    doc = {'ok': True, 'content': '# Example\n', 'name': 'example.md', 'path': ''}
    with playwright.sync_playwright() as p:
        browser, page = _open_host_page(p, doc)
        try:
            state = page.evaluate(
                """() => ({
                  name: fileName, path: filePath, mode: currentMode,
                  folderShown: document.querySelector('#folderSection').style.display !== 'none'
                })"""
            )
        finally:
            browser.close()

    assert state == {'name': 'example.md', 'path': '', 'mode': 'read', 'folderShown': False}


def test_host_page_has_no_document_and_marks_host_mode():
    html = Path(app._host_page_path()).read_text(encoding='utf-8')
    assert html.index('<script>window.__mdlookHost=1;</script>') < html.index('id="md-data"')
    assert 'MDCONTENT_PLACEHOLDER' in html
