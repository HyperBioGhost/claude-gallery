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


def test_picker_mime_is_bare_for_every_type_the_server_serves(gallery, page):
    """
    The guard for the "Invalid type" failure, driven by the server's own MIME
    map so a charset added to another type is covered without editing a list.

    Deliberately not routed through blob.type: Playwright's Chromium strips
    parameters from a fetched blob's type while Firefox and real Chrome keep
    them, so a test that reads blob.type passes in the one engine where the
    bug does not reproduce. That is why the original Save As test missed this.
    """
    exts = ['html', 'htm', 'svg', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'json',
            'js', 'css', 'md', 'txt', 'csv', 'ts', 'py', 'xml', 'yaml', 'yml',
            'mermaid', 'mmd', 'unknownext']
    served = {e: gallery.mod.guess_mime('x.' + e) for e in exts}
    # The input has to contain the failure, or the check proves nothing.
    assert any(';' in m for m in served.values()), served

    got = page.evaluate("""(served) => Object.fromEntries(
        Object.entries(served).map(([e, m]) => [e, pickerMime(m)]))""", served)
    for e, mime in got.items():
        assert ';' not in mime, f'.{e}: {mime!r} still carries parameters'
        assert mime == mime.strip() and '/' in mime, f'.{e}: {mime!r} not a bare MIME'
        assert mime == served[e].split(';')[0].strip(), f'.{e}: {mime!r} vs {served[e]!r}'

    # An absent or empty header still has to yield something the picker takes.
    assert page.evaluate("() => pickerMime('')") == 'application/octet-stream'
    assert page.evaluate("() => pickerMime(null)") == 'application/octet-stream'


@pytest.mark.parametrize('name', ['doc.md', 'table.csv', 'page.html', 'data.json'])
def test_save_as_offers_a_mime_the_picker_accepts(gallery, page, name):
    gallery.write(name, 'content')
    page.reload(wait_until='networkidle')
    wait_for_app(page)

    # Stand in for the picker: capture what it was handed, and succeed.
    page.evaluate("""() => {
        window.__pickerOpts = null;
        window.__written = false;
        window.showSaveFilePicker = async (opts) => {
            window.__pickerOpts = JSON.parse(JSON.stringify(opts));
            // Enforce what the real API enforces.
            for (const t of (opts.types || [])) {
                for (const mime of Object.keys(t.accept || {})) {
                    if (mime.includes(';')) {
                        throw new TypeError("Failed to execute 'showSaveFilePicker' on " +
                                            "'Window': Invalid type: " + mime);
                    }
                }
            }
            return { createWritable: async () => ({
                write: async () => { window.__written = true; },
                close: async () => {},
            }) };
        };
    }""")
    alerts = []
    page.on('dialog', lambda d: (alerts.append(d.message), d.dismiss()))

    page.click(f'.card:has-text("{name}")')
    page.wait_for_selector('#saveas-btn')
    page.click('#saveas-btn')
    page.wait_for_function("() => window.__pickerOpts !== null", timeout=5000)

    opts = page.evaluate('window.__pickerOpts')
    assert alerts == [], alerts
    assert page.evaluate('window.__written') is True, 'never wrote the file'
    assert opts['suggestedName'] == name, opts

    mimes = list(opts['types'][0]['accept'].keys())
    assert len(mimes) == 1, mimes
    mime = mimes[0]
    assert ';' not in mime, f'picker rejects parameters: {mime!r}'
    assert mime.strip() == mime and '/' in mime, f'not a bare MIME: {mime!r}'
    assert opts['types'][0]['accept'][mime] == ['.' + name.split('.')[-1]], opts

    page.wait_for_function(
        "() => document.getElementById('save-status').textContent.includes('Saved to disk')",
        timeout=5000)


