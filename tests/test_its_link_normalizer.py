from pathlib import Path

from tools.normalize_its_book_links import normalize_root


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def test_content_links_are_rewritten_to_relative_markdown_files(tmp_path):
    root = tmp_path / 'books'
    book = root / 'fakebook'
    write(
        book / '_toc.md',
        '\n'.join([
            '# Оглавление',
            '*   **Root**',
            '    *   [MARKDOWN](./markdown/0001_Root.md)',
            '    *   **Chapter 2. Target Demo**',
            '        *   [MARKDOWN](./markdown/0002_Target_(Demo).md)',
        ]),
    )
    write(
        book / 'markdown' / '0001_Root.md',
        '\n'.join([
            '# Root',
            '',
            '[Target Demo](/db/fakebook/content/42/hdoc)',
            '[Different label](/db/fakebook/content/42/hdoc/h7)',
            '[External](/db/otherbook/content/42/hdoc)',
        ]),
    )
    write(book / 'markdown' / '0002_Target_(Demo).md', '# Chapter 2. Target Demo\n')

    result = normalize_root(root, apply=True)

    text = (book / 'markdown' / '0001_Root.md').read_text(encoding='utf-8')
    assert '[Target Demo](<0002_Target_(Demo).md>)' in text
    assert '[Different label](<0002_Target_(Demo).md>)' in text
    assert '[External](/db/otherbook/content/42/hdoc)' in text
    assert result.totals['content_rewritten'] == 2
    assert result.totals['external_db_skipped'] == 1


def test_bookmark_links_add_stable_anchor_to_unique_heading(tmp_path):
    root = tmp_path / 'books'
    book = root / 'fakebook'
    write(
        book / '_toc.md',
        '\n'.join([
            '# Оглавление',
            '*   **Root**',
            '    *   [MARKDOWN](./markdown/0001_Root.md)',
            '*   **Settings**',
            '    *   [MARKDOWN](./markdown/0002_Settings.md)',
        ]),
    )
    write(
        book / 'markdown' / '0001_Root.md',
        'См. [Сценарии планирования](/db/fakebook/bookmark/planningsettings/PlanningScenarios).\n',
    )
    write(
        book / 'markdown' / '0002_Settings.md',
        '# Settings\n\n## Сценарии планирования\n\nText\n',
    )

    result = normalize_root(root, apply=True)

    source = (book / 'markdown' / '0001_Root.md').read_text(encoding='utf-8')
    target = (book / 'markdown' / '0002_Settings.md').read_text(encoding='utf-8')
    assert '[Сценарии планирования](0002_Settings.md#planning-scenarios)' in source
    assert '## Сценарии планирования <a id="planning-scenarios"></a>' in target
    assert result.totals['bookmark_rewritten'] == 1
    assert result.totals['anchors_added'] == 1


def test_ambiguous_bookmark_heading_is_left_unchanged(tmp_path):
    root = tmp_path / 'books'
    book = root / 'fakebook'
    write(book / '_toc.md', '# Оглавление\n')
    write(
        book / 'markdown' / '0001_Root.md',
        'См. [Сценарии планирования](/db/fakebook/bookmark/planningsettings/PlanningScenarios).\n',
    )
    write(book / 'markdown' / '0002_A.md', '## Сценарии планирования\n')
    write(book / 'markdown' / '0003_B.md', '## Сценарии планирования\n')

    result = normalize_root(root, apply=True)

    source = (book / 'markdown' / '0001_Root.md').read_text(encoding='utf-8')
    assert '[Сценарии планирования](/db/fakebook/bookmark/planningsettings/PlanningScenarios)' in source
    assert result.totals['bookmark_rewritten'] == 0
    assert result.totals['ambiguous_bookmark_label'] == 1


def test_bookmark_does_not_target_low_level_incidental_heading(tmp_path):
    root = tmp_path / 'books'
    book = root / 'fakebook'
    write(book / '_toc.md', '# Оглавление\n')
    write(
        book / 'markdown' / '0001_Root.md',
        '[Склад](/db/fakebook/bookmark/warehouse/Warehouse)\n',
    )
    write(book / 'markdown' / '0002_Glossary.md', '# Glossary\n\n### Склад\n')

    result = normalize_root(root, apply=True)

    source = (book / 'markdown' / '0001_Root.md').read_text(encoding='utf-8')
    assert '[Склад](/db/fakebook/bookmark/warehouse/Warehouse)' in source
    assert result.totals['bookmark_rewritten'] == 0
    assert result.totals['unsafe_bookmark_heading_level'] == 1


