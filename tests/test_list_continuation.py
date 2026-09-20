"""Wrapped (indented) continuation lines of a list item must stay inside the <li>
instead of closing the list and starting a stray paragraph."""
from pathlib import Path

import pytest

import app


DOC = """**Что сделать:**
1. Переписать сохранение трассы: вместо `Новый Массив(Размер)` класть массив длиной `д + 1`
   со значениями диагоналей от минус `д` до плюс `д`.
2. Поправить обратный проход.
3. Добавить порог: если сумма строк больше N, автослияние не пытаться, строка
   остается ручной. N подобрать по замеру.

- пункт один
  продолжение один
- [ ] задача
  продолжение задачи

Абзац после списка.
"""


@pytest.fixture(scope='module')
def page(tmp_path_factory):
    playwright = pytest.importorskip('playwright.sync_api')
    tmp_path = tmp_path_factory.mktemp('listcont')
    html_path = app.build_html(DOC, 'list-probe.md', str(tmp_path))
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error as exc:
            if 'Executable doesn' in str(exc) or 'playwright install' in str(exc):
                pytest.skip('Playwright Chromium browser is not installed')
            raise
        pg = browser.new_page()
        pg.goto(Path(html_path).as_uri())
        pg.wait_for_selector('#readerContent ol')
        yield pg
        browser.close()


def test_ordered_list_is_one_ol_with_three_items(page):
    assert page.evaluate("document.querySelectorAll('#readerContent ol').length") == 1
    assert page.evaluate("document.querySelectorAll('#readerContent ol > li').length") == 3


def test_continuation_text_lives_inside_li(page):
    texts = page.evaluate(
        "[...document.querySelectorAll('#readerContent ol > li')].map(li=>li.textContent)")
    assert 'со значениями диагоналей' in texts[0]
    assert texts[2].startswith('Добавить порог') and 'остается ручной' in texts[2]
    # No stray paragraph made from a continuation line
    paras = page.evaluate(
        "[...document.querySelectorAll('#readerContent p')].map(p=>p.textContent)")
    assert not any(t.startswith('со значениями') or t.startswith('остается ручной') for t in paras)
    assert 'Абзац после списка.' in paras


def test_unordered_and_task_items_keep_continuations(page):
    ul_texts = page.evaluate(
        "[...document.querySelectorAll('#readerContent ul li')].map(li=>li.textContent)")
    assert any('пункт один продолжение один' in t for t in ul_texts)
    assert any('задача продолжение задачи' in t for t in ul_texts)
