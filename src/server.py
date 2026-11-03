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


# Why the version fell back, if it did. Recorded rather than logged because
# this runs at import time, before setup_logging(); __main__ logs it afterwards.
_version_note = ''


def resolve_display_version() -> str:
    """
    What to show the user.

    Release builds report the stamped tag. A source checkout asks git instead,
    so running from source still names something specific -- including whether
    the working tree is dirty. "0.0.0-dev" is an internal marker and is never
    shown: it tells the user nothing about what they are running.

    Every fallback records a reason. A bare `except` here previously turned any
    failure into a silent "dev", which made the difference between "not a
    checkout", "git missing" and "git too slow" impossible to tell apart from
    the outside. The timeout is generous because the first git.exe launch after
    boot can be slow enough to lose a 3s race against on-access scanning.
    """
    global _version_note
    if IS_RELEASE:
        return VERSION
    try:
        import subprocess
        src = Path(__file__).resolve().parent
        if not (src.parent / '.git').exists():
            _version_note = f'no .git beside {src}, so there is no tag to read'
            return 'dev'
        out = subprocess.run(
            ['git', 'describe', '--tags', '--always', '--dirty'],
            cwd=src, capture_output=True, text=True, timeout=8,
            # pythonw has no usable console; give git a real stdin regardless.
            stdin=subprocess.DEVNULL)
        described = out.stdout.strip().lstrip('v')
        if not described:
            _version_note = (f'git describe exited {out.returncode}: '
                             f'{out.stderr.strip()[:200] or "no output"}')
            return 'dev'
        return described
    except Exception as e:
        _version_note = f'{type(e).__name__}: {e}'
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
# Grid-card thumbnails: one PNG per HTML artifact, captured by the browser
# when the artifact is opened. Deliberately allowed to go stale -- the grid
# shows the last capture rather than re-rendering every document on load.
# (.thumbnails/ was an earlier html2canvas cache and is no longer read.)
THUMBNAIL_DIR = ROOT / '.card-thumbnails'
HISTORY_DIR = ROOT / '.history'
# Pasted-in artifacts that have not been saved to the gallery. They live in
# their own folder, so "not a registered artifact" is literally true: nothing
# here is in artifacts.json, and leaving the gallery clears the folder.
# Named for the Save button rather than "temp", so the state and the action
# that resolves it share one word.
UNSAVED_DIR = ROOT / '.unsaved'
LOG_FILE = ROOT.parent / 'gallery.log' if getattr(sys, 'frozen', False) else ROOT / 'gallery.log'
SKIP = {'gallery.html', 'server.py', 'artifacts.json', '.thumbnails', '.card-thumbnails', '.history',
        '.unsaved', 'gallery.log', 'gallery.log.1'}
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


def thumbnail_path(fname: str) -> Path:
    return THUMBNAIL_DIR / (fname + '.png')

def thumbnail_mtime(fname: str):
    """Epoch ms of the card thumbnail for fname, or None if never captured."""
    p = thumbnail_path(fname)
    return int(p.stat().st_mtime * 1000) if p.is_file() else None


# ── Virtual links ──────────────────────────────────────────────────────
# A `<display-name>.link` file holds the absolute path of an asset that lives
# wherever its project keeps it. The gallery presents the asset without a copy
# landing in the artifacts folder.
#
# Scope is the machine, not a folder: any local fixed volume is allowed, and
# anything off this machine is refused. A pointer can only be created by
# writing to disk -- /save and /import refuse the name -- because artifacts
# run same-origin and could otherwise mint a pointer at any file and read it.

LINK_SUFFIX = '.link'


