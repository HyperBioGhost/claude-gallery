#!/usr/bin/env python3
"""
Claude Gallery Server
Serves the artifact gallery on port 7477.
Push model via SSE — zero polling, near-zero resource usage when idle.
"""
import http.server
import json
import os
import queue
import sys
import threading
import urllib.parse
from pathlib import Path

PORT = 7477
SKIP = {'gallery.html', 'server.py', 'claude-gallery-server', 'claude-gallery-server.exe'}

# Artifacts folder: ~/ClaudeGallery/artifacts
ARTIFACTS_DIR = Path.home() / 'ClaudeGallery' / 'artifacts'
GALLERY_HTML  = Path.home() / 'ClaudeGallery' / 'gallery.html'

_clients: list[queue.Queue] = []
_clients_lock = threading.Lock()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ('/', '/gallery.html'):
            self.serve_file(GALLERY_HTML, 'text/html')

        elif path == '/notify':
            fname = qs.get('file', [None])[0]
            if fname:
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
            files = []
            for f in sorted(ARTIFACTS_DIR.iterdir(), key=lambda x: -x.stat().st_mtime):
                if f.is_file() and f.name not in SKIP:
                    files.append({'name': f.name, 'mtime': int(f.stat().st_mtime * 1000)})
            body = json.dumps({'files': files}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(body))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)

        elif path.startswith('/files/'):
            name = path[7:]
            fpath = ARTIFACTS_DIR / name
            try:
                if not fpath.resolve().is_relative_to(ARTIFACTS_DIR.resolve()):
                    self.send_error(403)
                    return
            except ValueError:
                self.send_error(403)
                return
            if not fpath.is_file():
                self.send_error(404)
                return
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
    }.get(ext, 'application/octet-stream')


if __name__ == '__main__':
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(ARTIFACTS_DIR)
    threading.Thread(target=heartbeat, daemon=True).start()
    print(f'Claude Gallery → http://localhost:{PORT}', flush=True)
    try:
        with http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler) as srv:
            srv.serve_forever()
    except OSError as e:
        print(f'Failed to start: {e}', file=sys.stderr)
        sys.exit(1)