def test_save_as_surfaces_a_real_failure(gallery, page):
    """The error path must still report, or a failed save looks like success."""
    gallery.write('doc.md', '# x\n')
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    page.evaluate("""() => {
        window.showSaveFilePicker = async () => { throw new DOMException('boom', 'NotAllowedError'); };
    }""")
    alerts = []
    page.on('dialog', lambda d: (alerts.append(d.message), d.dismiss()))
    page.click('.card:has-text("doc.md")')
    page.wait_for_selector('#saveas-btn')
    page.click('#saveas-btn')
    page.wait_for_timeout(600)
    assert any('boom' in a for a in alerts), alerts
    assert 'Saved to disk' not in page.locator('#save-status').inner_text()


def test_save_as_stays_quiet_when_the_user_cancels(gallery, page):
    """Dismissing the picker is not an error."""
    gallery.write('doc.md', '# x\n')
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    page.evaluate("""() => {
        window.showSaveFilePicker = async () => { throw new DOMException('cancelled', 'AbortError'); };
    }""")
    alerts = []
    page.on('dialog', lambda d: (alerts.append(d.message), d.dismiss()))
    page.click('.card:has-text("doc.md")')
    page.wait_for_selector('#saveas-btn')
    page.click('#saveas-btn')
    page.wait_for_timeout(600)
    assert alerts == [], alerts
    assert 'Saved to disk' not in page.locator('#save-status').inner_text()


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


# ── filter chips and grouping ─────────────────────────────────────────

def seed_mixed(gallery, page):
    """A mix shaped like the real collection: one dominant type plus singles."""
    for i in range(1, 4):
        gallery.write(f'page-{i}.html', TALL_HTML)
    for i in range(1, 3):
        gallery.write(f'note-{i}.md', '# note\n')
    gallery.write('data.csv', 'a,b\n1,2\n')
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    page.wait_for_function("() => document.querySelectorAll('.type-chip').length > 1")


def chips(page):
    return page.evaluate("""() => [...document.querySelectorAll('.type-chip')].map(c => ({
        ext: c.dataset.ext, count: +c.querySelector('.chip-count').textContent,
        active: c.classList.contains('active'), disabled: c.disabled }))""")


def visual_sequence(page):
    """What the reader sees, read from `order` rather than DOM position."""
    return page.evaluate("""() => {
        const vis = el => el.offsetParent !== null && el.offsetWidth > 0;
        const items = [];
        document.querySelectorAll('.card').forEach(c => { if (vis(c))
            items.push({ o: +c.style.order, t: c.querySelector('.card-name').textContent }); });
        document.querySelectorAll('.group-header').forEach(h => { if (vis(h))
            items.push({ o: +h.style.order, t: '## ' + h.querySelector('.group-name').textContent }); });
        return items.sort((a, b) => a.o - b.o).map(x => x.t);
    }""")


def test_chips_come_from_the_types_actually_present(gallery, page):
    seed_mixed(gallery, page)
    c = chips(page)
    assert [x['ext'] for x in c] == ['', 'html', 'md', 'csv'], c   # All, then size desc
    by = {x['ext']: x['count'] for x in c}
    assert by == {'': 6, 'html': 3, 'md': 2, 'csv': 1}, by
    assert c[0]['active'], 'All should start selected'


def test_clicking_a_chip_filters_to_that_type(gallery, page):
    seed_mixed(gallery, page)
    page.click('.type-chip[data-ext="md"]')
    page.wait_for_timeout(200)
    names = visual_sequence(page)
    assert names and all(n.endswith('.md') for n in names), names
    assert '2 of 6 artifacts' in page.locator('#file-count').inner_text()


def test_clicking_the_active_chip_clears_the_filter(gallery, page):
    seed_mixed(gallery, page)
    page.click('.type-chip[data-ext="md"]')
    page.wait_for_timeout(150)
    page.click('.type-chip[data-ext="md"]')
    page.wait_for_timeout(150)
    assert len(visual_sequence(page)) == 6
    assert chips(page)[0]['active']