def test_content_link_is_not_rewritten_to_duplicate_title_when_toc_target_is_missing(tmp_path):
    root = tmp_path / 'books'
    book = root / 'fakebook'
    write(
        book / '_toc.md',
        '\n'.join([
            '# Оглавление',
            '*   **5. Обеспечение потребностей**',
            '    *   [MARKDOWN](./markdown/0002_Missing.md)',
            '*   **Обеспечение потребностей**',
            '    *   [MARKDOWN](./markdown/0999_Обеспечение_потребностей.md)',
        ]),
    )
    write(
        book / 'markdown' / '0001_Root.md',
        '[Обеспечение потребностей](/db/fakebook/content/61/hdoc)\n',
    )
    write(
        book / 'markdown' / '0999_Обеспечение_потребностей.md',
        '# Обеспечение потребностей\n',
    )

    result = normalize_root(root, apply=True)

    source = (book / 'markdown' / '0001_Root.md').read_text(encoding='utf-8')
    assert '[Обеспечение потребностей](/db/fakebook/content/61/hdoc)' in source
    assert result.totals['content_rewritten'] == 0
    assert result.totals['missing_toc_content_target'] == 1


def test_content_link_uses_numbered_existing_toc_title_over_unnumbered_missing_duplicate(tmp_path):
    root = tmp_path / 'books'
    book = root / 'fakebook'
    write(
        book / '_toc.md',
        '\n'.join([
            '# Оглавление',
            '*   **2.1. Позиционирование товарного планирования**',
            '    *   [MARKDOWN](./markdown/0089_2.1._Позиционирование_товарного_планирования.md)',
            '*   **Позиционирование товарного планирования**',
            '    *   [MARKDOWN](./markdown/2080_Позиционирование_товарного_планирования.md)',
        ]),
    )
    write(
        book / 'markdown' / '0088_2._Планирование.md',
        '[Позиционирование товарного планирования](/db/fakebook/content/12/hdoc)\n',
    )
    write(
        book / 'markdown' / '0089_2.1._Позиционирование_товарного_планирования.md',
        '# 2.1. Позиционирование товарного планирования\n',
    )

    result = normalize_root(root, apply=True)

    source = (book / 'markdown' / '0088_2._Планирование.md').read_text(encoding='utf-8')
    assert (
        '[Позиционирование товарного планирования]'
        '(0089_2.1._Позиционирование_товарного_планирования.md)'
    ) in source
    assert result.totals['content_rewritten'] == 1


def test_bookmark_is_not_rewritten_to_duplicate_title_when_toc_target_is_missing(tmp_path):
    root = tmp_path / 'books'
    book = root / 'fakebook'
    write(
        book / '_toc.md',
        '\n'.join([
            '# Оглавление',
            '*   **5. Обеспечение потребностей**',
            '    *   [MARKDOWN](./markdown/0002_Missing.md)',
            '*   **Обеспечение потребностей**',
            '    *   [MARKDOWN](./markdown/0999_Обеспечение_потребностей.md)',
        ]),
    )
    write(
        book / 'markdown' / '0001_Root.md',
        '[Обеспечение потребностей](/db/fakebook/bookmark/provision/Provision)\n',
    )
    write(
        book / 'markdown' / '0999_Обеспечение_потребностей.md',
        '# Обеспечение потребностей\n',
    )

    result = normalize_root(root, apply=True)

    source = (book / 'markdown' / '0001_Root.md').read_text(encoding='utf-8')
    assert '[Обеспечение потребностей](/db/fakebook/bookmark/provision/Provision)' in source
    assert result.totals['bookmark_rewritten'] == 0
    assert result.totals['missing_toc_bookmark_target'] == 1


def test_bookmark_prefers_existing_anchor_from_tail_over_generic_link_label(tmp_path):
    root = tmp_path / 'books'
    book = root / 'fakebook'
    write(book / '_toc.md', '# Оглавление\n')
    write(
        book / 'markdown' / '0001_Root.md',
        '[сценарий](/db/fakebook/bookmark/planningsettings/PlanningScenarios)\n',
    )
    write(
        book / 'markdown' / '0002_Settings.md',
        '## Сценарии планирования <a id="planning-scenarios"></a>\n',
    )
    write(
        book / 'markdown' / '0003_Glossary.md',
        '# Сценарий\n\n## Сценарий\n',
    )

    result = normalize_root(root, apply=True)

    source = (book / 'markdown' / '0001_Root.md').read_text(encoding='utf-8')
    assert '[сценарий](0002_Settings.md#planning-scenarios)' in source
    assert result.totals['bookmark_rewritten'] == 1
    assert result.totals['anchors_added'] == 0


def test_rewritten_link_label_newlines_are_collapsed_to_valid_inline_markdown(tmp_path):
    root = tmp_path / 'books'
    book = root / 'fakebook'
    write(book / '_toc.md', '# Оглавление\n')
    write(
        book / 'markdown' / '0001_Root.md',
        '[вид\nплана](/db/fakebook/bookmark/planningsettings/PlansTypes)\n',
    )
    write(
        book / 'markdown' / '0002_Settings.md',
        '## Виды планов <a id="plans-types"></a>\n',
    )

    normalize_root(root, apply=True)

    source = (book / 'markdown' / '0001_Root.md').read_text(encoding='utf-8')
    assert '[вид плана](0002_Settings.md#plans-types)' in source