def is_local_fixed_path(target: Path) -> tuple[bool, str]:
    """Is this path on a local fixed volume? Returns (ok, reason-if-not)."""
    raw = str(target)
    if raw.startswith('\\\\') or raw.startswith('//'):
        return False, 'network path'
    if sys.platform == 'win32':
        drive = os.path.splitdrive(str(target.resolve()))[0]
        if not drive:
            return False, 'no drive letter'
        try:
            import ctypes
            # 0 unknown, 1 no root, 2 removable, 3 fixed, 4 remote, 5 cdrom, 6 ramdisk
            kind = ctypes.windll.kernel32.GetDriveTypeW(drive + '\\')
        except Exception as e:
            return False, f'could not identify the drive ({e})'
        names = {0: 'unknown', 1: 'missing', 2: 'removable',
                 3: 'fixed', 4: 'network', 5: 'optical', 6: 'ram disk'}
        if kind != 3:
            return False, f'{names.get(kind, kind)} drive'
        return True, ''
    # macOS and Linux: external media mounts under /Volumes or /media.
    parts = target.resolve().parts
    if len(parts) > 1 and parts[1] in ('Volumes', 'media', 'mnt', 'net'):
        return False, f'mounted volume (/{parts[1]})'
    return True, ''


def read_link(pointer: Path) -> dict:
    """
    Resolve a pointer file. Always returns a dict; `error` is set when the
    asset cannot be served, so the gallery can say why instead of 404ing.
    """
    info = {'pointer': pointer.name, 'name': pointer.name[:-len(LINK_SUFFIX)],
            'target': None, 'error': None}
    try:
        lines = [l.strip() for l in pointer.read_text(encoding='utf-8').splitlines()]
    except Exception as e:
        info['error'] = f'cannot read the link file: {e}'
        return info
    body = [l for l in lines if l and not l.startswith('#')]
    if not body:
        info['error'] = 'the link file is empty'
        return info
    target = Path(os.path.expandvars(body[0])).expanduser()
    if not target.is_absolute():
        info['error'] = f'not an absolute path: {body[0]}'
        return info
    ok, why = is_local_fixed_path(target)
    if not ok:
        info['error'] = f'refused, {why}: {target}'
        return info
    info['target'] = target
    if not target.exists():
        info['error'] = f'target is missing: {target}'
    elif not target.is_file():
        info['error'] = f'target is not a file: {target}'
    return info


def link_pointers() -> dict:
    """display name -> resolved link info, for every pointer in ROOT."""
    out = {}
    for f in ROOT.iterdir():
        if f.is_file() and f.name.endswith(LINK_SUFFIX):
            info = read_link(f)
            if info['name']:
                out[info['name']] = info
    return out


def resolve_served_path(name: str):
    """
    Where the bytes for `name` live: the artifacts folder, a link target, or an
    unsaved paste. Returns (path, error). A real file always wins over a
    pointer, and a saved artifact wins over an unsaved one of the same name.
    """
    direct = ROOT / name
    try:
        if direct.is_file() and direct.resolve().is_relative_to(ROOT.resolve()):
            return direct, None
    except ValueError:
        return None, 'outside the artifacts folder'
    pointer = ROOT / (name + LINK_SUFFIX)
    if pointer.is_file():
        info = read_link(pointer)
        if info['error']:
            return None, info['error']
        return info['target'], None
    # Not saved to the gallery yet.
    if name in unsaved_names():
        why = unsaved_error(name)
        if why:
            return None, why
        got = unsaved_path(name)
        return (got, None) if got else (None, 'the pasted file is unreadable')
    return None, None              # plain 404, not a link problem


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
        # .tmp is an in-flight atomic write, not an artifact; .link is the
        # pointer, listed below under the name of the asset it points at.
        if f.is_file() and f.name not in SKIP and not f.name.endswith('.tmp')
        and not f.name.endswith(LINK_SUFFIX)
    }

    # Linked assets. The date is the target's, so grouping by date describes
    # the asset rather than when the pointer was written.
    links = link_pointers()
    for name, info in links.items():
        if name in on_disk:
            continue               # a real file of that name wins
        when = None
        if info['target'] and not info['error']:
            try:
                when = int(info['target'].stat().st_mtime * 1000)
            except OSError:
                pass
        if when is None:
            when = int((ROOT / info['pointer']).stat().st_mtime * 1000)
        on_disk[name] = when

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
    # Unsaved pastes sit at the top: they are the thing the user is looking
    # at and the thing that disappears if they walk away.
    for entry in unsaved_entries():
        combined.insert(0, entry)

    for f in combined:
        f['thumbnail_mtime'] = thumbnail_mtime(f['name'])
        info = links.get(f['name'])
        if info:
            # The gallery says so on the card, and the delete wording changes:
            # removing a linked artifact removes the pointer, not the asset.
            f['linked'] = True
            f['link_target'] = str(info['target']) if info['target'] else None
            f['link_error'] = info['error']
    return combined


