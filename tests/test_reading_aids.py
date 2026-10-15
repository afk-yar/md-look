"""Reading aids: text zoom, inline code open/copy, change marks since the previous view,
remembered reading position, sticky table header, and the Python side (state, open_path)."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import app


PARAS = '\n\n'.join(f'Абзац номер {i}, достаточно длинный, чтобы страница прокручивалась. ' * 3 for i in range(60))
DOC = f"""# Документ

Пути: `E:\\_AI\\dev\\protocol.md`, `README.md`, `cfe/Module.bsl:3-15`. Не пути: `claude-opus-5-5`, `effort: low`.

Абзац, который потом изменится.

| Колонка | Значение |
|---|---|
| a | 1 |

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
    return Path(app.build_html(DOC, 'aids.md', str(tmp_path))).as_uri()


def open_page(browser, url, ctx=None):
    ctx = ctx or browser.new_context(viewport={'width': 1400, 'height': 800})
    ctx.grant_permissions(['clipboard-read', 'clipboard-write'])
    pg = ctx.new_page()
    pg.goto(url)
    pg.wait_for_selector('#readerContent p')
    pg.wait_for_timeout(1700)  # no pywebview here: state falls back to localStorage after the wait
    return pg


def test_text_zoom_keys(browser, doc_url):
    pg = open_page(browser, doc_url)
    size = "getComputedStyle(document.querySelector('#readerContent p')).fontSize"
    pg.keyboard.press('Control+Equal')
    assert pg.evaluate(size) == '18.15px'
    pg.keyboard.press('Control+Minus')
    pg.keyboard.press('Control+Minus')
    assert pg.evaluate(size) == '14.85px'
    pg.keyboard.press('Control+0')
    assert pg.evaluate(size) == '16.5px'


def test_inline_code_classified_as_path_or_copy(browser, doc_url):
    pg = open_page(browser, doc_url)
    kinds = pg.evaluate("""Object.fromEntries([...document.querySelectorAll('#readerContent p code')]
      .map(c=>[c.textContent,c.classList.contains('md-path')?'path':(c.classList.contains('md-copy')?'copy':'')]))""")
    assert kinds == {
        'E:\\_AI\\dev\\protocol.md': 'path', 'README.md': 'path', 'cfe/Module.bsl:3-15': 'path',
        'claude-opus-5-5': 'copy', 'effort: low': 'copy',
    }


def test_click_copies_code_and_path_falls_back_to_copy(browser, doc_url):
    pg = open_page(browser, doc_url)
    pg.click("#readerContent code:text('claude-opus-5-5')")
    pg.wait_for_timeout(200)
    assert pg.evaluate('navigator.clipboard.readText()') == 'claude-opus-5-5'
    assert pg.evaluate("document.getElementById('mdToast').textContent") == 'Copied'
    # outside the app there is no open_path: a path click copies instead
    pg.click("#readerContent code:text('README.md')")
    pg.wait_for_timeout(200)
    assert pg.evaluate('navigator.clipboard.readText()') == 'README.md'


def test_changed_blocks_marked_against_previous_view(browser, doc_url):
    pg = open_page(browser, doc_url)
    assert pg.evaluate("document.querySelectorAll('.md-changed').length") == 0
    pg.evaluate("rawMd=rawMd.replace('потом изменится','уже изменился'); setMode('read')")
    pg.wait_for_timeout(300)
    changed = pg.evaluate("[...document.querySelectorAll('.md-changed')].map(e=>e.textContent)")
    assert changed == ['Абзац, который уже изменился.']


def test_reading_position_restored_on_reopen(browser, doc_url):
    ctx = browser.new_context(viewport={'width': 1400, 'height': 800})
    pg = open_page(browser, doc_url, ctx)
    pg.evaluate("document.querySelector('#reader').scrollTop=1500")
    pg.wait_for_timeout(700)
    pg2 = open_page(browser, doc_url, ctx)
    assert abs(pg2.evaluate("document.querySelector('#reader').scrollTop") - 1500) <= 2


def test_table_header_is_sticky(browser, doc_url):
    pg = open_page(browser, doc_url)
    assert pg.evaluate("getComputedStyle(document.querySelector('#readerContent thead th')).position") == 'sticky'


def test_state_roundtrip_and_file_cap(tmp_path, monkeypatch):
    monkeypatch.setenv('APPDATA', str(tmp_path))
    monkeypatch.setattr(app, 'STATE_MAX_FILES', 2)
    api = app.Api()
    api.state_set('zoom', 1.2)
    for i in range(3):
        api.state_set(f'file:{i}', {'scroll': i})
    assert api.state_get('zoom') == 1.2
    assert api.state_get('file:0') is None
    assert api.state_get('file:2')['scroll'] == 2


def test_open_path_resolves_relative_and_never_runs_files(tmp_path):
    (tmp_path / 'note.md').write_text('# n', encoding='utf-8')
    (tmp_path / 'run.bat').write_text('echo hi', encoding='utf-8')
    api = app.Api()
    with patch.object(app, '_create_window') as cw, patch.object(app.subprocess, 'Popen') as popen:
        assert api.open_path('note.md:12', str(tmp_path))['action'] == 'mdlook'
        cw.assert_called_once_with(str(tmp_path / 'note.md'))
        res = api.open_path(str(tmp_path / 'run.bat'))
        assert res['action'] == 'reveal'
        assert popen.call_args[0][0] == ['explorer', '/select,', str(tmp_path / 'run.bat')]
        assert api.open_path('missing.md', str(tmp_path))['ok'] is False
