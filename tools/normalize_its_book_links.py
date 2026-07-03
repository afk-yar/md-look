from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_ROOT = Path(r'E:/_AI/_Доки/1С/ERP-WE/ИТС-книги')

DB_LINK_RE = re.compile(
    r'(?<!!)\[(?P<label>(?:\\.|[^\]])+)\]\((?P<url>/db/[^)\s]+)\)'
)
MULTILINE_LINK_RE = re.compile(
    r'(?<!!)\[(?P<label>(?:\\.|[^\]])*?\n(?:\\.|[^\]])*?)\]\((?P<url>[^)\s]+)\)'
)
HEADING_RE = re.compile(r'^(?P<marks>#{1,6})\s+(?P<text>.+?)\s*$')
ANCHOR_RE = re.compile(
    r'<a\s+[^>]*\bid=["\'](?P<id>[^"\']+)["\'][^>]*>\s*</a>',
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r'<[^>]+>')
TOC_TITLE_RE = re.compile(r'\*\*(?P<title>.+?)\*\*')
TOC_MARKDOWN_RE = re.compile(r'\[MARKDOWN\]\(\./markdown/(?P<file>[^)]+)\)')


@dataclass(frozen=True)
class StoredText:
    text: str
    has_bom: bool


@dataclass(frozen=True)
class Heading:
    file: Path
    line_index: int
    level: int
    text: str
    existing_ids: frozenset[str]


@dataclass(frozen=True)
class LinkOccurrence:
    file: Path
    label: str
    raw_label: str
    url: str
    link_start: int
    link_end: int
    url_start: int
    url_end: int
    line: int
    db: str
    kind: str
    content_id: str | None = None
    anchor_tail: str | None = None


@dataclass(frozen=True)
class LinkReplacement:
    file: Path
    line: int
    old_url: str
    new_url: str
    kind: str

    def as_dict(self, root: Path) -> dict[str, object]:
        return {
            'file': rel_to(self.file, root),
            'line': self.line,
            'kind': self.kind,
            'old_url': self.old_url,
            'new_url': self.new_url,
        }


@dataclass(frozen=True)
class AnchorInsertion:
    file: Path
    line_index: int
    anchor_id: str

    def as_dict(self, root: Path) -> dict[str, object]:
        return {
            'file': rel_to(self.file, root),
            'line': self.line_index + 1,
            'anchor_id': self.anchor_id,
        }


@dataclass(frozen=True)
class UnresolvedLink:
    file: Path
    line: int
    label: str
    url: str
    reason: str

    def as_dict(self, root: Path) -> dict[str, object]:
        return {
            'file': rel_to(self.file, root),
            'line': self.line,
            'reason': self.reason,
            'label': self.label,
            'url': self.url,
        }


@dataclass
class NormalizationResult:
    root: Path
    applied: bool
    totals: Counter = field(default_factory=Counter)
    replacements: list[LinkReplacement] = field(default_factory=list)
    anchors: list[AnchorInsertion] = field(default_factory=list)
    unresolved: list[UnresolvedLink] = field(default_factory=list)
    books: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            'root': str(self.root),
            'applied': self.applied,
            'totals': dict(self.totals),
            'books': self.books,
            'replacements': [item.as_dict(self.root) for item in self.replacements],
            'anchors': [item.as_dict(self.root) for item in self.anchors],
            'unresolved': [item.as_dict(self.root) for item in self.unresolved],
        }


