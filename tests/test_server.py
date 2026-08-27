"""
Server behaviour tests. These run against a real ThreadingHTTPServer, so they
cover the request handling and not just the helpers.

Several cases exist because the bug they describe actually shipped:
  - test_index_survives_non_ascii_names: an em dash in a filename was read back
    as GBK mojibake, so a file stopped matching its own index entry and sank to
    the bottom of the gallery.
  - test_save_is_atomic / test_index_write_is_atomic: writes used to be a bare
    write_text(), so a crash mid-write truncated the file.
  - test_corrupt_index_does_not_lose_files: load_index() swallows parse errors
    and returns [], which must degrade to mtime order rather than an empty grid.
"""
import json
import time
from pathlib import Path

import pytest


# ── mime mapping ──────────────────────────────────────────────────────

@pytest.mark.parametrize('name,expected', [
    ('a.html', 'text/html'),
    ('a.htm', 'text/html'),
    ('a.svg', 'image/svg+xml'),
    ('a.png', 'image/png'),
    ('a.jpg', 'image/jpeg'),
    ('a.jpeg', 'image/jpeg'),
    ('a.md', 'text/plain; charset=utf-8'),
    ('a.mermaid', 'text/plain; charset=utf-8'),
    ('a.unknownext', 'application/octet-stream'),
    ('noextension', 'application/octet-stream'),
])
def test_guess_mime(gallery, name, expected):
    assert gallery.mod.guess_mime(name) == expected


def test_svg_is_served_as_svg_not_download(gallery):
    """A wrong mime here breaks preview silently, so pin it at the HTTP layer."""
    gallery.write('pic.svg', '<svg xmlns="http://www.w3.org/2000/svg"/>')
    status, body = gallery.get('/files/pic.svg')
    assert status == 200
    assert b'<svg' in body


# ── ordering ──────────────────────────────────────────────────────────

def test_newest_notified_file_is_first(gallery):
    for n in ('one.md', 'two.md', 'three.md'):
        gallery.write(n, '# ' + n)
    assert gallery.names() == ['three.md', 'two.md', 'one.md']


def test_unindexed_files_sort_below_indexed_ones(gallery):
    gallery.write('indexed.md', 'x')
    # A file copied in by hand never hits /notify, so it has no index entry.
    gallery.write('manual.md', 'x', register=False)
    names = gallery.names()
    assert names.index('indexed.md') < names.index('manual.md')


def test_index_survives_non_ascii_names(gallery):
    """An em dash in a filename must round-trip through artifacts.json."""
    name = 'Aki Workshop — MLU Summit Tokyo 2026.html'
    gallery.write(name, '<h1>hi</h1>')
    assert name in json.loads((gallery.root / 'artifacts.json').read_text(encoding='utf-8'))
    # ...and still be recognised as indexed, i.e. it sorts above an unindexed file
    gallery.write('plain.md', 'x', register=False)
    names = gallery.names()
    assert names.index(name) < names.index('plain.md')


def test_corrupt_index_does_not_lose_files(gallery):
    gallery.write('kept.md', 'x')
    (gallery.root / 'artifacts.json').write_text('{ truncated', encoding='utf-8')
    assert 'kept.md' in gallery.names()


def test_internal_files_are_not_listed(gallery):
    gallery.write('real.md', 'x')
    (gallery.root / 'gallery.log').write_text('log line', encoding='utf-8')
    (gallery.root / 'in-flight.md.tmp').write_text('half written', encoding='utf-8')
    names = gallery.names()
    assert names == ['real.md']


# ── durability ────────────────────────────────────────────────────────

def test_save_leaves_no_temp_file(gallery):
    gallery.write('doc.md', 'before')
    gallery.post('/save', {'file': 'doc.md', 'content': 'after'})
    assert (gallery.root / 'doc.md').read_text(encoding='utf-8') == 'after'
    assert not list(gallery.root.glob('*.tmp'))


def test_index_write_is_atomic(gallery):
    """save_index must replace, never truncate-then-write."""
    calls = []
    real_replace = gallery.mod.os.replace
    gallery.mod.os.replace = lambda a, b: (calls.append((str(a), str(b))), real_replace(a, b))[1]
    try:
        gallery.write('x.md', 'x')
    finally:
        gallery.mod.os.replace = real_replace
    assert any(dst.endswith('artifacts.json') and src.endswith('.tmp')
               for src, dst in calls), 'index was not written through os.replace'


