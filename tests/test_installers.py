"""
Installer behaviour that can be checked without installing.

The instructions block in CLAUDE.md is rewritten on every install, upgrades
included. It used to be added only when missing, so anyone who installed
before a rule changed (the collection rule in v2.9.0) never received it.

Both installers' scripts run the same cases: the Windows PowerShell one where
PowerShell exists, and the Mac one (plain bash and perl) anywhere bash does.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / 'src'
START, END = '<!-- claude-gallery-start -->', '<!-- claude-gallery-end -->'

OLD_BLOCK = f'{START}\n# Claude Gallery\n\nAn old rule set, before collections.\n{END}'
MINE_BEFORE = '# My own rules\n\nKeep answers short.'
MINE_AFTER = '## Later rules\n\nUse British spelling.'


def _shell(kind):
    if kind == 'windows':
        if sys.platform != 'win32':
            pytest.skip('Windows PowerShell script')
        return shutil.which('powershell') or shutil.which('pwsh')
    bash = shutil.which('bash')
    if not bash or not shutil.which('perl'):
        pytest.skip('needs bash and perl')
    return bash


def run(kind, tmp_path, existing=None):
    """Run the installer's instructions step on a scratch CLAUDE.md; return its bytes."""
    claude_md = tmp_path / '.claude' / 'CLAUDE.md'
    if existing is not None:
        claude_md.parent.mkdir(parents=True, exist_ok=True)
        claude_md.write_bytes(existing)
    if kind == 'windows':
        cmd = [_shell(kind), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SRC / 'inject-claude.ps1'),
               '-ClaudeMd', str(claude_md), '-Artifacts', str(tmp_path / 'artifacts')]
    else:
        cmd = [_shell(kind), str(REPO / 'build' / 'mac' / 'update-instructions.sh'),
               claude_md.as_posix(), (SRC / 'claude-instructions.md').as_posix()]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return claude_md.read_bytes()


both = pytest.mark.parametrize('kind', ['windows', 'mac'])


def test_the_instructions_carry_todays_rules():
    text = (SRC / 'claude-instructions.md').read_text(encoding='utf-8')
    assert '&collection=<name>' in text
    assert START not in text and END not in text, 'the installers add the markers'


@both
@pytest.mark.parametrize('nl', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_an_upgrade_replaces_the_old_block_where_it_sits(tmp_path, kind, nl):
    before = f'{MINE_BEFORE}\n\n{OLD_BLOCK}\n\n{MINE_AFTER}\n'.replace('\n', nl).encode()
    raw = run(kind, tmp_path, before)
    text = raw.decode('utf-8')
    assert text.count(START) == 1 and text.count(END) == 1, text
    assert 'An old rule set' not in text and '&collection=<name>' in text
    # The person's own sections keep their order and spacing.
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1]
    assert head == (MINE_BEFORE + '\n\n').replace('\n', nl), repr(head)
    assert tail == ('\n\n' + MINE_AFTER + '\n').replace('\n', nl), repr(tail)
    # One kind of line ending throughout.
    if nl == '\r\n':
        assert '\n' not in text.replace('\r\n', ''), 'bare LF mixed into a CRLF file'
    else:
        assert '\r' not in text
    assert not raw.startswith(b'\xef\xbb\xbf'), 'no byte-order mark'


@both
def test_installing_twice_changes_nothing(tmp_path, kind):
    first = run(kind, tmp_path, (MINE_BEFORE + '\n').encode())
    assert first.decode().count(START) == 1
    assert run(kind, tmp_path, first) == first


@both
def test_with_no_block_it_is_added_at_the_end(tmp_path, kind):
    text = run(kind, tmp_path, (MINE_BEFORE + '\n').encode()).decode()
    assert text.startswith(MINE_BEFORE + '\n\n' + START)
    assert text.endswith(END + '\n')


@both
@pytest.mark.parametrize('existing', [None, b''], ids=['missing', 'empty'])
def test_a_first_install_writes_just_the_block(tmp_path, kind, existing):
    text = run(kind, tmp_path, existing).decode()
    assert text.startswith(START) and text.endswith(END + '\n')
    assert '&collection=<name>' in text


@both
def test_two_old_copies_become_one(tmp_path, kind):
    text = run(kind, tmp_path, f'{OLD_BLOCK}\n\n{MINE_BEFORE}\n\n{OLD_BLOCK}\n'.encode()).decode()
    assert text.count(START) == 1 and MINE_BEFORE in text
