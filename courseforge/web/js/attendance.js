/* Attendance: a month calendar of who was here, tardy, absent, or excused.

   Marks stay on this computer and copy to the instructor's Canvas files.
   Nothing is written to the gradebook. Names on screen use the nickname
   spelling; the saved book is user ids only. */
(function () {
  'use strict';

  const AUTOSAVE_MS = 4000;
  let saveTimer = null;
  let leaveHooked = false;

  const DAYS = [
    ['mon', 'Mon'], ['tue', 'Tue'], ['wed', 'Wed'], ['thu', 'Thu'],
    ['fri', 'Fri'], ['sat', 'Sat'], ['sun', 'Sun'],
  ];
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  let token = 0;
  // Status clicks paint before the request returns. One request is in flight;
  // the rest wait in markQueue so an older reply cannot land last and win.
  let markQueue = [];
  let markFlight = null;
  let markGen = 0;

  function mem() {
    S.attendance = S.attendance || { filter: 'trouble' };
    return S.attendance;
  }

  function pad(n) { return String(n).padStart(2, '0'); }
  function isoToday() {
    const d = new Date();
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }
  function monthName(ym) {
    const [y, m] = ym.split('-').map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString([], { month: 'long', year: 'numeric' });
  }
  function longDay(iso) {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString([], {
      weekday: 'long', month: 'long', day: 'numeric',
    });
  }
  function shiftMonth(ym, delta) {
    const [y, m] = ym.split('-').map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1);
  }
  function inSpan(data, ym) {
    const range = (data && data.range) || {};
    if (!range.start || !range.end) return true;
    return ym >= range.start.slice(0, 7) && ym <= range.end.slice(0, 7);
  }
  function person(data, uid) {
    return ((data && data.roster) || []).find(p => String(p.user_id) === String(uid)) || null;
  }
  function labelOf(data, uid) {
    const p = person(data, uid);
    if (!p) return 'Not on this class list';
    return (typeof studentLabel === 'function' ? studentLabel(p) : p.name) || p.name || 'Student';
  }
  function dayCounts(data, iso) {
    const marks = ((data && data.marks) || {})[iso] || {};
    const counts = { present: 0, tardy: 0, absent: 0, excused: 0 };
    Object.keys(marks).forEach(uid => {
      const status = marks[uid] && marks[uid].status;
      if (counts[status] != null) counts[status] += 1;
    });
    return counts;
  }

  async function openAttendance(courseId, rest) {
    const state = mem();
    const same = String(state.courseId) === String(courseId);
    if (!same && state.courseId) flush(true);
    state.courseId = courseId;
    if (!same) {
      state.data = null;
      state.selected = '';
      state.month = '';
    }
    if (rest && /^\d{4}-\d{2}$/.test(rest[0] || '')) state.month = rest[0];
    showView('area');
    if (typeof ensureCourse === 'function') await ensureCourse(courseId);
    if (String(mem().courseId) !== String(courseId)) return;
    crumbs([
      { label: 'Courses', href: '#/' },
      { label: courseTitle(S.course), href: '#/c/' + courseId },
      { label: 'Attendance' },
    ]);
    areaHead('Attendance',
      'Who was here, tardy, or absent. Nothing is written to the gradebook.');
    if (typeof areaTabs === 'function') areaTabs([]);
    $('#headerActions').innerHTML = '<button class="btn" type="button" id="atRoster">Reload class list</button>';
    $('#atRoster').onclick = () => send('/roster', {});
    hookLeave();
    // Names can arrive after the calendar. Waiting on them, or on Canvas,
    // is what made opening a course sit on "Loading the calendar…".
    if (typeof loadNicks === 'function') {
      loadNicks().then(() => {
        if (String(mem().courseId) === String(courseId) && mem().data) paint();
      }).catch(() => {});
    }
    if (String(mem().courseId) !== String(courseId)) return;
    await reload();
  }

  async function reload() {
    const mine = ++token;
    const cid = mem().courseId;
    const body = $('#areaBody');
    if (body && !mem().data) body.innerHTML = '<p class="hint">Loading the calendar…</p>';
    try {
      const data = await api('/attendance/' + encodeURIComponent(cid));
      if (mine !== token || String(mem().courseId) !== String(cid)) return;
      mem().data = data;
      mem().needsPush = false;
      paint();
      // The first open already waited for Canvas. Later opens paint the
      // copy on this computer, then pick up anything newer.
      if (!data.pulled) quietSync(cid, mine, data);
    } catch (err) {
      if (mine !== token) return;
      if (body) {
        body.innerHTML = '';
        body.appendChild(emptyState('Could not open attendance: ' + firstLine(err.message)));
      }
    }
  }

  function snap(data) {
    data = data || {};
    return JSON.stringify([data.updated || '', data.pattern || {}, data.meetings || [], data.marks || {}]);
  }

  async function quietSync(cid, mine, shown) {
    if (patternDirty() || mem().needsPush || marksBusy()) return;
    try {
      const data = await api('/attendance/' + encodeURIComponent(cid) + '/sync', { body: {} });
      if (mine !== token || String(mem().courseId) !== String(cid)) return;
      if (patternDirty() || marksBusy()) return;
      if (snap(data) === snap(shown)) return;
      mem().data = data;
      paint();
    } catch (_) { /* the calendar from this computer is already on screen */ }
  }

  function readPattern() {
    const form = $('#atPattern');
    if (!form) return null;
    return {
      weekdays: [...form.querySelectorAll('input[name="wd"]:checked')].map(el => el.value),
      start: ($('#atStart') || {}).value || '',
      end: ($('#atEnd') || {}).value || '',
      skip_breaks: !!($('#atSkip') && $('#atSkip').checked),
    };
  }

  function patternDirty() {
    const now = readPattern();
    if (!now) return false;
    const saved = (mem().data && mem().data.pattern) || {};
    const days = list => (list || []).slice().sort().join(',');
    return days(now.weekdays) !== days(saved.weekdays)
      || now.start !== (saved.start || '')
      || now.end !== (saved.end || '')
      || now.skip_breaks !== (saved.skip_breaks !== false);
  }

  function pendingPattern() {
    const now = readPattern();
    if (!now || !patternDirty()) return null;
    if (!now.weekdays.length || !now.start || !now.end || now.end < now.start) return null;
    return now;
  }

  function pendingMinutes() {
    const input = document.querySelector('#areaBody [data-minutes]');
    if (!input) return null;
    const uid = input.getAttribute('data-minutes');
    const day = mem().selected;
    const mark = (((mem().data || {}).marks || {})[day] || {})[uid] || {};
    if (String(mark.minutes || 0) === String(input.value || 0)) return null;
    return {
      date: day,
      marks: [{ user_id: uid, status: 'tardy', minutes: input.value }],
    };
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => flush(false), AUTOSAVE_MS);
  }

  function hookLeave() {
    if (typeof onLeave === 'function') onLeave(() => flush(true));
    if (leaveHooked) return;
    leaveHooked = true;
    window.addEventListener('pagehide', () => flush(true));
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') flush(true);
    });
  }

  function keepalivePost(cid, path, payload) {
    const headers = { 'Content-Type': 'application/json' };
    if (typeof studioKey === 'function' && studioKey()) headers['X-Studio-Key'] = studioKey();
    try {
      fetch('/api/attendance/' + encodeURIComponent(cid) + path, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload || {}),
        keepalive: true,
      });
    } catch (_) { /* the file on this computer is already written when a click saved */ }
  }

  function pendingBody() {
    const payload = {};
    const minutes = pendingMinutes();
    const pattern = pendingPattern();
    if (minutes) Object.assign(payload, minutes);
    if (pattern) Object.assign(payload, pattern);
    if (!Object.keys(payload).length) return null;
    return payload;
  }

  function flush(leaving) {
    clearTimeout(saveTimer);
    saveTimer = null;
    const cid = mem().courseId;
    if (!cid || (S.view && S.view !== 'area' && !leaving)) return;
    const payload = pendingBody();
    const retry = mem().retry;
    const queued = unackedGroups();
    if (!payload && !retry && !mem().needsPush && !queued.length) return;
    if (leaving) {
      // The click is already on disk once its request landed. This sends
      // anything still queued, including a newer status than the request
      // already in flight. The server keeps the later timestamp.
      if (queued.length) queued.forEach(body => keepalivePost(cid, '/marks', body));
      else if (retry) keepalivePost(cid, retry.path, retry.body);
      if (payload) keepalivePost(cid, '/flush', payload);
      else if (!retry && !queued.length && mem().needsPush) keepalivePost(cid, '/flush', {});
      mem().needsPush = false;
      mem().retry = null;
      mem().markDirty = false;
      markQueue = [];
      markFlight = null;
      markGen++;
      return;
    }
    if (queued.length || marksBusy()) {
      if (payload) scheduleSave();
      return;
    }
    if (retry) send(retry.path, retry.body);
    else if (payload) send('/flush', payload);
    else send('/flush', {});
  }

  function nowIso() {
    const d = new Date();
    const p = (n, w) => String(n).padStart(w || 2, '0');
    return p(d.getUTCFullYear(), 4) + '-' + p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate())
      + 'T' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds())
      + '.' + p(d.getUTCMilliseconds(), 3) + '+00:00';
  }

  function marksBusy() {
    return markFlight !== null || markQueue.length > 0 || !!mem().markDirty;
  }

  function coalesceMarks(ops) {
    const list = (ops || []).filter(op => op && op.date);
    if (!list.length) return null;
    const date = String(list[0].date);
    if (list.some(op => String(op.date) !== date)) return null;
    const marks = new Map();
    let fill = '';
    list.forEach(op => {
      if (op.fill === 'present') fill = 'present';
      (op.marks || []).forEach(m => {
        if (m && m.user_id != null) marks.set(String(m.user_id), m);
      });
    });
    const body = { date: date, marks: [...marks.values()] };
    if (fill) body.fill = fill;
    return body;
  }

  function unackedGroups() {
    const ops = [];
    if (markFlight) ops.push(markFlight);
    markQueue.forEach(op => ops.push(op));
    const order = [];
    const byDate = new Map();
    ops.forEach(op => {
      const date = String(op.date || '');
      if (!byDate.has(date)) {
        byDate.set(date, []);
        order.push(date);
      }
      byDate.get(date).push(op);
    });
    return order.map(date => coalesceMarks(byDate.get(date))).filter(Boolean);
  }

  function takeBatch() {
    if (!markQueue.length) return null;
    const date = String(markQueue[0].date);
    const group = [];
    while (markQueue.length && String(markQueue[0].date) === date) group.push(markQueue.shift());
    return coalesceMarks(group);
  }

  function localTotals(data) {
    const meetings = data.meetings || [];
    const marks = data.marks || {};
    const ids = [];
    const seen = new Set();
    (data.roster || []).forEach(p => {
      const uid = String(p.user_id);
      if (uid && !seen.has(uid)) { seen.add(uid); ids.push(uid); }
    });
    Object.keys(marks).forEach(day => {
      Object.keys(marks[day] || {}).forEach(uid => {
        if (uid && !seen.has(uid)) { seen.add(uid); ids.push(uid); }
      });
    });
    return ids.map(uid => {
      const count = { user_id: uid, present: 0, tardy: 0, absent: 0, excused: 0, minutes: 0 };
      meetings.forEach(day => {
        const mark = (marks[day] || {})[uid] || {};
        if (Object.prototype.hasOwnProperty.call(count, mark.status)) count[mark.status] += 1;
        if (mark.status === 'tardy') count.minutes += Number(mark.minutes) || 0;
      });
      return count;
    });
  }

  function visibleSnap(data) {
    data = data || {};
    const marks = {};
    Object.keys(data.marks || {}).sort().forEach(day => {
      const cell = {};
      Object.keys(data.marks[day] || {}).sort().forEach(uid => {
        const m = data.marks[day][uid] || {};
        cell[uid] = [m.status || '', Number(m.minutes) || 0, m.note || ''];
      });
      marks[day] = cell;
    });
    const totals = (data.totals || []).map(r => [
      String(r.user_id), r.present || 0, r.tardy || 0, r.absent || 0,
      r.excused || 0, Number(r.minutes) || 0,
    ]).sort();
    const range = data.range || {};
    return JSON.stringify({
      meetings: (data.meetings || []).slice().sort(),
      marks: marks,
      totals: totals,
      range: [range.start || '', range.end || ''],
    });
  }

  function applyLocal(body) {
    const data = mem().data;
    if (!data || !body || !body.date) return false;
    const date = String(body.date);
    if (!data.marks) data.marks = {};
    if (!data.marks[date]) data.marks[date] = {};
    const cell = data.marks[date];
    if (body.fill === 'present') {
      (data.roster || []).forEach(p => {
        const uid = String(p.user_id);
        const current = cell[uid];
        if (current && current.status) return;
        cell[uid] = { status: 'present', minutes: 0, note: '', updated: nowIso() };
      });
    }
    (body.marks || []).forEach(raw => {
      const uid = String(raw.user_id || '');
      if (!uid) return;
      const prev = cell[uid] || {};
      let status = String(raw.status || '').toLowerCase();
      if (status === 'clear' || status === 'unmarked') status = '';
      const stamp = raw.updated || nowIso();
      if (stamp && prev.updated && stamp < prev.updated) return;
      if (!status) {
        delete cell[uid];
        return;
      }
      cell[uid] = {
        status: status,
        minutes: status === 'tardy'
          ? (Number(raw.minutes != null ? raw.minutes : (prev.minutes || 0)) || 0) : 0,
        note: raw.note != null ? String(raw.note) : (prev.note || ''),
        updated: stamp,
      };
    });
    if (!Object.keys(cell).length) delete data.marks[date];
    const any = Object.keys(data.marks[date] || {}).some(uid => data.marks[date][uid] && data.marks[date][uid].status);
    if (any) {
      data.meetings = data.meetings || [];
      if (data.meetings.indexOf(date) < 0) data.meetings.push(date);
      data.meetings.sort();
    }
    data.totals = localTotals(data);
    return true;
  }

  function queueMarks(body) {
    body = Object.assign({}, body || {});
    body.marks = (body.marks || []).map(raw => Object.assign({}, raw, {
      updated: raw.updated || nowIso(),
    }));
    const wasMeeting = !!(mem().data && (mem().data.meetings || []).indexOf(String(body.date)) >= 0);
    if (!applyLocal(body)) return;
    const one = !body.fill && body.marks.length === 1;
    if (one && wasMeeting) paintTouched(String(body.marks[0].user_id), String(body.date));
    else paintStill();
    markQueue.push(body);
    mem().markDirty = true;
    pumpMarks();
  }

  function takeBatchAndSend() {
    return takeBatch();
  }

  async function pumpMarks() {
    if (markFlight || !markQueue.length) return;
    const cid = mem().courseId;
    const gen = markGen;
    const body = takeBatchAndSend();
    if (!body) return;
    markFlight = body;
    try {
      const data = await api('/attendance/' + encodeURIComponent(cid) + '/marks', { body: body });
      if (String(mem().courseId) !== String(cid) || markGen !== gen) {
        markFlight = null;
        return;
      }
      markFlight = null;
      if (markQueue.length) {
        pumpMarks();
        return;
      }
      mem().markDirty = false;
      mem().needsPush = data.sync === 'error';
      const same = visibleSnap(mem().data) === visibleSnap(data);
      mem().data = data;
      if (!same) paintStill();
      if (mem().needsPush) {
        scheduleSave();
        setStatus('saved here; the Canvas copy will be sent again', 'err');
      } else {
        setStatus('saved', 'ok');
      }
    } catch (err) {
      const failed = markFlight;
      markFlight = null;
      if (String(mem().courseId) !== String(cid) || markGen !== gen) return;
      const rejected = err && err.status === 400;
      if (rejected) {
        markQueue = [];
        mem().markDirty = false;
        setStatus(firstLine(err.message), 'err');
        reload();
        return;
      }
      if (failed) markQueue.unshift(failed);
      mem().needsPush = true;
      setStatus(firstLine(err.message), 'err');
      setTimeout(() => { if (!markFlight && markQueue.length) pumpMarks(); }, 800);
    }
  }

  async function send(path, body) {
    if (path === '/marks') {
      queueMarks(body || {});
      return;
    }
    const mine = ++token;
    const cid = mem().courseId;
    const keep = document.activeElement && document.activeElement.getAttribute
      ? document.activeElement.getAttribute('data-keep') : '';
    setStatus('saving…');
    mem().retry = { path: path, body: body || {} };
    try {
      const data = await api('/attendance/' + encodeURIComponent(cid) + path, { body: body || {} });
      if (mine !== token || String(mem().courseId) !== String(cid)) return;
      if ((marksBusy() || mem().markDirty) && mem().data) {
        data.marks = mem().data.marks;
        data.totals = mem().data.totals;
        data.meetings = mem().data.meetings;
      }
      mem().data = data;
      mem().retry = null;
      mem().needsPush = data.sync === 'error';
      if (mem().needsPush || patternDirty()) {
        scheduleSave();
        if (patternDirty()) {
          setStatus(data.sync === 'error'
            ? 'saved here; the Canvas copy will be sent again' : 'saved',
            data.sync === 'error' ? 'err' : 'ok');
          return;
        }
      }
      paint(keep);
      setStatus(data.sync === 'error'
        ? 'saved here; the Canvas copy will be sent again' : 'saved',
        data.sync === 'error' ? 'err' : 'ok');
    } catch (err) {
      if (mine !== token) return;
      mem().needsPush = true;
      scheduleSave();
      setStatus(firstLine(err.message), 'err');
    }
  }

  function paint(keep) {
    const data = mem().data;
    const host = $('#areaBody');
    if (!host || !data) return;
    const month = chooseMonth(data);
    mem().month = month;
    if (!mem().selected || mem().selected.slice(0, 7) !== month) {
      mem().selected = defaultDay(data, month);
    }
    host.innerHTML = patternHtml(data) + calendarHtml(data, month)
      + dayHtml(data, mem().selected) + totalsHtml(data);
    wire(host);
    if (keep) {
      const back = host.querySelector('[data-keep="' + CSS.escape(keep) + '"]');
      if (back) back.focus({ preventScroll: true });
    }
  }

  function paintStill(keep) {
    const scroller = $('#viewArea');
    const top = scroller ? scroller.scrollTop : 0;
    const left = scroller ? scroller.scrollLeft : 0;
    if (!keep && document.activeElement && document.activeElement.getAttribute) {
      keep = document.activeElement.getAttribute('data-keep') || '';
    }
    paint(keep);
    if (scroller) {
      scroller.scrollTop = top;
      scroller.scrollLeft = left;
    }
  }

  /* One row, the day cell, and the totals. Rebuilding the whole calendar on
     every click threw away the scroll position and waited on the network. */
  function paintTouched(uid, iso) {
    const data = mem().data;
    const host = $('#areaBody');
    if (!host || !data || !iso || iso !== mem().selected) return paintStill();
    const scroller = $('#viewArea');
    const top = scroller ? scroller.scrollTop : 0;
    const left = scroller ? scroller.scrollLeft : 0;
    const keep = document.activeElement && document.activeElement.getAttribute
      ? document.activeElement.getAttribute('data-keep') : '';
    const probe = host.querySelector('[data-uid="' + CSS.escape(String(uid)) + '"]');
    const person = probe && probe.closest('.atPerson');
    if (!person) return paintStill(keep);
    const mark = ((data.marks || {})[iso] || {})[String(uid)] || {};
    const wrap = document.createElement('div');
    wrap.innerHTML = personRow(data, String(uid), mark);
    const next = wrap.firstElementChild;
    if (!next) return paintStill(keep);
    person.replaceWith(next);
    wirePerson(next);
    const oldDay = host.querySelector('[data-date="' + CSS.escape(iso) + '"]');
    if (oldDay) {
      const dayWrap = document.createElement('div');
      dayWrap.innerHTML = dayCell(data, iso);
      const fresh = dayWrap.firstElementChild;
      if (fresh) {
        oldDay.replaceWith(fresh);
        wireDay(fresh);
      }
    }
    const tot = host.querySelector('#atTotH');
    const section = tot && tot.closest('section');
    if (section) {
      const totWrap = document.createElement('div');
      totWrap.innerHTML = totalsHtml(data);
      if (totWrap.firstElementChild) section.replaceWith(totWrap.firstElementChild);
    }
    if (keep) {
      const back = host.querySelector('[data-keep="' + CSS.escape(keep) + '"]');
      if (back) back.focus({ preventScroll: true });
    }
    if (scroller) {
      scroller.scrollTop = top;
      scroller.scrollLeft = left;
    }
  }

  function chooseMonth(data) {
    const want = mem().month;
    if (want && inSpan(data, want)) return want;
    const today = isoToday().slice(0, 7);
    if (inSpan(data, today)) return today;
    return ((data.range || {}).start || isoToday()).slice(0, 7);
  }

  function defaultDay(data, month) {
    const today = isoToday();
    if (today.slice(0, 7) === month) return today;
    const meet = (data.meetings || []).find(d => d.slice(0, 7) === month);
    return meet || (month + '-01');
  }

  function patternHtml(data) {
    const p = data.pattern || {};
    const suggest = data.suggest || {};
    const chosen = new Set(p.weekdays || []);
    const start = p.start || suggest.start || '';
    const end = p.end || suggest.end || '';
    const skip = p.skip_breaks !== false;
    return `<form class="atPattern" id="atPattern">
      <div class="atDays">${DAYS.map(([id, label]) =>
        `<label><input type="checkbox" name="wd" value="${id}"${chosen.has(id) ? ' checked' : ''}> ${label}</label>`
      ).join('')}</div>
      <label>First day <input type="date" id="atStart" value="${esc(start)}" required></label>
      <label>Last day <input type="date" id="atEnd" value="${esc(end)}" required></label>
      <label class="atSkip"><input type="checkbox" id="atSkip"${skip ? ' checked' : ''}>
        Skip college breaks</label>
      <button class="btn primary" type="submit">Save class days</button>
    </form>
    <p class="hint">Edits save on their own after a few seconds, and again when you leave this page. The copy in your Canvas files is updated with them.</p>
    ${p.weekdays && p.weekdays.length ? '' : `<p class="callout atNote">Pick the weekdays this class meets.
      The calendar can show a single added or cancelled day after that.
      Nothing is sent to the gradebook.</p>`}`;
  }

  function dayCell(data, iso) {
    const meetings = new Set(data.meetings || []);
    const breaks = new Set(data.breaks || []);
    const today = isoToday();
    const filter = mem().filter || 'trouble';
    const d = Number(iso.slice(8));
    const meet = meetings.has(iso);
    const brk = breaks.has(iso);
    const counts = dayCounts(data, iso);
    const cls = ['atDay'];
    if (iso === today) cls.push('today');
    if (!meet) cls.push('off');
    if (brk && !meet) cls.push('break');
    if (meet && counts.absent) cls.push('absent');
    else if (meet && counts.tardy) cls.push('tardy');
    else if (meet && allHere(data, counts)) cls.push('here');
    const dim = (filter === 'absent' && meet && !counts.absent)
      || (filter === 'tardy' && meet && !counts.tardy);
    if (dim) cls.push('dim');
    const people = meet ? glancePeople(data, iso) : [];
    const line = people.length
      ? people.map(p => p.short + (p.word ? ', ' + p.word : '')).join('; ')
      : cellLine(data, meet, brk, counts);
    const pressed = iso === mem().selected;
    const glance = people.map(p =>
      `<span class="atWhoLine ${p.status}" title="${esc(p.full + (p.word ? ', ' + p.word : ''))}">${
        esc(p.short)}${p.word ? `<i>${esc(p.word)}</i>` : ''}</span>`).join('');
    return `<button type="button" class="${cls.join(' ')}" data-date="${iso}"
      data-keep="day-${iso}" aria-pressed="${pressed ? 'true' : 'false'}"
      aria-label="${esc(longDay(iso) + (line ? ', ' + line : ''))}">
      <span class="atNum">${d}</span>
      ${glance ? `<span class="atGlance">${glance}</span>` : (line ? `<span class="atCount">${esc(line)}</span>` : '')}
    </button>`;
  }

  function calendarHtml(data, month) {
    const [y, m] = month.split('-').map(Number);
    const firstDow = new Date(y, m - 1, 1).getDay();
    const count = new Date(y, m, 0).getDate();
    let cells = '';
    for (let i = 0; i < firstDow; i++) cells += '<div></div>';
    for (let d = 1; d <= count; d++) cells += dayCell(data, month + '-' + pad(d));
    const prev = shiftMonth(month, -1);
    const next = shiftMonth(month, 1);
    return `<div class="atCalHead">
        <button class="btn sm" type="button" id="atPrev" data-month="${prev}"
          ${inSpan(data, prev) ? '' : 'disabled'}>Previous</button>
        <h2 id="atMonth">${esc(monthName(month))}</h2>
        <span class="spacer"></span>
        <button class="btn sm" type="button" id="atNext" data-month="${next}"
          ${inSpan(data, next) ? '' : 'disabled'}>Next</button>
      </div>
      <div class="atDow">${DOW.map(n => `<span>${n}</span>`).join('')}</div>
      <div class="atCal" role="group" aria-label="${esc(monthName(month))}">${cells}</div>
      <div class="atLegend" role="group" aria-label="What to highlight">
        ${chip('trouble', 'Absences and tardies')}
        ${chip('absent', 'Absences')}
        ${chip('tardy', 'Tardies')}
        ${chip('all', 'Everyone')}
      </div>
      <p class="hint">${esc(data.note || '')}</p>`;
  }

  function chip(id, label) {
    const on = (mem().filter || 'trouble') === id;
    return `<button class="chip" type="button" data-filter="${id}" aria-pressed="${on ? 'true' : 'false'}">${esc(label)}</button>`;
  }

  function allHere(data, counts) {
    const rosterN = (data.roster || []).length;
    const marked = counts.present + counts.tardy + counts.absent + counts.excused;
    return rosterN > 0 && marked >= rosterN && !counts.absent && !counts.tardy;
  }

  /* Jane. D from the name the instructor sees. A quoted nickname is the
     first name they go by; the letter is the last name. */
  function glanceName(data, uid) {
    const full = String(labelOf(data, uid) || '').replace(/\s+/g, ' ').trim();
    const quoted = full.match(/^(.*?)\s+"([^"]+)"\s+(.+)$/);
    let first = '';
    let last = '';
    if (quoted && quoted[2]) {
      first = quoted[2];
      last = quoted[3].trim().split(' ').filter(Boolean).pop() || '';
    } else if (full.indexOf(',') !== -1) {
      const bits = full.split(',');
      last = (bits[0] || '').trim().split(' ').filter(Boolean).pop() || '';
      first = (bits.slice(1).join(' ').trim().split(' ').filter(Boolean)[0]) || '';
    } else {
      const parts = full.split(' ').filter(Boolean);
      first = parts[0] || '';
      last = parts.length > 1 ? parts[parts.length - 1] : '';
    }
    const initial = last ? last.charAt(0).toUpperCase() : '';
    if (!first) return initial || 'Student';
    return initial ? first + '. ' + initial : first;
  }

  function glancePeople(data, iso) {
    const marks = (data.marks || {})[iso] || {};
    const rank = { absent: 0, tardy: 1, excused: 2 };
    const word = { tardy: 'Tardy', excused: 'Excused' };
    return Object.keys(marks).filter(uid => {
      const status = marks[uid] && marks[uid].status;
      return status && status !== 'present';
    }).sort((a, b) => {
      const diff = (rank[marks[a].status] || 9) - (rank[marks[b].status] || 9);
      if (diff) return diff;
      return glanceName(data, a).localeCompare(glanceName(data, b));
    }).map(uid => ({
      status: marks[uid].status,
      short: glanceName(data, uid),
      full: labelOf(data, uid),
      word: word[marks[uid].status] || '',
    }));
  }

  function cellLine(data, meet, brk, counts) {
    if (!meet) return brk ? 'Break' : '';
    const bits = [];
    if (counts.absent) bits.push(counts.absent + ' absent');
    if (counts.tardy) bits.push(counts.tardy + ' tardy');
    if (bits.length) return bits.join(', ');
    if (allHere(data, counts)) return 'All here';
    return brk ? 'Break' : 'Not taken';
  }

  function dayHtml(data, iso) {
    if (!iso) return '';
    const meetings = new Set(data.meetings || []);
    const meet = meetings.has(iso);
    const marks = (data.marks || {})[iso] || {};
    const ids = (data.roster || []).map(p => String(p.user_id));
    Object.keys(marks).forEach(uid => { if (!ids.includes(String(uid))) ids.push(String(uid)); });
    const rows = ids.map(uid => personRow(data, uid, marks[uid] || {})).join('');
    const toggle = meet
      ? '<button class="btn sm" type="button" id="atNoClass">No class this day</button>'
      : '<button class="btn sm" type="button" id="atHold">Hold class this day</button>';
    const rest = (data.roster || []).length
      ? '<button class="btn sm" type="button" id="atRest">Mark the rest present</button>' : '';
    const empty = ids.length ? '' : `<p class="atEmpty">The class list is not on this computer yet.
      Reload the class list. Nothing is written to the gradebook.</p>`;
    return `<section class="atDayPanel" aria-labelledby="atDayH">
      <div class="atCalHead">
        <h3 id="atDayH">${esc(longDay(iso))}</h3>
        <span class="spacer"></span>
        ${toggle}
        ${rest}
      </div>
      ${empty}
      ${rows}
    </section>`;
  }

  const MARK_LABEL = { present: 'Present', tardy: 'Tardy', absent: 'Absent', excused: 'Excused' };

  function personRow(data, uid, mark) {
    const status = mark.status || '';
    const buttons = ['present', 'tardy', 'absent', 'excused'].map(name => {
      const on = status === name;
      return `<button class="btn sm atMark ${name}" type="button" data-mark="${name}" data-uid="${esc(uid)}"
        data-keep="mark-${esc(uid)}-${name}" aria-pressed="${on ? 'true' : 'false'}">${
        MARK_LABEL[name]}</button>`;
    }).join('');
    const chip = status
      ? `<span class="atStat ${status}">${MARK_LABEL[status]}</span>` : '';
    const clear = status
      ? `<button class="btn sm" type="button" data-mark="clear" data-uid="${esc(uid)}"
          data-keep="mark-${esc(uid)}-clear">Clear</button>` : '';
    const minutes = status === 'tardy'
      ? `<label class="atMin">Minutes late
          <input type="number" min="0" max="180" data-minutes="${esc(uid)}"
            data-keep="min-${esc(uid)}" value="${esc(mark.minutes || 0)}"></label>` : '';
    return `<div class="atPerson${status ? ' ' + status : ''}">
      <div class="atWho">${esc(labelOf(data, uid))}${chip}</div>
      <div class="atBtns">${buttons}${clear}${minutes}</div>
    </div>`;
  }

  function totalsHtml(data) {
    const filter = mem().filter || 'trouble';
    const byId = {};
    (data.roster || []).forEach(p => { byId[String(p.user_id)] = p; });
    let rows = (data.totals || []).filter(r => {
      if (filter === 'absent') return r.absent > 0;
      if (filter === 'tardy') return r.tardy > 0;
      if (filter === 'all') return true;
      return r.absent > 0 || r.tardy > 0;
    });
    rows.sort((a, b) => {
      if (filter === 'tardy' && b.tardy !== a.tardy) return b.tardy - a.tardy;
      if (b.absent !== a.absent) return b.absent - a.absent;
      if (b.tardy !== a.tardy) return b.tardy - a.tardy;
      const as = (byId[String(a.user_id)] || {}).sortable_name || '';
      const bs = (byId[String(b.user_id)] || {}).sortable_name || '';
      return as.localeCompare(bs);
    });
    const emptyText = (data.roster || []).length && filter !== 'all'
      ? 'No absences or tardies on the class days yet.'
      : 'No one is in this list yet.';
    const body = rows.length ? rows.map(r => `<tr>
      <td>${esc(labelOf(data, r.user_id))}</td>
      <td class="n">${r.absent}</td>
      <td class="n">${r.tardy}</td>
      <td class="n">${r.minutes || 0}</td>
      <td class="n">${r.excused}</td>
      <td class="n">${r.present}</td>
    </tr>`).join('') : `<tr><td colspan="6" class="atEmpty">${esc(emptyText)}</td></tr>`;
    return `<section aria-labelledby="atTotH">
      <h3 id="atTotH">Across these class days</h3>
      <table class="atTable">
        <thead><tr>
          <th scope="col">Student</th><th scope="col">Absent</th><th scope="col">Tardy</th>
          <th scope="col">Minutes late</th><th scope="col">Excused</th><th scope="col">Present</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </section>`;
  }

  function wire(host) {
    const form = host.querySelector('#atPattern');
    if (form) {
      form.onsubmit = ev => {
        ev.preventDefault();
        const now = readPattern();
        if (now) send('/pattern', now);
      };
      form.querySelectorAll('input').forEach(el => {
        el.addEventListener('change', scheduleSave);
        if (el.type !== 'checkbox') el.addEventListener('input', scheduleSave);
      });
    }
    host.querySelectorAll('[data-month]').forEach(btn => {
      btn.onclick = () => {
        if (btn.disabled) return;
        mem().month = btn.getAttribute('data-month');
        mem().selected = '';
        paint();
      };
    });
    host.querySelectorAll('[data-date]').forEach(wireDay);
    host.querySelectorAll('[data-filter]').forEach(btn => {
      btn.onclick = () => {
        mem().filter = btn.getAttribute('data-filter');
        paint();
      };
    });
    const no = host.querySelector('#atNoClass');
    if (no) no.onclick = () => send('/meet', { date: mem().selected, on: false });
    const hold = host.querySelector('#atHold');
    if (hold) hold.onclick = () => send('/meet', { date: mem().selected, on: true });
    const rest = host.querySelector('#atRest');
    if (rest) rest.onclick = () => send('/marks', { date: mem().selected, fill: 'present', marks: [] });
    host.querySelectorAll('.atPerson').forEach(wirePerson);
  }

  function wireDay(btn) {
    btn.onclick = () => {
      mem().selected = btn.getAttribute('data-date');
      paint('day-' + mem().selected);
    };
  }

  function wirePerson(row) {
    if (!row) return;
    row.querySelectorAll('[data-mark]').forEach(btn => {
      btn.onclick = () => send('/marks', {
        date: mem().selected,
        marks: [{ user_id: btn.getAttribute('data-uid'), status: btn.getAttribute('data-mark') }],
      });
    });
    row.querySelectorAll('[data-minutes]').forEach(input => {
      input.onchange = () => send('/marks', {
        date: mem().selected,
        marks: [{
          user_id: input.getAttribute('data-minutes'),
          status: 'tardy',
          minutes: input.value,
        }],
      });
    });
  }

  registerArea({
    id: 'attendance',
    label: 'Attendance',
    zone: 'record',
    open: openAttendance,
  });
})();
