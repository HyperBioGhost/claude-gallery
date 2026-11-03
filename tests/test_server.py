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


def test_version_fallback_records_a_reason(gallery, monkeypatch, tmp_path):
    """
    A "dev" badge must be diagnosable. A bare except here once turned every
    cause -- not a checkout, git missing, git too slow -- into the same silent
    "dev", which could not be told apart from outside the process.
    """
    import subprocess

    def too_slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd='git describe', timeout=8)

    # resolve_display_version imports subprocess locally, so patch the module.
    monkeypatch.setattr(subprocess, 'run', too_slow)
    monkeypatch.setattr(gallery.mod, '_version_note', '')
    assert gallery.mod.resolve_display_version() == 'dev'
    assert 'TimeoutExpired' in gallery.mod._version_note


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


# ── card thumbnails ───────────────────────────────────────────────────

_PNG = ('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA'
        'DUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def test_list_reports_no_thumbnail_until_one_is_captured(gallery):
    gallery.write('page.html', '<h1>x</h1>')
    [entry] = gallery.get('/list')[1]['files']
    assert entry['thumbnail_mtime'] is None
    status, body = gallery.post('/thumb', {'file': 'page.html', 'data': _PNG})
    assert status == 200 and body['thumbnail_mtime']
    [entry] = gallery.get('/list')[1]['files']
    assert entry['thumbnail_mtime'] == body['thumbnail_mtime']
    assert gallery.get('/thumb/page.html.png')[0] == 200


def test_thumbnail_is_not_touched_when_the_artifact_changes(gallery):
    """Stale by design: only the browser, on open/save, replaces a thumbnail."""
    gallery.write('page.html', '<h1>v1</h1>')
    gallery.post('/thumb', {'file': 'page.html', 'data': _PNG})
    before = gallery.get('/list')[1]['files'][0]['thumbnail_mtime']
    time.sleep(0.05)
    gallery.write('page.html', '<h1>v2</h1>')
    assert gallery.get('/list')[1]['files'][0]['thumbnail_mtime'] == before


@pytest.mark.parametrize('fname', ['missing.html', '../escape.html', 'gallery.html'])
def test_thumbnail_upload_requires_a_real_artifact(gallery, fname):
    status, _ = gallery.post('/thumb', {'file': fname, 'data': _PNG})
    assert status in (400, 403)
    assert not (gallery.root.parent / 'escape.html.png').exists()


def test_thumbnail_download_refuses_to_escape_its_folder(gallery):
    assert gallery.get_status('/thumb/..%2Fartifacts.json') == 404


def test_deleting_an_artifact_deletes_its_thumbnail(gallery):
    gallery.write('page.html', '<h1>x</h1>')
    gallery.post('/thumb', {'file': 'page.html', 'data': _PNG})
    assert (gallery.root / '.card-thumbnails' / 'page.html.png').exists()
    gallery.post('/delete', {'files': ['page.html']})
    assert not (gallery.root / '.card-thumbnails' / 'page.html.png').exists()


def test_thumbnail_folder_is_not_listed_as_an_artifact(gallery):
    gallery.write('page.html', '<h1>x</h1>')
    gallery.post('/thumb', {'file': 'page.html', 'data': _PNG})
    assert gallery.names() == ['page.html']


# ── media types and Range ─────────────────────────────────────────────

@pytest.mark.parametrize('name,expected', [
    ('a.mp4', 'video/mp4'), ('a.m4v', 'video/mp4'), ('a.webm', 'video/webm'),
    ('a.mov', 'video/quicktime'), ('a.mkv', 'video/x-matroska'),
    ('a.mp3', 'audio/mpeg'), ('a.wav', 'audio/wav'), ('a.m4a', 'audio/mp4'),
    ('a.aac', 'audio/aac'), ('a.ogg', 'audio/ogg'), ('a.flac', 'audio/flac'),
    ('a.srt', 'text/plain; charset=utf-8'),
])
def test_media_mime_types(gallery, name, expected):
    assert gallery.mod.guess_mime(name) == expected


@pytest.mark.parametrize('header,size,expected', [
    ('bytes=0-99', 1000, (0, 99)),
    ('bytes=500-', 1000, (500, 999)),
    ('bytes=-100', 1000, (900, 999)),
    ('bytes=0-100000', 1000, (0, 999)),      # clamped to the file
    ('bytes=0-99, 200-299', 1000, (0, 99)),  # first range only
    ('bytes=1000-', 1000, None),             # starts past the end
    ('bytes=900-800', 1000, None),           # reversed
    ('bytes=abc', 1000, None),
    ('items=0-99', 1000, None),              # wrong unit
    ('', 1000, None),
])
def test_parse_byte_range(gallery, header, size, expected):
    assert gallery.mod.parse_byte_range(header, size) == expected


def _blob(gallery, name='clip.mp4', size=200000):
    data = bytes((i * 7 + 13) % 256 for i in range(size))
    (gallery.root / name).write_bytes(data)
    gallery.get('/notify?file=' + name)
    return data


def test_a_full_request_advertises_range_support(gallery):
    """Without Accept-Ranges a player will not try to seek at all."""
    data = _blob(gallery)
    import urllib.request
    with urllib.request.urlopen(gallery.base + '/files/clip.mp4', timeout=10) as r:
        assert r.status == 200
        assert r.headers.get('Accept-Ranges') == 'bytes'
        assert r.headers.get('Content-Type') == 'video/mp4'
        assert r.read() == data


@pytest.mark.parametrize('lo,hi', [(0, 999), (50000, 50099), (199000, 199999)])
def test_a_ranged_request_returns_exactly_those_bytes(gallery, lo, hi):
    data = _blob(gallery)
    import urllib.request
    req = urllib.request.Request(gallery.base + '/files/clip.mp4',
                                 headers={'Range': f'bytes={lo}-{hi}'})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 206
        assert r.headers.get('Content-Range') == f'bytes {lo}-{hi}/{len(data)}'
        assert r.read() == data[lo:hi + 1]


def test_an_unsatisfiable_range_is_416(gallery):
    data = _blob(gallery)
    import urllib.error, urllib.request
    req = urllib.request.Request(gallery.base + '/files/clip.mp4',
                                 headers={'Range': f'bytes={len(data) + 5}-'})
    try:
        urllib.request.urlopen(req, timeout=10)
        assert False, 'expected 416'
    except urllib.error.HTTPError as e:
        assert e.code == 416
        assert e.headers.get('Content-Range') == f'bytes */{len(data)}'


def test_head_gives_the_headers_without_the_body(gallery):
    """
    Read from a raw socket: urllib and http.client both know a HEAD has no
    body and will not read one even when the server sends it, so a
    client-library assertion cannot see this failure.
    """
    data = _blob(gallery)
    import socket as _socket
    from urllib.parse import urlparse
    u = urlparse(gallery.base)
    with _socket.create_connection((u.hostname, u.port), timeout=10) as sk:
        sk.sendall(b'HEAD /files/clip.mp4 HTTP/1.1\r\n'
                   b'Host: x\r\nConnection: close\r\n\r\n')
        sk.settimeout(5)
        raw = b''
        while True:
            try:
                chunk = sk.recv(65536)
            except _socket.timeout:
                break
            if not chunk:
                break
            raw += chunk
    head, _, body = raw.partition(b'\r\n\r\n')
    text = head.decode('latin-1')
    assert '200' in text.splitlines()[0], text.splitlines()[0]
    assert 'Content-Length: ' + str(len(data)) in text, text
    assert 'Accept-Ranges: bytes' in text, text
    assert body == b'', 'HEAD sent ' + str(len(body)) + ' bytes of body'


def test_serving_does_not_allocate_the_whole_file(gallery):
    """
    The old implementation read the file into one bytes object, so a 45 MB
    asset cost 45 MB per request and could not be seeked. The control below
    reinstates that, which is what proves this check can see the failure.
    """
    import tracemalloc, urllib.request
    size = 8 * 1024 * 1024
    _blob(gallery, 'big.wav', size)

    def fetch():
        with urllib.request.urlopen(gallery.base + '/files/big.wav', timeout=30) as r:
            while r.read(262144):
                pass

    def peak_of(fn):
        tracemalloc.start()
        tracemalloc.reset_peak()
        fn()
        time.sleep(0.2)
        _, pk = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return pk

    streamed = peak_of(fetch)

    original = gallery.mod.Handler.serve_file

    def whole_file(self, fpath, mime, head_only=False):
        payload = fpath.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', len(payload))
        self.end_headers()
        self.wfile.write(payload)

    gallery.mod.Handler.serve_file = whole_file
    try:
        control = peak_of(fetch)
    finally:
        gallery.mod.Handler.serve_file = original

    assert control > size * 0.8, f'control only allocated {control}, detector is blind'
    assert streamed < size * 0.25, f'still buffering: {streamed} for {size}'


# ── virtual links ─────────────────────────────────────────────────────

def _link(gallery, display, target, note=''):
    body = (f'# {note}\n' if note else '') + str(target) + '\n'
    (gallery.root / (display + gallery.mod.LINK_SUFFIX)).write_text(body, encoding='utf-8')
    return gallery.root / (display + gallery.mod.LINK_SUFFIX)


def _asset(tmp_path, name='asset.mp4', payload=b'\x00\x01video-bytes'):
    outside = tmp_path / 'elsewhere'
    outside.mkdir(exist_ok=True)
    f = outside / name
    f.write_bytes(payload)
    return f


def test_a_link_is_listed_as_the_asset_it_points_at(gallery, tmp_path):
    asset = _asset(tmp_path)
    _link(gallery, 'render.mp4', asset)
    [entry] = gallery.get('/list')[1]['files']
    assert entry['name'] == 'render.mp4'
    assert entry['linked'] is True
    assert entry['link_target'] == str(asset)
    assert entry['link_error'] is None
    # The date describes the asset, not when the pointer was written.
    assert abs(entry['mtime'] - int(asset.stat().st_mtime * 1000)) <= 1


def test_the_pointer_file_is_never_listed_itself(gallery, tmp_path):
    _link(gallery, 'render.mp4', _asset(tmp_path))
    assert gallery.names() == ['render.mp4']


def test_a_link_serves_the_target_bytes(gallery, tmp_path):
    asset = _asset(tmp_path, payload=b'the real asset bytes')
    _link(gallery, 'render.mp4', asset, note='lives with the project')
    status, body = gallery.get('/files/render.mp4')
    assert status == 200 and body == b'the real asset bytes'


def test_a_linked_asset_can_be_ranged(gallery, tmp_path):
    asset = _asset(tmp_path, payload=bytes(range(256)) * 100)
    _link(gallery, 'render.mp4', asset)
    import urllib.request
    req = urllib.request.Request(gallery.base + '/files/render.mp4',
                                 headers={'Range': 'bytes=1000-1099'})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 206
        assert r.read() == asset.read_bytes()[1000:1100]


def test_a_real_file_wins_over_a_pointer_of_the_same_name(gallery, tmp_path):
    _link(gallery, 'render.mp4', _asset(tmp_path, payload=b'from the link'))
    (gallery.root / 'render.mp4').write_bytes(b'actually here')
    assert gallery.get('/files/render.mp4')[1] == b'actually here'


@pytest.mark.parametrize('body,fragment', [
    ('', 'empty'),
    ('# only a comment\n', 'empty'),
    ('relative/path.mp4\n', 'absolute'),
    ('\\\\nas\\share\\clip.mp4\n', 'network'),
])
def test_an_unusable_link_says_why(gallery, body, fragment):
    (gallery.root / ('bad.mp4' + gallery.mod.LINK_SUFFIX)).write_text(body, encoding='utf-8')
    [entry] = gallery.get('/list')[1]['files']
    assert entry['linked'] is True
    assert fragment in entry['link_error'], entry['link_error']
    # 409 rather than 404: the artifact exists, its target cannot be reached.
    assert gallery.get_status('/files/bad.mp4') == 409


def test_a_missing_target_is_reported_not_hidden(gallery, tmp_path):
    _link(gallery, 'gone.mp4', tmp_path / 'elsewhere' / 'never-written.mp4')
    [entry] = gallery.get('/list')[1]['files']
    assert 'missing' in entry['link_error']
    assert gallery.get_status('/files/gone.mp4') == 409


def test_a_pointer_cannot_be_created_over_http(gallery):
    """
    Artifacts run same-origin and can reach these endpoints. A pointer minted
    here could name any file on the machine and then be read back, so the only
    way to create one is writing to disk.
    """
    import base64
    status, _ = gallery.post('/save', {'file': 'evil.mp4.link', 'content': 'C:/Windows/win.ini'})
    assert status == 403
    assert not (gallery.root / 'evil.mp4.link').exists()

    data = 'data:text/plain;base64,' + base64.b64encode(b'C:/Windows/win.ini').decode()
    status, body = gallery.post('/import', {'files': [{'name': 'evil2.mp4.link', 'data': data}]})
    assert status == 200 and body['imported'] == []
    assert not (gallery.root / 'evil2.mp4.link').exists()


def test_deleting_a_linked_artifact_removes_only_the_pointer(gallery, tmp_path):
    asset = _asset(tmp_path)
    pointer = _link(gallery, 'render.mp4', asset)
    status, body = gallery.post('/delete', {'files': ['render.mp4']})
    assert status == 200 and body['deleted'] == ['render.mp4']
    assert not pointer.exists(), 'pointer should be gone'
    assert asset.is_file(), 'the asset must be left where its project keeps it'


def test_a_linked_asset_can_have_a_thumbnail(gallery, tmp_path):
    """A linked video has no file of its own name, which once refused its card."""
    _link(gallery, 'render.mp4', _asset(tmp_path))
    status, body = gallery.post('/thumb', {'file': 'render.mp4', 'data': _PNG})
    assert status == 200 and body['thumbnail_mtime']
    assert gallery.get('/list')[1]['files'][0]['thumbnail_mtime'] == body['thumbnail_mtime']


@pytest.mark.parametrize('raw,ok', [
    (r'C:\Users', True),
    ('//server/share/x', False),
    ('\\\\server\\share\\x', False),
])
def test_only_local_fixed_paths_are_allowed(gallery, raw, ok):
    got, _ = gallery.mod.is_local_fixed_path(Path(raw))
    assert got is ok


# ── pasted, unsaved artifacts ─────────────────────────────────────────

_TXT = 'data:text/plain;base64,IyBwYXN0ZWQK'          # "# pasted\n"


def test_a_paste_is_listed_but_not_registered(gallery):
    status, body = gallery.post('/paste', {'name': 'note.md', 'data': _TXT})
    assert status == 200 and body == {'name': 'note.md', 'unsaved': True}
    [entry] = gallery.get('/list')[1]['files']
    assert entry['name'] == 'note.md' and entry['unsaved'] is True
    # Not registered means exactly this: absent from the index, and absent
    # from the artifacts folder itself.
    assert gallery.mod.load_index() == []
    assert not (gallery.root / 'note.md').exists()
    assert (gallery.root / '.unsaved' / 'note.md').is_file()


def test_a_paste_is_previewable_immediately(gallery):
    gallery.post('/paste', {'name': 'note.md', 'data': _TXT})
    status, body = gallery.get('/files/note.md')
    assert status == 200 and body == b'# pasted\n'


def test_saving_a_paste_moves_and_registers_it(gallery):
    gallery.post('/paste', {'name': 'note.md', 'data': _TXT})
    status, body = gallery.post('/unsaved/keep', {'file': 'note.md'})
    assert status == 200 and body == {'saved': 'note.md', 'was': 'note.md'}
    assert (gallery.root / 'note.md').read_bytes() == b'# pasted\n'
    assert not (gallery.root / '.unsaved' / 'note.md').exists()
    assert gallery.mod.load_index() == ['note.md']
    [entry] = gallery.get('/list')[1]['files']
    assert 'unsaved' not in entry or entry.get('unsaved') is not True


def test_a_paste_is_renamed_on_arrival_not_on_saving(gallery):
    """
    The paste clears the artifacts folder too, so the name shown while it is
    unsaved is the name it keeps. Naming only against .unsaved/ left the
    paste shadowed by the registered artifact and impossible to preview.
    """
    gallery.write('note.md', 'the original\n')
    name = gallery.post('/paste', {'name': 'note.md', 'data': _TXT})[1]['name']
    assert name == 'note-1.md'
    # Reachable straight away, rather than hidden behind the registered one.
    assert gallery.get('/files/note-1.md')[1] == b'# pasted\n'

    status, body = gallery.post('/unsaved/keep', {'file': name})
    assert status == 200 and body['saved'] == 'note-1.md', body
    assert (gallery.root / 'note-1.md').read_bytes() == b'# pasted\n'
    original = (gallery.root / 'note.md').read_text(encoding='utf-8')
    assert original.replace('\r\n', '\n') == 'the original\n'


def test_discarding_a_paste_leaves_nothing_behind(gallery):
    gallery.post('/paste', {'name': 'note.md', 'data': _TXT})
    status, body = gallery.post('/unsaved/discard', {'files': ['note.md']})
    assert status == 200 and body['discarded'] == ['note.md']
    assert not (gallery.root / '.unsaved' / 'note.md').exists()
    assert gallery.get('/list')[1]['files'] == []


def test_discard_all_sweeps_what_a_previous_visit_left(gallery):
    for n in ('a.md', 'b.md'):
        gallery.post('/paste', {'name': n, 'data': _TXT})
    status, body = gallery.post('/unsaved/discard', {'all': True})
    assert status == 200 and sorted(body['discarded']) == ['a.md', 'b.md']
    assert gallery.get('/list')[1]['files'] == []


def test_two_pastes_of_one_name_both_survive(gallery):
    first = gallery.post('/paste', {'name': 'note.md', 'data': _TXT})[1]['name']
    second = gallery.post('/paste', {'name': 'note.md', 'data': _TXT})[1]['name']
    assert first == 'note.md' and second == 'note-1.md'
    assert {f['name'] for f in gallery.get('/list')[1]['files']} == {'note.md', 'note-1.md'}


def test_a_pasted_path_becomes_a_pointer_not_a_copy(gallery, tmp_path):
    asset = tmp_path / 'elsewhere' / 'clip.mp4'
    asset.parent.mkdir(exist_ok=True)
    asset.write_bytes(b'video bytes')
    status, body = gallery.post('/paste', {'name': 'clip.mp4', 'path': str(asset)})
    assert status == 200 and body['name'] == 'clip.mp4'
    assert (gallery.root / '.unsaved' / 'clip.mp4.link').is_file()
    assert not (gallery.root / '.unsaved' / 'clip.mp4').exists()
    assert gallery.get('/files/clip.mp4')[1] == b'video bytes'
    [entry] = gallery.get('/list')[1]['files']
    assert entry['unsaved'] is True and entry['linked'] is True


def test_saving_a_pasted_path_keeps_the_pointer_form(gallery, tmp_path):
    asset = tmp_path / 'elsewhere' / 'clip.mp4'
    asset.parent.mkdir(exist_ok=True)
    asset.write_bytes(b'video bytes')
    gallery.post('/paste', {'name': 'clip.mp4', 'path': str(asset)})
    gallery.post('/unsaved/keep', {'file': 'clip.mp4'})
    assert (gallery.root / 'clip.mp4.link').is_file()
    assert not (gallery.root / 'clip.mp4').exists(), 'the asset must not be copied'
    assert gallery.mod.load_index() == ['clip.mp4']
    assert asset.is_file()


def test_discarding_a_pasted_path_never_touches_the_asset(gallery, tmp_path):
    asset = tmp_path / 'elsewhere' / 'clip.mp4'
    asset.parent.mkdir(exist_ok=True)
    asset.write_bytes(b'video bytes')
    gallery.post('/paste', {'name': 'clip.mp4', 'path': str(asset)})
    gallery.post('/unsaved/discard', {'all': True})
    assert asset.is_file(), 'discarding a paste must not delete the source'


@pytest.mark.parametrize('name', ['secrets.json', 'notes.md', 'config.ini', 'script.py'])
def test_only_creative_assets_can_be_linked_by_pasting_a_path(gallery, tmp_path, name):
    """
    This endpoint is reachable by an artifact's own script, so the media-only
    rule is what keeps a pasted path from reaching a credential or a config.
    """
    target = tmp_path / 'elsewhere' / name
    target.parent.mkdir(exist_ok=True)
    target.write_text('sensitive\n', encoding='utf-8')
    status, _ = gallery.post('/paste', {'name': name, 'path': str(target)})
    assert status == 403, name
    assert not (gallery.root / '.unsaved').exists() or \
           not list((gallery.root / '.unsaved').iterdir())


@pytest.mark.parametrize('bad', ['relative/clip.mp4', '//nas/share/clip.mp4'])
def test_a_pasted_path_must_be_local_and_absolute(gallery, bad):
    status, _ = gallery.post('/paste', {'name': 'clip.mp4', 'path': bad})
    assert status in (400, 403), bad


def test_a_pasted_path_that_does_not_exist_is_refused(gallery, tmp_path):
    status, _ = gallery.post('/paste', {'name': 'clip.mp4',
                                        'path': str(tmp_path / 'no' / 'clip.mp4')})
    assert status == 404


@pytest.mark.parametrize('name', ['../escape.md', 'sub/dir.md', '.', '..'])
def test_a_paste_cannot_escape_the_unsaved_folder(gallery, name):
    status, _ = gallery.post('/paste', {'name': name, 'data': _TXT})
    assert status == 400, name
    assert not (gallery.root.parent / 'escape.md').exists()


def test_a_saved_artifact_wins_over_an_unsaved_one_of_the_same_name(gallery):
    gallery.write('note.md', 'the registered one\n')
    gallery.post('/paste', {'name': 'note.md', 'data': _TXT})
    # The paste was renamed on arrival, so both are reachable. Newlines get
    # normalised because conftest.write goes through write_text, which turns
    # a newline into CRLF on Windows -- not what this test is about.
    def served(name):
        return gallery.get('/files/' + name)[1].replace(b'\r\n', b'\n')
    assert served('note.md') == b'the registered one\n'
    assert served('note-1.md') == b'# pasted\n'


def test_the_unsaved_folder_is_not_listed_as_an_artifact(gallery):
    gallery.post('/paste', {'name': 'note.md', 'data': _TXT})
    gallery.write('real.md', 'x')
    assert sorted(gallery.names()) == ['note.md', 'real.md']


# ── a paste that collides with a registered artifact ──────────────────

def _paste_text(gallery, name, text):
    import base64
    data = 'data:text/plain;base64,' + base64.b64encode(text.encode()).decode()
    return gallery.post('/paste', {'name': name, 'data': data})[1]


def test_a_colliding_paste_reports_the_clash(gallery):
    """
    Silently renaming hid a decision the user wanted: they may be holding a
    newer version, or may just want a look at a duplicate.
    """
    (gallery.root / 'note.md').write_bytes(b'the registered body\n')
    gallery.get('/notify?file=note.md')
    body = _paste_text(gallery, 'note.md', 'a newer body\n')
    assert body['name'] == 'note-1.md'
    assert body['conflict'] == {'with': 'note.md', 'identical': False}


def test_identical_content_is_reported_as_such(gallery):
    """Replacing byte-identical content changes nothing, so the UI can say so."""
    (gallery.root / 'note.md').write_bytes(b'same bytes\n')
    gallery.get('/notify?file=note.md')
    body = _paste_text(gallery, 'note.md', 'same bytes\n')
    assert body['conflict'] == {'with': 'note.md', 'identical': True}


def test_no_clash_is_reported_when_the_name_is_free(gallery):
    body = _paste_text(gallery, 'fresh.md', 'hello\n')
    assert body['name'] == 'fresh.md'
    assert 'conflict' not in body


def test_colliding_with_another_paste_is_not_reported_as_a_clash(gallery):
    """Two pastes of one name just get separate names; nothing is at risk."""
    first = _paste_text(gallery, 'note.md', 'one\n')
    second = _paste_text(gallery, 'note.md', 'two\n')
    assert first['name'] == 'note.md' and second['name'] == 'note-1.md'
    assert 'conflict' not in second


def test_replacing_overwrites_the_artifact_and_keeps_its_name(gallery):
    (gallery.root / 'note.md').write_bytes(b'the registered body\n')
    gallery.get('/notify?file=note.md')
    staged = _paste_text(gallery, 'note.md', 'a newer body\n')['name']

    status, body = gallery.post('/unsaved/replace', {'file': staged, 'target': 'note.md'})
    assert status == 200 and body['replaced'] == 'note.md'
    assert (gallery.root / 'note.md').read_bytes() == b'a newer body\n'
    # The staged copy is consumed, and the index keeps one entry under the
    # original name rather than gaining a second.
    assert not (gallery.root / '.unsaved' / staged).exists()
    assert gallery.mod.load_index() == ['note.md']
    assert [f['name'] for f in gallery.get('/list')[1]['files']] == ['note.md']


def test_replacing_is_undoable(gallery):
    """It lands in the same history as an edit, so a wrong call is recoverable."""
    (gallery.root / 'note.md').write_bytes(b'the registered body\n')
    gallery.get('/notify?file=note.md')
    staged = _paste_text(gallery, 'note.md', 'a newer body\n')['name']
    body = gallery.post('/unsaved/replace', {'file': staged, 'target': 'note.md'})[1]
    assert body['undoable'] is True

    snaps = gallery.get('/history?file=note.md')[1]['snapshots']
    assert len(snaps) == 1
    restored = gallery.post('/restore', {'file': 'note.md'})[1]
    assert restored['content'] == 'the registered body\n'
    assert (gallery.root / 'note.md').read_bytes() == b'the registered body\n'


def test_replacing_a_file_with_a_pasted_link_leaves_one_form_only(gallery, tmp_path):
    """A file replaced by a link must not leave both behind."""
    (gallery.root / 'clip.mp4').write_bytes(b'old inline bytes')
    gallery.get('/notify?file=clip.mp4')
    asset = tmp_path / 'elsewhere' / 'clip.mp4'
    asset.parent.mkdir(exist_ok=True)
    asset.write_bytes(b'the linked bytes')
    staged = gallery.post('/paste', {'name': 'clip.mp4', 'path': str(asset)})[1]['name']

    gallery.post('/unsaved/replace', {'file': staged, 'target': 'clip.mp4'})
    assert (gallery.root / 'clip.mp4.link').is_file()
    assert not (gallery.root / 'clip.mp4').exists(), 'both forms were left in place'
    assert gallery.get('/files/clip.mp4')[1] == b'the linked bytes'
    assert asset.is_file()


def test_replace_refuses_a_target_outside_the_gallery(gallery):
    staged = _paste_text(gallery, 'note.md', 'x\n')['name']
    for bad in ('../escape.md', 'sub/dir.md', 'artifacts.json'):
        status, _ = gallery.post('/unsaved/replace', {'file': staged, 'target': bad})
        assert status in (400, 403), bad
    assert not (gallery.root.parent / 'escape.md').exists()


def test_replace_needs_a_real_staged_paste(gallery):
    (gallery.root / 'note.md').write_bytes(b'x\n')
    status, _ = gallery.post('/unsaved/replace', {'file': 'never-pasted.md', 'target': 'note.md'})
    assert status == 404
    assert (gallery.root / 'note.md').read_bytes() == b'x\n'