def rel_to(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def read_stored_text(path: Path) -> StoredText:
    raw = path.read_bytes()
    has_bom = raw.startswith(b'\xef\xbb\xbf')
    if has_bom:
        raw = raw[3:]
    return StoredText(raw.decode('utf-8'), has_bom)


def write_stored_text(path: Path, stored: StoredText, text: str) -> None:
    raw = text.encode('utf-8')
    if stored.has_bom:
        raw = b'\xef\xbb\xbf' + raw
    path.write_bytes(raw)


def normalize_base(text: str) -> str:
    text = html.unescape(text)
    text = ANCHOR_RE.sub('', text)
    text = HTML_TAG_RE.sub('', text)
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = text.replace('\\_', '_')
    text = text.replace('_', ' ')
    text = text.replace('ё', 'е').replace('Ё', 'е')
    text = text.lower()
    text = re.sub(r'[^0-9a-zа-я]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def strip_leading_section_key(key: str) -> str:
    tokens = key.split()
    if not tokens:
        return key

    start = 0
    if tokens and tokens[0] in {'глава', 'главы', 'главу', 'раздел', 'подраздел'}:
        start = 1

    i = start
    seen_digit = False
    while i < len(tokens):
        token = tokens[i]
        if re.fullmatch(r'\d+[a-zа-я]?', token):
            seen_digit = True
            i += 1
            continue
        if seen_digit and re.fullmatch(r'[a-zа-я]', token):
            i += 1
            continue
        break

    if seen_digit and i < len(tokens):
        return ' '.join(tokens[i:])
    return key


def text_keys(text: str) -> set[str]:
    base = normalize_base(text)
    if not base:
        return set()
    keys = {base}
    stripped = strip_leading_section_key(base)
    if stripped:
        keys.add(stripped)
    return keys


def has_leading_section_number(text: str) -> bool:
    text = html.unescape(text).strip()
    return bool(
        re.match(r'^(?:глава\s+)?\d+(?:\.[0-9a-zA-Zа-яА-Я]+)*\.\s*.+', text, re.IGNORECASE)
        or re.match(r'^(?:глава\s+)?\d+(?:\.[0-9a-zA-Zа-яА-Я]+)+\s+.+', text, re.IGNORECASE)
    )


def clean_heading_text(raw: str) -> str:
    raw = re.sub(r'\s+#+\s*$', '', raw.strip())
    raw = ANCHOR_RE.sub('', raw)
    raw = HTML_TAG_RE.sub('', raw)
    return html.unescape(raw).strip()


def filename_title(path: Path) -> str:
    stem = re.sub(r'^\d+_', '', path.stem)
    return stem.replace('_', ' ')


def first_preamble_title(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('#'):
            return None
        return stripped.split('::', 1)[0].strip()
    return None


def parse_toc_titles(book: Path) -> dict[Path, list[str]]:
    toc = book / '_toc.md'
    if not toc.exists():
        return {}

    titles: dict[Path, list[str]] = defaultdict(list)
    pending_title: str | None = None
    text = read_stored_text(toc).text
    for line in text.splitlines():
        title_match = TOC_TITLE_RE.search(line)
        if title_match:
            pending_title = html.unescape(title_match.group('title')).strip()

        markdown_match = TOC_MARKDOWN_RE.search(line)
        if markdown_match and pending_title:
            filename = urllib.parse.unquote(markdown_match.group('file'))
            titles[book / 'markdown' / filename].append(pending_title)

    return titles


def parse_headings(path: Path, text: str) -> list[Heading]:
    headings: list[Heading] = []
    for line_index, line in enumerate(text.splitlines()):
        match = HEADING_RE.match(line)
        if not match:
            continue
        raw = match.group('text').strip()
        headings.append(
            Heading(
                file=path,
                line_index=line_index,
                level=len(match.group('marks')),
                text=clean_heading_text(raw),
                existing_ids=frozenset(anchor.group('id') for anchor in ANCHOR_RE.finditer(raw)),
            )
        )
    return headings


def parse_db_url(url: str) -> tuple[str, str, str | None, str | None]:
    parts = url.split('/')
    if len(parts) < 4 or parts[0] != '' or parts[1] != 'db':
        return '', '', None, None

    db = parts[2]
    kind = parts[3] if len(parts) > 3 else ''
    if kind == 'content' and len(parts) >= 6 and parts[5] == 'hdoc':
        return db, kind, parts[4], parts[6] if len(parts) > 6 else None
    if kind == 'bookmark' and len(parts) >= 6:
        return db, kind, None, parts[-1]
    return db, kind, None, None


def parse_links(path: Path, text: str) -> list[LinkOccurrence]:
    links: list[LinkOccurrence] = []
    for match in DB_LINK_RE.finditer(text):
        url = match.group('url')
        db, kind, content_id, anchor_tail = parse_db_url(url)
        links.append(
            LinkOccurrence(
                file=path,
                label=collapse_link_label(match.group('label')),
                raw_label=match.group('label'),
                url=url,
                link_start=match.start(),
                link_end=match.end(),
                url_start=match.start('url'),
                url_end=match.end('url'),
                line=text.count('\n', 0, match.start()) + 1,
                db=db,
                kind=kind,
                content_id=content_id,
                anchor_tail=anchor_tail,
            )
        )
    return links


def collapse_link_label(label: str) -> str:
    return re.sub(r'\s+', ' ', label.replace('\\]', ']')).strip()


def markdown_link(label: str, url: str) -> str:
    return f'[{collapse_link_label(label)}]({url})'


def discover_books(root: Path) -> list[Path]:
    if (root / 'markdown').is_dir():
        return [root]
    if not root.exists():
        raise FileNotFoundError(root)
    return sorted(path for path in root.iterdir() if path.is_dir() and (path / 'markdown').is_dir())


def markdown_files(book: Path) -> list[Path]:
    files = sorted((book / 'markdown').glob('*.md'))
    toc = book / '_toc.md'
    if toc.exists():
        files.append(toc)
    return files


def add_index(index: dict[str, list], keys: Iterable[str], value) -> None:
    for key in keys:
        index[key].append(value)


def unique_values(values: Iterable) -> list:
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def lookup(index: dict[str, list], label: str) -> list:
    values = []
    for key in text_keys(label):
        values.extend(index.get(key, []))
    return unique_values(values)


def markdown_destination(raw: str) -> str:
    if re.search(r'[\s()]', raw):
        return f'<{raw}>'
    return raw


def relative_markdown_url(source: Path, target: Path, anchor: str | None = None) -> str:
    rel = os.path.relpath(target, start=source.parent).replace(os.sep, '/')
    if anchor:
        rel = f'{rel}#{anchor}'
    return markdown_destination(rel)


def anchor_from_tail(tail: str | None) -> str:
    if not tail:
        return 'bookmark'
    decoded = urllib.parse.unquote(tail)
    decoded = re.sub(r'([a-z0-9])([A-Z])', r'\1-\2', decoded)
    decoded = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1-\2', decoded)
    decoded = decoded.replace('_', '-').replace(' ', '-')
    decoded = decoded.lower()
    decoded = re.sub(r'[^0-9a-zа-яё-]+', '-', decoded)
    decoded = re.sub(r'-+', '-', decoded).strip('-')
    return decoded or 'bookmark'


def build_content_map(
    book_name: str,
    links: list[LinkOccurrence],
    title_index: dict[str, list[Path]],
    strong_title_index: dict[str, list[Path]],
    missing_title_index: dict[str, list[Path]],
) -> tuple[dict[str, Path], set[str], set[str]]:
    candidates: dict[str, Counter] = defaultdict(Counter)
    missing_toc: set[str] = set()
    for link in links:
        if link.db != book_name or link.kind != 'content' or not link.content_id:
            continue
        strong_targets = lookup(strong_title_index, link.label)
        if lookup(missing_title_index, link.label) and not strong_targets:
            missing_toc.add(link.content_id)
            continue
        targets = strong_targets or lookup(title_index, link.label)
        for target in targets:
            candidates[link.content_id][target] += 1

    content_map: dict[str, Path] = {}
    ambiguous: set[str] = set()
    for content_id, counter in candidates.items():
        if len(counter) == 1:
            content_map[content_id] = next(iter(counter))
        elif counter:
            ambiguous.add(content_id)
    return content_map, ambiguous, missing_toc


def existing_ids_by_file(headings: list[Heading]) -> dict[Path, dict[str, int]]:
    result: dict[Path, dict[str, int]] = defaultdict(dict)
    for heading in headings:
        for anchor_id in heading.existing_ids:
            result[heading.file][anchor_id] = heading.line_index
    return result


def existing_anchor_headings(headings: list[Heading]) -> dict[str, list[Heading]]:
    result: dict[str, list[Heading]] = defaultdict(list)
    for heading in headings:
        for anchor_id in heading.existing_ids:
            result[anchor_id].append(heading)
    return result


def apply_replacements(text: str, replacements: list[tuple[int, int, str]]) -> str:
    for start, end, new_text in sorted(replacements, reverse=True):
        text = text[:start] + new_text + text[end:]
    return text


def add_multiline_label_cleanups(
    path: Path,
    text: str,
    replacements_by_file: dict[Path, list[tuple[int, int, str]]],
    blocked_spans: set[tuple[Path, int, int]],
) -> int:
    added = 0
    for match in MULTILINE_LINK_RE.finditer(text):
        span_key = (path, match.start(), match.end())
        if span_key in blocked_spans:
            continue
        collapsed = collapse_link_label(match.group('label'))
        if collapsed == match.group('label'):
            continue
        replacements_by_file[path].append(
            (match.start(), match.end(), markdown_link(collapsed, match.group('url')))
        )
        blocked_spans.add(span_key)
        added += 1
    return added


def apply_anchors(text: str, anchors_by_line: dict[int, set[str]]) -> tuple[str, int]:
    if not anchors_by_line:
        return text, 0

    lines = text.splitlines(keepends=True)
    added = 0
    for line_index, anchor_ids in anchors_by_line.items():
        if line_index >= len(lines):
            continue
        line = lines[line_index]
        newline = ''
        if line.endswith('\r\n'):
            line, newline = line[:-2], '\r\n'
        elif line.endswith('\n'):
            line, newline = line[:-1], '\n'

        for anchor_id in sorted(anchor_ids):
            if f'id="{anchor_id}"' in line or f"id='{anchor_id}'" in line:
                continue
            line += f' <a id="{anchor_id}"></a>'
            added += 1
        lines[line_index] = line + newline

    return ''.join(lines), added


def process_book(book: Path, root: Path, apply: bool) -> NormalizationResult:
    result = NormalizationResult(root=root, applied=apply)
    files = markdown_files(book)
    stored = {path: read_stored_text(path) for path in files}
    toc_titles = parse_toc_titles(book)
    missing_title_index: dict[str, list[Path]] = defaultdict(list)
    for toc_path, titles in toc_titles.items():
        if toc_path.exists():
            continue
        for title in titles:
            add_index(missing_title_index, text_keys(title), toc_path)

    headings: list[Heading] = []
    title_index: dict[str, list[Path]] = defaultdict(list)
    strong_title_index: dict[str, list[Path]] = defaultdict(list)
    heading_index: dict[str, list[Heading]] = defaultdict(list)
    links: list[LinkOccurrence] = []

    for path, content in stored.items():
        if path.name == '_toc.md':
            links.extend(parse_links(path, content.text))
            continue

        file_headings = parse_headings(path, content.text)
        headings.extend(file_headings)
        links.extend(parse_links(path, content.text))

        title_values = list(toc_titles.get(path, []))
        if file_headings:
            title_values.append(file_headings[0].text)
        preamble = first_preamble_title(content.text)
        if preamble:
            title_values.append(preamble)
        title_values.append(filename_title(path))

        for title in title_values:
            add_index(title_index, text_keys(title), path)
            if has_leading_section_number(title):
                add_index(strong_title_index, text_keys(title), path)
        for heading in file_headings:
            add_index(heading_index, text_keys(heading.text), heading)

    content_map, ambiguous_content, missing_toc_content = build_content_map(
        book.name,
        links,
        title_index,
        strong_title_index,
        missing_title_index,
    )
    id_locations = existing_ids_by_file(headings)
    anchor_heading_index = existing_anchor_headings(headings)
    replacements_by_file: dict[Path, list[tuple[int, int, str]]] = defaultdict(list)
    blocked_replacement_spans: set[tuple[Path, int, int]] = set()
    anchors_by_file_line: dict[Path, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    planned_anchor_keys: set[tuple[Path, int, str]] = set()

    for link in links:
        if not link.db:
            continue

        if link.db != book.name:
            result.totals['external_db_skipped'] += 1
            continue

        if link.kind == 'content':
            if not link.content_id:
                result.totals['unsupported_same_db_skipped'] += 1
                result.unresolved.append(unresolved(link, 'unsupported_same_db_link'))
                continue

            if link.content_id in missing_toc_content:
                target = None
            else:
                target = content_map.get(link.content_id)

            if not target and link.content_id not in missing_toc_content:
                strong_matches = lookup(strong_title_index, link.label)
                if lookup(missing_title_index, link.label) and not strong_matches:
                    missing_toc_content.add(link.content_id)
                else:
                    direct_matches = strong_matches or lookup(title_index, link.label)
                    if len(direct_matches) == 1:
                        target = direct_matches[0]

            if not target:
                if link.content_id in missing_toc_content:
                    reason = 'missing_toc_content_target'
                elif link.content_id in ambiguous_content:
                    reason = 'ambiguous_content_target'
                else:
                    reason = 'missing_content_target'
                result.totals[reason] += 1
                result.unresolved.append(unresolved(link, reason))
                continue

            new_url = relative_markdown_url(link.file, target)
            replacements_by_file[link.file].append(
                (link.link_start, link.link_end, markdown_link(link.raw_label, new_url))
            )
            blocked_replacement_spans.add((link.file, link.link_start, link.link_end))
            result.replacements.append(
                LinkReplacement(link.file, link.line, link.url, new_url, 'content')
            )
            result.totals['content_rewritten'] += 1
            continue

        if link.kind == 'bookmark':
            anchor_id = anchor_from_tail(link.anchor_tail)
            anchor_matches = anchor_heading_index.get(anchor_id, [])
            if len(anchor_matches) == 1:
                heading = anchor_matches[0]
                new_url = relative_markdown_url(link.file, heading.file, anchor_id)
                replacements_by_file[link.file].append(
                    (link.link_start, link.link_end, markdown_link(link.raw_label, new_url))
                )
                blocked_replacement_spans.add((link.file, link.link_start, link.link_end))
                result.replacements.append(
                    LinkReplacement(link.file, link.line, link.url, new_url, 'bookmark')
                )
                result.totals['bookmark_rewritten'] += 1
                continue
            if len(anchor_matches) > 1:
                reason = 'ambiguous_existing_anchor'
                result.totals[reason] += 1
                result.unresolved.append(unresolved(link, reason))
                continue

            if lookup(missing_title_index, link.label):
                reason = 'missing_toc_bookmark_target'
                result.totals[reason] += 1
                result.unresolved.append(unresolved(link, reason))
                continue

            matches = lookup(heading_index, link.label)
            safe_matches = [heading for heading in matches if heading.level <= 2]
            if matches and not safe_matches:
                reason = 'unsafe_bookmark_heading_level'
                result.totals[reason] += 1
                result.unresolved.append(unresolved(link, reason))
                continue
            if len(safe_matches) != 1:
                reason = 'missing_bookmark_label' if not safe_matches else 'ambiguous_bookmark_label'
                result.totals[reason] += 1
                result.unresolved.append(unresolved(link, reason))
                continue

            heading = safe_matches[0]
            existing_line = id_locations.get(heading.file, {}).get(anchor_id)
            if existing_line is not None and existing_line != heading.line_index:
                result.totals['anchor_id_conflict'] += 1
                result.unresolved.append(unresolved(link, 'anchor_id_conflict'))
                continue

            new_url = relative_markdown_url(link.file, heading.file, anchor_id)
            replacements_by_file[link.file].append(
                (link.link_start, link.link_end, markdown_link(link.raw_label, new_url))
            )
            blocked_replacement_spans.add((link.file, link.link_start, link.link_end))
            result.replacements.append(
                LinkReplacement(link.file, link.line, link.url, new_url, 'bookmark')
            )
            result.totals['bookmark_rewritten'] += 1
            if anchor_id not in heading.existing_ids:
                anchor_key = (heading.file, heading.line_index, anchor_id)
                anchors_by_file_line[heading.file][heading.line_index].add(anchor_id)
                if anchor_key not in planned_anchor_keys:
                    planned_anchor_keys.add(anchor_key)
                    result.anchors.append(AnchorInsertion(heading.file, heading.line_index, anchor_id))
            continue

        result.totals['unsupported_same_db_skipped'] += 1
        result.unresolved.append(unresolved(link, 'unsupported_same_db_link'))

    for path, content in stored.items():
        result.totals['link_labels_collapsed'] += add_multiline_label_cleanups(
            path,
            content.text,
            replacements_by_file,
            blocked_replacement_spans,
        )

    changed_files = 0
    for path, content in stored.items():
        new_text = content.text
        if path in replacements_by_file:
            new_text = apply_replacements(new_text, replacements_by_file[path])
        if path in anchors_by_file_line:
            new_text, anchors_added = apply_anchors(new_text, anchors_by_file_line[path])
            result.totals['anchors_added'] += anchors_added
        if new_text != content.text:
            changed_files += 1
            if apply:
                write_stored_text(path, content, new_text)

    result.totals['files_scanned'] = len(files)
    result.totals['files_changed'] = changed_files
    result.totals['db_links_seen'] = len(links)
    result.books.append(
        {
            'book': book.name,
            'files_scanned': len(files),
            'db_links_seen': len(links),
            'content_rewritten': result.totals['content_rewritten'],
            'bookmark_rewritten': result.totals['bookmark_rewritten'],
            'anchors_added': result.totals['anchors_added'],
            'files_changed': changed_files,
            'unresolved': len(result.unresolved),
        }
    )
    return result


def unresolved(link: LinkOccurrence, reason: str) -> UnresolvedLink:
    return UnresolvedLink(
        file=link.file,
        line=link.line,
        label=link.label,
        url=link.url,
        reason=reason,
    )


def merge_results(root: Path, apply: bool, results: list[NormalizationResult]) -> NormalizationResult:
    merged = NormalizationResult(root=root, applied=apply)
    for item in results:
        merged.totals.update(item.totals)
        merged.replacements.extend(item.replacements)
        merged.anchors.extend(item.anchors)
        merged.unresolved.extend(item.unresolved)
        merged.books.extend(item.books)
    merged.totals['books_scanned'] = len(results)
    return merged


def normalize_root(root: str | Path = DEFAULT_ROOT, apply: bool = False) -> NormalizationResult:
    root = Path(root)
    books = discover_books(root)
    return merge_results(root, apply, [process_book(book, root, apply) for book in books])


def write_report_json(path: Path, result: NormalizationResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def print_summary(result: NormalizationResult) -> None:
    mode = 'APPLY' if result.applied else 'DRY-RUN'
    print(f'Mode: {mode}')
    print(f'Root: {result.root}')
    print(f'Books scanned: {result.totals["books_scanned"]}')
    print(f'Files scanned: {result.totals["files_scanned"]}')
    print(f'DB links seen: {result.totals["db_links_seen"]}')
    print(f'Content links rewritten: {result.totals["content_rewritten"]}')
    print(f'Bookmark links rewritten: {result.totals["bookmark_rewritten"]}')
    print(f'Anchors added: {result.totals["anchors_added"]}')
    print(f'Link labels collapsed: {result.totals["link_labels_collapsed"]}')
    print(f'Files changed: {result.totals["files_changed"]}')
    print(f'External DB links skipped: {result.totals["external_db_skipped"]}')

    unresolved_counts = Counter(item.reason for item in result.unresolved)
    if unresolved_counts:
        print('Unresolved same-book links:')
        for reason, count in unresolved_counts.most_common():
            print(f'  {reason}: {count}')
    if not result.applied:
        print('Dry-run only: files were not modified. Use --apply to write changes.')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Normalize ITS book /db/... markdown links to local .md links.'
    )
    parser.add_argument(
        '--root',
        default=str(DEFAULT_ROOT),
        help=f'Books root or a single book directory. Default: {DEFAULT_ROOT}',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Write changes. Without this flag the script only reports planned changes.',
    )
    parser.add_argument(
        '--report-json',
        help='Optional path to write a detailed JSON report.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = normalize_root(args.root, apply=args.apply)
    except FileNotFoundError as exc:
        print(f'Root not found: {exc}', file=sys.stderr)
        return 2

    if args.report_json:
        write_report_json(Path(args.report_json), result)

    print_summary(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