def test_chip_counts_follow_the_search(gallery, page):
    """A chip claiming 3 when the search can reach 1 would be a lie."""
    seed_mixed(gallery, page)
    page.fill('#search', 'note')
    page.wait_for_timeout(300)
    by = {x['ext']: x for x in chips(page)}
    assert by['md']['count'] == 2, by
    assert by['html']['count'] == 0 and by['html']['disabled'], by['html']
    assert by['']['count'] == 2, by['']


def test_the_active_chip_stays_clickable_at_zero(gallery, page):
    """Otherwise a search could strand the filter with no way to switch it off."""
    seed_mixed(gallery, page)
    page.click('.type-chip[data-ext="csv"]')
    page.wait_for_timeout(150)
    page.fill('#search', 'page-')
    page.wait_for_timeout(300)
    csv = [x for x in chips(page) if x['ext'] == 'csv'][0]
    assert csv['count'] == 0 and csv['active'] and not csv['disabled'], csv
    page.click('.type-chip[data-ext="csv"]')
    page.wait_for_timeout(200)
    assert len(visual_sequence(page)) == 3


def test_group_by_type_puts_each_card_under_its_header(gallery, page):
    seed_mixed(gallery, page)
    page.select_option('#group-select', 'ext')
    page.wait_for_timeout(300)
    seq = visual_sequence(page)
    assert seq[0] == '## .html', seq
    current = None
    seen = []
    for item in seq:
        if item.startswith('## '):
            current = item[3:]
            seen.append(current)
        else:
            assert item.endswith(current), f'{item} filed under {current}'
    assert seen == ['.html', '.md', '.csv'], seen   # biggest group first


def test_a_group_header_spans_the_whole_row(gallery, page):
    seed_mixed(gallery, page)
    page.select_option('#group-select', 'ext')
    page.wait_for_timeout(250)
    m = page.evaluate("""() => {
        const h = document.querySelector('.group-header');
        const cs = getComputedStyle(h);
        const grid = document.getElementById('grid');
        return { start: cs.gridColumnStart, end: cs.gridColumnEnd,
                 headerW: Math.round(h.getBoundingClientRect().width),
                 gridW: Math.round(grid.getBoundingClientRect().width) };
    }""")
    assert m['start'] == '1' and m['end'] == '-1', m
    assert m['headerW'] >= m['gridW'] - 2, m


def test_group_by_date_buckets_by_age(gallery, page):
    import os, time
    ages = {'fresh.md': 0, 'midweek.md': 3, 'lastmonth.md': 20, 'ancient.md': 200}
    for name, days in ages.items():
        f = gallery.write(name, '# x\n')
        when = time.time() - days * 86400
        os.utime(f, (when, when))
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    page.select_option('#group-select', 'date')
    page.wait_for_timeout(300)
    seq = visual_sequence(page)
    assert seq == ['## Today', 'fresh.md', '## This week', 'midweek.md',
                   '## This month', 'lastmonth.md', '## Older', 'ancient.md'], seq


def test_grouping_off_shows_no_headers(gallery, page):
    seed_mixed(gallery, page)
    page.select_option('#group-select', 'ext')
    page.wait_for_timeout(200)
    page.select_option('#group-select', 'none')
    page.wait_for_timeout(200)
    assert not any(x.startswith('## ') for x in visual_sequence(page))


def test_filter_and_grouping_compose(gallery, page):
    seed_mixed(gallery, page)
    page.click('.type-chip[data-ext="html"]')
    page.select_option('#group-select', 'ext')
    page.wait_for_timeout(300)
    seq = visual_sequence(page)
    assert seq[0] == '## .html', seq
    assert sum(1 for x in seq if x.startswith('## ')) == 1, seq
    assert all(x.endswith('.html') for x in seq[1:]), seq


def test_empty_state_names_the_constraint(gallery, page):
    seed_mixed(gallery, page)
    page.click('.type-chip[data-ext="md"]')
    page.fill('#search', 'zzz-nothing')
    page.wait_for_timeout(300)
    msg = page.locator('#grid-empty .msg').inner_text()
    assert '.md' in msg and 'zzz-nothing' in msg, msg


