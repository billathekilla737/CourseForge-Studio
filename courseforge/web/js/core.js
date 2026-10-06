/* CourseForge Studio - the shell. Talks only to the Python server on 127.0.0.1.
   Shared state, the API call, jobs and their dock, the confirm dialog, banners,
   the router, the views and the area registry. Every area script loads after
   this and before boot.js, which makes the first route. */
'use strict';

const S = {
  health: null, courses: [], course: null, assignments: [], assignment: null,
  ws: null, sel: 0, filter: 'all', query: '', showWork: false, snap: true,
  // Multi-selection for group actions. Keyed by user_id, so it survives a
  // filter or search change that reorders the rows underneath it.
  picked: new Set(), anchor: 0, insightsScope: 'class',
  // Timed re-read of the Canvas gradebook while an assignment is open.
  pullTimer: null, pulling: false,
  // The shell: which view is up, the parsed route, and what to stop on leaving.
  view: null, route: null, onLeave: [],
};

const $ = s => document.querySelector(s);
const esc = s => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

function studioKey() {
  const el = document.querySelector('meta[name="cf-secret"]');
  return (el && el.getAttribute('content')) || '';
}

async function api(path, opts = {}) {
  const headers = {};
  const key = studioKey();
  if (key) headers['X-Studio-Key'] = key;
  if (opts.body) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch('/api' + path, {
      method: opts.body ? 'POST' : (opts.method || 'GET'),
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (err) {
    // The browser could not reach 127.0.0.1 at all. That is not Canvas and not
    // a bug in the page: the little CourseForge Studio window that owns the
    // server has been closed, or the server it was pointing at has gone. The
    // browser's own words for this are "Failed to fetch", which reads as a
    // Canvas problem and sends people off to check their token.
    if (err && err.name === 'AbortError') throw err;
    noticeServerDown();
    const down = new Error('CourseForge Studio is not running on this computer. '
      + 'Open CourseForge Studio again (the small window) and keep it open, then reload this page.');
    down.serverDown = true;
    throw down;
  }
  if ($('#downBar')) $('#downBar').remove();
  noticeBuild(res.headers.get('X-App-Build'));
  const data = await res.json().catch(() => ({ error: 'bad JSON from server' }));
  if (!res.ok) {
    // A restart rotates the page key. The open tab still has the old one, so
    // every button, including Refresh from Canvas, is refused until reload.
    if (res.status === 403 && /Reload this browser page/.test(data.error || '')) {
      noticeKeyStale();
    }
    // Carry the whole body along. A refused write is not a failure -- it is the
    // server asking, and its answer (the summary and the one-time token) lives
    // in here.
    const err = new Error(data.error || ('HTTP ' + res.status));
    err.body = data;
    err.status = res.status;
    throw err;
  }
  return data;
}

/* This tab keeps running the script it booted with, so after an update it asks
   the old questions of a new server and the answers stop lining up: a push
   dialog quoting a deleted endpoint, a confirmation that contradicts the line
   above it. The server stamps every reply with what it is currently serving.
   The moment that stops matching what we started with, say so plainly rather
   than letting someone try to reconcile two versions of the truth. */
function noticeServerDown() {
  if ($('#downBar')) return;
  const bar = document.createElement('div');
  bar.id = 'downBar';
  bar.className = 'staleBar';
  bar.innerHTML = `<b>CourseForge Studio is not running.</b> This page cannot
    reach the app on this computer. The small CourseForge Studio window that
    runs it has been closed, or was never opened. Open CourseForge Studio again,
    keep that window open, then reload this page. Nothing here is a Canvas or
    token problem.
    <button class="btn" onclick="location.reload()">Reload</button>`;
  document.body.prepend(bar);
}

function noticeKeyStale() {
  if ($('#staleBar') || $('#keyBar')) return;
  const bar = document.createElement('div');
  bar.id = 'keyBar';
  bar.className = 'staleBar';
  bar.innerHTML = `<b>Reload this page.</b> Studio was restarted, so this tab's
    key no longer matches. Refresh from Canvas cannot work until you reload.
    <button class="btn sm" id="keyReload" type="button">Reload now</button>`;
  document.body.prepend(bar);
  $('#keyReload').onclick = () => location.reload();
}

function noticeBuild(build) {
  if (!build) return;
  if (!S.build) { S.build = build; return; }
  if (S.build === build || $('#staleBar')) return;
  const bar = document.createElement('div');
  bar.id = 'staleBar';
  bar.className = 'staleBar';
  bar.innerHTML = `<b>This page is out of date.</b> The app was updated while
    this tab was open, so what you see here no longer matches the server.
    Reload before pushing anything to Canvas.
    <button class="btn sm" id="staleReload">Reload now</button>`;
  document.body.appendChild(bar);
  $('#staleReload').onclick = () => location.reload();
}

/* ------------------------------------------------------- the second click */
/* Nothing reaches Canvas on one click. The server refuses the first attempt and
   hands back a plain sentence plus a one-time token; this shows the sentence and
   sends the token. The token only spends on that exact change, so agreeing to
   one thing can never apply another. */
/* A value on the confirmation screen, in words a person reads. The server
   sends dates as UTC instants because it has no timezone of its own; this is
   where the instructor's wall clock comes back. */
function shownValue(value) {
  const text = String(value == null ? '' : value);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(text)) return text;
  const when = new Date(text);
  if (isNaN(when)) return text;
  return `${fmtDay(when)} at ${fmtTime(when)}`;
}

function askConfirm(info, proceed, opts = {}) {
  const host = $('#modalHost');
  const previous = host.innerHTML;              // put the review screen back on cancel
  const lines = (() => {
    try {
      const parsed = JSON.parse(info.detail || 'null');
      if (Array.isArray(parsed) && parsed.length && parsed[0].label) {
        return `<ul class="cfList">${parsed.map(d => `<li><b>${esc(d.label)}</b>
          <span class="cfFrom">${esc(shownValue(d.from))}</span>
          <span class="cfArrow">→</span>
          <span class="cfTo">${esc(shownValue(d.to))}</span></li>`).join('')}</ul>`;
      }
    } catch (_) { /* detail is free text, or absent */ }
    return info.detail ? `<div class="cfDetail">${esc(info.detail)}</div>` : '';
  })();

  // A confirm can offer more than one way to send the same change. Each
  // button carries its own value back to the caller.
  const actions = (opts.actions && opts.actions.length) ? opts.actions
    : [{ label: opts.verb || 'Yes, send it', value: undefined, cls: 'danger' }];

  host.innerHTML = `<div class="modalBack"><div class="modal narrow cfModal">
      <h3>${esc(opts.title || 'Send this to Canvas?')}</h3>
      <div class="cfSummary">${esc(info.summary || 'This changes a live course.')}</div>
      ${lines}
      <div class="cfNote">${esc(opts.note
        || 'This is a live course. Nothing has been sent yet.')}</div>
      <div class="foot">
        <span class="spacer"></span>
        <button class="btn" id="cfNo">Cancel</button>
        ${actions.map((a, i) => `<button class="btn ${esc(a.cls == null ? 'danger' : a.cls)}"
          data-cf="${i}">${esc(a.label)}</button>`).join('')}
      </div>
    </div></div>`;
  $('#cfNo').onclick = () => {
    host.innerHTML = previous;
    if (opts.onCancel) opts.onCancel();
    setStatus('nothing was sent', 'ok');
  };
  host.querySelectorAll('[data-cf]').forEach(btn => {
    btn.onclick = () => {
      const picked = actions[+btn.dataset.cf];
      host.innerHTML = '';
      proceed(info.confirm, picked.value);
    };
  });
  const first = host.querySelector('[data-cf]');
  if (first) first.focus();
}

/* A plain POST that may be refused once. `start(token)` does the call. */
async function postConfirmed(start, opts = {}) {
  try {
    return await start(null);
  } catch (err) {
    const body = err.body || {};
    if (!body.needs_confirm) throw err;
    return await new Promise((resolve, reject) => {
      askConfirm(body, async (token, choice) => {
        try { resolve(await start(token, choice)); }
        catch (again) { reject(again); }
      }, { ...opts, onCancel: () => resolve(null) });
    });
  }
}

/* The same handshake for work that runs as a background job, where the refusal
   comes back on the finished job rather than on the HTTP response. */
function runJobConfirmed(title, start, onDone, opts = {}) {
  const go = (token, choice) => runJob(title, () => start(token, choice), onDone, {
    ...opts,
    onNeedsConfirm: info => askConfirm(info, go, opts),
  });
  go(null);
}
/* Some errors are written as several lines for a terminal (the token message
   names one path per line). A one-line hint gets the headline; the banner above
   already carries the whole thing. */
function firstLine(message) {
  const line = String(message || '').split(/\r?\n/).find(l => l.trim()) || '';
  return line.length > 120 ? line.slice(0, 117) + '…' : line;
}

function setStatus(msg, cls) { const el = $('#status'); el.textContent = msg || ''; el.className = 'status ' + (cls || ''); }
function fmtDate(iso) {
  if (!iso) return '';
  try { return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
  catch { return iso; }
}
const fmtDay = d => d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
const fmtTime = d => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const num = v => (v == null || v === '') ? '' : (Number.isInteger(+v) ? String(+v) : String(+v));

/* Escape closes a shell dialog, and gives you the roster back after a range
   select. The grader's own dialogs and the confirm screen keep their buttons:
   a question about a Canvas write is answered, never dismissed. */
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape') return;
  const host = $('#modalHost');
  if (host.innerHTML) {
    const dialog = host.querySelector('[data-studio-modal]');
    if (dialog && !dialog.hasAttribute('data-no-escape')) { ev.preventDefault(); closeModal(); }
    return;
  }
  if (S.picked.size && typeof clearPicked === 'function') { ev.preventDefault(); clearPicked(); }
});