def test_failed_write_does_not_destroy_the_original(gallery):
    gallery.write('precious.md', 'original')
    target = gallery.root / 'precious.md'

    def boom(self, *a, **k):
        raise OSError('disk full')

    original_write = type(target).write_text
    type(target).write_text = boom
    try:
        status, _ = gallery.post('/save', {'file': 'precious.md', 'content': 'new'})
    finally:
        type(target).write_text = original_write
    assert status == 500
    assert target.read_text(encoding='utf-8') == 'original'
    assert not list(gallery.root.glob('*.tmp'))


# ── history / undo ────────────────────────────────────────────────────

def test_save_snapshots_previous_content(gallery):
    gallery.write('doc.md', 'v1')
    status, body = gallery.post('/save', {'file': 'doc.md', 'content': 'v2'})
    assert status == 200 and body['undoable'] is True
    snaps = gallery.get('/history?file=doc.md')[1]['snapshots']
    assert len(snaps) == 1


def test_restore_brings_back_the_previous_content(gallery):
    gallery.write('doc.md', 'v1')
    gallery.post('/save', {'file': 'doc.md', 'content': 'v2'})
    status, body = gallery.post('/restore', {'file': 'doc.md'})
    assert status == 200
    assert body['content'] == 'v1'
    assert (gallery.root / 'doc.md').read_text(encoding='utf-8') == 'v1'


def test_restore_is_itself_undoable(gallery):
    gallery.write('doc.md', 'v1')
    gallery.post('/save', {'file': 'doc.md', 'content': 'v2'})
    gallery.post('/restore', {'file': 'doc.md'})       # back to v1
    status, body = gallery.post('/restore', {'file': 'doc.md'})   # forward to v2
    assert status == 200 and body['content'] == 'v2'


def test_history_is_bounded(gallery):
    gallery.write('doc.md', 'v0')
    for i in range(gallery.mod.HISTORY_KEEP + 5):
        gallery.post('/save', {'file': 'doc.md', 'content': f'v{i + 1}'})
        time.sleep(0.002)   # snapshot names are epoch ms
    snaps = gallery.get('/history?file=doc.md')[1]['snapshots']
    assert len(snaps) == gallery.mod.HISTORY_KEEP


def test_restore_without_history_is_404(gallery):
    gallery.write('doc.md', 'only version')
    assert gallery.post('/restore', {'file': 'doc.md'})[0] == 404


def test_snapshots_are_not_listed_as_artifacts(gallery):
    gallery.write('doc.md', 'v1')
    gallery.post('/save', {'file': 'doc.md', 'content': 'v2'})
    assert gallery.names() == ['doc.md']


# ── guards ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('path', [
    '/files/../server.py',
    '/files/..%2Fserver.py',
    '/files/....//server.py',
])
def test_files_endpoint_refuses_to_escape_root(gallery, path):
    assert gallery.get_status(path) in (403, 404)


@pytest.mark.parametrize('fname', ['gallery.html', 'server.py', 'artifacts.json'])
def test_save_refuses_reserved_names(gallery, fname):
    assert gallery.post('/save', {'file': fname, 'content': 'pwned'})[0] == 403


def test_save_refuses_paths_outside_root(gallery):
    assert gallery.post('/save', {'file': '../escaped.md', 'content': 'x'})[0] == 403
    assert not (gallery.root.parent / 'escaped.md').exists()


# ── import ────────────────────────────────────────────────────────────

def _data_url(text: str) -> str:
    import base64
    return 'data:text/plain;base64,' + base64.b64encode(text.encode()).decode()


def test_import_writes_and_registers(gallery):
    status, body = gallery.post('/import', {
        'files': [{'name': 'dropped.md', 'data': _data_url('# dropped')}]})
    assert status == 200 and body['imported'] == ['dropped.md']
    assert (gallery.root / 'dropped.md').read_text(encoding='utf-8') == '# dropped'
    assert gallery.names() == ['dropped.md']


def test_import_does_not_overwrite(gallery):
    gallery.write('dup.md', 'original')
    status, body = gallery.post('/import', {
        'files': [{'name': 'dup.md', 'data': _data_url('incoming')}]})
    assert body['imported'] == ['dup-1.md']
    assert (gallery.root / 'dup.md').read_text(encoding='utf-8') == 'original'


@pytest.mark.parametrize('name', ['../escape.md', 'sub/dir.md', '..', '.'])
def test_import_rejects_path_like_names(gallery, name):
    status, body = gallery.post('/import', {
        'files': [{'name': name, 'data': _data_url('x')}]})
    assert status == 200 and body['imported'] == []


# ── delete ────────────────────────────────────────────────────────────