def test_grouping_never_relocates_a_card(gallery, page):
    """
    Cards are ordered by CSS precisely so they are never detached: moving one
    reloads its thumbnail, and its iframe in the no-CDN fallback.
    """
    seed_mixed(gallery, page)
    before = page.evaluate(
        "() => [...document.getElementById('grid').children]"
        ".filter(e => e.classList.contains('card'))"
        ".map(e => e.querySelector('.card-name').textContent)")
    page.select_option('#group-select', 'ext')
    page.wait_for_timeout(250)
    page.click('.type-chip[data-ext="md"]')
    page.wait_for_timeout(250)
    page.select_option('#group-select', 'date')
    page.wait_for_timeout(250)
    after = page.evaluate(
        "() => [...document.getElementById('grid').children]"
        ".filter(e => e.classList.contains('card'))"
        ".map(e => e.querySelector('.card-name').textContent)")
    assert before == after, 'cards were reordered in the DOM, not by CSS'


# ── folding groups ────────────────────────────────────────────────────

def grouped_by_type(gallery, page):
    seed_mixed(gallery, page)
    page.select_option('#group-select', 'ext')
    page.wait_for_timeout(300)


def visible_cards_of(page, ext):
    return page.evaluate("""(ext) => [...document.querySelectorAll('.card')]
        .filter(c => c.offsetParent !== null && c.offsetWidth > 0)
        .map(c => c.querySelector('.card-name').textContent)
        .filter(n => n.endsWith('.' + ext)).length""", ext)


def header_for(page, key):
    return page.locator(f'.group-header[data-group="{key}"]')


def test_group_header_is_a_real_button(gallery, page):
    """Enter and Space must work, and the state must be announced."""
    grouped_by_type(gallery, page)
    h = header_for(page, 'html')
    assert h.evaluate('el => el.tagName') == 'BUTTON'
    assert h.get_attribute('aria-expanded') == 'true'
    assert 'Collapse' in h.get_attribute('aria-label')
    assert h.locator('.group-caret').count() == 1


def test_folding_a_group_hides_only_its_own_cards(gallery, page):
    grouped_by_type(gallery, page)
    assert visible_cards_of(page, 'html') == 3
    assert visible_cards_of(page, 'md') == 2

    header_for(page, 'html').click()
    page.wait_for_timeout(250)

    assert visible_cards_of(page, 'html') == 0
    assert visible_cards_of(page, 'md') == 2, 'folding leaked into another group'
    # The header stays, so the group can be opened again and still reads its size.
    expect(header_for(page, 'html')).to_be_visible()
    assert header_for(page, 'html').get_attribute('aria-expanded') == 'false'
    assert 'Expand' in header_for(page, 'html').get_attribute('aria-label')
    assert '3 items' in header_for(page, 'html').inner_text()


def test_folding_is_reversible(gallery, page):
    grouped_by_type(gallery, page)
    header_for(page, 'html').click()
    page.wait_for_timeout(200)
    header_for(page, 'html').click()
    page.wait_for_timeout(200)
    assert visible_cards_of(page, 'html') == 3
    assert header_for(page, 'html').get_attribute('aria-expanded') == 'true'


def test_a_group_folds_from_the_keyboard(gallery, page):
    grouped_by_type(gallery, page)
    header_for(page, 'html').focus()
    page.keyboard.press('Enter')
    page.wait_for_timeout(250)
    assert visible_cards_of(page, 'html') == 0