/* ------------------------------------------------------------------ boot */
async function boot() {
  const ctrl = new AbortController();
  const kill = setTimeout(() => ctrl.abort(), 12000);
  let localDown = false;
  try {
    S.health = await api('/health', { signal: ctrl.signal });
    $('#hostLine').textContent = S.health.canvas.ok
      ? `${S.health.canvas.name} · ${S.health.base_url.replace(/^https?:\/\//, '')}`
      : 'Canvas not connected';
  } catch (err) {
    const aborted = err && err.name === 'AbortError';
    if (!aborted) {
      banner('err', 'Cannot reach the local server: ' + esc(err.message));
      localDown = true;
    } else {
      S.health = {
        canvas: { ok: false, error: 'Canvas did not answer. The connection is stuck.' },
        claude: { logged_in: null },
        pseudonymize: false,
      };
      $('#hostLine').textContent = 'Canvas not connected';
    }
  } finally {
    clearTimeout(kill);
  }
  if (localDown) return;
  renderBanners();
  const skip = $('#skipLink');
  if (skip) skip.onclick = skipToContent;
  route();
  api('/claude-check').then(info => { S.health.claude = info; renderBanners(); }).catch(() => {});
  // The inbox count lives in the header, so it starts once and outlives every
  // view change rather than being painted by whichever screen is up.
  document.dispatchEvent(new CustomEvent('studio:booted'));
}

/* Notices the instructor has read once and does not need again. Only the
   informational ones are dismissible: an error you can hide is an error that
   goes unfixed. Kept per-browser, because it is a reading preference, not a
   property of the install. */
const HIDDEN_BANNERS_KEY = 'cg.hiddenBanners';
function hiddenBanners() {
  try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_BANNERS_KEY) || '[]')); }
  catch (_) { return new Set(); }          // private mode, or a stale value
}
function hideBanner(id) {
  const set = hiddenBanners();
  set.add(id);
  try { localStorage.setItem(HIDDEN_BANNERS_KEY, JSON.stringify([...set])); }
  catch (_) { /* private mode: hidden for this session only */ }
  renderBanners();
}

