// Score the gallery's pasted-type detector against a folder of real files.
//
//   node tools/detect-bench/bench.mjs <corpus-dir> [tune|holdout|all] [show]
//
// Truth is each file's extension. Every file is also scored as a partial copy
// (its first 40 lines), and plain paragraphs from .md files are scored as
// "prose", which must stay markdown -- most of what gets pasted is prose, and
// prose shown as code is the costly mistake. Files are split by a hash of their
// path, so rules can be tuned on one half and judged on the other.
//
// The detector is read out of src/gallery.html between the
// <detect-pasted-type> markers, so this always scores the shipped code.
// Run locally against your own files; CI covers the detector with fixed cases
// in tests/test_gallery_ui.py instead, since it has no such folder.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const html = fs.readFileSync(path.join(here, '..', '..', 'src', 'gallery.html'), 'utf8');
const m = html.match(/\/\/ <detect-pasted-type>([\s\S]*?)\/\/ <\/detect-pasted-type>/);
if (!m) { console.error('detector markers not found in gallery.html'); process.exit(2); }
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(m[1] + '\nglobalThis.detectPastedType = detectPastedType;', sandbox);
const detect = sandbox.detectPastedType;

const root = process.argv[2];
if (!root || !fs.existsSync(root)) {
  console.error('usage: node bench.mjs <corpus-dir> [tune|holdout|all] [show]');
  process.exit(2);
}
const half = process.argv[3] || 'all';
const SKIP = ['node_modules', '.git', 'site-packages', '__pycache__', '.venv', 'venv', 'dist', 'build'];
const TRUTH = { '.md': 'md', '.py': 'py', '.js': 'js', '.mjs': 'js', '.yaml': 'yaml',
                '.yml': 'yaml', '.json': 'json', '.csv': 'csv', '.html': 'html', '.svg': 'svg' };

const items = [];
for (const rel of fs.readdirSync(root, { recursive: true })) {
  const p = path.join(root, rel);
  if (SKIP.some(s => p.split(path.sep).includes(s))) continue;
  const truth = TRUTH[path.extname(p).toLowerCase()];
  if (!truth) continue;
  let st; try { st = fs.statSync(p); } catch { continue; }
  if (!st.isFile() || st.size > 400_000 || st.size < 20) continue;
  const text = fs.readFileSync(p, 'utf8');
  if (!text.trim()) continue;
  const h = crypto.createHash('md5').update(rel).digest()[0] % 2 ? 'tune' : 'holdout';
  if (half !== 'all' && h !== half) continue;
  items.push({ rel, truth, shape: 'whole', text });
  const head = text.split(/\r?\n/).slice(0, 40).join('\n');
  if (head.length < text.length) items.push({ rel, truth, shape: 'first 40 lines', text: head });
  if (truth === 'md') {
    for (const para of text.split(/\n\s*\n/)) {
      const t = para.trim();
      if (t.length < 80 || t.length > 1200 || /^[#>|\-*\d`<]|\]\(|\*\*|`/.test(t)) continue;
      items.push({ rel, truth: 'prose', shape: 'paragraph', text: t });
    }
  }
}
if (!items.length) { console.error('no scorable files found -- refusing to report a score'); process.exit(1); }

const rows = {}, wrong = {};
const t0 = process.hrtime.bigint();
for (const it of items) {
  const got = detect(it.text);
  const want = it.truth === 'prose' ? 'md' : it.truth;
  const key = it.truth + (it.shape === 'first 40 lines' ? ' (first 40 lines)' : '');
  rows[key] ??= { n: 0, right: 0, as: {} };
  rows[key].n++;
  if (got === want) rows[key].right++;
  else { rows[key].as[got] = (rows[key].as[got] || 0) + 1; (wrong[key] ??= []).push(`${it.rel} -> ${got}`); }
}
const ms = Number(process.hrtime.bigint() - t0) / 1e6 / items.length;
let right = 0, total = 0;
console.log(`detector from src/gallery.html, ${half} half, ${ms.toFixed(3)} ms per paste\n`);
for (const k of Object.keys(rows).sort()) {
  const r = rows[k]; right += r.right; total += r.n;
  const miss = Object.entries(r.as).sort((a, b) => b[1] - a[1]).map(([g, c]) => `${g}:${c}`).join(' ');
  console.log(`  ${k.padEnd(26)} ${String(r.right).padStart(5)}/${String(r.n).padEnd(5)} ` +
              `${(100 * r.right / r.n).toFixed(1).padStart(5)}%   ${miss ? 'wrong as ' + miss : ''}`);
}
console.log(`  ${'ALL'.padEnd(26)} ${right}/${total}  ${(100 * right / total).toFixed(1)}%`);
if (process.argv[4] === 'show')
  for (const [k, v] of Object.entries(wrong)) console.log(`\n-- wrong: ${k} --\n  ` + v.slice(0, 10).join('\n  '));
