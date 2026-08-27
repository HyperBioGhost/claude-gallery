#!/usr/bin/env python3
"""
Stamp the release version into the sources before a build.

The git tag is the single source of truth. This script is the only thing that
writes a version number into the tree, and it refuses to run against a tree
that has already been stamped or hand-edited -- otherwise the placeholder
quietly rots back to a lie, which is exactly the failure this replaces
(every release from v1.0.0 to v2.2.0 shipped labelled "1.0.0").

Usage:
    python build/stamp_version.py 2.3.0
    python build/stamp_version.py --check      # verify the placeholder is intact
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLACEHOLDER = "VERSION = '0.0.0-dev'"
SERVER = REPO / 'src' / 'server.py'

# Files that must NOT carry a hardcoded version, with the pattern that would
# mean someone reintroduced one.
NO_HARDCODE = [
    (REPO / 'build' / 'windows' / 'installer.iss',
     re.compile(r'^\s*#define\s+AppVersion\s+"(?!0\.0\.0-dev)', re.M),
     'installer.iss must leave AppVersion to /DAppVersion= from CI'),
    (REPO / 'build' / 'mac' / 'build-pkg.sh',
     re.compile(r'^VERSION="(?!\$)(?!0\.0\.0-dev)', re.M),
     'build-pkg.sh must take VERSION from the environment'),
]


def fail(msg: str) -> None:
    print(f'stamp_version: {msg}', file=sys.stderr)
    sys.exit(1)


def check() -> None:
    text = SERVER.read_text(encoding='utf-8')
    if PLACEHOLDER not in text:
        found = re.search(r"^VERSION = .*$", text, re.M)
        fail(f'expected the placeholder {PLACEHOLDER!r} in src/server.py, found '
             f'{found.group(0) if found else "no VERSION line"!r}. The version '
             f'must come from the git tag, not from the source.')
    for path, pattern, why in NO_HARDCODE:
        if pattern.search(path.read_text(encoding='utf-8')):
            fail(f'{path.relative_to(REPO).as_posix()}: {why}')
    print('stamp_version: sources are unstamped and clean')


def stamp(version: str) -> None:
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+.\w]*)?', version):
        fail(f'{version!r} does not look like a semantic version')
    check()
    text = SERVER.read_text(encoding='utf-8')
    SERVER.write_text(text.replace(PLACEHOLDER, f"VERSION = '{version}'", 1),
                      encoding='utf-8')
    print(f'stamp_version: src/server.py -> {version}')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        fail(__doc__.strip().splitlines()[-3].strip())
    if sys.argv[1] == '--check':
        check()
    else:
        stamp(sys.argv[1])
