#!/usr/bin/env python3
"""
Claude Gallery Server — push model via SSE
Port 7477. Zero polling — browser only updates when /notify is called.

Artifact ordering is tracked in artifacts.json (insertion order, newest first).
Files not in the index fall back to mtime and are added on next /notify call.

Every write goes through atomic_write_* so a crash mid-write cannot truncate
either an artifact or the index. Saves snapshot the previous content into
.history/ so an edit can be undone.
"""
import http.server, json, logging, logging.handlers, os, queue, shutil, sys, threading, time, traceback, urllib.parse
from pathlib import Path

PORT = 7477

# Replaced at release-build time by build/stamp_version.py, which is driven by
# the git tag in .github/workflows/release.yml. A build that still says
# 0.0.0-dev was not produced by the release pipeline.
VERSION = '0.0.0-dev'
IS_RELEASE = VERSION != '0.0.0-dev'


def resolve_display_version() -> str:
    """
    What to show the user.

    Release builds report the stamped tag. A source checkout asks git instead,
    so running from source still names something specific -- including whether
    the working tree is dirty. "0.0.0-dev" is an internal marker and is never
    shown: it tells the user nothing about what they are running.
    """
    if IS_RELEASE:
        return VERSION
    try:
        import subprocess
        src = Path(__file__).resolve().parent
        if not (src.parent / '.git').exists():
            return 'dev'
        out = subprocess.run(['git', 'describe', '--tags', '--always', '--dirty'],
                             cwd=src, capture_output=True, text=True, timeout=3)
        return out.stdout.strip().lstrip('v') or 'dev'
    except Exception:
        return 'dev'


DISPLAY_VERSION = resolve_display_version()
if getattr(sys, 'frozen', False):
    if sys.platform == 'win32':
        _docs = Path(os.environ.get('USERPROFILE', Path.home())) / 'Documents'
        _base = _docs / 'ClaudeGallery'
    else:
        _base = Path.home() / 'ClaudeGallery'
    ROOT = _base / 'artifacts'
    GALLERY_HTML = _base / 'gallery.html'
else:
    _src = Path(__file__).resolve().parent
    # Running from a checkout: the artifacts folder can live anywhere, so the
    # repo stays the only copy of the code. Without this the server could only
    # serve files sitting next to itself, which forced the code to be copied
    # into the artifacts folder and then drift from the repo.
    ROOT = Path(os.environ.get('CLAUDE_GALLERY_ROOT', _src)).expanduser()
    GALLERY_HTML = _src / 'gallery.html'
THUMB_DIR = ROOT / '.thumbnails'
HISTORY_DIR = ROOT / '.history'
LOG_FILE = ROOT.parent / 'gallery.log' if getattr(sys, 'frozen', False) else ROOT / 'gallery.log'
SKIP = {'gallery.html', 'server.py', 'artifacts.json', '.thumbnails', '.history',
        'gallery.log', 'gallery.log.1'}
INDEX_FILE = ROOT / 'artifacts.json'
HISTORY_KEEP = 10            # snapshots retained per file
UPDATE_CACHE_TTL = 6 * 3600  # don't re-ask GitHub more often than this
RELEASES_API = 'https://api.github.com/repos/HyperBioGhost/claude-gallery/releases/latest'
RELEASES_PAGE = 'https://github.com/HyperBioGhost/claude-gallery/releases/latest'

_clients: list[queue.Queue] = []
_clients_lock = threading.Lock()
_index_lock = threading.Lock()
_update_lock = threading.Lock()
_update_cache: dict = {}     # {'checked': epoch, 'result': {...}}

log = logging.getLogger('gallery')


def setup_logging() -> None:
    """
    Log to a rotating file next to the artifacts folder. Best-effort: an
    unwritable directory must not stop the server from serving.
    """
    log.setLevel(logging.INFO)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=1, encoding='utf-8')
        h.setFormatter(logging.Formatter('%(asctime)s %(levelname)-7s %(message)s'))
        log.addHandler(h)
    except Exception:
        log.addHandler(logging.NullHandler())


# ── Durable writes ───────────────────────────────────────────
# A plain write_text() that dies partway through leaves a truncated file. For
# artifacts.json that silently empties the gallery ordering (load_index()
# swallows the parse error); for an artifact it destroys the user's work.
# Writing to a sibling temp file and renaming makes the swap all-or-nothing.