function banner(kind, html) {
  const div = document.createElement('div');
  div.className = 'banner ' + kind;
  div.innerHTML = html;
  $('#banners').appendChild(div);
}
function renderBanners() {
  $('#banners').innerHTML = '';
  const h = S.health;
  if (!h) return;
  if (!h.canvas.ok && h.canvas.reason === 'no_token') {
    banner('err', '<b>This computer has no Canvas token yet.</b>' +
      '<span class="bannerHint">Paste it once. It is saved on this machine and ' +
      'the courses load after that.</span>' +
      '<button class="btn sm" id="bnSetup">Paste Canvas token…</button>' +
      '<details class="bannerWhy"><summary>details</summary>' +
      '<span class="bannerDetail">' + esc(h.canvas.error || '') + '</span></details>');
    const bn = $('#bnSetup');
    if (bn) bn.onclick = () => openSetup();
    if (!S.tokenPrompted && typeof openSetup === 'function') {
      S.tokenPrompted = true;
      openSetup();
    }
  } else if (!h.canvas.ok) {
    // A missing token and a Canvas outage used the same sentence, so a rate
    // limit or a failed address lookup looked like the token had vanished.
    const err = h.canvas.error || '';
    const reached = /Rate Limit|getaddrinfo|Cannot reach Canvas|timed out|urlopen error|11001|did not answer/i.test(err);
    if (reached) {
      banner('err', '<b>Canvas did not answer.</b>' +
        '<span class="bannerHint">The saved token is still there. This was the ' +
        'connection, not a missing token.</span>' +
        '<button class="btn sm" id="bnRetry" type="button">Try again</button>' +
        '<details class="bannerWhy"><summary>details</summary>' +
        '<span class="bannerDetail">' + esc(err) + '</span></details>');
      const again = $('#bnRetry');
      if (again) again.onclick = () => boot();
    } else {
      banner('err', '<b>Canvas is not connected.</b>' +
        '<span class="bannerHint">Paste your Canvas access token and this will ' +
        'save it for you.</span>' +
        '<button class="btn sm" id="bnSetup">Connect to Canvas…</button>' +
        '<details class="bannerWhy"><summary>details</summary>' +
        '<span class="bannerDetail">' + esc(err) + '</span></details>');
      const bn = $('#bnSetup');
      if (bn) bn.onclick = () => openSetup();
    }
  }
  const c = h.claude || {};
  if (c.logged_in === false) {
    banner('err', '<b>Claude CLI is not logged in</b> — auto-grading is disabled. ' +
      'Open a plain terminal, run <code>claude</code>, then <code>/login</code>, and restart this tool from that terminal.' +
      (c.detail ? ' <span style="opacity:.75">(' + esc(c.detail).slice(0, 160) + ')</span>' : ''));
  } else if (c.logged_in == null) {
    banner('warn', 'Checking the Claude CLI login…');
  }
  // The pseudonym notice describes the safe state, and it is the same sentence
  // every time the app opens, so it can be put away. It comes back on its own
  // if pseudonymize is ever turned off and on again in config.json.
  if (h.pseudonymize && !hiddenBanners().has('pseudonymize')) {
    banner('warn', 'Student names are swapped for tags before anything is sent to Claude: ' +
      'S-001 while grading, Student-1 in the Assistant. Type the real name either place — ' +
      'the swap happens here, and the list of who is who never leaves this machine.' +
      '<span class="spacer"></span>' +
      '<button class="btn sm" id="bnHidePseudo" title="Hide this notice on this machine">Hide</button>');
    const hide = $('#bnHidePseudo');
    if (hide) hide.onclick = () => hideBanner('pseudonymize');
  }
}

/* ----------------------------------------------------------------- router */
/* #/                      the courses picker
   #/schedule              the term schedule
   #/batch                 batch accessibility across courses (a11y.js)
   #/c/<cid>               the course hub (hub.js)
   #/c/<cid>/grade         the assignment list
   #/c/<cid>/a/<aid>       the grading workspace
   #/c/<cid>/<area>/...    whichever script registered that area id */
function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean)
    .map(p => { try { return decodeURIComponent(p); } catch (_) { return p; } });
  const inCourse = parts[0] === 'c' && !!parts[1];
  return { parts, courseId: inCourse ? parts[1] : null, areaId: inCourse ? (parts[2] || null) : null };
}
function missingOpener(label) {
  setStatus(label + ' is not installed in this build', 'err');
  return openCourses();
}
const TITLES = {
  schedule: 'Term schedule · CourseForge Studio', inbox: 'Inbox · CourseForge Studio',
  reports: 'Reports · CourseForge Studio', batch: 'Batch Course Restyle · CourseForge Studio',
  files: 'ADA file compliance · CourseForge Studio',
  extensions: 'Deadline extensions · CourseForge Studio',
  roster: 'Accommodation roster · CourseForge Studio',
  students: 'Students · CourseForge Studio',
  student: 'Student · CourseForge Studio',
};
function route() {
  const r = parseRoute();
  const parts = r.parts;
  S.route = r;
  // The tab title is whatever the last screen wrote there, so leaving a
  // course for the list kept the course's name in the tab. Reset it first;
  // the hub and the areas write their own name over this once they paint.
  document.title = TITLES[parts[0]] || 'CourseForge Studio';
  let opened;
  if (parts[0] === 'schedule') opened = openSchedule();
  else if (parts[0] === 'batch') {
    opened = typeof openBatch === 'function' ? openBatch() : missingOpener('Batch Course Restyle');
  } else if (parts[0] === 'reports') {
    opened = typeof openReports === 'function' ? openReports(parts[1]) : missingOpener('Reports');
  } else if (parts[0] === 'inbox') {
    opened = typeof openInbox === 'function' ? openInbox(parts[1]) : missingOpener('the Inbox');
  } else if (parts[0] === 'files') {
    opened = typeof openFileCompliance === 'function'
      ? openFileCompliance() : missingOpener('ADA file compliance');
  } else if (parts[0] === 'extensions') {
    opened = typeof openExtensions === 'function'
      ? openExtensions() : missingOpener('Deadline extensions');
  } else if (parts[0] === 'roster') {
    opened = Promise.resolve(openCourses()).then(() => {
      if (typeof openRoster === 'function') return openRoster();
      return missingOpener('the accommodations roster');
    });
  } else if (parts[0] === 'students') {
    opened = typeof openStudents === 'function'
      ? openStudents()
      : missingOpener('the student search');
  } else if (parts[0] === 'student' && parts[1]) {
    opened = typeof openStudent === 'function'
      ? openStudent(parts[1], parts[2] || '')
      : missingOpener('the student page');
  } else if (parts[0] === 'c' && parts[2] === 'student' && parts[3]) {
    opened = typeof openStudent === 'function'
      ? openStudent(parts[3], parts[1])
      : missingOpener('the student page');
  } else if (parts[0] === 'c' && parts[2] === 'a' && parts[3]) {
    rememberAssignment(parts[1], parts[3]);
    opened = openAssignment(parts[1], parts[3], { arriving: true });
  } else if (parts[0] === 'c' && parts[2] === 'grade') opened = openCourse(parts[1]);
  else if (parts[0] === 'c' && parts[2] && AREAS.has(parts[2])) {
    opened = AREAS.get(parts[2]).open(parts[1], parts.slice(3));
  } else if (parts[0] === 'c' && parts[1]) {
    opened = typeof openHub === 'function' ? openHub(parts[1]) : openCourse(parts[1]);
  } else opened = openCourses();
  // Once the view has painted, its heading takes focus (see focusView).
  Promise.resolve(opened)
    .catch(err => setStatus(firstLine(err && err.message), 'err'))
    .then(() => { if (S.route === r) focusView(); });
  return opened;
}
window.addEventListener('hashchange', route);

