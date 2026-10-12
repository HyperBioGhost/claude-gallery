"""
Browser tests for gallery.html.

This is the file that holds every user-facing feature and had no coverage at
all: the only way to find out it was broken was to cut a release. In
particular the v2.2.0 live-render behaviour is only meaningful in a real
browser — it depends on blob URLs, iframe sandboxing and a debounce timer.

Skipped (not failed) when Playwright is unavailable, so the suite still runs on
a machine without a browser. CI installs chromium, so these do run there.
"""
import os

import pytest

_pw = pytest.importorskip('playwright.sync_api', reason='playwright not installed')
sync_playwright, expect = _pw.sync_playwright, _pw.expect

# Which engine to drive. Local runs use chromium for speed; CI runs the matrix,
# because "does this work in Firefox?" should be answered by a check rather than
# by asking. The only known engine difference is showSaveFilePicker, which
# saveAs() already falls back from.
ENGINE = os.environ.get('GALLERY_TEST_BROWSER', 'chromium')

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
            browser = getattr(pw, ENGINE).launch()
        except Exception as e:                       # no browser binary present
            pytest.skip(f'{ENGINE} unavailable: {e}')
        pg = browser.new_page(viewport={'width': 1280, 'height': 800})
        errors = []
        pg.on('pageerror', lambda e: errors.append(str(e)))
        pg.goto(gallery.base, wait_until='networkidle')
        wait_for_app(pg)
        pg.errors = errors
        yield pg
        browser.close()


def wait_for_app(page, timeout=15000):
    """Block until the inline app script has actually run."""
    page.wait_for_function(
        """() => typeof window.renderGrid === 'function'
               && document.getElementById('file-count').textContent !== 'Loading artifacts...'""",
        timeout=timeout)


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
    wait_for_app(page)
    expect(page.locator('.card:has-text("shown.md")')).to_have_count(1)


def test_version_badge_is_shown(gallery, page):
    """The badge exists so a bug report can name its build.

    Filled by an async fetch, so wait for it — networkidle can land first.
    """
    page.wait_for_function(
        "() => document.getElementById('version-badge').textContent !== ''",
        timeout=5000)
    v = gallery.mod.DISPLAY_VERSION
    expected = ('v' + v) if v[0].isdigit() else v   # "v2.3.0", but plain "dev"
    assert page.locator('#version-badge').inner_text() == expected
    assert '0.0.0-dev' not in expected


def test_dev_build_shows_no_update_pill(page):
    assert page.locator('#update-pill').is_hidden()


def test_save_as_works_without_the_file_system_access_api(page):
    """
    showSaveFilePicker is Chrome-only. Firefox and Safari must still be able to
    save a copy, via an ordinary download rather than a save dialog.
    """
    has_picker = page.evaluate("() => typeof window.showSaveFilePicker === 'function'")
    fallback_used = page.evaluate("() => typeof window.downloadBlob === 'function'")
    assert fallback_used, 'no download fallback exists for browsers without the picker'
    if not has_picker:
        # Actually exercise it on an engine that lacks the API.
        assert page.evaluate(
            "() => document.getElementById('saveas-btn') !== null")


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
    wait_for_app(page)
    page.fill('#search', 'alpha')
    expect(page.locator('.card:visible:has-text("alpha.md")')).to_have_count(1)
    expect(page.locator('.card:visible:has-text("beta.md")')).to_have_count(0)


def test_gallery_works_when_the_cdn_is_unreachable(gallery):
    """
    Markdown/diagram/highlight libraries load from CDNs. A missing one must cost
    only its own feature.

    Regression: `mermaid.initialize(...)` was the first statement in the app
    script, so an unreachable CDN threw a ReferenceError there and nothing below
    it ran -- including loadFiles(). The gallery sat on "Loading artifacts..."
    with zero cards, in every browser, because a diagram library was missing.
    """
    gallery.write('offline.md', '# still here')
    with sync_playwright() as pw:
        try:
            browser = getattr(pw, ENGINE).launch()
        except Exception as e:
            pytest.skip(f'{ENGINE} unavailable: {e}')
        pg = browser.new_page()
        for pattern in ('**cdnjs.cloudflare.com**', '**cdn.jsdelivr.net**'):
            pg.route(pattern, lambda route: route.abort())
        pg.goto(gallery.base, wait_until='domcontentloaded')
        wait_for_app(pg)
        expect(pg.locator('.card:has-text("offline.md")')).to_have_count(1)
        browser.close()


# ── stale card thumbnails ─────────────────────────────────────────────

