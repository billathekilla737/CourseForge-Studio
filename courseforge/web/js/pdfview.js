/* Draw a student's PDF in the work pane. The extracted words are what grading
   reads; they are not the page. Pictures, rules, and charts are on the page,
   so the page is what gets shown. PDF JavaScript stays off, and every file
   this loads is on this machine. */
import {
  getDocument, GlobalWorkerOptions, OutputScale,
} from '../vendor/pdfjs/pdf.min.mjs';

const assetBase = new URL('../vendor/pdfjs/', import.meta.url);
GlobalWorkerOptions.workerSrc = new URL('pdf.worker.min.mjs', assetBase).href;

const MAX_PAGES = 30;
const live = new Map();

function paintPage(pdf, host, index) {
  return pdf.getPage(index).then(async page => {
    const width = Math.max(280, host.clientWidth - 32);
    const base = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: width / base.width });
    const wrap = document.createElement('div');
    wrap.className = 'pdfPage';
    const canvas = document.createElement('canvas');
    const ratio = new OutputScale();
    ratio.sx = Math.min(ratio.sx, 2);
    ratio.sy = Math.min(ratio.sy, 2);
    canvas.width = Math.floor(viewport.width * ratio.sx);
    canvas.height = Math.floor(viewport.height * ratio.sy);
    canvas.style.width = Math.floor(viewport.width) + 'px';
    canvas.style.height = Math.floor(viewport.height) + 'px';
    wrap.appendChild(canvas);
    host.appendChild(wrap);
    const ctx = canvas.getContext('2d', { alpha: false });
    await page.render({
      canvasContext: ctx,
      viewport,
      transform: ratio.scaled ? [ratio.sx, 0, 0, ratio.sy, 0, 0] : null,
    }).promise;
    page.cleanup();
  });
}

function mount(host, url) {
  const previous = live.get(url);
  if (previous) {
    try { previous.viewer.destroy(); } catch (_) { /* already gone */ }
    live.delete(url);
  }
  let task = null;
  let pdf = null;
  let stopped = false;
  const viewer = {
    host,
    destroy() {
      stopped = true;
      try { if (task) task.destroy(); } catch (_) { /* already gone */ }
      try { if (pdf) pdf.destroy(); } catch (_) { /* already gone */ }
    },
  };
  live.set(url, { viewer, host });

  const fail = err => {
    const current = live.get(url);
    if (current && current.viewer === viewer) {
      live.delete(url);
      try { viewer.destroy(); } catch (_) { /* already gone */ }
    }
    throw err;
  };

  return fetch(url, { credentials: 'same-origin' }).then(resp => {
    if (!resp.ok) throw new Error('missing');
    return resp.arrayBuffer();
  }).then(buf => {
    if (stopped) return null;
    task = getDocument({
      data: new Uint8Array(buf),
      enableScripting: false,
      cMapUrl: new URL('cmaps/', assetBase).href,
      cMapPacked: true,
      standardFontDataUrl: new URL('standard_fonts/', assetBase).href,
      wasmUrl: new URL('wasm/', assetBase).href,
      verbosity: 0,
    });
    return task.promise;
  }).then(async doc => {
    if (stopped || !doc) return null;
    pdf = doc;
    const shown = Math.min(doc.numPages, MAX_PAGES);
    host.replaceChildren();
    await paintPage(doc, host, 1);
    if (stopped) return null;
    host.dataset.ready = '1';
    host.setAttribute('aria-busy', 'false');
    const rest = (async () => {
      for (let i = 2; i <= shown; i++) {
        if (stopped) return;
        await paintPage(doc, host, i);
      }
      if (!stopped && shown < doc.numPages) {
        const note = document.createElement('p');
        note.className = 'pdfNote';
        note.textContent = 'Showing the first ' + shown + ' of ' + doc.numPages
          + ' pages. Download the file for the rest.';
        host.appendChild(note);
      }
    })();
    rest.catch(() => { /* the first page is enough to keep */ });
    return viewer;
  }).catch(fail);
}

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

function relayout() {}

window.PdfView = { mount, take, dispose, relayout };