/* Which of the five views is on screen. Everything the last view left running
   stops here, the zone colour follows the route, and the area bar shows
   wherever a course is open and the work pane is not. */
const VIEWS = {
  picker: '#viewPicker', work: '#viewWork', schedule: '#viewSchedule',
  hub: '#viewHub', area: '#viewArea', inbox: '#viewInbox',
};
function showView(which) {
  for (const [name, sel] of Object.entries(VIEWS)) {
    const el = $(sel);
    if (el) el.classList.toggle('hidden', name !== which);
  }
  if (which !== 'work' && typeof stopAutoPull === 'function') stopAutoPull();
  if (which !== 'work') { const tb = $('#teachBar'); if (tb) tb.classList.add('hidden'); }
  runLeave();
  S.view = which;
  const r = S.route || parseRoute();
  // The zone colour: an area's own; grade for the list and the workspace;
  // none on the picker or the schedule.
  let zone = '';
  if (which === 'area') zone = (AREAS.get(r.areaId) || {}).zone || r.areaId || '';
  else if (which === 'hub') zone = 'hub';
  else if (which === 'work' || (which === 'picker' && r.courseId)) zone = 'grade';
  if (zone) document.body.dataset.area = zone; else delete document.body.dataset.area;
  // The area bar is hidden on the picker, the schedule and inside the workspace.
  const bar = $('#areaBar');
  const showBar = !!r.courseId && (which === 'hub' || which === 'area' || which === 'picker');
  if (bar) {
    bar.classList.toggle('hidden', !showBar);
    if (showBar) renderAreaBar(r.courseId, which === 'area' ? r.areaId : which === 'picker' ? 'grade' : null);
  }
  // The picker's cross-course strip belongs to the course list only. hub.js
  // paints it on this event; the assignment list shares the view and must not
  // inherit it.
  const strip = $('#pickerStrip');
  const home = which === 'picker' && !r.courseId;
  if (strip) {
    if (home) {
      document.dispatchEvent(new CustomEvent('studio:picker', { detail: { host: strip } }));
    } else strip.innerHTML = '';
  }
  // Two columns for the course list, one for the assignment list inside it.
  // They share the section, so the layout has to be switched rather than set.
  const picker = $('#viewPicker');
  if (picker) picker.classList.toggle('pickerHome', home);
  if (!home) { const band = $('#pickerResume'); if (band) band.innerHTML = ''; }
  document.dispatchEvent(new CustomEvent('studio:view', { detail: { view: which, route: r } }));
}
function crumbs(items) {
  $('#crumbs').innerHTML = items.map((it, i) =>
    (i ? '<span class="sep">/</span>' : '') +
    (it.href ? `<a href="${it.href}">${esc(it.label)}</a>` : `<b>${esc(it.label)}</b>`)
  ).join(' ');
}
/* ================================================================== shell */
/* The Studio's shell: the parts every area shares and none of them owns. An
   area is a script that calls registerArea() once; from then on the router
   knows its routes, the area bar shows its tab, and the hub asks it for a
   badge. Nothing here knows what any area does. */

window.Studio = window.Studio || {};
const Studio = window.Studio;
Studio.a11yKinds = Studio.a11yKinds || {};

/* ------------------------------------------------------------ registry */
const AREAS = new Map();
Studio.areas = AREAS;

function registerArea(area) {
  if (!area || !area.id || typeof area.open !== 'function') {
    throw new Error('registerArea needs at least { id, open }');
  }
  const entry = { label: area.id, zone: area.id, badge: () => null, ...area };
  AREAS.set(entry.id, entry);
  // A bar already on screen picks the new tab up. Areas register before boot,
  // so this only matters for a script that arrives late.
  const bar = $('#areaBar');
  if (bar && !bar.classList.contains('hidden') && S.route && S.route.courseId) {
    renderAreaBar(S.route.courseId, S.route.areaId);
  }
  return entry;
}

/* Something to run when the person leaves the current view: a poller to stop,
   a timer to clear. showView runs the list and empties it, so each view starts
   with nothing left over from the last. */
function onLeave(fn) {
  if (typeof fn === 'function') S.onLeave.push(fn);
  return fn;
}
function runLeave() {
  const fns = S.onLeave.splice(0);
  for (const fn of fns) {
    try { fn(); } catch (err) { console.warn('onLeave', err); }
  }
}

/* ------------------------------------------------------------- announce */
/* A hidden live region for the things a sighted person sees move: a job that
   finished, a page that changed underneath a dialog. Cleared first so the same
   sentence twice is still read twice. */
function announce(text) {
  const live = $('#live');
  if (!live) return;
  live.textContent = '';
  setTimeout(() => { live.textContent = String(text || ''); }, 30);
}

/* -------------------------------------------------------------- course */
/* The course record for a course id, for crumbs and headings. The picker
   fills S.courses when it paints; a tab opened straight on a course route
   has not been through the picker, so read the server's cached list once. */
async function ensureCourse(courseId) {
  const same = c => c && String(c.id) === String(courseId);
  const named = c => c && c.name && !/^Course \d+$/.test(c.name);
  if (same(S.course) && named(S.course)) return S.course;
  let found = S.courses.find(same);
  if (!found) {
    try { S.courses = await api('/courses'); } catch (_) { /* offline: the id will do */ }
    found = S.courses.find(same);
  }
  S.course = found || { id: courseId, name: 'Course ' + courseId };
  return S.course;
}

/* Where grading left off in a course, so the hub can offer to continue it.
   A reading preference, kept per browser. */
/* What a person calls this course. The server already strips the registrar's
   prefix and the delivery mode into `title` (schedule.course_title); this is
   the one place the browser decides to use it, so a heading never opens with
   "202630". Falls back to the full Canvas name when a course arrived from
   somewhere that has no title, and the full name is always the tooltip. */
function courseTitle(c) {
  if (!c) return 'Course';
  return c.title || c.name || ('Course ' + (c.id == null ? '' : c.id));
}