def test_delete_removes_file_and_index_entry(gallery):
    gallery.write('gone.md', 'x')
    gallery.write('stays.md', 'x')
    status, body = gallery.post('/delete', {'files': ['gone.md']})
    assert status == 200 and body['deleted'] == ['gone.md']
    assert not (gallery.root / 'gone.md').exists()
    assert json.loads((gallery.root / 'artifacts.json').read_text(encoding='utf-8')) == ['stays.md']


def test_delete_refuses_reserved_names(gallery):
    (gallery.root / 'gallery.html').write_text('x', encoding='utf-8')
    status, body = gallery.post('/delete', {'files': ['gallery.html']})
    assert body['deleted'] == []
    assert (gallery.root / 'gallery.html').exists()


# ── version + update check ────────────────────────────────────────────

def test_version_endpoint_reports_the_build(gallery):
    body = gallery.get('/version')[1]
    assert body['version'] == gallery.mod.DISPLAY_VERSION
    assert body['release'] is gallery.mod.IS_RELEASE


def test_artifacts_root_is_overridable(tmp_path, monkeypatch):
    """A checkout must be able to serve an artifacts folder elsewhere, or the
    code has to be copied next to the artifacts and then drifts from the repo."""
    import importlib.util
    elsewhere = tmp_path / 'somewhere-else'
    elsewhere.mkdir()
    monkeypatch.setenv('CLAUDE_GALLERY_ROOT', str(elsewhere))
    spec = importlib.util.spec_from_file_location(
        'gs_env', Path(__file__).resolve().parent.parent / 'src' / 'server.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.ROOT == elsewhere
    # ...but the page itself still comes from the checkout, not the artifacts dir
    assert mod.GALLERY_HTML.name == 'gallery.html'
    assert mod.GALLERY_HTML.parent != elsewhere


def test_placeholder_version_is_never_shown(gallery):
    """0.0.0-dev is an internal marker; showing it tells the user nothing."""
    assert gallery.mod.IS_RELEASE is False
    assert gallery.get('/version')[1]['version'] != '0.0.0-dev'


def test_source_checkout_reports_its_git_description(gallery):
    """Running from the repo should name the commit, not just say "dev"."""
    v = gallery.mod.resolve_display_version()
    assert v != '0.0.0-dev'
    # This test tree IS a checkout, so git describe should have answered.
    assert v == 'dev' or v[0].isdigit() or v.replace('-dirty', '').isalnum()


def test_dev_builds_never_report_an_update(gallery):
    """Unstamped builds must not nag, and must not call out to the network."""
    assert gallery.mod.IS_RELEASE is False
    assert gallery.get('/update-check')[1]['outdated'] is False


@pytest.mark.parametrize('current,latest,outdated', [
    ('2.2.0', '2.3.0', True),
    ('2.2.0', '2.2.1', True),
    ('2.2.0', '2.2.0', False),
    ('2.10.0', '2.9.0', False),    # numeric compare, not string compare
    ('2.2.0', 'not-a-version', False),
])
def test_version_comparison(gallery, current, latest, outdated):
    vt = gallery.mod._ver_tuple
    assert (vt(latest) > vt(current)) is outdated


def test_update_check_is_silent_when_offline(gallery, monkeypatch):
    monkeypatch.setattr(gallery.mod, 'IS_RELEASE', True)
    monkeypatch.setattr(gallery.mod, 'DISPLAY_VERSION', '2.2.0')
    monkeypatch.setattr(gallery.mod, 'RELEASES_API', 'http://127.0.0.1:1/nope')
    gallery.mod._update_cache.clear()
    body = gallery.get('/update-check')[1]
    assert body['outdated'] is False


def test_update_check_can_be_opted_out(gallery, monkeypatch):
    """Opting out must skip the network entirely, not just hide the result.

    Called in-process rather than over HTTP: patching urlopen globally would
    also break the test client's own request.
    """
    monkeypatch.setattr(gallery.mod, 'IS_RELEASE', True)
    monkeypatch.setattr(gallery.mod, 'DISPLAY_VERSION', '2.2.0')
    monkeypatch.setenv('CLAUDE_GALLERY_NO_UPDATE_CHECK', '1')

    def fail(*a, **k):
        raise AssertionError('opted out but still hit the network')

    monkeypatch.setattr(gallery.mod.urllib.request, 'urlopen', fail)
    gallery.mod._update_cache.clear()
    assert gallery.mod.check_for_update()['outdated'] is False


# ── logging ───────────────────────────────────────────────────────────

def test_actions_are_logged(gallery):
    gallery.write('logged.md', 'v1')
    gallery.post('/save', {'file': 'logged.md', 'content': 'v2'})
    for h in gallery.mod.log.handlers:
        h.flush()
    text = (gallery.root / 'gallery.log').read_text(encoding='utf-8')
    assert 'saved logged.md' in text