# ── Unsaved pastes ─────────────────────────────────────────────────────

def unsaved_names() -> set:
    if not UNSAVED_DIR.is_dir():
        return set()
    out = set()
    for f in UNSAVED_DIR.iterdir():
        if not f.is_file():
            continue
        out.add(f.name[:-len(LINK_SUFFIX)] if f.name.endswith(LINK_SUFFIX) else f.name)
    return out


def unsaved_entries() -> list:
    """List entries for every unsaved paste, newest first."""
    rows = []
    for name in unsaved_names():
        direct = UNSAVED_DIR / name
        pointer = UNSAVED_DIR / (name + LINK_SUFFIX)
        row = {'name': name, 'unsaved': True}
        if direct.is_file():
            row['mtime'] = int(direct.stat().st_mtime * 1000)
        elif pointer.is_file():
            info = read_link(pointer)
            row['linked'] = True
            row['link_target'] = str(info['target']) if info['target'] else None
            row['link_error'] = info['error']
            when = None
            if info['target'] and not info['error']:
                try:
                    when = int(info['target'].stat().st_mtime * 1000)
                except OSError:
                    pass
            row['mtime'] = when if when is not None else int(pointer.stat().st_mtime * 1000)
        else:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r['mtime'], reverse=True)
    return rows


def unsaved_path(name: str):
    """Where an unsaved paste's bytes are, or None."""
    direct = UNSAVED_DIR / name
    if direct.is_file():
        return direct
    pointer = UNSAVED_DIR / (name + LINK_SUFFIX)
    if pointer.is_file():
        info = read_link(pointer)
        return None if info['error'] else info['target']
    return None


def unsaved_error(name: str):
    pointer = UNSAVED_DIR / (name + LINK_SUFFIX)
    if pointer.is_file():
        return read_link(pointer)['error']
    return None


def free_artifact_name(name: str, *folders: Path) -> str:
    """
    A name not taken in any of `folders`, appending -1, -2 and so on.

    A paste has to clear the artifacts folder as well as .unsaved/, because a
    registered artifact of the same name wins when the bytes are looked up --
    so a paste named only against .unsaved/ would be invisible.
    """
    def taken(candidate: str) -> bool:
        return any((f / candidate).exists() or (f / (candidate + LINK_SUFFIX)).exists()
                   for f in folders)

    if not taken(name):
        return name
    stem, dot, ext = name.rpartition('.')
    n = 1
    while True:
        candidate = f'{stem}-{n}{dot}{ext}' if dot else f'{name}-{n}'
        if not taken(candidate):
            return candidate
        n += 1


