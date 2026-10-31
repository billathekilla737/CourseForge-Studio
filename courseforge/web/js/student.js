/* Search across courses, then one student's record: courses, accommodation,
   extensions, grades, the audit lines, inbox and notes. The note and the
   history copy to Canvas user files.

   Nicknames are a display spelling (John "Jack" Doe). The legal name stays
   on .name for sorting, for Canvas, and for anything that gets saved. */
'use strict';

let nickById = {};
let nickLoad = null;

function formatNick(legal, nick) {
  const name = String(legal || '').trim().replace(/\s+/g, ' ');
  let n = String(nick || '').replace(/[“”"]/g, '').trim();
  n = n.replace(/^['‘’]+|['‘’]+$/g, '').replace(/\s+/g, ' ');
  if (!n) return name;
  const quoted = '"' + n + '"';
  if (name.indexOf(quoted) !== -1) return name;
  if (!name) return quoted;
  if (name.indexOf(',') !== -1) {
    const bits = name.split(',');
    const last = bits[0].trim();
    const rest = bits.slice(1).join(' ').trim().split(/\s+/).filter(Boolean);
    if (rest.length && last) return [rest[0], quoted].concat(rest.slice(1), [last]).join(' ');
  }
  const parts = name.split(' ');
  return [parts[0], quoted].concat(parts.slice(1)).join(' ');
}

function studentLabel(person) {
  if (person == null) return '';
  if (typeof person === 'string') return person;
  const uid = String(person.user_id || person.id || '');
  const nick = person.nickname || nickById[uid] || '';
  const legal = person.legal_name || person.name || '';
  return formatNick(legal, nick) || legal || uid;
}

function loadNicks() {
  if (!nickLoad) {
    nickLoad = api('/nicknames').then(data => {
      nickById = (data && data.by_id) || {};
      return nickById;
    }).catch(() => nickById);
  }
  return nickLoad;
}

async function saveNickname(userId, raw) {
  const data = await api('/student/' + encodeURIComponent(userId) + '/nickname', {
    body: { nickname: raw },
  });
  const nick = (data && data.nickname) || '';
  if (nick) nickById[String(userId)] = nick;
  else delete nickById[String(userId)];
  return data;
}

/* Swap a stored legal name for the nickname spelling in a sentence the
   instructor is reading. Short single words are left alone. */
function nickSentence(text, people) {
  let out = String(text || '');
  (people || []).forEach(p => {
    const legal = (p && (p.legal_name || p.name)) || '';
    const shown = studentLabel(p);
    if (!legal || shown === legal) return;
    if (legal.indexOf(' ') === -1 && legal.indexOf(',') === -1) return;
    out = out.split(legal).join(shown);
  });
  return out;
}

function courseCount(s) {
  if (s.course_refs && s.course_refs.length) return s.course_refs.length;
  return (s.courses || []).length;
}

/* RISK-INDEX: riskById, riskMark, the Check button, the Needs-a-look sort,
   and the section on the student page. See docs/RISK-INDEX.md. */
let riskById = {};
let repaintStudents = function () {};
let riskWatch = null;

function riskMove(n) {
  if (n == null) return '';
  return n > 0 ? ' +' + n : ' ' + n;
}

function riskMark(s) {
  const row = riskById[String(s.user_id)];
  if (!row) return '';
  const attend = row.attend == null ? 0 : row.attend;
  const why = (row.reasons || []).slice(0, 3).concat(row.attend_reasons || []).slice(0, 4).join('; ');
  return `<span class="stScore" title="${esc(why)}">
    <span class="stRisk stRisk-${esc(row.band)}">${row.score}${esc(riskMove(row.trend))}</span><span class="stKey">work</span>
    <span class="stRisk stRisk-${esc(row.attend_band || 'low')}">${attend}${esc(riskMove(row.attend_trend))}</span><span class="stKey">attend</span>
  </span>`;
}

function studentHit(s, mode) {
  const courses = s.courses || [];
  const marks = [];
  if (mode === 'courses') {
    const n = courseCount(s);
    marks.push(`<span class="stCount" title="${n} course${n === 1 ? '' : 's'} this term">${n}</span>`);
  }
  if (s.on_roster) marks.push('<span class="pill good">On the list</span>');
  else if (mode === 'accommodation') marks.push('<span class="pill muted">Not on the list</span>');
  return `<a class="stHit" href="#/student/${esc(s.user_id)}">
    <b>${esc(studentLabel(s))}</b>
    ${riskMark(s)}
    ${marks.join('')}
    <span>${esc(courses.slice(0, 3).join(', '))}${
      courses.length > 3 ? ' +' + (courses.length - 3) : ''}</span>
  </a>`;
}

function openStudents() {
  showView('area');
  $('#areaTitle').textContent = 'Students';
  $('#areaHint').textContent = 'This term, across the courses you are teaching now.';
  $('#areaActions').innerHTML = '';
  $('#areaTabs').hidden = true;
  $('#areaBody').innerHTML = `
    <div class="studentPage">
      <div class="stSearchRow">
        <label class="stSearch">Find a student
          <input id="stQuery" type="search" autocomplete="off"
            placeholder="Filter the list by name, SIS id or login">
        </label>
        <label class="rosterSort">Term
          <select id="stTerm" aria-label="Which term"></select>
        </label>
        <label class="rosterSort">Organize
          <select id="stSort" aria-label="Organize the student list">
            <option value="name">Name A–Z</option>
            <option value="name-desc">Name Z–A</option>
            <option value="course">By course, then name</option>
            <option value="accommodation">Accommodation list first</option>
            <option value="courses">In the most courses</option>
            <option value="risk">Needs a look</option>
          </select>
        </label>
      </div>
      <section class="stRiskBox" id="stRiskBox">
        <h3>Who needs a look</h3>
        <p class="muted" id="stRiskNote">No check yet. This reads each class you are teaching this term, once, and saves the result on this computer.</p>
        <div class="stBar" id="stRiskBar" hidden role="progressbar"
          aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><i></i></div>
        <div class="foot">
          <button class="btn" id="stRiskRun" type="button">Check this term</button>
        </div>
        <div id="stRiskSummary"></div>
      </section>
      <p class="muted" id="stSearchNote">Loading this term’s students…</p>
      <div id="stResults"></div>
    </div>`;
  document.title = 'Students · CourseForge Studio';
  crumbs([{ label: 'Courses', href: '#/' }, { label: 'Students' }]);

  let master = [];
  const lastName = s => {
    const sort = String(s.sortable_name || '').trim();
    if (sort) return sort.toLowerCase();
    const parts = String(s.name || '').trim().split(/\s+/);
    return ((parts[parts.length - 1] || '') + ' ' + (s.name || '')).toLowerCase();
  };
  const byName = (a, b) => lastName(a).localeCompare(lastName(b))
    || String(a.name || '').localeCompare(String(b.name || ''));
  const paint = () => {
    const q = (($('#stQuery') || {}).value || '').trim().toLowerCase();
    const mode = (($('#stSort') || {}).value) || 'name';
    let rows = !q ? master.slice() : master.filter(s =>
      [s.name, s.sortable_name, s.nickname, studentLabel(s), s.sis_user_id, s.login_id,
        (s.courses || []).join(' ')]
        .join(' ').toLowerCase().includes(q));
    const note = $('#stSearchNote');
    const host = $('#stResults');
    if (note) note.textContent = master.length
      ? (q ? `${rows.length} of ${master.length}` : `${master.length} student${master.length === 1 ? '' : 's'}`)
      : 'No students in this term yet.';
    if (!host) return;
    if (!rows.length) {
      host.innerHTML = '<p class="muted">No student matches that.</p>';
      return;
    }
    if (mode === 'course') {
      const groups = new Map();
      rows.forEach(s => {
        const refs = (s.course_refs && s.course_refs.length)
          ? s.course_refs : (s.courses || []).map(name => ({ name }));
        (refs.length ? refs : [{ name: 'No course' }]).forEach(ref => {
          const title = ref.name || 'Course';
          if (!groups.has(title)) groups.set(title, []);
          groups.get(title).push(s);
        });
      });
      const titles = [...groups.keys()].sort((a, b) => a.localeCompare(b));
      host.innerHTML = titles.map(title => {
        const people = groups.get(title).slice().sort(byName);
        return `<h3 class="stGroup">${esc(title)} <span class="muted">${people.length}</span></h3>`
          + people.map(s => studentHit(s, mode)).join('');
      }).join('');
      return;
    }
    if (mode === 'name-desc') rows.sort((a, b) => byName(b, a));
    else if (mode === 'accommodation') {
      rows.sort((a, b) => ((b.on_roster === true) - (a.on_roster === true)) || byName(a, b));
    } else if (mode === 'risk') {
      rows.sort((a, b) => {
        const ra = riskById[String(a.user_id)];
        const rb = riskById[String(b.user_id)];
        const sa = ra ? Math.max(ra.score || 0, ra.attend || 0) : -1;
        const sb = rb ? Math.max(rb.score || 0, rb.attend || 0) : -1;
        return sb - sa || byName(a, b);
      });
    } else if (mode === 'courses') {
      const groups = new Map();
      rows.forEach(s => {
        const n = courseCount(s);
        if (!groups.has(n)) groups.set(n, []);
        groups.get(n).push(s);
      });
      const levels = [...groups.keys()].sort((a, b) => b - a);
      host.innerHTML = levels.map(n => {
        const people = groups.get(n).slice().sort(byName);
        const label = n === 1 ? '1 course' : n + ' courses';
        return `<h3 class="stGroup">${esc(label)}</h3>` + people.map(s => studentHit(s, mode)).join('');
      }).join('');
      return;
    } else rows.sort(byName);
    host.innerHTML = rows.map(s => studentHit(s, mode)).join('');
  };

  const load = async (term) => {
    await loadNicks();
    const note = $('#stSearchNote');
    if (note) note.textContent = 'Loading the roster…';
    const qs = term ? ('?term=' + encodeURIComponent(term)) : '';
    const data = await api('/students' + qs);
    master = data.students || [];
    const sel = $('#stTerm');
    if (sel && !sel.dataset.ready) {
      const terms = data.terms || [];
      sel.innerHTML = terms.map(t =>
        `<option value="${esc(t.label)}"${t.label === data.term ? ' selected' : ''}>${
          esc(t.label)} (${t.count})</option>`).join('')
        + `<option value="__all"${data.term ? '' : ' selected'}>All terms</option>`;
      sel.dataset.ready = '1';
      sel.onchange = () => load(sel.value).catch(err => { if (note) note.textContent = err.message; });
    }
    paint();
  };

  const sortBox = $('#stSort');
  if (sortBox) {
    let saved = 'name';
    try { saved = localStorage.getItem('cg.studentSort') || 'name'; } catch (_) { /* private mode */ }
    if ([...sortBox.options].some(o => o.value === saved)) sortBox.value = saved;
    sortBox.onchange = () => {
      try { localStorage.setItem('cg.studentSort', sortBox.value); } catch (_) { /* private mode */ }
      paint();
    };
  }
  $('#stQuery').oninput = paint;
  repaintStudents = paint;
  const riskBtn = $('#stRiskRun');
  if (riskBtn) riskBtn.onclick = () => runRiskScan();
  loadSavedRisk().catch(() => {});
  load('').catch(err => {
    const note = $('#stSearchNote');
    if (note) note.textContent = err.message;
  });
  $('#stQuery').focus();
}

function scannedLabel(iso) {
  if (!iso) return 'Not scanned yet';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return 'Last scanned ' + iso;
  return 'Last scanned ' + d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function paintRiskSummary(data) {
  if (riskWatch) return;
  const note = $('#stRiskNote');
  const host = $('#stRiskSummary');
  if (!data || data.empty) {
    if (note) note.textContent = 'No check yet. This reads each class you are teaching this term, once, and saves the result on this computer.';
    if (host) host.innerHTML = '';
    return;
  }
  const counts = data.counts || {};
  if (note) {
    const when = scannedLabel(data.scanned_at);
    const fresh = data.new_due ? ` Read ${data.new_due} newly due.` : '';
    const kept = data.already_scanned ? ` ${data.already_scanned} already scanned were left as they were.` : '';
    note.textContent = when
      + `. Checked ${data.courses_checked || 0} class(es)`
      + (data.seconds != null ? ` in ${data.seconds}s` : '')
      + '.' + fresh + kept
      + ' Work is grades and missing assignments. Attend is absences; tardies add a little. Higher needs a look sooner.';
  }
  if (!host) return;
  const fails = data.failures || [];
  const attendCounts = data.attend_counts || {};
  host.innerHTML = `<p><span class="stKey">Work</span>
      <span class="stRisk stRisk-high">${counts.high || 0} high</span>
      <span class="stRisk stRisk-medium">${counts.medium || 0} medium</span>
      <span class="stRisk stRisk-low">${counts.low || 0} low</span></p>
    <p><span class="stKey">Attend</span>
      <span class="stRisk stRisk-high">${attendCounts.high || 0} high</span>
      <span class="stRisk stRisk-medium">${attendCounts.medium || 0} medium</span>
      <span class="stRisk stRisk-low">${attendCounts.low || 0} low</span></p>`
    + (fails.length ? `<p class="muted">${fails.length} class(es) could not be read: ${
        esc(fails.map(f => f.name).join(', '))}</p>` : '');
}

function applyRisk(data) {
  riskById = {};
  (data && data.students || []).forEach(row => { riskById[String(row.user_id)] = row; });
  paintRiskSummary(data);
  repaintStudents();
}

async function loadSavedRisk() {
  const data = await api('/students/risk');
  applyRisk(data);
  return data;
}

function ensureRiskChip() {
  if ($('#riskChip')) return;
  const el = document.createElement('div');
  el.id = 'riskChip';
  const inbox = $('#inboxChip');
  if (inbox && inbox.parentNode) inbox.parentNode.insertBefore(el, inbox);
  else document.querySelector('header') && document.querySelector('header').appendChild(el);
}

function paintRiskProgress(info) {
  const note = $('#stRiskNote');
  const bar = $('#stRiskBar');
  const btn = $('#stRiskRun');
  const running = !info || info.state === 'running';
  const done = +((info && info.done) || 0);
  const total = +((info && info.total) || 0);
  const pct = info && info.state === 'done' ? 100
    : (total ? Math.min(99, Math.round(100 * done / total)) : 8);
  if (bar) {
    bar.hidden = !running;
    const fill = bar.querySelector('i');
    if (fill) fill.style.width = pct + '%';
    bar.setAttribute('aria-valuenow', String(running ? pct : 100));
  }
  if (note && running) note.textContent = (info && info.message) || 'Checking classes…';
  if (btn) btn.disabled = !!running;
  ensureRiskChip();
  const host = $('#riskChip');
  if (!host) return;
  if (running) {
    host.innerHTML = `<a class="riskChip" href="#/students" title="${esc((info && info.message) || 'Checking classes')}">
      <span class="riskChipBar" aria-hidden="true"><i style="width:${pct}%"></i></span>
      ${total ? `Reading ${Math.min(done + 1, total)} of ${total}` : 'Checking…'}</a>`;
    return;
  }
  if (info && info.state === 'error') {
    host.innerHTML = `<a class="riskChip bad" href="#/students" title="${esc(info.error || 'The check failed')}">Check failed</a>`;
    if (note) note.textContent = info.error || 'The check failed. The student list is unchanged.';
  }
}

async function watchRiskJob(id, opts) {
  opts = opts || {};
  if (riskWatch) return riskWatch;
  ensureRiskChip();
  try { sessionStorage.setItem('cg.riskJob', id); } catch (_) { /* private mode */ }
  riskWatch = (async () => {
    try {
      for (;;) {
        let info;
        try { info = await api('/jobs/' + id); }
        catch (err) {
          try { sessionStorage.removeItem('cg.riskJob'); } catch (_) {}
          paintRiskProgress({ state: 'error', error: err.message });
          return;
        }
        paintRiskProgress(info);
        if (info.state === 'done') {
          try { sessionStorage.removeItem('cg.riskJob'); } catch (_) {}
          const data = await api('/students/risk');
          riskWatch = null;
          applyRisk(data);
          paintRiskProgress({ state: 'done', done: info.total, total: info.total, message: info.message });
          const counts = (data && data.counts) || {};
          const host = $('#riskChip');
          const high = counts.look != null ? counts.look : (counts.high || 0);
          if (host) host.innerHTML = `<a class="riskChip ${high ? 'lit' : ''}" href="#/students"
            title="${esc(scannedLabel(data && data.scanned_at))}${data && data.seconds != null ? ' · ' + data.seconds + 's' : ''}">${
            high ? high + ' need a look' : 'Check finished'}</a>`;
          if (!opts.background) {
            const box = $('#stSort');
            if (box) {
              box.value = 'risk';
              try { localStorage.setItem('cg.studentSort', 'risk'); } catch (_) {}
            }
            repaintStudents();
          }
          return;
        }
        if (info.state === 'error') {
          try { sessionStorage.removeItem('cg.riskJob'); } catch (_) {}
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    } finally {
      riskWatch = null;
    }
  })();
  return riskWatch;
}

async function runRiskScan(opts) {
  opts = opts || {};
  if (riskWatch) return riskWatch;
  const note = $('#stRiskNote');
  if (note) note.textContent = 'Starting…';
  paintRiskProgress({ state: 'running', done: 0, total: 0, message: 'Starting…' });
  try {
    const started = await api('/students/risk', { method: 'POST', body: {} });
    return watchRiskJob(started.job, opts);
  } catch (err) {
    paintRiskProgress({ state: 'error', error: err.message });
  }
}

document.addEventListener('studio:booted', () => {
  if (!S.health || !S.health.canvas || S.health.canvas.ok === false) return;
  ensureRiskChip();
  let existing = '';
  try { existing = sessionStorage.getItem('cg.riskJob') || ''; } catch (_) {}
  if (existing) {
    watchRiskJob(existing, { background: true });
    return;
  }
  try {
    if (sessionStorage.getItem('cg.riskBoot')) return;
    sessionStorage.setItem('cg.riskBoot', '1');
  } catch (_) { /* still start; a second tab may overlap */ }
  runRiskScan({ background: true });
});

async function openStudent(userId, courseId) {
  showView('area');
  $('#areaTitle').textContent = 'Student';
  $('#areaHint').textContent = 'loading…';
  $('#areaActions').innerHTML = '<a class="btn sm" href="#/students">All students</a>';
  $('#areaTabs').hidden = true;
  $('#areaTabs').innerHTML = '';
  $('#areaBody').innerHTML = '<p class="muted">Loading what this machine and Canvas already know…</p>';
  document.title = 'Student · CourseForge Studio';
  crumbs([
    { label: 'Courses', href: '#/' },
    { label: 'Students', href: '#/students' },
    { label: 'Student' },
  ]);

  let data;
  try {
    const q = courseId ? ('?course=' + encodeURIComponent(courseId)) : '';
    data = await api('/student/' + encodeURIComponent(userId) + q);
  } catch (err) {
    $('#areaHint').textContent = '';
    $('#areaBody').innerHTML = '<p class="err">' + esc(err.message) + '</p>';
    return;
  }

  const shown = data.display_name || studentLabel(data);
  latePageStudent = { id: String(userId), name: data.name || shown || '' };
  if (data.nickname) nickById[String(userId)] = data.nickname;
  $('#areaTitle').textContent = shown || ('Student ' + userId);
  document.title = (shown || 'Student') + ' · CourseForge Studio';
  crumbs([
    { label: 'Courses', href: '#/' },
    { label: 'Students', href: '#/students' },
    { label: shown || 'Student' },
  ]);
  $('#areaHint').textContent = (data.nickname && data.name && data.nickname !== data.name)
    ? ('Canvas name: ' + data.name) : (data.note || '');
  const stand = data.standing;
  const notes = (data.notes && data.notes.notes) || '';
  const threads = data.inbox || [];
  const refs = data.course_refs && data.course_refs.length
    ? data.course_refs
    : (data.courses || []).map(name => ({ id: '', name }));
  const history = data.history || [];

  $('#areaBody').innerHTML = `
    <div class="studentPage">
      <section class="stIdentity">
        <div><span class="muted">Canvas name</span> ${esc(data.name || '')}</div>
        <div><span class="muted">Canvas id</span> ${esc(data.user_id)}</div>
        ${data.sis_user_id ? `<div><span class="muted">SIS</span> ${esc(data.sis_user_id)}</div>` : ''}
        ${data.login_id ? `<div><span class="muted">Login</span> ${esc(data.login_id)}</div>` : ''}
        <div><span class="muted">On the accommodation list</span> ${data.on_roster ? 'yes' : 'no'}</div>
      </section>

      <section id="stWatchSec">
        <h3>Notes from grading</h3>
        <p class="muted">Notes you wrote on this student, most from the grader on one
          assignment. Open notes show beside their work on every assignment you grade.
          Nothing here is sent to Claude.</p>
        <div id="stWatch"></div>
        <div class="stWatchAdd">
          <select id="stWatchKind" aria-label="Kind of note">
            <option value="note">General note</option>
            <option value="integrity">Academic integrity concern</option>
          </select>
          <textarea id="stWatchText" rows="2" maxlength="2000"
            placeholder="Add a note that is not about one assignment"></textarea>
          <button class="btn" id="stWatchSave" type="button">Add note</button>
        </div>
      </section>

      <section id="stLateSec">
        <h3>Late and missing</h3>
        <p class="muted" id="stLateNote">Reading each class…</p>
        <div id="stLateBody"></div>
      </section>

      <section id="stRiskSec" hidden>
        <h3>How they're doing in each class</h3>
        <div id="stRiskBody"></div>
      </section>

      <section>
        <h3>Courses</h3>
        <p class="muted" id="stCourseNote">Open a class to see what is late or missing, and for how long.
          This term only.</p>
        ${courseDropdowns(refs)}
      </section>

      <section>
        <h3>Standing accommodation</h3>
        <p>${stand
          ? `<b>${esc(stand.label || stand.kind)}</b>${stand.note ? ' — ' + esc(stand.note) : ''}`
          : 'Not on the standing list.'}
          <a href="#/roster">Open the roster</a></p>
      </section>

      <section>
        <h3>Applied extras and extensions</h3>
        <p class="muted" id="stLive">Checking this term only…</p>
        <div id="stExtras"></div>
      </section>

      <section>
        <h3>History</h3>
        <p class="muted">Grades and submissions on this computer, plus the record of
          what was done. A copy is kept in your Canvas files so another PC can show it.</p>
        ${history.length ? `<ol class="stHistory">${history.map(h => `<li>
            <span class="muted">${esc(h.at || '')}</span>
            <b>${esc(h.kind || '')}</b>
            ${h.course ? esc(h.course) : ''}
            ${h.title ? ' · ' + esc(h.title) : ''}
            <div>${esc(nickSentence(h.summary || '', [data]))}</div>
            ${h.course_id && h.assignment_id
              ? `<a href="#/c/${esc(h.course_id)}/a/${esc(h.assignment_id)}">Open the assignment</a>`
              : ''}
          </li>`).join('')}</ol>`
          : '<p class="muted">Nothing recorded yet. Grade, extend or write a note and it will land here.</p>'}
      </section>

      <section>
        <h3>Canvas Inbox</h3>
        <div id="stInbox">${threads.map(t => t.error
          ? `<p class="err">${esc(t.error)}</p>`
          : `<div class="stThread"><a href="#/inbox/${esc(t.id)}">${esc(t.subject)}</a>
              <span class="muted">${esc(t.ago || t.at || '')}</span>
              <p>${esc(t.preview || '')}</p></div>`).join('')
          || '<p class="muted">No recent Canvas Inbox threads with this person.</p>'}</div>
      </section>

      <section>
        <h3>Nickname</h3>
        <p class="muted">Studio shows this spelling on your screens. A saved
          nickname appears in quotes between the first and last name. An empty
          box is not a nickname. Grades pushed to Canvas still use the Canvas
          name, and the model never receives the nickname. The nickname is
          kept in your Canvas files so another computer signed in as you
          shows the same spelling.</p>
        <div class="nickRow">
          <label>Goes by
            <input id="stNick" maxlength="40" value="${esc(data.nickname || '')}"
              placeholder="nickname" autocomplete="off">
          </label>
          <button class="btn" id="stNickSave" type="button">Save nickname</button>
        </div>
        <p class="nickPreview" id="stNickPreview"></p>
      </section>

      <section>
        <h3>Instructor notes</h3>
        <p class="muted">Stored with the history in your Canvas user files. Nothing here is sent to Claude.</p>
        <textarea id="stNotes" rows="6" maxlength="8000">${esc(notes)}</textarea>
        <div class="foot" style="margin:.6rem 0 1.2rem">
          <button class="btn" id="stSave">Save note</button>
          <span id="stSaveMsg" class="muted">${data.notes && data.notes.conflict
            ? 'Canvas has a newer copy — reload before saving over it.'
            : (data.notes && data.notes.written_at
              ? ('last written ' + esc(data.notes.written_at)) : '')}</span>
        </div>
      </section>
    </div>`;

  const nickInput = $('#stNick');
  const nickPreview = $('#stNickPreview');
  const paintNick = () => {
    if (!nickPreview) return;
    const next = formatNick(data.name || '', (nickInput && nickInput.value) || '');
    nickPreview.textContent = next && next !== (data.name || '')
      ? ('Shows as ' + next) : 'No nickname. The Canvas name is used as it is.';
  };
  paintNick();
  if (nickInput) nickInput.oninput = paintNick;
  const nickSave = $('#stNickSave');
  if (nickSave) nickSave.onclick = async () => {
    nickSave.disabled = true;
    try {
      const saved = await saveNickname(userId, (nickInput && nickInput.value) || '');
      data.nickname = saved.nickname || '';
      data.display_name = saved.display_name || formatNick(data.name || '', data.nickname);
      $('#areaTitle').textContent = data.display_name || data.name || ('Student ' + userId);
      document.title = ($('#areaTitle').textContent) + ' · CourseForge Studio';
      if (nickPreview) nickPreview.textContent = data.nickname
        ? ('Saved. Shows as ' + (data.display_name || data.name))
        : 'Cleared. The Canvas name is used as it is.';
      setStatus(data.nickname ? 'nickname saved' : 'nickname cleared', 'ok');
    } catch (err) {
      if (nickPreview) nickPreview.textContent = err.message;
    } finally {
      nickSave.disabled = false;
    }
  };

  const save = $('#stSave');
  if (save) save.onclick = async () => {
    const msg = $('#stSaveMsg');
    try {
      const r = await api('/student/' + encodeURIComponent(userId) + '/notes', {
        body: { notes: ($('#stNotes') || {}).value || '' },
      });
      if (msg) msg.textContent = (r && (r.did === 'sent' || r.state === 'sent' || r.did === 'in_sync'))
        ? 'saved to your Canvas files'
        : 'saved';
    } catch (err) {
      if (msg) msg.textContent = err.message;
    }
  };

  paintWatchNotes(userId, data.watch_notes || []);

  const pageGen = ++studentPageGen;
  paintStudentWork(userId, pageGen);
  paintStudentLive(userId, pageGen);
  paintStudentRisk(userId);
}

let studentPageGen = 0;

function paintWatchNotes(userId, notes) {
  const host = document.getElementById('stWatch');
  if (!host) return;
  const base = '/student/' + encodeURIComponent(userId) + '/watch';
  host.innerHTML = notes.length ? `<ul class="stWatchList">${notes.map(n => `
      <li class="${n.kind === 'integrity' ? 'concern' : ''}${n.resolved ? ' resolved' : ''}">
        <div class="noteHead">
          <span class="tag ${n.kind === 'integrity' && !n.resolved ? 'danger' : ''}">${
            n.kind === 'integrity' ? 'integrity concern' : 'note'}${n.resolved ? ' · resolved' : ''}</span>
          ${n.course_id && n.assignment_id
            ? `<a href="#/c/${esc(n.course_id)}/a/${esc(n.assignment_id)}">${esc(n.assignment_name || 'the assignment')}</a>`
            : '<span class="muted">not about one assignment</span>'}
          <span class="muted">${esc(typeof fmtDate === 'function' ? fmtDate(n.created) : n.created)}</span>
          <span class="noteBtns">
            <button class="btn sm" data-wres="${esc(n.id)}">${n.resolved ? 'Reopen' : 'Resolve'}</button>
            <button class="btn sm" data-wdel="${esc(n.id)}">Delete</button></span>
        </div>
        ${!n.about_user_id ? '' : String(n.user_id) === String(userId)
          ? `<div class="noteLink">May have copied from <a href="#/student/${esc(n.about_user_id)}">${
              esc(studentLabel({ user_id: n.about_user_id, name: n.about_name }))}</a></div>`
          : `<div class="noteLink">Named as the possible source in your note on <a href="#/student/${
              esc(n.user_id)}">${esc(studentLabel({ user_id: n.user_id, name: n.user_name }))}</a></div>`}
        <div class="noteText">${esc(n.text)}</div>
      </li>`).join('')}</ul>`
    : '<p class="muted">No notes yet. Use Note… beside a submission in the grader, or add one here.</p>';

  const update = async (nid, body) => {
    try {
      const r = await api(base + '/' + encodeURIComponent(nid), { body });
      const next = notes.map(n => n.id === nid ? r.note : n).filter(n => !n.deleted);
      paintWatchNotes(userId, next);
      setStatus(body.delete ? 'note deleted' : (body.resolved ? 'note resolved' : 'note reopened'), 'ok');
    } catch (err) { setStatus('could not change the note: ' + err.message, 'err'); }
  };
  host.querySelectorAll('[data-wres]').forEach(btn => btn.onclick = () => {
    const note = notes.find(n => n.id === btn.dataset.wres) || {};
    update(btn.dataset.wres, { resolved: !note.resolved });
  });
  host.querySelectorAll('[data-wdel]').forEach(btn => btn.onclick = () =>
    askConfirm({ summary: 'Delete this note from the student page and the grader. '
      + 'Resolve it instead to keep it here.' },
    () => update(btn.dataset.wdel, { delete: true }),
    { title: 'Delete this note?', verb: 'Yes, delete it',
      note: 'Removes the note here and the copy in your Canvas files. Nothing in the course changes.' }));

  const save = document.getElementById('stWatchSave');
  if (save) save.onclick = async () => {
    const box = document.getElementById('stWatchText');
    const text = (box && box.value || '').trim();
    if (!text) { setStatus('write something in the note first', 'err'); return; }
    save.disabled = true;
    try {
      const r = await api(base, { body: { text,
        kind: (document.getElementById('stWatchKind') || {}).value || 'note' } });
      box.value = '';
      paintWatchNotes(userId, [r.note, ...notes]);
      setStatus('note added', 'ok');
    } catch (err) {
      setStatus('could not add the note: ' + err.message, 'err');
    } finally {
      save.disabled = false;
    }
  };
}

function courseDropdowns(refs) {
  if (!refs.length) return '<p class="muted">No course on this login.</p>';
  return `<div class="stCourseList" id="stCourseList">${refs.map(c => {
    const name = c.name || c.id || 'Course';
    if (!c.id) return `<p class="stCourseBare">${esc(name)}</p>`;
    return `<details class="stCourse" data-cid="${esc(c.id)}">
      <summary>
        <span class="nm">${esc(name)}</span>
        <span class="stCourseMeta" data-state="busy">Waiting</span>
      </summary>
      <div class="stCourseBody">
        <p><a href="#/c/${esc(c.id)}/grade">Open the course</a></p>
        <p class="muted stBusy">Waiting to read this class.</p>
      </div>
    </details>`;
  }).join('')}</div>`;
}

function workCount(missing, late) {
  if (!missing && !late) return 'Nothing late or missing';
  const bits = [];
  if (missing) bits.push(missing === 1 ? '1 missing' : missing + ' missing');
  if (late) bits.push(late === 1 ? '1 late' : late + ' late');
  return bits.join(', ');
}

function workItemHtml(item, courseId) {
  const due = item.due_at && typeof fmtDate === 'function' ? fmtDate(item.due_at) : '';
  const title = item.title || 'Assignment';
  const name = item.assignment_id
    ? `<a href="#/c/${esc(courseId)}/a/${esc(item.assignment_id)}">${esc(title)}</a>`
    : esc(title);
  return `<li>${name}<span class="${item.kind === 'missing' ? 'missing' : 'late'}">${
    esc(item.label || item.kind || '')}</span>${
    due ? `<span class="muted">due ${esc(due)}</span>` : ''}</li>`;
}

function courseStillHere() {
  return !!document.getElementById('stCourseList');
}

function studentPageOpen() {
  const view = document.getElementById('viewArea');
  return !!(document.getElementById('stCourseList') && view && !view.classList.contains('hidden'));
}

function setNote(line) {
  ['stCourseNote', 'stLateNote', 'stuLateNote'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = line;
  });
}

function lateListHtml(courses) {
  const shown = (courses || []).filter(course =>
    course && (course.error || course.unread || (course.items || []).length));
  if (!shown.length) return '<p class="muted">Nothing is late or missing this term.</p>';
  return shown.map(course => {
    const name = course.course || 'Class';
    const items = course.items || [];
    const missing = items.filter(item => item.kind === 'missing').length;
    const late = items.filter(item => item.kind === 'late').length;
    const bits = [];
    if (missing) bits.push(missing === 1 ? '1 missing' : missing + ' missing');
    if (late) bits.push(late === 1 ? '1 late' : late + ' late');
    const meta = course.error ? 'Could not read' : (course.unread ? 'Not read' : bits.join(', '));
    const rows = items.map(item => {
      const due = item.due_at && typeof fmtDate === 'function' ? fmtDate(item.due_at) : '';
      const title = item.title || 'Assignment';
      const link = item.assignment_id && course.course_id
        ? `<a href="#/c/${esc(course.course_id)}/a/${esc(item.assignment_id)}">${esc(title)}</a>`
        : esc(title);
      return `<li>${link}
        <span class="${item.kind === 'missing' ? 'missing' : 'late'}">${esc(item.label || item.kind || '')}</span>
        ${due ? `<span class="muted">due ${esc(due)}</span>` : ''}</li>`;
    }).join('');
    const body = course.error
      ? `<p class="err">${esc(course.error)}</p>`
      : (course.unread
        ? '<p class="muted">This class was not read.</p>'
        : `<ul class="stWork">${rows}</ul>`);
    const remind = items.length && course.course_id
      ? `<button type="button" class="btn sm stRemind" data-course="${esc(course.course_id)}"
          data-name="${esc(name)}">Remind</button>`
      : '';
    return `<details class="stLateClass">
      <summary><span class="nm">${esc(name)}</span><span class="stCourseMeta${
        missing ? ' bad' : (late ? ' warn' : '')}">${esc(meta)}</span>${remind}</summary>
      <div class="stLateBody">${body}</div>
    </details>`;
  }).join('');
}

let latePageStudent = { id: '', name: '' };
let lateReminderItems = {};

function paintLateList(courses, message) {
  lateReminderItems = {};
  (courses || []).forEach(course => {
    if (!course || course.course_id == null) return;
    lateReminderItems[String(course.course_id)] = (course.items || [])
      .map(item => item.title || 'Assignment');
  });
  ['stLateBody', 'stuLateBody'].forEach(id => {
    const host = document.getElementById(id);
    if (!host) return;
    host.innerHTML = message
      ? `<p class="muted">${esc(message)}</p>`
      : lateListHtml(courses);
    host.querySelectorAll('.stRemind').forEach(btn => {
      const stop = ev => { ev.preventDefault(); ev.stopPropagation(); };
      btn.onmousedown = stop;
      btn.onclick = ev => {
        stop(ev);
        const id = btn.getAttribute('data-course');
        remindLateCourse(id, btn.getAttribute('data-name') || 'this course',
          lateReminderItems[id] || []);
      };
    });
  });
}

async function remindLateCourse(courseId, courseName, titles) {
  if (!latePageStudent.id || !titles.length) return;
  try {
    const sent = await postConfirmed(
      token => api('/student/' + encodeURIComponent(latePageStudent.id) + '/remind', {
        body: {
          course_id: courseId,
          course_name: courseName,
          items: titles,
          confirm: token,
        },
      }),
      { verb: 'Yes, send it',
        note: 'One private Canvas Inbox message. A sent message cannot be unsent.' });
    if (sent) setStatus('Reminder sent', 'ok');
  } catch (err) {
    setStatus(firstLine(err.message), 'err');
  }
}

function setCourseBusy(info, startedAt) {
  if (!courseStillHere()) return;
  const secs = Math.max(0, Math.round((Date.now() - (startedAt || Date.now())) / 1000));
  const msg = (info && info.message) || 'Reading late and missing work';
  const idle = Number(info && info.idle_s) || 0;
  const done = info && info.done;
  const total = info && info.total;
  const frac = total ? `${done || 0} of ${total}` : '';
  const stall = idle > 15 ? ` No update for ${Math.round(idle)}s.` : '';
  setNote([msg, frac, secs + 's'].filter(Boolean).join(' · ') + '.' + stall);
  document.querySelectorAll('#stCourseList .stCourse').forEach(el => {
    const meta = el.querySelector('.stCourseMeta');
    if (!meta || meta.dataset.state === 'done') return;
    const name = (el.querySelector('.nm') && el.querySelector('.nm').textContent) || '';
    const current = name && msg.indexOf(name.slice(0, 24)) !== -1;
    meta.textContent = current ? 'Reading now' : (frac || ('Reading · ' + secs + 's'));
    const busy = el.querySelector('.stBusy');
    if (busy) busy.textContent = current ? msg : (frac ? ('Waiting. ' + frac + ' read.') : 'The read is running.');
  });
}

function finishCourseRead(message, metaText) {
  const courseNote = document.getElementById('stCourseNote');
  if (courseNote) courseNote.textContent = message;
  const lateNote = document.getElementById('stLateNote');
  if (lateNote) lateNote.textContent = message;
  paintLateList(null, message);
  document.querySelectorAll('#stCourseList .stCourse').forEach(el => {
    const meta = el.querySelector('.stCourseMeta');
    const busy = el.querySelector('.stBusy');
    if (meta && meta.dataset.state !== 'done') {
      meta.textContent = metaText || 'Could not read';
      meta.dataset.state = 'done';
      if (metaText === 'Could not read' || metaText === 'Needs a restart') meta.classList.add('bad');
    }
    if (busy) busy.textContent = message;
  });
}

function paintCourseWork(live) {
  const work = live && live.work;
  if (!Array.isArray(work)) {
    finishCourseRead(
      'The read finished, and this Studio window does not have the late and missing list yet. Close CourseForge Studio and open it again, then reload this page.',
      'Needs a restart');
    return;
  }
  const byId = {};
  work.forEach(row => { if (row && row.course_id != null) byId[String(row.course_id)] = row; });
  const term = (live && live.term) || 'this term';
  document.querySelectorAll('#stCourseList .stCourse').forEach(el => {
    const meta = el.querySelector('.stCourseMeta');
    const body = el.querySelector('.stCourseBody');
    const row = byId[String(el.dataset.cid || '')];
    const open = `<p><a href="#/c/${esc(el.dataset.cid)}/grade">Open the course</a></p>`;
    if (meta) meta.dataset.state = 'done';
    if (row && (row.items || []).length) el.open = true;
    if (!row) {
      if (meta) meta.textContent = 'Not in this term';
      if (body) body.innerHTML = open + '<p class="muted">Late and missing work is read for this term.</p>';
      return;
    }
    if (row.unread) {
      if (meta) meta.textContent = 'Not read';
      if (body) body.innerHTML = open + '<p class="muted">Submissions for this class were not read.</p>';
      return;
    }
    if (row.error) {
      if (meta) { meta.textContent = 'Could not read'; meta.classList.add('bad'); }
      if (body) body.innerHTML = open + `<p class="err">${esc(row.error)}</p>`;
      return;
    }
    const items = row.items || [];
    const missing = items.filter(item => item.kind === 'missing').length;
    const late = items.filter(item => item.kind === 'late').length;
    if (meta) {
      meta.textContent = workCount(missing, late);
      meta.classList.toggle('bad', missing > 0);
      meta.classList.toggle('warn', !missing && late > 0);
    }
    if (!body) return;
    body.innerHTML = open + (items.length
      ? `<ul class="stWork">${items.map(item => workItemHtml(item, el.dataset.cid)).join('')}</ul>`
      : '<p class="muted">Nothing is late or missing.</p>');
  });
  const n = countLateRows(work);
  const line = n
    ? (n + (n === 1 ? ' item is' : ' items are') + ' late or missing for ' + term + '.')
    : ('Nothing is late or missing for ' + term + '.');
  const courseNote = document.getElementById('stCourseNote');
  if (courseNote) courseNote.textContent = line;
  const lateNote = document.getElementById('stLateNote');
  if (lateNote) lateNote.textContent = line;
  paintLateList(work);
}

function countLateRows(courses) {
  let n = 0;
  (courses || []).forEach(course => { n += (course.items || []).length; });
  return n;
}

function paintStudentRisk(userId) {
  const sec = $('#stRiskSec');
  const host = $('#stRiskBody');
  if (!sec || !host) return;
  const row = riskById[String(userId)];
  if (!row) {
    api('/students/risk').then(data => {
      applyRisk(data);
      const again = riskById[String(userId)];
      if (!again) return;
      paintStudentRisk(userId);
    }).catch(() => {});
    return;
  }
  sec.hidden = false;
  const trend = row.trend == null ? 'First check, so there is no trend yet.'
    : (row.trend > 0 ? `Up ${row.trend} since the last check.`
      : row.trend < 0 ? `Down ${Math.abs(row.trend)} since the last check.`
      : 'Same as the last check.');
  const attend = row.attend == null ? 0 : row.attend;
  const attendTrend = row.attend_trend == null ? ''
    : (row.attend_trend > 0 ? `Up ${row.attend_trend} since the last check.`
      : row.attend_trend < 0 ? `Down ${Math.abs(row.attend_trend)} since the last check.`
      : 'Same as the last check.');
  host.innerHTML = `<p><span class="stKey">Work</span>
      <span class="stRisk stRisk-${esc(row.band)}">${row.score}</span>
      <span class="stHint">grades and missing work. ${esc(trend)}</span></p>
    <p><span class="stKey">Attend</span>
      <span class="stRisk stRisk-${esc(row.attend_band || 'low')}">${attend}</span>
      <span class="stHint">absences; tardies count less. ${esc(attendTrend)}</span></p>
    <ul class="stCourses">${(row.courses || []).map(c => {
      const move = c.trend == null ? '' : (c.trend > 0 ? ` +${c.trend}` : ` ${c.trend}`);
      const attendMove = c.attend_trend == null ? ''
        : (c.attend_trend > 0 ? ` +${c.attend_trend}` : ` ${c.attend_trend}`);
      const attendN = c.attend == null ? 0 : c.attend;
      const why = (c.reasons || []).concat(c.attend_reasons || []);
      return `<li><b>${esc(c.name)}</b>
        <span class="stKey">work</span> <span class="stRisk stRisk-${esc(c.band)}">${c.score}${esc(move)}</span>
        <span class="stKey">attend</span> <span class="stRisk stRisk-${esc(c.attend_band || 'low')}">${attendN}${esc(attendMove)}</span>
        <div>${esc(why.join('; ') || 'Nothing standing out.')}</div></li>`;
    }).join('')}</ul>`;
}

/* The class list has its own read, so a slow quiz-extra pass cannot leave
   every dropdown on "Waiting" with no sign of life. studentWorkOwned is set
   once that read has started; the extras job must not paint over it. */
let studentWorkOwned = false;

async function watchJob(jobId, startedAt, onTick, still) {
  const deadline = startedAt + 10 * 60 * 1000;
  for (;;) {
    if (still ? !still() : !courseStillHere()) return null;
    if (Date.now() > deadline) {
      const err = new Error('Stopped waiting after 10 minutes. Reload this page to try again.');
      err.timedOut = true;
      throw err;
    }
    const info = await api('/jobs/' + jobId);
    if (still ? !still() : !courseStillHere()) return null;
    if (onTick) onTick(info);
    if (info.state === 'done' || info.state === 'error') return info;
    await new Promise(resolve => setTimeout(resolve, 400));
  }
}

async function paintStudentWork(userId, pageGen) {
  const still = () => pageGen === studentPageGen && studentPageOpen();
  studentWorkOwned = false;
  const startedAt = Date.now();
  setCourseBusy({ message: 'Starting the late and missing read' }, startedAt);
  let started;
  try {
    started = await api('/student/' + encodeURIComponent(userId) + '/work',
      { method: 'POST', body: {} });
  } catch (err) {
    if (!still()) return;
    if (err && err.status === 404) return;
    finishCourseRead(err.message || 'Could not start the read.', 'Could not read');
    return;
  }
  if (!still()) return;
  studentWorkOwned = true;
  try {
    const info = await watchJob(started.job, startedAt, step => setCourseBusy(step, startedAt), still);
    if (!info || !still()) return;
    if (info.state === 'error') {
      finishCourseRead(info.error || 'The read failed.', 'Could not read');
      return;
    }
    paintCourseWork(info.result || {});
  } catch (err) {
    if (courseStillHere()) finishCourseRead(err.message || 'The read failed.', 'Could not read');
  }
}

/* Stays on this page. The old job dialog walked every course ever taught
   and covered the record while it counted. */
async function paintStudentLive(userId, pageGen) {
  const box = $('#stLive');
  const extras = $('#stExtras');
  const startedAt = Date.now();
  const still = () => pageGen === studentPageGen && studentPageOpen();
  const paint = (live) => {
    if (!box || !extras || !live) return;
    const quizzes = live.quiz_extras || [];
    const overs = live.extensions || [];
    const where = live.term ? `this term (${live.term})` : 'this term';
    const n = live.courses_checked;
    box.textContent = (quizzes.length || overs.length)
      ? `${quizzes.length} quiz extra(s), ${overs.length} deadline override(s) · ${where}`
      : (n === 0 ? `No courses ${where}.` : `Nothing extra ${where}.`);
    const blocks = [];
    if (quizzes.length) {
      blocks.push('<h4>Quiz extras</h4><ul class="stCourses">' + quizzes.map(x =>
        `<li>${esc(x.course)} · ${esc(x.title)} · extra ${esc(x.extra_time || x.extra_minutes || 0)} min</li>`
      ).join('') + '</ul>');
    }
    if (overs.length) {
      blocks.push('<h4>Extensions</h4><ul class="stCourses">' + overs.map(x =>
        `<li>${esc(x.course)} · <a href="#/c/${esc(x.course_id)}/a/${esc(x.assignment_id)}">${
          esc(x.title)}</a> · due ${esc(x.due_at || '—')}</li>`
      ).join('') + '</ul>');
    }
    extras.innerHTML = blocks.join('')
      || '<p class="muted">No quiz extras or deadline overrides in this term.</p>';
  };
  try {
    const started = await api('/student/' + encodeURIComponent(userId) + '/live',
      { method: 'POST', body: {} });
    const info = await watchJob(started.job, startedAt, step => {
      if (box && step.message) box.textContent = step.message;
      if (!studentWorkOwned) setCourseBusy(step, startedAt);
    }, still);
    if (!info || !still()) return;
    if (info.state === 'error') {
      if (box) box.textContent = info.error || 'Could not read extras. The rest of the page is still good.';
      if (!studentWorkOwned) finishCourseRead(info.error || 'Could not read this class.', 'Could not read');
      return;
    }
    paint(info.result || {});
    if (!studentWorkOwned) paintCourseWork(info.result || {});
  } catch (err) {
    if (box) box.textContent = 'Could not read extras. The rest of the page is still good.';
    if (!studentWorkOwned && courseStillHere()) {
      finishCourseRead(err.message || 'Could not read this class.', 'Could not read');
    }
  }
}

let latePanelGen = 0;

async function loadStudentLate(userId) {
  const gen = ++latePanelGen;
  const body = document.getElementById('stuLateBody');
  const note = document.getElementById('stuLateNote');
  if (!body) return;
  const still = () => gen === latePanelGen && document.getElementById('stuLateBody') === body;
  const startedAt = Date.now();
  if (note) note.textContent = 'Reading each class…';
  let started;
  try {
    started = await api('/student/' + encodeURIComponent(userId) + '/work',
      { method: 'POST', body: {} });
  } catch (err) {
    if (!still()) return;
    const message = err && err.status === 404
      ? 'Close CourseForge Studio and open it again, then reload this page.'
      : ((err && err.message) || 'Could not read late and missing work.');
    if (note) note.textContent = err && err.status === 404 ? 'Needs a restart' : 'Could not read';
    body.innerHTML = `<p class="muted">${esc(message)}</p>`;
    return;
  }
  if (!still()) return;
  try {
    const info = await watchJob(started.job, startedAt, step => {
      if (still()) setCourseBusy(step, startedAt);
    }, still);
    if (!info || !still()) return;
    if (info.state === 'error') {
      if (note) note.textContent = 'Could not read';
      body.innerHTML = `<p class="err">${esc(info.error || 'The read failed.')}</p>`;
      return;
    }
    const result = info.result || {};
    const work = result.work || [];
    const term = result.term || 'this term';
    const n = countLateRows(work);
    if (note) note.textContent = n
      ? (n + (n === 1 ? ' item is' : ' items are') + ' late or missing for ' + term + '.')
      : ('Nothing is late or missing for ' + term + '.');
    body.innerHTML = lateListHtml(result.work || []);
  } catch (err) {
    if (!still()) return;
    if (note) note.textContent = 'Could not read';
    body.innerHTML = `<p class="err">${esc(err.message || 'The read failed.')}</p>`;
  }
}

window.openStudent = openStudent;
window.openStudents = openStudents;
window.loadStudentLate = loadStudentLate;