def _atomic(path: Path, write) -> None:
    tmp = path.parent / (path.name + '.tmp')
    try:
        write(tmp)
        os.replace(tmp, path)   # same directory, so this is atomic
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

def atomic_write_text(path: Path, text: str) -> None:
    _atomic(path, lambda p: p.write_text(text, encoding='utf-8'))

def atomic_write_bytes(path: Path, data: bytes) -> None:
    _atomic(path, lambda p: p.write_bytes(data))


# ── Save history ─────────────────────────────────────────────
# Snapshot before overwriting so a bad edit is recoverable. Bounded to
# HISTORY_KEEP per file — this is undo, not version control.

def _hist_dir(fname: str) -> Path:
    return HISTORY_DIR / fname

def snapshot(fname: str) -> None:
    """Copy the current content of fname into its history, then prune."""
    src = ROOT / fname
    if not src.is_file():
        return
    d = _hist_dir(fname)
    try:
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d / f'{int(time.time() * 1000)}')
        snaps = sorted(p for p in d.iterdir() if p.is_file())
        for old in snaps[:-HISTORY_KEEP]:
            old.unlink(missing_ok=True)
    except Exception:
        # Losing a snapshot must never block the save itself.
        log.warning('snapshot failed for %s\n%s', fname, traceback.format_exc())

def history_list(fname: str) -> list[dict]:
    d = _hist_dir(fname)
    if not d.is_dir():
        return []
    out = []
    for p in d.iterdir():
        if p.is_file() and p.name.isdigit():
            out.append({'ts': int(p.name), 'size': p.stat().st_size})
    out.sort(key=lambda s: s['ts'], reverse=True)
    return out


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
    atomic_write_text(INDEX_FILE, json.dumps(order, indent=2, ensure_ascii=False))

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
        # .tmp is an in-flight atomic write, not an artifact
        if f.is_file() and f.name not in SKIP and not f.name.endswith('.tmp')
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


# ── Update check ─────────────────────────────────────────────
# The gallery asks once per page load. This is the only outbound request the
# app ever makes; it sends nothing but the request itself, and any failure is
# reported as "up to date" so being offline is silent.

def _ver_tuple(v: str) -> tuple:
    nums = []
    for part in v.split('-')[0].split('.'):
        nums.append(int(part) if part.isdigit() else 0)
    return tuple(nums)