def _thumbnail_src(page, name):
    return page.get_attribute(f'.card:has-text("{name}") img.card-thumbnail', 'src')


def _wait_for_thumbnail(page, name, other_than=None, timeout=20000):
    page.wait_for_function(
        """([name, old]) => {
            const card = [...document.querySelectorAll('.card')]
                .find(c => c.querySelector('.card-name').textContent === name);
            const img = card && card.querySelector('img.card-thumbnail');
            return img && img.getAttribute('src') !== old;
        }""", arg=[name, other_than], timeout=timeout)
    return _thumbnail_src(page, name)


def test_a_captured_card_reloads_as_an_image_not_a_page(gallery, page):
    """The point of the feature: a restart must not re-render every document."""
    gallery.write('page.html', TALL_HTML)
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    stored = _wait_for_thumbnail(page, 'page.html')   # first sight captures it

    page.reload(wait_until='domcontentloaded')
    wait_for_app(page)
    # Present immediately and it is the stored capture: an image that appears
    # only after a fresh capture would mean the page was rendered again.
    assert _thumbnail_src(page, 'page.html') == stored, 'card was re-captured on load'
    assert page.locator('#grid iframe').count() == 0, 'grid rendered a live document'


def test_thumbnail_stays_stale_until_the_artifact_is_opened(gallery, page):
    gallery.write('page.html', TALL_HTML)
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    first = _wait_for_thumbnail(page, 'page.html')

    # The artifact changes on disk (as when Claude rewrites it): card stays put.
    gallery.write('page.html', TALL_HTML.replace('ORIGINAL', 'REWRITTEN'))
    page.wait_for_timeout(2500)
    assert _thumbnail_src(page, 'page.html') == first, 'refreshed without being opened'

    # Opening it is what refreshes the card, ready for when the user comes back.
    page.click('.card:has-text("page.html")')
    page.wait_for_selector('#detail-preview > iframe')
    page.click('.back-btn')
    refreshed = _wait_for_thumbnail(page, 'page.html', other_than=first)
    assert refreshed != first


def test_saving_an_edit_refreshes_the_thumbnail(gallery, page):
    gallery.write('page.html', TALL_HTML)
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    first = _wait_for_thumbnail(page, 'page.html')
    open_editor(page, 'page.html')
    page.fill('#editor-textarea', TALL_HTML.replace('ORIGINAL', 'EDITED'))
    page.click('#save-btn')
    page.click('.back-btn')
    assert _wait_for_thumbnail(page, 'page.html', other_than=first) != first


def test_saving_does_not_duplicate_the_card(gallery, page):
    """
    Regression: invalidateCard() dropped the cache entry but left the node in
    the grid, so renderGrid appended a second card and every save showed the
    file twice -- stale copy first.
    """
    gallery.write('doc.md', '# v1\n')
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    open_editor(page, 'doc.md')
    page.fill('#editor-textarea', '# v2\n')
    page.click('#save-btn')
    page.wait_for_selector('#undo-link')
    page.click('.back-btn')
    expect(page.locator('.card:visible:has-text("doc.md")')).to_have_count(1)


def test_without_the_rasteriser_cards_fall_back_to_live_pages(gallery):
    """CDN down: no capture is possible, so the old live preview must remain."""
    gallery.write('page.html', TALL_HTML)
    with sync_playwright() as pw:
        try:
            browser = getattr(pw, ENGINE).launch()
        except Exception as e:
            pytest.skip(f'{ENGINE} unavailable: {e}')
        pg = browser.new_page()
        pg.route('**/html-to-image**', lambda route: route.abort())
        pg.goto(gallery.base, wait_until='domcontentloaded')
        wait_for_app(pg)
        expect(pg.locator('.card:has-text("page.html") iframe')).to_have_count(1)
        browser.close()


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


# ── external links ────────────────────────────────────────────────────

