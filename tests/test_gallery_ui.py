"""
Browser tests for gallery.html.

This is the file that holds every user-facing feature and had no coverage at
all: the only way to find out it was broken was to cut a release. In
particular the v2.2.0 live-render behaviour is only meaningful in a real
browser — it depends on blob URLs, iframe sandboxing and a debounce timer.

Skipped (not failed) when Playwright is unavailable, so the suite still runs on
a machine without a browser. CI installs chromium, so these do run there.
"""
import pytest

sync_playwright = pytest.importorskip(
    'playwright.sync_api', reason='playwright not installed').sync_playwright

TALL_HTML = (
    '<!doctype html><html><head><meta charset="utf-8"><title>Fixture</title></head>\n'
    '<body>\n'
    '<h1 id="headline">ORIGINAL</h1>\n'
    + '\n'.join(f'<p>filler line {i}</p>' for i in range(60))
    # Placed after <body> exists — in <head> document.body is null and this throws.
    + '\n<script>document.body.setAttribute("data-script-ran","yes")</script>\n'
    '</body></html>'
)
SVG_FIXTURE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
               '<text id="label" x="10" y="50">ORIGINAL</text></svg>')


@pytest.fixture
def page(gallery):
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:                       # no browser binary present
            pytest.skip(f'chromium unavailable: {e}')
        pg = browser.new_page(viewport={'width': 1280, 'height': 800})
        errors = []
        pg.on('pageerror', lambda e: errors.append(str(e)))
        pg.goto(gallery.base, wait_until='networkidle')
        pg.errors = errors
        yield pg
        browser.close()


def open_editor(page, name):
    """Open an artifact and enter edit mode."""
    page.click(f'.card:has-text("{name}")')
    page.wait_for_selector('#edit-btn:visible')
    page.click('#edit-btn')
    page.wait_for_selector('#editor-textarea')


# ── homepage ──────────────────────────────────────────────────────────

def test_artifacts_appear_as_cards(gallery, page):
    gallery.write('shown.md', '# shown')
    page.reload(wait_until='networkidle')
    assert page.locator('.card:has-text("shown.md")').count() == 1


def test_version_badge_is_shown(gallery, page):
    """The badge exists so a bug report can name its build.

    Filled by an async fetch, so wait for it — networkidle can land first.
    """
    page.wait_for_function(
        "() => document.getElementById('version-badge').textContent !== ''",
        timeout=5000)
    assert page.locator('#version-badge').inner_text() == 'v' + gallery.mod.VERSION


def test_dev_build_shows_no_update_pill(page):
    assert page.locator('#update-pill').is_hidden()


def test_imported_file_appears_without_a_reload(gallery, page):
    """Covers the SSE push path end to end."""
    import base64
    data = 'data:text/plain;base64,' + base64.b64encode(b'# pushed').decode()
    page.evaluate("""async (data) => {
        await fetch('/import', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({files: [{name: 'pushed.md', data}]})
        });
    }""", data)
    page.wait_for_selector('.card:has-text("pushed.md")', timeout=5000)


def test_search_filters_cards(gallery, page):
    gallery.write('alpha.md', 'a')
    gallery.write('beta.md', 'b')
    page.reload(wait_until='networkidle')
    page.fill('#search', 'alpha')
    page.wait_for_function(
        "() => document.querySelector('.card:has(.card-name)') !== null")
    assert page.locator('.card:visible:has-text("alpha.md")').count() == 1
    assert page.locator('.card:visible:has-text("beta.md")').count() == 0


# ── preview ───────────────────────────────────────────────────────────

def test_html_preview_renders_the_document(gallery, page):
    gallery.write('page.html', TALL_HTML)
    page.reload(wait_until='networkidle')
    page.click('.card:has-text("page.html")')
    frame = page.frame_locator('#detail-preview > iframe')
    assert frame.locator('#headline').inner_text() == 'ORIGINAL'


# ── live render while editing (v2.2.0) ────────────────────────────────

def test_html_live_render_follows_typing(gallery, page):
    gallery.write('live.html', TALL_HTML)
    page.reload(wait_until='networkidle')
    open_editor(page, 'live.html')

    frame = page.frame_locator('#editor-live iframe')
    assert frame.locator('#headline').inner_text() == 'ORIGINAL'

    page.fill('#editor-textarea', TALL_HTML.replace('ORIGINAL', 'EDITED'))
    frame.locator('#headline').wait_for(state='visible')
    page.wait_for_function(
        """() => {
            const f = document.querySelector('#editor-live iframe');
            const h = f && f.contentDocument && f.contentDocument.getElementById('headline');
            return h && h.textContent === 'EDITED';
        }""", timeout=5000)


def test_scripts_run_in_the_live_render(gallery, page):
    """A preview that doesn't execute scripts is useless for real pages."""
    gallery.write('live.html', TALL_HTML)
    page.reload(wait_until='networkidle')
    open_editor(page, 'live.html')
    page.wait_for_function(
        """() => {
            const f = document.querySelector('#editor-live iframe');
            return f && f.contentDocument
                && f.contentDocument.body.getAttribute('data-script-ran') === 'yes';
        }""", timeout=5000)


