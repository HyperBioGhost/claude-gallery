"""
Shared fixtures: a real gallery server on a free port, against a temp
artifacts directory.

server.py resolves ROOT and friends at import time from its own location, so
the fixture rebinds those module globals before starting the server. That is
deliberate — it exercises the actual handler code rather than a reimplementation
of it.
"""
import importlib.util
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / 'src'


def _load_server():
    spec = importlib.util.spec_from_file_location('gallery_server', SRC / 'server.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Client:
    """Minimal HTTP client so the tests don't need `requests`."""

    def __init__(self, base: str, root: Path):
        self.base = base
        self.root = root

    def get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            body = r.read()
            ctype = r.headers.get('Content-Type', '')
            return r.status, (json.loads(body) if 'json' in ctype else body)

    def get_status(self, path: str) -> int:
        """Status only, without raising on 4xx/5xx."""
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def post(self, path: str, payload: dict):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST')
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                body = r.read()
                return r.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            return e.code, None

    def names(self) -> list[str]:
        return [f['name'] for f in self.get('/list')[1]['files']]

    def write(self, name: str, text: str, *, register=True) -> Path:
        """Put a file in the artifacts dir the way Claude Code would."""
        p = self.root / name
        p.write_text(text, encoding='utf-8')
        if register:
            self.get('/notify?file=' + urllib.parse.quote(name))
        return p


@pytest.fixture
def gallery(tmp_path, monkeypatch):
    root = tmp_path / 'artifacts'
    root.mkdir()
    mod = _load_server()
    mod.ROOT = root
    mod.THUMB_DIR = root / '.thumbnails'
    mod.HISTORY_DIR = root / '.history'
    mod.INDEX_FILE = root / 'artifacts.json'
    mod.LOG_FILE = root / 'gallery.log'
    mod.GALLERY_HTML = SRC / 'gallery.html'
    mod.setup_logging()

    port = _free_port()
    srv = mod.http.server.ThreadingHTTPServer(('127.0.0.1', port), mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    client = Client(f'http://127.0.0.1:{port}', root)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            client.get('/list')
            break
        except Exception:
            time.sleep(0.05)
    else:
        srv.shutdown()
        pytest.fail('test server did not come up')

    client.mod = mod
    yield client
    srv.shutdown()
    srv.server_close()
