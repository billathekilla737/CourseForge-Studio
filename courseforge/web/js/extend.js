/* Deadline extensions: a student was ill, so their dates move and nobody else's.

   Four things on one screen, in the order the thought happens: who was away,
   when, how much longer they need, and in which courses. Then a dry run that
   shows every date it would move and where each one is measured from, with a
   tick box on each row, and only then a push.

   The dry run is the point. "Extend by 3 days" is a sentence with a lot of
   hidden arithmetic in it -- which assignments count as being in the window,
   what date the student is actually held to right now, whether the thing locks
   before the new deadline -- and all of it is shown as dates you can read
   rather than trusted. */
(function () {
  'use strict';

  const S = {
    students: [], courses: [], loaded: false,
    picked: new Set(), scope: 'term', pickedCourses: new Set(), term: '',
    query: '', days: 3, start: '', end: '',
    includeSubmitted: false,
    plan: null, off: new Set(), running: false,
  };

  const key = r => `${r.course_id}:${r.assignment_id}:${r.user_id}`;
  function ymd(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }

  function defaultWindow() {
    const today = new Date();
    const week = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6);
    S.start = S.start || ymd(week);
    S.end = S.end || ymd(today);
  }

  /* ------------------------------------------------------------ the screen */
  async function open() {
    const nicks = (typeof loadNicks === 'function') ? loadNicks() : null;
    showView('inbox');                    // the wide full-width shell
    const host = $('#viewInbox');
    defaultWindow();
    host.innerHTML = `<h2>Deadline extensions</h2>
      <div class="sub">loading your students…</div>`;
    if (!S.loaded) {
      try {
        const [people, courses] = await Promise.all([
          api('/extend/students'), api('/extend/courses'),
        ]);
        S.students = people.students || [];
        S.courses = courses.courses || [];
        S.term = S.term || courses.default_term || '';
        S.loaded = true;
      } catch (err) {
        host.innerHTML = `<h2>Deadline extensions</h2>`;
        host.appendChild(emptyState('Could not read your courses',
          esc(firstLine(err.message))));
        return;
      }
    }
    if (nicks) await nicks;
    draw();
  }

  function draw() {
    const host = $('#viewInbox');
    host.innerHTML = `
      <h2>Deadline extensions</h2>
      <div class="sub">Give named students longer on the work that fell during an
        absence. Everyone else's dates stay exactly as they are.</div>

      <div class="exGrid">
        ${whoPanel()}
        ${whenPanel()}
      </div>

      <div class="exActions">
        <button class="btn ai" id="exPlan" ${S.picked.size ? '' : 'disabled'}>
          Show me what would move</button>
        <span class="muted" id="exHint">${S.picked.size
          ? `${S.picked.size} student${S.picked.size === 1 ? '' : 's'} selected`
          : 'Pick at least one student'}</span>
      </div>

      <div id="exPlanHost">${S.plan ? planHtml(S.plan) : ''}</div>`;
    wire();
    if (S.plan) wirePlan();
  }

  /* ------------------------------------------------------------------ who */
  /* Only the inside of the pool is redrawn as you type. Re-rendering the panel
     would take the search box down with it and drop focus on every keystroke,
     which is the classic way to make a search box impossible to type into. */
  function poolInner() {
    const q = S.query.toLowerCase();
    const matches = S.students.filter(s =>
      !q || s.name.toLowerCase().includes(q)
         || studentLabel(s).toLowerCase().includes(q)
         || (s.sis_user_id || '').toLowerCase().includes(q));
    return (matches.slice(0, 40).map(s => `
      <button class="exPick${S.picked.has(String(s.user_id)) ? ' on' : ''}"
        data-uid="${esc(s.user_id)}">
        <b>${esc(studentLabel(s))}</b>
        <span>${esc(s.courses.slice(0, 3).join(', '))}${
          s.courses.length > 3 ? ` +${s.courses.length - 3} more` : ''}</span>
      </button>`).join('') || '<div class="muted">no match</div>')
      + (matches.length > 40 ? `<div class="muted exMore">${matches.length - 40}
          more — narrow the search</div>` : '');
  }

  function whoPanel() {
    const chosen = S.students.filter(s => S.picked.has(String(s.user_id)));
    return `<section class="exPanel">
      <h3>Who was away</h3>
      ${chosen.length ? `<div class="exChosen">${chosen.map(s => `
        <button class="exChip" data-drop="${esc(s.user_id)}"
          title="Remove from this extension">${esc(studentLabel(s))}
          <span class="exX">×</span></button>`).join('')}</div>` : ''}
      <input type="search" id="exSearch" placeholder="Search your students by name or ID"
        value="${esc(S.query)}" autocomplete="off">
      <div class="exPool" id="exPool">${poolInner()}</div>
    </section>`;
  }

  /* ----------------------------------------------------------------- when */
  function whenPanel() {
    return `<section class="exPanel">
      <h3>When, and how much longer</h3>

      <div class="exRow">
        <label>First day away
          <input type="date" id="exStart" value="${esc(S.start)}"></label>
        <label>Last day away
          <input type="date" id="exEnd" value="${esc(S.end)}"></label>
      </div>
      <p class="muted exNote">Anything due between these two days, inclusive, is
        picked up. You can untick individual items before anything is written.</p>

      <div class="exRow">
        <label>Extend by
          <input type="number" id="exDays" min="1" max="90" value="${esc(String(S.days))}">
        </label>
        <span class="exUnit">day${S.days === 1 ? '' : 's'}</span>
      </div>

      <h4>Which courses</h4>
      <label class="exRadio"><input type="radio" name="exScope" value="term"
        ${S.scope === 'term' ? 'checked' : ''}> Every course they are in this term
        ${S.term ? `<span class="muted">(${esc(S.term)})</span>` : ''}</label>
      <label class="exRadio"><input type="radio" name="exScope" value="pick"
        ${S.scope === 'pick' ? 'checked' : ''}> Only the courses I tick</label>
      ${S.scope === 'pick' ? `<div class="exCourses">${S.courses.map(c => `
        <label class="exCourse"><input type="checkbox" data-cid="${esc(c.course_id)}"
          ${S.pickedCourses.has(c.course_id) ? 'checked' : ''}>
          ${esc(c.label)}</label>`).join('')}</div>` : ''}

      <label class="exRadio exSub"><input type="checkbox" id="exSubmitted"
        ${S.includeSubmitted ? 'checked' : ''}> Include work they have already
        turned in</label>
    </section>`;
  }

  function wire() {
    const search = $('#exSearch');
    if (search) {
      search.oninput = () => {
        S.query = search.value;
        const pool = $('#exPool');
        if (pool) { pool.innerHTML = poolInner(); wirePool(); }
      };
    }
    wirePool();
    $('#viewInbox').querySelectorAll('[data-drop]').forEach(b => {
      b.onclick = () => { S.picked.delete(String(b.dataset.drop)); draw(); };
    });
    const bind = (id, fn) => { const el = $(id); if (el) el.onchange = fn; };
    bind('#exStart', e => { S.start = e.target.value; });
    bind('#exEnd', e => { S.end = e.target.value; });
    bind('#exDays', e => { S.days = Math.max(1, Math.min(90, +e.target.value || 1)); draw(); });
    bind('#exSubmitted', e => { S.includeSubmitted = e.target.checked; });
    $('#viewInbox').querySelectorAll('[name="exScope"]').forEach(r => {
      r.onchange = () => { S.scope = r.value; draw(); };
    });
    $('#viewInbox').querySelectorAll('[data-cid]').forEach(c => {
      c.onchange = () => {
        if (c.checked) S.pickedCourses.add(c.dataset.cid);
        else S.pickedCourses.delete(c.dataset.cid);
      };
    });
    const go = $('#exPlan');
    if (go) go.onclick = doPlan;
  }

  function wirePool() {
    $('#viewInbox').querySelectorAll('.exPick').forEach(b => {
      b.onclick = () => {
        const uid = String(b.dataset.uid);
        if (S.picked.has(uid)) S.picked.delete(uid); else S.picked.add(uid);
        draw();
      };
    });
  }

  /* ------------------------------------------------------------- the plan */
  function args() {
    return {
      user_ids: [...S.picked], days: S.days, start: S.start, end: S.end,
      term: S.term,
      course_ids: S.scope === 'pick' ? [...S.pickedCourses] : [],
      include_submitted: S.includeSubmitted,
    };
  }

  function doPlan() {
    if (S.running) return;
    S.running = true;
    S.plan = null; S.off = new Set();
    runJob('Working out which dates would move',
      () => api('/extend/plan', { body: args() }),
      r => {
        S.running = false;
        if (!r) return;
        S.plan = r;
        // Rows for work already handed in arrive pre-unticked: they are shown
        // so you can see they were considered, not so they move by default.
        (r.rows || []).forEach(row => { if (row.submitted) S.off.add(key(row)); });
        draw();
      });
  }

  function planHtml(p) {
    const rows = p.rows || [];
    const live = rows.filter(r => !S.off.has(key(r)));
    if (!rows.length) {
      return `<section class="exPlan"><h3>Nothing to move</h3>
        ${scopeHtml(p)}
        <p class="muted">Nothing was due between ${esc(p.window.start)} and
          ${esc(p.window.end)} for ${esc((p.students || []).map(s => studentLabel(s)).join(', '))}
          in the ${esc(String(p.courses_in_scope))} course(s) searched.</p>
        ${skippedHtml(p)}</section>`;
    }
    const byCourse = new Map();
    rows.forEach(r => {
      if (!byCourse.has(r.course_label)) byCourse.set(r.course_label, []);
      byCourse.get(r.course_label).push(r);
    });

    return `<section class="exPlan">
      <h3>${esc(String(rows.length))} due date${rows.length === 1 ? '' : 's'}
        would move</h3>
      <div class="sub">${esc(p.summary)}</div>
      ${scopeHtml(p)}
      <div class="callout exZone">Dates worked out in
        <b>${esc(String(p.zone || '').replace(/\.$/, ''))}</b>.
        ${p.zones_differ ? 'Your courses are not all in the same timezone; each one uses its own.' : ''}</div>

      ${[...byCourse.entries()].map(([label, items]) => `
        <table class="exTable"><caption>${esc(label)}</caption>
        <thead><tr><th class="exTick"></th><th>Assignment</th><th>Student</th>
          <th>Due now</th><th></th><th>Would become</th><th>Measured from</th>
        </tr></thead><tbody>${items.map(r => `
          <tr class="${S.off.has(key(r)) ? 'exOffRow' : ''}">
            <td class="exTick"><input type="checkbox" data-row="${esc(key(r))}"
              ${S.off.has(key(r)) ? '' : 'checked'}></td>
            <td><b>${esc(r.title)}</b>${r.kind !== 'assignment'
              ? ` <span class="pill sm">${esc(r.kind)}</span>` : ''}
              ${r.submitted ? ' <span class="pill warn sm">already turned in</span>' : ''}</td>
            <td>${esc(studentLabel(r))}</td>
            <td class="exDate">${esc(fmt(r.from_due))}</td>
            <td class="exArrow">→</td>
            <td class="exDate exNew">${esc(fmt(r.to_due))}</td>
            <td class="muted exFrom">${esc(r.source)}${r.to_lock
              ? `<br><span class="exLock">locks ${esc(fmt(r.to_lock))}</span>` : ''}</td>
          </tr>`).join('')}</tbody></table>`).join('')}

      ${skippedHtml(p)}
      ${(p.failed || []).length ? `<div class="callout warn">
        ${p.failed.map(f => `Could not read ${esc(f.course_label)}: ${esc(f.error)}`)
          .join('<br>')}</div>` : ''}

      <div class="exFoot">
        <button class="btn danger" id="exApply" ${live.length ? '' : 'disabled'}>
          Move ${esc(String(live.length))} date${live.length === 1 ? '' : 's'} in Canvas…</button>
        <span class="muted">Each student gets their own override. Nothing else on
          the assignment changes, and no other student is touched.</span>
      </div>
    </section>`;
  }

  /* Picked, but in none of the courses that were searched -- their course is in
     another term, or among the sandboxes. Without this the screen just says
     "nothing to move", which reads as a broken tool rather than as the answer. */
  function scopeHtml(p) {
    const missing = p.not_in_scope || [];
    if (!missing.length) return '';
    return `<div class="callout">${missing.map(m => `<div><b>${esc(studentLabel(m))}</b> is
      not in any of the courses searched.${m.courses.length
        ? ` Canvas has them in ${esc(m.courses.join(', '))}.`
        : ' Canvas shows them in none of your courses.'}</div>`).join('')}
      <div class="exFix">Switch <b>Which courses</b> to <b>Only the courses I
        tick</b> and choose theirs.</div></div>`;
  }

  function skippedHtml(p) {
    const loud = (p.skipped || []).filter(s => !s.quiet);
    if (!loud.length) return '';
    return `<details class="exSkipped"><summary>${loud.length} left alone</summary>
      <ul>${loud.map(s => `<li><b>${esc(s.title)}</b> — ${esc(studentLabel(s))}:
        ${esc(s.why)}</li>`).join('')}</ul></details>`;
  }

  function fmt(isoText) {
    if (!isoText) return 'no date';
    const d = new Date(isoText);
    if (isNaN(d)) return isoText;
    return d.toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short',
      hour: 'numeric', minute: '2-digit' });
  }

  function wirePlan() {
    $('#viewInbox').querySelectorAll('[data-row]').forEach(box => {
      box.onchange = () => {
        if (box.checked) S.off.delete(box.dataset.row);
        else S.off.add(box.dataset.row);
        draw();
      };
    });
    const apply = $('#exApply');
    if (apply) apply.onclick = doApply;
  }

  /* ------------------------------------------------------------ the write */
  /* The sentence, the from/to list and the token all come from the server's own
     gate: it refuses the first call, hands back what it would write, and only
     the second call carries a token bound to exactly those rows. Composing a
     confirmation here instead would be describing one thing and sending
     another. */
  function doApply() {
    const rows = (S.plan.rows || []).filter(r => !S.off.has(key(r)));
    if (!rows.length) return;
    const keys = rows.map(key);
    runJobConfirmed('Moving due dates in Canvas',
      // The token goes inside the body: api() sends opts.body and nothing
      // else, and the gate reads it from there.
      token => api('/extend/apply', {
        body: { ...args(), keys, confirm: token },
      }),
      r => {
        if (!r) return;
        setStatus(r.message, (r.failed_writes || []).length ? 'err' : 'ok');
        S.plan = null; S.off = new Set();
        draw();
      },
      { title: 'Move these due dates?', verb: 'Yes, move them',
        note: 'Each student gets their own override in Canvas. Nobody else\'s '
            + 'dates change, and the change is written to your record.' });
  }

  window.openExtensions = open;
  Object.assign(window.Studio || (window.Studio = {}), { openExtensions: open });
})();