def test_live_render_is_debounced(gallery, page):
    """Reloading the document on every keystroke would thrash."""
    gallery.write('live.html', TALL_HTML)
    page.reload(wait_until='networkidle')
    open_editor(page, 'live.html')
    page.wait_for_selector('#editor-live iframe')

    page.evaluate("""() => {
        window.__loads = 0;
        const f = document.querySelector('#editor-live iframe');
        f.addEventListener('load', () => window.__loads++);
    }""")
    page.click('#editor-textarea')
    page.keyboard.type('<!-- abcdefghij -->')      # 19 keystrokes
    page.wait_for_timeout(900)
    loads = page.evaluate('window.__loads')
    assert 1 <= loads <= 3, f'expected a settled re-render, got {loads} reloads'


def test_scroll_position_survives_re_render(gallery, page):
    """Editing below the fold must not snap the preview back to the top."""
    gallery.write('live.html', TALL_HTML)
    page.reload(wait_until='networkidle')
    open_editor(page, 'live.html')
    page.wait_for_selector('#editor-live iframe')

    page.evaluate("""() => {
        document.querySelector('#editor-live iframe').contentWindow.scrollTo(0, 600);
    }""")
    page.click('#editor-textarea')
    page.keyboard.type('<!-- x -->')
    page.wait_for_timeout(900)
    assert page.evaluate(
        "() => document.querySelector('#editor-live iframe').contentWindow.scrollY") > 300


def test_svg_live_render_follows_typing(gallery, page):
    gallery.write('live.svg', SVG_FIXTURE)
    page.reload(wait_until='networkidle')
    open_editor(page, 'live.svg')
    page.fill('#editor-textarea', SVG_FIXTURE.replace('ORIGINAL', 'EDITED'))
    page.wait_for_function(
        """() => {
            const f = document.querySelector('#editor-live iframe');
            const l = f && f.contentDocument && f.contentDocument.getElementById('label');
            return l && l.textContent === 'EDITED';
        }""", timeout=5000)


def test_markdown_live_render_is_immediate(gallery, page):
    """DOM-rebuild types are not debounced; a delay here would be a regression."""
    gallery.write('live.md', '# ORIGINAL\n')
    page.reload(wait_until='networkidle')
    open_editor(page, 'live.md')
    page.fill('#editor-textarea', '# EDITED\n')
    page.wait_for_function(
        "() => document.querySelector('#editor-live').textContent.includes('EDITED')",
        timeout=1000)


def test_leaving_edit_mode_clears_the_live_pane(gallery, page):
    gallery.write('live.html', TALL_HTML)
    page.reload(wait_until='networkidle')
    open_editor(page, 'live.html')
    page.wait_for_selector('#editor-live iframe')
    page.keyboard.press('Escape')
    assert page.locator('#editor-live iframe').count() == 0


# ── save + undo ───────────────────────────────────────────────────────

def test_save_then_undo_restores_the_file(gallery, page):
    gallery.write('doc.md', '# v1\n')
    page.reload(wait_until='networkidle')
    open_editor(page, 'doc.md')
    page.fill('#editor-textarea', '# v2\n')
    page.click('#save-btn')

    page.wait_for_selector('#undo-link', timeout=5000)
    assert (gallery.root / 'doc.md').read_text(encoding='utf-8') == '# v2\n'

    page.click('#undo-link')
    page.wait_for_function(
        "() => document.getElementById('save-status').textContent.includes('Reverted')",
        timeout=5000)
    assert (gallery.root / 'doc.md').read_text(encoding='utf-8') == '# v1\n'


def test_first_save_of_a_new_file_confirms_without_undo(gallery, page):
    """Nothing to go back to, so don't offer a way back."""
    gallery.write('fresh.md', '# only\n')
    page.reload(wait_until='networkidle')
    open_editor(page, 'fresh.md')
    page.fill('#editor-textarea', '# changed\n')
    page.click('#save-btn')
    page.wait_for_function(
        "() => document.getElementById('save-status').textContent.startsWith('Saved')",
        timeout=5000)


# ── delete ────────────────────────────────────────────────────────────

def test_delete_removes_the_card(gallery, page):
    gallery.write('doomed.md', 'x')
    page.reload(wait_until='networkidle')
    page.click('#select-mode-btn')
    page.check('.card:has-text("doomed.md") .select-check')
    page.once('dialog', lambda d: d.accept())
    page.click('#delete-selected-btn')
    page.wait_for_function(
        "() => !document.body.innerText.includes('doomed.md')", timeout=5000)
    assert not (gallery.root / 'doomed.md').exists()


# ── no console errors anywhere ────────────────────────────────────────

def test_no_uncaught_page_errors(gallery, page):
    gallery.write('doc.md', '# hi\n')
    page.reload(wait_until='networkidle')
    open_editor(page, 'doc.md')
    page.keyboard.press('Escape')
    page.keyboard.press('Escape')
    assert page.errors == []
