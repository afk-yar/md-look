"""
Runtime tests for Api.reload_file() and _file_mtime tracking.

Covers T-01 edge cases not exercised by existing tests:
  - reload_file(force=False) → None when file unchanged (mtime equal)
  - reload_file(force=False) → content when file changed (mtime differs)
  - reload_file(force=True) → content unconditionally (even when mtime unchanged)
  - reload_file → None when _current_path is None
  - reload_file → None when file does not exist on disk
  - reload_file when _file_mtime is None (first read) → content returned
  - _file_mtime updated after successful reload
  - reload_file handles read exception gracefully → None
"""
import os
import time
import unittest.mock as mock
import pytest

import app


class TestReloadFileRuntime:

    def test_returns_none_when_path_is_none(self):
        """reload_file returns None when _current_path is None."""
        api = app.Api()
        assert api._current_path is None
        assert api.reload_file() is None
        assert api.reload_file(force=True) is None

    def test_returns_none_when_file_missing(self, tmp_path):
        """reload_file returns None when _current_path points to a non-existent file."""
        api = app.Api()
        api._current_path = str(tmp_path / 'ghost.md')
        assert api.reload_file() is None
        assert api.reload_file(force=True) is None

    def test_returns_none_when_mtime_unchanged(self, tmp_path):
        """reload_file(force=False) returns None when mtime has not changed."""
        md = tmp_path / 'doc.md'
        md.write_text('# Hello', encoding='utf-8')

        api = app.Api()
        api._current_path = str(md)
        api._file_mtime = os.path.getmtime(str(md))  # same mtime

        result = api.reload_file(force=False)
        assert result is None

    def test_returns_content_when_mtime_changed(self, tmp_path):
        """reload_file(force=False) returns content when mtime changed."""
        md = tmp_path / 'doc.md'
        md.write_text('# Old content', encoding='utf-8')

        api = app.Api()
        api._current_path = str(md)
        api._file_mtime = os.path.getmtime(str(md)) - 1.0  # older mtime

        result = api.reload_file(force=False)
        assert result is not None
        assert result['content'] == '# Old content'
        assert result['name'] == 'doc.md'
        assert result['path'] == str(md)

    def test_force_true_returns_content_even_when_mtime_unchanged(self, tmp_path):
        """reload_file(force=True) returns content regardless of mtime."""
        md = tmp_path / 'doc.md'
        md.write_text('# Force reload', encoding='utf-8')

        api = app.Api()
        api._current_path = str(md)
        api._file_mtime = os.path.getmtime(str(md))  # same mtime, no change

        result = api.reload_file(force=True)
        assert result is not None
        assert result['content'] == '# Force reload'
        assert result['name'] == 'doc.md'

    def test_mtime_is_none_triggers_reload(self, tmp_path):
        """reload_file with _file_mtime=None (initial state) always returns content."""
        md = tmp_path / 'doc.md'
        md.write_text('# Initial', encoding='utf-8')

        api = app.Api()
        api._current_path = str(md)
        # _file_mtime is None (default) — should trigger reload
        assert api._file_mtime is None

        result = api.reload_file(force=False)
        assert result is not None
        assert result['content'] == '# Initial'

    def test_mtime_updated_after_reload(self, tmp_path):
        """After a successful reload, _file_mtime is updated to current mtime."""
        md = tmp_path / 'doc.md'
        md.write_text('# Track mtime', encoding='utf-8')
        current_mtime = os.path.getmtime(str(md))

        api = app.Api()
        api._current_path = str(md)
        api._file_mtime = current_mtime - 5.0  # outdated

        api.reload_file(force=False)
        assert api._file_mtime == current_mtime

    def test_mtime_updated_after_force_reload(self, tmp_path):
        """After a forced reload, _file_mtime is updated even when mtime was equal."""
        md = tmp_path / 'doc.md'
        md.write_text('# Force mtime', encoding='utf-8')
        current_mtime = os.path.getmtime(str(md))

        api = app.Api()
        api._current_path = str(md)
        api._file_mtime = current_mtime  # same

        api.reload_file(force=True)
        assert api._file_mtime == current_mtime

    def test_returns_none_on_read_exception(self, tmp_path):
        """reload_file handles IOError gracefully — returns None, doesn't raise."""
        md = tmp_path / 'doc.md'
        md.write_text('# Readable', encoding='utf-8')

        api = app.Api()
        api._current_path = str(md)
        api._file_mtime = os.path.getmtime(str(md)) - 1.0  # trigger reload

        with mock.patch('builtins.open', side_effect=PermissionError('access denied')):
            result = api.reload_file(force=False)

        assert result is None

    def test_result_dict_has_correct_keys(self, tmp_path):
        """reload_file result dict contains exactly content, name, path."""
        md = tmp_path / 'notes.md'
        md.write_text('content here', encoding='utf-8')

        api = app.Api()
        api._current_path = str(md)
        api._file_mtime = None  # first read

        result = api.reload_file()
        assert isinstance(result, dict)
        assert set(result.keys()) == {'content', 'name', 'path'}
        assert result['content'] == 'content here'
        assert result['name'] == 'notes.md'
        assert result['path'] == str(md)