const LAST_ASSIGNMENT_KEY = 'cf.lastAssignment.';
function rememberAssignment(courseId, assignmentId) {
  try { localStorage.setItem(LAST_ASSIGNMENT_KEY + courseId, String(assignmentId)); }
  catch (_) { /* private mode */ }
}
function lastAssignment(courseId) {
  try { return localStorage.getItem(LAST_ASSIGNMENT_KEY + courseId) || null; }
  catch (_) { return null; }
}

/* ------------------------------------------------------------ area bar */
/* The second row under the header: one tab per area, in a fixed order, so the
   five areas read as one tool whichever of them is installed. A tab whose
   script is not loaded is shown but says so, rather than vanishing and
   leaving the person to wonder whether the feature exists. */
const AREA_TABS = [
  { id: 'grade', label: 'Grade', zone: 'grade', alias: [] },
  { id: 'a11y', label: 'Accessibility', zone: 'a11y', alias: [] },
  { id: 'build', label: 'Build', zone: 'build', alias: ['content'] },
  { id: 'tools', label: 'Tools', zone: 'tools', alias: ['courseops'] },
  { id: 'assistant', label: 'Assistant', zone: 'ai', alias: [] },
  { id: 'record', label: 'Record', zone: 'record', alias: [] },
];

function areaForTab(tab) {
  return AREAS.get(tab.id) || tab.alias.map(a => AREAS.get(a)).find(Boolean) || null;
}

function renderAreaBar(courseId, activeId) {
  const bar = $('#areaBar');
  if (!bar) return;
  const hub = (S.hub && String(S.hub.courseId) === String(courseId)) ? (S.hub.areas || null) : null;
  const active = activeId ? (AREAS.get(activeId) || { id: activeId, zone: activeId }) : null;
  bar.innerHTML = '<div class="areaBarIn">' + AREA_TABS.map(tab => {
    const area = areaForTab(tab);
    if (!area) {
      return `<span class="areaTab off" aria-disabled="true"
        title="${esc(tab.label)} is not installed in this build">${esc(tab.label)}</span>`;
    }
    const isActive = !!active && (active.id === tab.id || tab.alias.includes(active.id)
      || (active.zone === tab.zone && tab.id !== 'grade'));
    let badge = null;
    try { badge = area.badge ? area.badge(hub) : null; } catch (_) { badge = null; }
    const n = (badge == null || badge === 0 || badge === '') ? '' :
      `<span class="n" aria-label="${esc(badge)} waiting">${esc(badge)}</span>`;
    return `<a class="areaTab" href="#/c/${esc(courseId)}/${esc(area.id)}"
      data-zone="${esc(tab.zone)}"${isActive ? ' aria-current="page"' : ''}>${esc(tab.label)}${n}</a>`;
  }).join('') + '</div>';
}

/* --------------------------------------------------------------- modal */
/* One modal host, one dialog at a time. openModal wraps the content, moves
   focus in, and closeModal hands focus back to where it came from. Tab is kept
   inside whichever dialog is up, the grader's own included. */
let MODAL_RETURN = null;