def check_for_update() -> dict:
    current = {'current': DISPLAY_VERSION, 'outdated': False}
    if not IS_RELEASE or os.environ.get('CLAUDE_GALLERY_NO_UPDATE_CHECK'):
        # Don't nag on unreleased builds, and honour the opt-out.
        return current

    with _update_lock:
        cached = _update_cache.get('result')
        if cached and time.time() - _update_cache.get('checked', 0) < UPDATE_CACHE_TTL:
            return cached

    result = current
    try:
        import urllib.request
        req = urllib.request.Request(RELEASES_API, headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': f'claude-gallery/{DISPLAY_VERSION}',
        })
        with urllib.request.urlopen(req, timeout=3) as r:
            latest = json.loads(r.read()).get('tag_name', '').lstrip('v')
        if latest and _ver_tuple(latest) > _ver_tuple(VERSION):
            result = {'current': DISPLAY_VERSION, 'latest': latest,
                      'outdated': True, 'url': RELEASES_PAGE}
        log.info('update check: current=%s latest=%s outdated=%s',
                 VERSION, latest or '?', result['outdated'])
    except Exception as e:
        log.info('update check skipped: %s', e)

    with _update_lock:
        _update_cache['checked'] = time.time()
        _update_cache['result'] = result
    return result


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
                # Keep the old content before replacing it, so the edit is undoable
                snapshot(fname)
                atomic_write_text(fpath, content)
                log.info('saved %s (%d bytes)', fname, len(content))
                self._json({'saved': fname, 'undoable': bool(history_list(fname))})
            except Exception as e:
                log.error('save failed\n%s', traceback.format_exc())
                self.send_error(500, str(e))

        elif path == '/restore':
            # Undo: put a snapshot back. Snapshots the current content first, so
            # restoring is itself reversible.
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                fname = body.get('file', '')
                ts = body.get('ts')
                fpath = (ROOT / fname).resolve()
                if not fpath.is_relative_to(ROOT.resolve()) or fname in SKIP:
                    self.send_error(403); return
                snaps = history_list(fname)
                if not snaps:
                    self.send_error(404, 'no history'); return
                ts = int(ts) if ts is not None else snaps[0]['ts']
                src = _hist_dir(fname) / str(ts)
                if not src.is_file() or not src.resolve().is_relative_to(HISTORY_DIR.resolve()):
                    self.send_error(404, 'no such snapshot'); return
                data = src.read_bytes()
                snapshot(fname)
                atomic_write_bytes(fpath, data)
                log.info('restored %s from snapshot %s', fname, ts)
                self._json({'restored': fname, 'ts': ts,
                            'content': data.decode('utf-8', errors='replace')})
            except Exception as e:
                log.error('restore failed\n%s', traceback.format_exc())
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
                if deleted:
                    log.info('deleted %s', ', '.join(deleted))
                self._json({'deleted': deleted})
            except Exception as e:
                log.error('delete failed\n%s', traceback.format_exc())
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
                atomic_write_bytes(THUMB_DIR / (fname + '.png'), png_data)
                self._empty_ok()
            except Exception as e:
                self.send_error(500, str(e))

        elif path == '/import':
            try:
                import base64
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                imported = []
                for item in body.get('files', []):
                    raw_name = item.get('name', '')
                    data_url = item.get('data', '')
                    # Reject anything with path separators rather than silently
                    # rewriting it, so a caller can't smuggle in a directory.
                    if ('/' in raw_name or '\\' in raw_name
                            or raw_name in ('.', '..')):
                        continue
                    fname = os.path.basename(raw_name)
                    if not fname or not data_url or fname in SKIP:
                        continue
                    fpath = (ROOT / fname).resolve()
                    if not fpath.is_relative_to(ROOT.resolve()):
                        continue
                    # Avoid overwrite: append -1, -2 etc if the name is taken
                    if fpath.exists():
                        stem, dot, ext = fname.rpartition('.')
                        n = 1
                        while fpath.exists():
                            newname = f'{stem}-{n}{dot}{ext}' if dot else f'{fname}-{n}'
                            fpath = (ROOT / newname).resolve()
                            n += 1
                        fname = fpath.name
                    atomic_write_bytes(fpath, base64.b64decode(data_url.split(',', 1)[1]))
                    register_file(fname)
                    imported.append(fname)
                    log.info('imported %s', fname)
                with _clients_lock:
                    for f in imported:
                        for q in _clients:
                            q.put(f)
                self._json({'imported': imported})
            except Exception as e:
                log.error('import failed\n%s', traceback.format_exc())
                self.send_error(500, str(e))

        else:
            self.send_error(404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ('/', '/gallery.html'):
            self.serve_file(GALLERY_HTML, 'text/html')

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
            self._json({'files': ordered_files()})

        elif path == '/version':
            self._json({'version': DISPLAY_VERSION, 'release': IS_RELEASE})

        elif path == '/update-check':
            self._json(check_for_update())

        elif path == '/history':
            fname = qs.get('file', [''])[0]
            if not fname or fname in SKIP:
                self.send_error(400); return
            self._json({'file': fname, 'snapshots': history_list(fname)})

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

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

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
    if sys.platform == 'win32' and sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if sys.platform == 'win32' and sys.stderr is not None:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    ROOT.mkdir(parents=True, exist_ok=True)
    GALLERY_HTML.parent.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    setup_logging()
    # Version and root go in the log first: when someone reports a problem,
    # these are the two facts needed before anything else can be diagnosed.
    log.info('--- Claude Gallery %s starting (python %s on %s) ---',
             DISPLAY_VERSION, sys.version.split()[0], sys.platform)
    log.info('artifacts root: %s', ROOT)
    threading.Thread(target=heartbeat, daemon=True).start()
    if sys.stdout is not None:
        print(f'Claude Gallery {DISPLAY_VERSION} -> http://localhost:{PORT}', flush=True)
    try:
        with http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler) as srv:
            log.info('listening on 127.0.0.1:%d', PORT)
            srv.serve_forever()
    except OSError as e:
        log.error('failed to bind port %d: %s', PORT, e)
        if sys.stderr is not None:
            print(f'Failed to start: {e}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        log.info('stopped by keyboard interrupt')