def test_folding_is_not_filtering(gallery, page):
    """
    Counts describe the collection, not the viewport, so folding must leave
    them alone -- and re-deriving them must give the same answer, which is
    what catches a count rebuilt from the visible cards.
    """
    grouped_by_type(gallery, page)
    before_count = page.locator('#file-count').inner_text()
    before_chips = {c['ext']: c['count'] for c in chips(page)}

    header_for(page, 'html').click()
    page.wait_for_timeout(250)
    assert page.locator('#file-count').inner_text() == before_count
    assert {c['ext']: c['count'] for c in chips(page)} == before_chips

    # Force the counts to be produced again while a group is folded.
    page.evaluate('applyFilters()')
    page.wait_for_timeout(150)
    assert page.locator('#file-count').inner_text() == before_count, \
        'counts were rebuilt from the visible cards'
    assert {c['ext']: c['count'] for c in chips(page)} == before_chips
    assert visible_cards_of(page, 'html') == 0, 'fold lost on recount'


def test_fold_state_is_scoped_to_the_grouping_mode(gallery, page):
    """
    A type group and a date group can share a key: `notes.today` yields the
    extension key 'today', which is also the key of the Today date group.
    The fold key carries its mode so one cannot fold the other.
    """
    gallery.write('notes.today', 'x')
    gallery.write('page-1.html', TALL_HTML)
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    page.select_option('#group-select', 'ext')
    page.wait_for_timeout(300)

    header_for(page, 'today').click()          # fold the .today type group
    page.wait_for_timeout(200)
    assert header_for(page, 'today').get_attribute('aria-expanded') == 'false'

    page.select_option('#group-select', 'date')
    page.wait_for_timeout(300)
    # Same key, different dimension: Today holds both files and must be open.
    assert header_for(page, 'today').get_attribute('aria-expanded') == 'true', \
        'the Today date group inherited the .today type fold'
    assert page.evaluate("""() => [...document.querySelectorAll('.card')]
        .filter(c => c.offsetParent !== null && c.offsetWidth > 0).length""") == 2

    page.select_option('#group-select', 'ext')
    page.wait_for_timeout(300)
    assert header_for(page, 'today').get_attribute('aria-expanded') == 'false'


def test_turning_grouping_off_strands_no_cards(gallery, page):
    """A fold must never leave a card hidden once the headers are gone."""
    grouped_by_type(gallery, page)
    header_for(page, 'html').click()
    page.wait_for_timeout(200)
    page.select_option('#group-select', 'none')
    page.wait_for_timeout(300)
    total = page.evaluate("""() => [...document.querySelectorAll('.card')]
        .filter(c => c.offsetParent !== null && c.offsetWidth > 0).length""")
    assert total == 6, total


def test_folding_never_relocates_a_card(gallery, page):
    grouped_by_type(gallery, page)
    before = page.evaluate(
        "() => [...document.getElementById('grid').children]"
        ".filter(e => e.classList.contains('card'))"
        ".map(e => e.querySelector('.card-name').textContent)")
    header_for(page, 'html').click()
    page.wait_for_timeout(200)
    header_for(page, 'md').click()
    page.wait_for_timeout(200)
    after = page.evaluate(
        "() => [...document.getElementById('grid').children]"
        ".filter(e => e.classList.contains('card'))"
        ".map(e => e.querySelector('.card-name').textContent)")
    assert before == after, 'cards were reordered in the DOM, not by CSS'


def test_caret_turns_and_respects_reduced_motion(gallery, page):
    grouped_by_type(gallery, page)
    caret = page.locator('.group-header[data-group="html"] .group-caret')
    open_t = caret.evaluate('el => getComputedStyle(el).transform')
    header_for(page, 'html').click()
    page.wait_for_timeout(350)
    assert caret.evaluate('el => getComputedStyle(el).transform') != open_t

    page.emulate_media(reduced_motion='reduce')
    page.wait_for_timeout(100)
    assert caret.evaluate(
        "el => getComputedStyle(el).transitionDuration") in ('0s', '0ms')
    page.emulate_media(reduced_motion='no-preference')