function focusables(root) {
  return [...root.querySelectorAll(
    'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),' +
    'select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
    .filter(el => el.offsetParent !== null || el === document.activeElement);
}

function openModal(html, opts = {}) {
  const host = $('#modalHost');
  MODAL_RETURN = document.activeElement;
  host.innerHTML = `<div class="modalBack"><div class="modal ${esc(opts.cls || '')}"
      role="dialog" aria-modal="true" data-studio-modal${opts.noEscape ? ' data-no-escape' : ''}
      ${opts.label ? `aria-label="${esc(opts.label)}"` : ''}>${html}</div></div>`;
  const dialog = host.querySelector('[data-studio-modal]');
  const first = dialog.querySelector('[autofocus]') || focusables(dialog)[0] || dialog;
  if (first === dialog) dialog.setAttribute('tabindex', '-1');
  first.focus();
  return dialog;
}

function closeModal() {
  const host = $('#modalHost');
  host.innerHTML = '';
  const back = MODAL_RETURN;
  MODAL_RETURN = null;
  if (back && back.isConnected && typeof back.focus === 'function') {
    back.focus({ preventScroll: true });
  }
}

document.addEventListener('keydown', ev => {
  if (ev.key !== 'Tab') return;
  const host = $('#modalHost');
  if (!host || !host.firstElementChild) return;
  const dialog = host.querySelector('.modal') || host.firstElementChild;
  const list = focusables(dialog);
  if (!list.length) { ev.preventDefault(); return; }
  const first = list[0], last = list[list.length - 1];
  const inside = dialog.contains(document.activeElement);
  if (ev.shiftKey && (!inside || document.activeElement === first)) {
    ev.preventDefault(); last.focus();
  } else if (!ev.shiftKey && (!inside || document.activeElement === last)) {
    ev.preventDefault(); first.focus();
  }
});

/* --------------------------------------------------------------- focus */
/* After a route the heading of the new view takes focus, so a keyboard or
   screen-reader user lands on what changed rather than at the top of the page.
   The grading workspace is left alone: its roster owns the keyboard. */
const VIEW_HEADING = {
  picker: '#pickerTitle', schedule: '#schedTitle', hub: '#hubTitle', area: '#areaTitle',
  inbox: '#viewInbox h2',
};
function focusView() {
  if ($('#modalHost').innerHTML) return;             // a dialog has the focus
  const sel = VIEW_HEADING[S.view];
  const el = sel && $(sel);
  if (!el || el.closest('.hidden')) return;
  if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
  el.focus({ preventScroll: true });
}
function skipToContent() {
  const view = ['#viewPicker', '#viewSchedule', '#viewHub', '#viewArea', '#viewInbox', '#viewWork']
    .map(s => $(s)).find(el => el && !el.classList.contains('hidden'));
  if (!view) return;
  const heading = VIEW_HEADING[S.view] && $(VIEW_HEADING[S.view]);
  const target = (heading && !heading.closest('.hidden')) ? heading
    : focusables(view)[0] || view;
  if (!target.hasAttribute('tabindex') && !focusables(view).includes(target)) {
    target.setAttribute('tabindex', '-1');
  }
  target.focus();
}

Object.assign(Studio, {
  registerArea, onLeave, announce, ensureCourse, courseTitle, lastAssignment, renderAreaBar,
  openModal, closeModal, focusView,
});

/* ----------------------------------------------------------------- busy */
/* A spinning cursor while anything long is running. This is the one signal that
   does not depend on a dialog being open or on the server reporting anything, so
   it is the last line of defence against the app looking hung. Refcounted:
   overlapping jobs must not cancel each other's cursor. */
let BUSY = 0;
function busyOn() {
  BUSY += 1;
  document.documentElement.classList.add('busy');
}
function busyOff() {
  BUSY = Math.max(0, BUSY - 1);
  if (!BUSY) document.documentElement.classList.remove('busy');
}

/* ------------------------------------------------------------------ jobs */
/* m:ss, because "147s" makes you do arithmetic to know if it is stuck */
function clock(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/* Live rows for the units currently in flight. With one student this is the
   only thing on screen that moves, so it carries the whole "not hung" job. */
function renderJobItems(items) {
  const box = $('#jobItems');
  if (!box) return;
  if (!items || !items.length) { box.classList.remove('show'); box.innerHTML = ''; return; }
  box.classList.add('show');
  box.innerHTML = items.map(it => {
    // opus can sit quiet for half a minute mid-thought on a long submission,
    // so only complain well past that: a warning that cries wolf gets ignored.
    const stalled = (it.quiet_s || 0) > 90;
    return `<div class="jobItem${stalled ? ' stalled' : ''}">
      <span class="jiDot"></span>
      <span class="jiKey">${esc(it.key)}</span>
      <span class="jiState">${esc(it.state || '')}</span>
      <span class="jiDetail">${esc(it.detail || '')}</span>
      <span class="jiTime">${clock(it.elapsed_s)}</span>
    </div>`;
  }).join('');
}

/* ---------------------------------------------------------- the job dock */
/* A job outlives its dialog. Auto-grading a section takes minutes, and a dialog
   that cannot be closed makes watching the bar the only thing the app can do.
   Closing one hands it to the dock in the header instead: it keeps polling,
   keeps its log, and clicking the chip puts the dialog back where it was.
   Nothing here cancels anything -- the work is on the server, and closing a
   window has never been a way to stop it. */
let JOB_SEQ = 0;
const DOCK = [];

function jobPct(info) {
  if (!info || !info.total) return null;
  return Math.round(100 * (info.done || 0) / info.total);
}

/* Job titles are sentences ("Auto-grading 26 students with opus"); a chip in the
   header has room for about half of one, and the tooltip carries the rest. */
function jobShort(title) {
  const text = String(title || 'working');
  return text.length > 34 ? text.slice(0, 33) + '…' : text;
}

function dockAdd(J) {
  J.docked = true;
  if (!DOCK.includes(J)) DOCK.push(J);
  renderDock();
}
function dockDrop(J) {
  J.docked = false;
  const at = DOCK.indexOf(J);
  if (at >= 0) DOCK.splice(at, 1);
  renderDock();
}

function renderDock() {
  const host = $('#jobDock');
  if (!host) return;
  host.classList.toggle('show', !!DOCK.length);
  host.innerHTML = DOCK.map(J => {
    const pct = jobPct(J.info);
    const stopping = !J.finished && !!((J.info && J.info.stopping) || J.stopQueued);
    const cls = J.state === 'error' ? 'bad'
      : J.needsAnswer ? 'ask' : J.state === 'stopped' ? 'stopped'
        : J.finished ? 'done' : stopping ? 'stopping' : '';
    const right = J.state === 'error' ? 'failed'
      : J.needsAnswer ? 'needs you'
        : J.state === 'stopped' ? 'stopped'
          : J.finished ? 'done'
            : J.stopQueued ? 'stop queued' : stopping ? 'stopping…'
              : pct == null ? clock((Date.now() - J.started) / 1000) : pct + '%';
    const fill = pct == null ? 100 : pct;
    const canStop = jobCanStop(J);
    return `<button class="jobChip ${cls}" data-job="${J.id}"
        title="${esc(J.title)} — ${esc(J.msg || '')}${canStop ? ' (right-click to stop)' : ''}">
        ${J.finished || J.needsAnswer ? '' : '<span class="spin"></span>'}
        <span class="jcName">${esc(jobShort(J.title))}</span>
        <span class="jcBar"><i class="${pct == null && !J.finished
          ? 'indeterminate' : ''}" style="width:${fill}%"></i></span>
        <span class="jcRight">${esc(right)}</span>
      </button>`;
  }).join('');
  host.querySelectorAll('[data-job]').forEach(btn => {
    btn.onclick = () => {
      const J = DOCK.find(x => String(x.id) === btn.dataset.job);
      if (!J) return;
      if (J.needsAnswer) {
        // It finished by asking a question while nothing was on screen to ask.
        const info = J.needsAnswer;
        J.needsAnswer = null;
        dockDrop(J);
        J.opts.onNeedsConfirm(info);
        return;
      }
      dockDrop(J);
      openJob(J);
    };
    btn.oncontextmenu = ev => {
      const J = DOCK.find(x => String(x.id) === btn.dataset.job);
      if (!J) return;
      ev.preventDefault();
      openJobMenu(J, ev.clientX, ev.clientY);
    };
  });
}

/* ------------------------------------------------------------- stopping */
/* Grading can be stopped: it checks between students and throws away a read
   that was cut off. A push or a sync stopped halfway would leave Canvas and
   this machine disagreeing, so those say so instead of offering a button that
   cannot keep its promise.

   Stop is a request that waits its turn. Pressed before the server has said
   the run can stop (the first poll has not come back, or the send failed), it
   is queued and goes in on the next poll that allows it. Nobody has to keep
   watching for the moment it becomes possible. */
function jobStoppable(J) {
  return !!(J && (J.opts.stoppable || (J.info && J.info.cancellable)));
}
function jobCanStop(J) {
  return !!(J && !J.finished && jobStoppable(J) && !J.stopQueued
    && !(J.info && J.info.stopping));
}

function stopJob(J) {
  if (!jobCanStop(J)) return;
  J.stopQueued = true;
  J.msg = 'stop queued — it goes in as soon as the run allows';
  paintJob(J);
  sendQueuedStop(J);
}

/* Called on every poll while a stop is waiting. */
async function sendQueuedStop(J) {
  if (!J.stopQueued || J.finished || J.stopSending) return;
  const info = J.info;
  if (!J.job || !info) return;                     // first poll not back yet
  if (!('cancellable' in info)) {
    // A server from before Stop existed never says yes. Say what fixes it
    // once, and keep the request in case the run is restarted on a new one.
    if (!J.stopOldServer) {
      J.stopOldServer = true;
      setStatus('Stop needs the updated Studio: close the small Studio window, '
        + 'open it again, then reload this page', 'err');
    }
    return;
  }
  if (!info.cancellable) return;
  J.stopSending = true;
  try {
    await api('/jobs/' + J.job + '/stop', { body: {} });
    J.stopQueued = false;
    J.info = { ...(J.info || {}), stopping: true };
    J.msg = 'stopping…';
    setStatus('stopping ' + J.title.toLowerCase() + '…');
  } catch (err) {
    // Left queued: the next poll tries again. A finished job clears it.
    J.msg = 'stop queued — retrying (' + firstLine(err.message) + ')';
  } finally {
    J.stopSending = false;
    paintJob(J);
  }
}

function closeJobMenu() {
  const m = $('#jobMenu');
  if (m) m.remove();
  document.removeEventListener('pointerdown', _jobMenuAway, true);
  document.removeEventListener('keydown', _jobMenuKey, true);
}
function _jobMenuAway(ev) {
  const m = $('#jobMenu');
  if (m && !m.contains(ev.target)) closeJobMenu();
}
function _jobMenuKey(ev) {
  if (ev.key === 'Escape') { ev.preventDefault(); closeJobMenu(); }
}

function openJobMenu(J, x, y) {
  closeJobMenu();
  const menu = document.createElement('div');
  menu.id = 'jobMenu';
  menu.className = 'rosterMenu';
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', J.title);
  const canStop = jobCanStop(J);
  const why = J.finished ? 'Already finished'
    : J.stopQueued ? 'Stop queued — goes in as soon as it can'
      : (J.info && J.info.stopping) ? 'Stopping…'
        : 'Only grading can be stopped partway';
  menu.innerHTML = `<div class="rosterMenuHead">${esc(jobShort(J.title))}</div>
    <button role="menuitem" data-act="open">Show progress</button>
    <div class="sep"></div>
    <button role="menuitem" data-act="stop" class="danger"${canStop ? '' : ' disabled'}
      title="${canStop ? 'Students already finished keep their drafts. Nobody else is changed.' : esc(why)}">${
      canStop ? 'Stop grading' : esc(why)}</button>`;
  document.body.appendChild(menu);
  // Keep it on screen: the dock sits at the top right.
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + 'px';
  menu.style.top = Math.max(8, Math.min(y, innerHeight - r.height - 8)) + 'px';
  menu.querySelector('[data-act=open]').onclick = () => {
    closeJobMenu();
    // The same as a left click, which also knows about a waiting question.
    const chip = $(`#jobDock [data-job="${J.id}"]`);
    if (chip) chip.click();
  };
  menu.querySelector('[data-act=stop]').onclick = () => {
    closeJobMenu();
    stopJob(J);
  };
  (menu.querySelector('button:not([disabled])') || menu).focus();
  document.addEventListener('pointerdown', _jobMenuAway, true);
  document.addEventListener('keydown', _jobMenuKey, true);
}

/* ------------------------------------------------------------------ a job */
/* opts.autoClose dismisses the dialog once the job succeeds. For work the
   user asked for by name, leaving it up is right -- the log is the receipt. For
   work the app started on its own, a dialog waiting to be dismissed is just the
   button press it was meant to save. Failures always stay on screen. */
function runJob(title, start, onDone, opts = {}) {
  const J = {
    id: ++JOB_SEQ, title, opts, onDone, started: Date.now(),
    job: null, info: null, state: 'starting', msg: 'starting…', log: '',
    error: null, needsLogin: false, needsAnswer: null,
    finished: false, docked: false, node: null, timer: null, held: false,
  };
  J.hold = () => { if (!J.held) { J.held = true; busyOn(); } };
  J.release = () => { if (J.held) { J.held = false; busyOff(); } };
  J.hold();
  openJob(J);

  start().then(({ job }) => {
    J.job = job;
    const tick = async () => {
      let info;
      try {
        info = await api('/jobs/' + J.job);
      } catch (err) {
        J.finished = true;
        J.state = 'error';
        J.error = 'lost the job: ' + err.message;
        J.msg = J.error;
        J.release();
        if (!J.node) { setStatus(firstLine(J.error), 'err'); dockAdd(J); }
        paintJob(J);
        return;
      }
      J.info = info;
      J.state = info.state;
      J.msg = info.message || info.state;
      J.log = (info.log || []).join('\n');
      if (info.state === 'running') {
        if (J.stopQueued) {
          J.msg = 'stop queued — it goes in as soon as the run allows';
          sendQueuedStop(J);
        }
        paintJob(J);
        J.timer = setTimeout(tick, J.docked ? 1400 : 700);
        return;
      }
      J.release();
      finishJob(J, info);
    };
    tick();
  }).catch(err => {
    J.finished = true;
    J.state = 'error';
    J.error = 'Could not start: ' + err.message;
    J.msg = J.error;
    J.release();
    if (!J.node) { setStatus(firstLine(J.error), 'err'); dockAdd(J); }
    paintJob(J);
  });
}

function finishJob(J, info) {
  J.finished = true;
  clearTimeout(J.timer);

  if (info.needs_confirm && J.opts.onNeedsConfirm) {
    // The job stopped at Canvas's doorstep to ask. Not an error: hand the
    // question straight to the caller, which shows it and sends the token.
    // If some other dialog is up, the chip holds the question until it is
    // clicked rather than throwing a confirmation over what the user is doing.
    if (!J.node && $('#modalHost').innerHTML) {
      J.needsAnswer = info;
      setStatus('a change is waiting for your answer', 'err');
      dockAdd(J);
      return;
    }
    closeJobNode(J);
    dockDrop(J);
    J.opts.onNeedsConfirm(info);
    return;
  }

  if (info.state === 'error') {
    J.error = info.error || 'failed';
    J.needsLogin = !!info.needs_login;
    J.msg = J.error;
    announce(J.title + ': failed. ' + firstLine(J.error));
    // A failure with no dialog on screen must not disappear: the chip goes red
    // and stays put until it is opened and read.
    if (!J.node) { setStatus(firstLine(J.error), 'err'); dockAdd(J); }
    paintJob(J);
    return;
  }

  if (info.state === 'stopped') {
    // Stopped on purpose. The page still reloads (onDone below) so the
    // students finished before the stop show their drafts.
    J.msg = info.message || 'Stopped.';
    announce(J.title + ': stopped');
    setStatus(firstLine(J.msg), 'ok');
    paintJob(J);
    if (J.docked) setTimeout(() => { if (J.finished && !J.node) dockDrop(J); }, 9000);
    if (J.onDone) J.onDone(info.result);
    return;
  }

  if (J.stopQueued) {
    // It ran out before the queued stop could go in. Say so, so a finished
    // run is not mistaken for a stopped one.
    J.stopQueued = false;
    setStatus(J.title + ' — finished before the stop could go in', 'err');
  }
  J.msg = 'Done.';
  announce(J.title + ': done');
  paintJob(J);
  if (J.node && J.opts.autoClose) closeJobNode(J);
  if (J.docked) {
    setStatus(J.title + ' — done', 'ok');
    // The chip is the only receipt a docked job has, so it lingers long enough
    // to be noticed and clicked, unless the job was meant to be invisible.
    if (J.opts.autoClose) dockDrop(J);
    else setTimeout(() => { if (J.finished && !J.node) dockDrop(J); }, 9000);
  }
  if (J.onDone) J.onDone(info.result);
}

/* Drop this job's dialog, and only this job's dialog: by the time a slow job
   finishes, the host may well be showing something else. */
function closeJobNode(J) {
  const host = $('#modalHost');
  if (J.node && J.node.isConnected && host.firstElementChild === J.node) {
    host.innerHTML = '';
  }
  J.node = null;
}

function openJob(J) {
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal">
      <h3><span class="spin" id="jobSpin"></span>${esc(J.title)}</h3>
      <div class="sub" id="jobMsg">starting…</div>
      <div class="progWrap" id="jobProgWrap">
        <div class="progTrack">
          <div class="progFill" id="jobFill" style="width:0%"></div>
          <div class="progGhost" id="jobGhost" style="left:0%;width:0%"></div>
        </div>
        <div class="progMeta"><span id="jobCount"></span><span id="jobEta"></span></div>
      </div>
      <div class="jobItems" id="jobItems"></div>
      <div class="log" id="jobLog"></div>
      <div class="foot">
        <span class="jobHint" id="jobHint"></span>
        <span class="spacer"></span>
        <button class="btn danger hidden" id="jobStop"
          title="Students already finished keep their drafts. Nobody else is changed.">Stop</button>
        <button class="btn" id="jobClose">Close</button>
      </div>
    </div></div>`;
  J.node = host.firstElementChild;
  J.docked = false;
  const at = DOCK.indexOf(J);
  if (at >= 0) DOCK.splice(at, 1);
  renderDock();

  $('#jobClose').onclick = () => {
    closeJobNode(J);
    if (J.finished) { dockDrop(J); return; }
    // Still working. The cursor belongs to the dialog, so let it go and let the
    // chip carry the job from here -- the chip says everything the status line
    // would have, so it says it alone.
    J.release();
    dockAdd(J);
  };
  $('#jobStop').onclick = () => stopJob(J);
  paintJob(J);
}

/* Paint whichever surface this job currently has: its dialog, or its chip. */
function paintJob(J) {
  if (J.node && !J.node.isConnected) {
    // Something replaced the dialog without going through Close. Keep the job
    // visible rather than letting it run with nothing on screen at all.
    J.node = null;
    if (!J.finished) { J.release(); dockAdd(J); }
  }
  if (J.node) paintJobModal(J);
  else if (J.docked) renderDock();
}

function paintJobModal(J) {
  const msg = $('#jobMsg');
  if (!msg) return;
  const info = J.info || {};
  const running = !J.finished;
  const elapsed = (Date.now() - J.started) / 1000;

  msg.textContent = J.msg || J.state;
  $('#jobLog').textContent = J.log || '';
  $('#jobLog').scrollTop = 1e6;

  const wrap = $('#jobProgWrap');
  const items = info.items || [];
  renderJobItems(running ? items : []);

  if (info.total) {
    const done = info.done || 0, pct = Math.round(100 * done / info.total);
    wrap.classList.add('show');
    $('#jobFill').style.width = pct + '%';
    $('#jobFill').classList.toggle('indeterminate', false);
    $('#jobFill').classList.toggle('working', running);
    // A faint segment for the students in flight: not finished, so it does
    // not count toward the fill, but it is where the time is going.
    const ghost = $('#jobGhost');
    const inflight = Math.min(items.length, info.total - done);
    ghost.style.left = pct + '%';
    ghost.style.width = (running ? 100 * inflight / info.total : 0) + '%';
    $('#jobCount').textContent = `${done} of ${info.total} · ${pct}%`;
    if (done >= 2 && done < info.total && running) {
      const left = Math.round((elapsed / done) * (info.total - done));
      $('#jobEta').textContent = left > 90
        ? `about ${Math.round(left / 60)} min left`
        : `about ${left}s left`;
    } else if (running) {
      $('#jobEta').textContent = clock(elapsed) + ' elapsed';
    } else { $('#jobEta').textContent = ''; }
  } else if (running) {
    wrap.classList.add('show');
    $('#jobFill').classList.add('indeterminate');
    $('#jobFill').style.width = '100%';
    $('#jobCount').textContent = 'working…';
    $('#jobEta').textContent = clock(elapsed) + ' elapsed';
  }

  const stop = $('#jobStop');
  if (stop) {
    const stopping = running && !!(info.stopping || J.stopQueued);
    stop.classList.toggle('hidden', !(running && jobStoppable(J)));
    stop.disabled = stopping;
    stop.textContent = J.stopQueued ? 'Stop queued' : stopping ? 'Stopping…' : 'Stop';
  }

  const hint = $('#jobHint');
  if (running) {
    hint.textContent = jobCanStop(J)
      ? 'Close keeps it running — a progress chip stays up top. Right-click the chip to stop.'
      : 'Close keeps it running — a progress chip stays up top.';
  } else {
    hint.textContent = '';
    const spin = $('#jobSpin');
    if (spin) spin.remove();
    $('#jobFill').classList.remove('indeterminate', 'working');
    $('#jobGhost').style.width = '0%';
    if (J.state === 'done') {
      $('#jobFill').style.width = '100%';
      $('#jobEta').textContent = 'took ' + clock(elapsed);
    }
    if (J.state === 'error') {
      msg.innerHTML = '<span style="color:var(--danger)"><b>Failed.</b> '
        + esc(J.error || '') + '</span>';
      if (J.needsLogin) {
        msg.innerHTML += '<br>Run <code>claude</code> then <code>/login</code> in a plain terminal, ' +
          'and restart this tool from there.';
      }
    } else if (J.state === 'stopped') {
      msg.innerHTML = '<b>Stopped.</b> ' + esc(String(J.msg || '').replace(/^Stopped\.\s*/, ''));
    } else {
      msg.textContent = 'Done.';
    }
  }
}