def same_bytes(a: Path, b: Path) -> bool:
    """Are these two files byte-identical? Size first, so most calls are cheap."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    import hashlib
    def digest(f: Path) -> str:
        h = hashlib.sha256()
        with f.open('rb') as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b''):
                h.update(chunk)
        return h.hexdigest()
    try:
        return digest(a) == digest(b)
    except OSError:
        return False


def paste_conflict(staged_name: str, wanted_name: str):
    """
    Does a registered artifact already hold the name this paste asked for?

    Returns None when the name was free, otherwise the registered name and
    whether the content matches -- because "replace it" is worth offering for
    a newer version and pointless for an identical copy.
    """
    # A name taken by another unsaved paste is not a clash: nothing registered
    # is at risk, and the second paste simply gets its own name. This also
    # covers the case where the name was free, since then the folder cannot
    # hold it either.
    if not ((ROOT / wanted_name).is_file() or (ROOT / (wanted_name + LINK_SUFFIX)).is_file()):
        return None
    staged = unsaved_path(staged_name)
    existing, why = resolve_served_path(wanted_name)
    identical = bool(staged and existing and not why and same_bytes(staged, existing))
    return {'with': wanted_name, 'identical': identical}


def discard_unsaved(names) -> list:
    """Remove unsaved pastes. A pointer here never touches its target."""
    gone = []
    for name in names:
        hit = False
        for candidate in (UNSAVED_DIR / name, UNSAVED_DIR / (name + LINK_SUFFIX)):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(UNSAVED_DIR.resolve()):
                continue
            if candidate.is_file():
                candidate.unlink()
                hit = True
        if hit:
            thumbnail_path(name).unlink(missing_ok=True)
            gone.append(name)
    return gone


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
                if fname.endswith(LINK_SUFFIX):
                    # Artifacts run same-origin and can reach this endpoint, so
                    # a pointer minted here could name any file on the machine
                    # and then be read back. Pointers are written to disk only.
                    self.send_error(403, 'a link file cannot be created over HTTP')
                    return
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
                        thumbnail_path(fname).unlink(missing_ok=True)
                        deleted.append(fname)
                        continue
                    # A linked artifact: drop the pointer and leave the asset
                    # where its project keeps it.
                    pointer = ROOT / (fname + LINK_SUFFIX)
                    if pointer.is_file():
                        pointer.unlink()
                        thumbnail_path(fname).unlink(missing_ok=True)
                        deleted.append(fname)
                        log.info('removed link %s (asset untouched)', fname)
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
                if (not fname or not data_url or fname in SKIP
                        or fname.endswith(LINK_SUFFIX)):
                    self.send_error(400); return
                # Resolve through a pointer: a linked video has no file of its
                # own name in the folder, and requiring one refused its
                # thumbnail with a 403 and left the card without a picture.
                source, why = resolve_served_path(fname)
                target = thumbnail_path(fname).resolve()
                if (source is None or why
                        or not target.is_relative_to(THUMBNAIL_DIR.resolve())):
                    self.send_error(403, why or 'no such artifact'); return
                THUMBNAIL_DIR.mkdir(exist_ok=True)
                png_data = base64.b64decode(data_url.split(',', 1)[1])
                atomic_write_bytes(target, png_data)
                self._json({'file': fname, 'thumbnail_mtime': thumbnail_mtime(fname)})
            except Exception as e:
                log.error('thumbnail save failed\n%s', traceback.format_exc())
                self.send_error(500, str(e))

        elif path == '/paste':
            # Hold pasted content without registering it. Bytes are written
            # into .unsaved/; a pasted path becomes a pointer there.
            try:
                import base64
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                raw_name = body.get('name', '')
                if ('/' in raw_name or '\\' in raw_name or raw_name in ('.', '..')
                        or not raw_name or raw_name in SKIP):
                    self.send_error(400, 'bad name'); return
                UNSAVED_DIR.mkdir(exist_ok=True)
                wanted = os.path.basename(raw_name)
                name = free_artifact_name(wanted, UNSAVED_DIR, ROOT)

                if body.get('path'):
                    # A pasted path is served straight from where it lives, so
                    # it previews at once. Restricted to media extensions: this
                    # endpoint is reachable by an artifact's own script, and a
                    # media-only rule keeps that from reaching anything else.
                    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
                    if ext not in PASTE_LINK_EXT:
                        self.send_error(403, f'.{ext} cannot be linked by pasting a path')
                        return
                    target = Path(os.path.expandvars(body['path'])).expanduser()
                    if not target.is_absolute():
                        self.send_error(400, 'not an absolute path'); return
                    ok, why = is_local_fixed_path(target)
                    if not ok:
                        self.send_error(403, f'refused, {why}'); return
                    if not target.is_file():
                        self.send_error(404, f'no such file: {target}'); return
                    atomic_write_text(UNSAVED_DIR / (name + LINK_SUFFIX),
                                      f'# pasted path, not yet saved\n{target}\n')
                    log.info('pasted link %s -> %s', name, target)
                else:
                    data_url = body.get('data', '')
                    if not data_url:
                        self.send_error(400, 'nothing to paste'); return
                    payload = base64.b64decode(data_url.split(',', 1)[1])
                    atomic_write_bytes(UNSAVED_DIR / name, payload)
                    log.info('pasted %s (%d bytes, unsaved)', name, len(payload))
                reply = {'name': name, 'unsaved': True}
                clash = paste_conflict(name, wanted)
                if clash:
                    reply['conflict'] = clash
                    log.info('pasted %s collides with %s (identical=%s)',
                             name, clash['with'], clash['identical'])
                self._json(reply)
            except Exception as e:
                log.error('paste failed\n%s', traceback.format_exc())
                self.send_error(500, str(e))

        elif path == '/unsaved/keep':
            # Save to gallery: move out of .unsaved/ and register it.
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                name = body.get('file', '')
                if name not in unsaved_names():
                    self.send_error(404, 'not an unsaved paste'); return
                final = free_artifact_name(name, ROOT)
                moved = None
                for suffix in ('', LINK_SUFFIX):
                    src = UNSAVED_DIR / (name + suffix)
                    if src.is_file():
                        os.replace(src, ROOT / (final + suffix))
                        moved = final + suffix
                if moved is None:
                    self.send_error(404, 'nothing to save'); return
                if final != name:
                    thumbnail_path(name).unlink(missing_ok=True)
                else:
                    old = thumbnail_path(name)
                    if old.is_file():
                        os.replace(old, thumbnail_path(final))
                register_file(final)
                with _clients_lock:
                    for q in _clients:
                        q.put(final)
                log.info('saved pasted artifact as %s', final)
                self._json({'saved': final, 'was': name})
            except Exception as e:
                log.error('keep failed\n%s', traceback.format_exc())
                self.send_error(500, str(e))

        elif path == '/unsaved/replace':
            # Overwrite a registered artifact with a paste. Snapshotted first,
            # so this lands in the same history as an edit and can be undone.
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                staged = body.get('file', '')
                target = body.get('target', '')
                if staged not in unsaved_names():
                    self.send_error(404, 'not an unsaved paste'); return
                if target in SKIP or '/' in target or '\\' in target:
                    self.send_error(400, 'bad target'); return
                dest = (ROOT / target).resolve()
                if not dest.is_relative_to(ROOT.resolve()):
                    self.send_error(403); return
                direct = UNSAVED_DIR / staged
                pointer = UNSAVED_DIR / (staged + LINK_SUFFIX)
                if not (direct.is_file() or pointer.is_file()):
                    self.send_error(404, 'nothing to replace with'); return

                snapshot(target)
                # Whichever form the old artifact had, it goes: replacing a
                # file with a link (or the reverse) must not leave both.
                (ROOT / target).unlink(missing_ok=True)
                (ROOT / (target + LINK_SUFFIX)).unlink(missing_ok=True)
                if direct.is_file():
                    os.replace(direct, ROOT / target)
                else:
                    os.replace(pointer, ROOT / (target + LINK_SUFFIX))
                thumbnail_path(staged).unlink(missing_ok=True)
                thumbnail_path(target).unlink(missing_ok=True)
                register_file(target)
                with _clients_lock:
                    for q in _clients:
                        q.put(target)
                log.info('replaced %s with pasted %s', target, staged)
                self._json({'replaced': target, 'from': staged,
                            'undoable': bool(history_list(target))})
            except Exception as e:
                log.error('replace failed\n%s', traceback.format_exc())
                self.send_error(500, str(e))

        elif path == '/unsaved/discard':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                names = sorted(unsaved_names()) if body.get('all') else body.get('files', [])
                gone = discard_unsaved(names)
                if gone:
                    log.info('discarded unsaved %s', ', '.join(gone))
                self._json({'discarded': gone})
            except Exception as e:
                log.error('discard failed\n%s', traceback.format_exc())
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
                    if fname.endswith(LINK_SUFFIX):
                        log.info('refused imported link file: %s', fname)
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
            fpath = (THUMBNAIL_DIR / path[7:]).resolve()
            if fpath.is_relative_to(THUMBNAIL_DIR.resolve()) and fpath.is_file():
                self.serve_file(fpath, 'image/png')
            else:
                self.send_error(404)

        elif path.startswith('/files/'):
            self.serve_artifact(path[7:])

        else:
            self.send_error(404)

    def serve_artifact(self, name: str, head_only: bool = False):
        """Serve an artifact, whether it sits in the folder or behind a link."""
        fpath, why = resolve_served_path(name)
        if why:
            # Say what is wrong with the link rather than return a bare 404.
            log.info('link unavailable for %s: %s', name, why)
            self.send_error(409, why)
            return
        if fpath is None:
            self.send_error(404); return
        self.serve_file(fpath, guess_mime(name), head_only=head_only)

    def do_HEAD(self):
        """Some players probe with HEAD before requesting ranges."""
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path.startswith('/files/'):
            self.serve_artifact(path[7:], head_only=True)
        elif path in ('/', '/gallery.html'):
            self.serve_file(GALLERY_HTML, 'text/html', head_only=True)
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

    def serve_file(self, fpath: Path, mime: str, head_only: bool = False):
        """
        Stream a file, honouring Range.

        This used to read the whole file into memory and ignore Range, which
        meant a 45 MB wav was read in full on every request and a video could
        not be seeked at all -- a player asks for a byte range and needs 206
        with Content-Range to scrub.
        """
        try:
            size = fpath.stat().st_size
        except FileNotFoundError:
            self.send_error(404); return
        except OSError as e:
            log.error('cannot stat %s: %s', fpath, e)
            self.send_error(500); return

        start, end = 0, size - 1
        partial = False
        rng = self.headers.get('Range')
        if rng and size:
            got = parse_byte_range(rng, size)
            if got is None:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            start, end = got
            partial = True

        length = 0 if size == 0 else end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(length))
        # Without this a player will not attempt to seek at all.
        self.send_header('Accept-Ranges', 'bytes')
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if head_only or not length:
            return

        try:
            with fpath.open('rb') as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(STREAM_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Normal: the player seeked elsewhere or the tab moved on.
            pass
        except Exception:
            log.error('while streaming %s\n%s', fpath, traceback.format_exc())


def heartbeat():
    import time
    while True:
        time.sleep(20)
        with _clients_lock:
            for q in _clients:
                q.put('__ping__')


STREAM_CHUNK = 256 * 1024


def parse_byte_range(header: str, size: int):
    """
    `Range: bytes=...` -> (start, end) inclusive, or None if unsatisfiable.
    Only a single range is honoured; a multi-range request falls back to its
    first range, which is what players actually send.
    """
    if not header or not header.strip().lower().startswith('bytes='):
        return None
    spec = header.split('=', 1)[1].split(',')[0].strip()
    if '-' not in spec:
        return None
    lo, _, hi = spec.partition('-')
    try:
        if not lo:                      # bytes=-N  -> the final N bytes
            n = int(hi)
            if n <= 0:
                return None
            start = max(0, size - n)
            return start, size - 1
        start = int(lo)
        end = int(hi) if hi else size - 1
    except ValueError:
        return None
    if start >= size or start < 0 or end < start:
        return None
    return start, min(end, size - 1)


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
        'srt':  'text/plain; charset=utf-8',
        # Creative assets. Served with Range support, so they seek and stream
        # rather than being loaded whole.
        'mp4':  'video/mp4',   'm4v': 'video/mp4',
        'webm': 'video/webm',  'mov': 'video/quicktime',
        'mkv':  'video/x-matroska',
        'mp3':  'audio/mpeg',  'wav': 'audio/wav',
        'm4a':  'audio/mp4',   'aac': 'audio/aac',
        'ogg':  'audio/ogg',   'flac': 'audio/flac',
    }.get(ext, 'application/octet-stream')


MEDIA_EXT = {'mp4', 'm4v', 'webm', 'mov', 'mkv',
             'mp3', 'wav', 'm4a', 'aac', 'ogg', 'flac'}

# A pasted path is turned into a pointer over HTTP, and this endpoint is
# reachable by an artifact's own script. Restricting it to creative assets
# means the worst an abuser gets is a media file, never a credential or a
# config. Pointers for anything else are still written to disk only.
PASTE_LINK_EXT = MEDIA_EXT | {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}


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
    if _version_note:
        log.warning('version shown as "%s" -- %s', DISPLAY_VERSION, _version_note)
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