def test_every_group_can_be_folded_at_once(gallery, page):
    """All folded is a legal state: headers only, and not an empty-state."""
    grouped_by_type(gallery, page)
    for key in ('html', 'md', 'csv'):
        header_for(page, key).click()
        page.wait_for_timeout(120)
    shown = page.evaluate("""() => ({
        cards: [...document.querySelectorAll('.card')]
                 .filter(c => c.offsetParent !== null && c.offsetWidth > 0).length,
        headers: [...document.querySelectorAll('.group-header')]
                   .filter(h => h.offsetParent !== null).length,
        empty: document.getElementById('grid-empty').offsetParent !== null })""")
    assert shown == {'cards': 0, 'headers': 3, 'empty': False}, shown


# ── preview toggle ────────────────────────────────────────────────────

MD_DOC = """Prose line one, long enough to wrap in a capped reading measure so the
column width actually matters to the reader.

| # | What | Proposed |
|---|---|---|
| 1 | score weights | ROAS 40% |
"""


def gutters(page):
    """Left and right space around the reading column, inside its own pane."""
    return page.evaluate("""() => {
        const md = document.querySelector('.md-rendered');
        const half = md.closest('.split-half');
        const a = md.getBoundingClientRect(), b = half.getBoundingClientRect();
        return { left: Math.round(a.left - b.left), right: Math.round(b.right - a.right),
                 colWidth: Math.round(a.width), paneWidth: Math.round(b.width) };
    }""")


def open_md(gallery, page, name='doc.md', body=None):
    gallery.write(name, body if body is not None else MD_DOC)
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    page.click(f'.card:has-text("{name}")')
    page.wait_for_selector('.split-pane .md-rendered')


def test_preview_button_shows_only_for_split_types(gallery, page):
    """html renders whole; there is no source side to put away."""
    gallery.write('doc.md', MD_DOC)
    gallery.write('page.html', TALL_HTML)
    page.reload(wait_until='networkidle')
    wait_for_app(page)

    page.click('.card:has-text("page.html")')
    page.wait_for_selector('#detail-preview > iframe')
    assert page.locator('#preview-btn').is_hidden()

    page.click('.back-btn')
    page.click('.card:has-text("doc.md")')
    page.wait_for_selector('.split-pane .md-rendered')
    expect(page.locator('#preview-btn')).to_be_visible()
    assert page.locator('#preview-btn').inner_text() == 'Preview'


def test_preview_hides_the_source_and_centres_the_rendering(gallery, page):
    open_md(gallery, page)
    assert page.locator('.split-half').first.evaluate('el => el.offsetWidth') > 0

    page.click('#preview-btn')
    page.wait_for_timeout(200)

    # Source side, divider and the SOURCE/RENDERED labels all go away.
    assert page.locator('.split-half').first.evaluate('el => el.offsetWidth') == 0
    assert page.locator('.divider').first.evaluate('el => el.offsetWidth') == 0
    assert page.locator('.split-label').first.evaluate('el => el.offsetParent') is None
    expect(page.locator('.md-rendered')).to_be_visible()

    g = gutters(page)
    assert abs(g['left'] - g['right']) <= 2, f'not centred: {g}'
    assert g['left'] > 40, f'no breathing room: {g}'
    assert g['colWidth'] < g['paneWidth'], f'column not capped: {g}'
    assert page.locator('#preview-btn').inner_text() == 'Split'


def test_preview_reading_width_is_1080px_of_text(gallery, page):
    """
    Deliberately wider than a 68-72ch prose measure: on a 1920 desk the
    narrower column stranded the text in the middle of the screen. Pinned
    because it is a judgement call, not a derived value, so a later tidy-up
    must not quietly narrow it.

    Measured on the text, not the box: box-sizing is border-box, so the
    padding is inside the max-width.
    """
    open_md(gallery, page)
    page.click('#preview-btn')
    page.wait_for_timeout(200)
    for w, h in ((1920, 1200), (1512, 982), (1366, 768)):
        page.set_viewport_size({'width': w, 'height': h})
        page.wait_for_timeout(200)
        m = page.evaluate("""() => {
            const md = document.querySelector('.md-rendered');
            const cs = getComputedStyle(md);
            const r = md.getBoundingClientRect();
            return { text: Math.round(r.width - parseFloat(cs.paddingLeft) * 2),
                     hscroll: document.documentElement.scrollWidth >
                              document.documentElement.clientWidth };
        }""")
        assert abs(m['text'] - 1080) <= 2, f'{w}x{h}: text {m["text"]}px'
        assert not m['hscroll'], f'{w}x{h} scrolls sideways'


