/* Draw a student's .docx in the work pane. The plain-text extract is what
   grading reads; it is not the document. This stays on this machine:
   useGoogleFonts is off, so no font file is fetched from anywhere else. */
import { DocxScrollViewer } from '../vendor/ooxml/dist/docx.mjs';

const live = new Map();

function mount(host, url) {
  const previous = live.get(url);
  if (previous) {
    try { previous.viewer.destroy(); } catch (_) { /* already gone */ }
    live.delete(url);
  }
  const viewer = new DocxScrollViewer(host, {
    background: 'var(--bg)',
    gap: 16,
    paddingTop: 16,
    paddingBottom: 16,
    enableTextSelection: true,
    enableHyperlinks: true,
    useGoogleFonts: false,
    progressiveLayout: true,
    pageShadow: '0 1px 3px rgba(0,0,0,.18), 0 0 0 1px rgba(0,0,0,.12)',
  });
  live.set(url, { viewer, host });
  return viewer.load(url).then(() => {
    const current = live.get(url);
    if (!current || current.viewer !== viewer) return null;
    host.dataset.ready = '1';
    host.setAttribute('aria-busy', 'false');
    return viewer;
  }).catch(err => {
    const current = live.get(url);
    if (current && current.viewer === viewer) {
      live.delete(url);
      try { viewer.destroy(); } catch (_) { /* already gone */ }
    }
    throw err;
  });
}

/* Pull a document that is already on screen out of the pane before the pane
   is rebuilt, so the next paint can put the same pages back. */
function take(url) {
  const rec = live.get(url);
  if (!rec || !rec.host || !rec.host.querySelector('canvas')) return null;
  rec.host.remove();
  return rec.host;
}

function dispose(keep) {
  const held = new Set(keep || []);
  for (const [url, rec] of [...live]) {
    if (held.has(url)) continue;
    live.delete(url);
    try { rec.viewer.destroy(); } catch (_) { /* already gone */ }
  }
}

function relayout(url) {
  const rec = live.get(url);
  if (rec && typeof rec.viewer.relayout === 'function') rec.viewer.relayout();
}

window.DocxView = { mount, take, dispose, relayout };
