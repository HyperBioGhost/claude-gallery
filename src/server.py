#!/usr/bin/env python3
"""
Claude Gallery Server — push model via SSE
Port 7477. Zero polling — browser only updates when /notify is called.

Artifact ordering is tracked in artifacts.json (insertion order, newest first).
Files not in the index fall back to mtime and are added on next /notify call.
"""
import http.server, json, os, queue, threading, urllib.parse
from pathlib import Path

PORT = 7477
ROOT = Path(__file__).parent
THUMB_DIR = ROOT / '.thumbnails'
SKIP = {'gallery.html', 'server.py', 'artifacts.json', '.thumbnails'}
INDEX_FILE = ROOT / 'artifacts.json'

_clients: list[queue.Queue] = []
_clients_lock = threading.Lock()
_index_lock = threading.Lock()


# ── Insertion-order index ──────────────────────────────────────────────
# artifacts.json stores a list of filenames in insertion order (oldest first).
# The /list endpoint reverses it so newest appears at the top of the gallery.

def load_index() -> list[str]:
    """Return list of known filenames, oldest-first."""
    try:
        data = json.loads(INDEX_FILE.read_text(encoding='utf-8'))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []

def save_index(order: list[str]) -> None:
    INDEX_FILE.write_text(json.dumps(order, indent=2, ensure_ascii=False), encoding='utf-8')

def register_file(fname: str) -> None:
    """Add fname to the index if not already present."""
    with _index_lock:
        order = load_index()
        if fname not in order:
            order.append(fname)
            save_index(order)

def ordered_files() -> list[dict]:
    """
    Return file list newest-first using the index for ordering.
    Files on disk but not in the index are appended by mtime (handles
    files added outside of /notify, e.g. manual copies).
    """
    with _index_lock:
        order = load_index()

    on_disk = {
        f.name: int(f.stat().st_mtime * 1000)
        for f in ROOT.iterdir()
        if f.is_file() and f.name not in SKIP
    }

    # Files known to the index, still present on disk
    indexed = [
        {'name': n, 'mtime': on_disk[n]}
        for n in order if n in on_disk
    ]

    # Files on disk but not yet indexed — sort by mtime ascending so they
    # slot in before the indexed ones when reversed
    unindexed_names = set(on_disk) - {f['name'] for f in indexed}
    unindexed = sorted(
        [{'name': n, 'mtime': on_disk[n]} for n in unindexed_names],
        key=lambda f: f['mtime']
    )

    # Combine: unindexed (oldest) + indexed (in arrival order), then reverse
    combined = unindexed + indexed
    combined.reverse()   # newest first
    return combined


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        if path == '/save':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                fname = body.get('file', '')
                content = body.get('content', '')
                fpath = (ROOT / fname).resolve()
                if not fpath.is_relative_to(ROOT.resolve()) or fname in SKIP:
                    self.send_error(403); return
                fpath.write_text(content, encoding='utf-8')
                self._empty_ok()
            except Exception as e:
                self.send_error(500, str(e))

        elif path == '/delete':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                names = body.get('files', [])
                deleted = []
                for fname in names:
                    fpath = (ROOT / fname).resolve()
                    if not fpath.is_relative_to(ROOT.resolve()) or fname in SKIP:
                        continue
                    if fpath.is_file():
                        fpath.unlink()
                        deleted.append(fname)
                with _index_lock:
                    order = load_index()
                    order = [n for n in order if n not in deleted]
                    save_index(order)
                resp = json.dumps({'deleted': deleted}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(resp))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                self.send_error(500, str(e))

        elif path == '/thumb':
            try:
                import base64
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                fname = body.get('file', '')
                data_url = body.get('data', '')
                if not fname or not data_url:
                    self.send_error(400); return
                THUMB_DIR.mkdir(exist_ok=True)
                png_data = base64.b64decode(data_url.split(',', 1)[1])
                (THUMB_DIR / (fname + '.png')).write_bytes(png_data)
                self._empty_ok()
            except Exception as e:
                self.send_error(500, str(e))

        else:
            self.send_error(404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ('/', '/gallery.html'):
            self.serve_file(ROOT / 'gallery.html', 'text/html')

        elif path == '/notify':
            fname = qs.get('file', [None])[0]
            if fname:
                register_file(fname)
                with _clients_lock:
                    for q in _clients:
                        q.put(fname)
            self._empty_ok()

        elif path == '/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            q: queue.Queue = queue.Queue()
            with _clients_lock:
                _clients.append(q)
            try:
                while True:
                    fname = q.get(timeout=25)
                    if fname == '__ping__':
                        self.wfile.write(b': ping\n\n')
                    else:
                        msg = f'data: {json.dumps({"file": fname})}\n\n'
                        self.wfile.write(msg.encode())
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                with _clients_lock:
                    if q in _clients:
                        _clients.remove(q)

        elif path == '/list':
            files = ordered_files()
            body = json.dumps({'files': files}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(body))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)

        elif path.startswith('/thumb/'):
            name = path[7:]
            fpath = THUMB_DIR / name
            if fpath.is_file():
                self.serve_file(fpath, 'image/png')
            else:
                self.send_error(404)

        elif path.startswith('/files/'):
            name = path[7:]
            fpath = ROOT / name
            try:
                if not fpath.resolve().is_relative_to(ROOT.resolve()):
                    self.send_error(403); return
            except ValueError:
                self.send_error(403); return
            if not fpath.is_file():
                self.send_error(404); return
            self.serve_file(fpath, guess_mime(name))

        else:
            self.send_error(404)

    def _empty_ok(self):
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def serve_file(self, fpath: Path, mime: str):
        try:
            data = fpath.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', len(data))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)
        except Exception:
            self.send_error(500)


def heartbeat():
    import time
    while True:
        time.sleep(20)
        with _clients_lock:
            for q in _clients:
                q.put('__ping__')


def guess_mime(name: str) -> str:
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    return {
        'html': 'text/html', 'htm': 'text/html',
        'svg':  'image/svg+xml',
        'png':  'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'gif':  'image/gif', 'webp': 'image/webp',
        'json': 'application/json',
        'js':   'text/javascript',
        'css':  'text/css',
        'md':   'text/plain; charset=utf-8',
        'txt':  'text/plain; charset=utf-8',
        'csv':  'text/plain; charset=utf-8',
        'ts':   'text/plain; charset=utf-8',
        'py':   'text/plain; charset=utf-8',
        'xml':  'text/xml; charset=utf-8',
        'yaml': 'text/plain; charset=utf-8',
        'yml':  'text/plain; charset=utf-8',
        'mermaid': 'text/plain; charset=utf-8',
        'mmd':  'text/plain; charset=utf-8',
    }.get(ext, 'application/octet-stream')


if __name__ == '__main__':
    ROOT.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    threading.Thread(target=heartbeat, daemon=True).start()
    print(f'Claude Gallery → http://localhost:{PORT}', flush=True)
    try:
        with http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler) as srv:
            srv.serve_forever()
    except OSError as e:
        import sys
        print(f'Failed to start: {e}', file=sys.stderr)
        sys.exit(1)