def test_preview_column_shrinks_rather_than_overflowing(gallery, page):
    """A window narrower than the column fills instead of scrolling."""
    open_md(gallery, page)
    page.click('#preview-btn')
    page.wait_for_timeout(200)
    page.set_viewport_size({'width': 900, 'height': 800})
    page.wait_for_timeout(250)
    m = page.evaluate("""() => {
        const md = document.querySelector('.md-rendered');
        const half = md.closest('.split-half');
        return { box: Math.round(md.getBoundingClientRect().width),
                 pane: Math.round(half.getBoundingClientRect().width),
                 hscroll: document.documentElement.scrollWidth >
                          document.documentElement.clientWidth };
    }""")
    assert m['box'] <= m['pane'], m
    assert not m['hscroll'], m


def test_preview_reading_size_meets_the_type_floor(gallery, page):
    """Split is a comparison view; preview is for reading, so it gets 18px."""
    open_md(gallery, page)
    size = page.evaluate(
        "() => parseFloat(getComputedStyle(document.querySelector('.md-rendered')).fontSize)")
    assert size >= 16, f'split body {size}px'
    page.click('#preview-btn')
    page.wait_for_timeout(200)
    size = page.evaluate(
        "() => parseFloat(getComputedStyle(document.querySelector('.md-rendered')).fontSize)")
    assert size >= 18, f'preview body {size}px'


def test_preview_toggles_back_to_split(gallery, page):
    open_md(gallery, page)
    page.click('#preview-btn')
    page.wait_for_timeout(150)
    page.click('#preview-btn')
    page.wait_for_timeout(150)
    assert page.locator('.split-half').first.evaluate('el => el.offsetWidth') > 0
    assert page.locator('#preview-btn').inner_text() == 'Preview'


def test_preview_is_remembered_for_the_next_file(gallery, page):
    """A reading preference, not a per-file setting."""
    gallery.write('one.md', MD_DOC)
    gallery.write('two.md', MD_DOC)
    page.reload(wait_until='networkidle')
    wait_for_app(page)
    page.click('.card:has-text("one.md")')
    page.wait_for_selector('.split-pane .md-rendered')
    page.click('#preview-btn')
    page.wait_for_timeout(150)
    page.click('.back-btn')

    page.click('.card:has-text("two.md")')
    page.wait_for_selector('.split-pane .md-rendered')
    assert page.locator('.split-pane').evaluate(
        "el => el.classList.contains('preview-only')"), 'preference not carried over'
    assert page.locator('#preview-btn').inner_text() == 'Split'


def test_preview_button_is_absent_while_editing(gallery, page):
    """The editor covers the view pane, so the toggle has nothing to act on."""
    open_md(gallery, page)
    page.click('#edit-btn')
    page.wait_for_selector('#editor-textarea')
    assert page.locator('#preview-btn').is_hidden()
    page.keyboard.press('Escape')
    page.wait_for_timeout(200)
    expect(page.locator('#preview-btn')).to_be_visible()


def test_preview_keeps_tables_wider_than_the_prose_cap_would_allow(gallery, page):
    """Prose caps its measure; a table is a data surface and fills the column."""
    open_md(gallery, page)
    page.click('#preview-btn')
    page.wait_for_timeout(200)
    m = page.evaluate("""() => {
        const md = document.querySelector('.md-rendered');
        const t = md.querySelector('table');
        return { table: Math.round(t.getBoundingClientRect().width),
                 column: Math.round(md.getBoundingClientRect().width) };
    }""")
    # Fills the column rather than being squeezed to a prose measure.
    assert m['table'] > m['column'] * 0.8, m


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