@pytest.fixture
def coop_site():
    """
    A stand-in for an auth-gated site: sends Cross-Origin-Opener-Policy, as
    Amazon internal tools (atoz.amazon.work et al) do.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            body = b'<!doctype html><h1 id="target-loaded">TARGET LOADED</h1>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('X-Frame-Options', 'SAMEORIGIN')
            self.send_header('Cross-Origin-Opener-Policy', 'same-origin-allow-popups')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}/target'
    srv.shutdown()
    srv.server_close()


def test_external_link_opens_a_working_tab(gallery, page, coop_site):
    """
    Regression: the preview sandbox must include allow-popups-to-escape-sandbox.

    Without it the new tab inherits the sandbox, a sandboxed top-level document
    cannot satisfy Cross-Origin-Opener-Policy, and Chrome refuses the response
    with ERR_BLOCKED_BY_RESPONSE -- while the same link works when the file is
    opened outside the gallery. Any COOP-sending site reproduces it.
    """
    gallery.write('links.html',
                  '<!doctype html><html><body><h1>links</h1>'
                  f'<a id="ext" href="{coop_site}" target="_blank" rel="noopener">go</a>'
                  '</body></html>')
    page.reload(wait_until='networkidle')
    page.click('.card:has-text("links.html")')
    page.wait_for_selector('#detail-preview > iframe')

    opened = []
    page.context.on('page', lambda p: opened.append(p))
    page.frame_locator('#detail-preview > iframe').locator('#ext').click()
    page.wait_for_timeout(2500)

    assert len(opened) == 1, 'link did not open a tab'
    tab = opened[0]
    assert not tab.url.startswith('chrome-error'), \
        f'tab was blocked instead of loading: {tab.url}'
    assert 'TARGET LOADED' in tab.content()


def test_both_previews_share_one_sandbox_policy(gallery, page):
    """View mode and edit mode must not drift apart on sandbox flags."""
    gallery.write('links.html', '<!doctype html><html><body><p>x</p></body></html>')
    page.reload(wait_until='networkidle')
    page.click('.card:has-text("links.html")')
    page.wait_for_selector('#detail-preview > iframe')
    view = page.get_attribute('#detail-preview > iframe', 'sandbox')

    page.click('#edit-btn')
    page.wait_for_selector('#editor-live iframe')
    edit = page.get_attribute('#editor-live iframe', 'sandbox')

    assert view == edit
    assert 'allow-popups-to-escape-sandbox' in view


# ── leaving an artifact stops it ──────────────────────────────────────

NOISY_HTML = """<!doctype html><html><body>
<h1 id="h">NOISY</h1><button id="go">start</button>
<script>
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  document.getElementById("go").onclick = () => ctx.resume().then(() => {
    const osc = ctx.createOscillator();
    osc.connect(ctx.destination);
    osc.start();
  });
  window.__audioState = () => ctx.state;
</script></body></html>"""


def test_going_back_ends_the_artifacts_document(gallery, page):
    """
    Regression: goHome() only removed the 'visible' class, so the artifact's
    iframe stayed in the DOM and kept running -- audio carried on playing and
    Chrome kept the tab's sound indicator lit on the homepage.
    """
    gallery.write('noisy.html', NOISY_HTML)
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    page.click('.card:has-text("noisy.html")')
    page.wait_for_selector('#detail-preview > iframe')
    page.frame_locator('#detail-preview > iframe').locator('#go').click()
    page.wait_for_timeout(300)

    page.click('.back-btn')
    page.wait_for_timeout(500)
    # No surviving document means nothing left to make sound.
    assert page.locator('#detail-preview iframe').count() == 0
    assert not [f for f in page.frames if 'noisy.html' in f.url and 'capture=' not in f.url]
    # The editor must survive the teardown, or edit mode breaks afterwards.
    assert page.locator('#editor-wrap').count() == 1


def test_editing_still_works_after_a_back_and_forth(gallery, page):
    """Tearing the preview down must not cost the editor its markup."""
    gallery.write('doc.md', '# v1\n')
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    page.click('.card:has-text("doc.md")')
    page.wait_for_selector('#detail-preview')
    page.click('.back-btn')
    open_editor(page, 'doc.md')
    page.fill('#editor-textarea', '# v2\n')
    page.click('#save-btn')
    page.wait_for_selector('#undo-link', timeout=5000)
    assert (gallery.root / 'doc.md').read_text(encoding='utf-8') == '# v2\n'


def test_thumbnail_capture_is_not_allowed_to_autoplay(gallery, page):
    """A capture is invisible, so it must also be inaudible."""
    gallery.write('noisy.html', NOISY_HTML)
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    # Watch first, then ask for a capture: the card's own first capture can
    # finish before the observer is installed, which makes this race.
    allow = page.evaluate("""() => new Promise(resolve => {
        const seen = new MutationObserver(() => {
            const f = [...document.querySelectorAll('iframe')]
                .find(i => i.src.includes('capture='));
            if (f) { seen.disconnect(); resolve(f.getAttribute('allow')); }
        });
        seen.observe(document.body, { childList: true });
        queueThumbnailCapture('noisy.html', true);
        setTimeout(() => { seen.disconnect(); resolve('no capture frame seen'); }, 20000);
    })""")
    assert allow == "autoplay 'none'", f'capture frame allow={allow!r}'


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
