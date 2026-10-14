/* CourseForge Studio - the grader. Everything here moved from app.js unchanged
   apart from two breadcrumb links; the shell it relies on (S, api, the jobs and
   their dock, the confirm dialog, the router) lives in core.js. */
'use strict';


/* ------------------------------------------------- reminders for one week */
/* The tests in a single week, as the filters currently leave them. One Claude
   call each, then one review screen: the drafts are worth reading together,
   because a week of five reminders written separately is where duplicated or
   contradictory wording shows up. */
function openWeekReminders(weekKey) {
  const rows = schedRows().filter(i => i.exam && weekKey === weekKey_(i));
  if (!rows.length) { setStatus('no tests in that week', 'err'); return; }
  const monday = new Date(weekKey + 'T00:00:00');
  const host = $('#modalHost');

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Reminders for the week of ${esc(monday.toLocaleDateString([],
        { month: 'long', day: 'numeric' }))}</h3>
      <div class="sub">${rows.length} assessment${rows.length > 1 ? 's' : ''},
        from what the filters are showing. One draft each, all editable before
        anything is posted.</div>
      <ul class="weekList">${rows.map(i => `<li>
        <span class="cbadge" style="background:${esc(i.colour)}">${
          esc(i.course_label || i.course_code)}</span>
        <span class="weekName">${esc(i.name)}</span>
        <span class="k ${KIND_CLASS[i.kind_label] || 'kind-asn'}">${
          esc(i.kind_label)}</span>
        <span class="weekWhen">${esc(fmtDay(i.when))}</span></li>`).join('')}</ul>
      <label class="annLabel">Anything to add to all of them?
        <span>optional — "mention the review session Thursday"</span>
        <input type="text" id="wkExtra" autocomplete="off">
      </label>
      <div class="foot">
        <span class="spacer"></span>
        <button class="btn" id="wkCancel">Cancel</button>
        <button class="btn ai" id="wkGo">Draft ${rows.length}</button>
      </div>
    </div></div>`;
  $('#wkCancel').onclick = () => { host.innerHTML = ''; };
  $('#wkExtra').focus();
  $('#wkGo').onclick = () => {
    const extra = $('#wkExtra').value;
    const targets = rows.map(i => ({ course_id: i.course_id,
                                     assignment_id: i.assignment_id }));
    runJob(`Drafting ${targets.length} reminder(s)`,
      () => api('/schedule/announce-batch', { body: { targets, extra } }),
      r => { $('#modalHost').innerHTML = ''; showBatchDrafts(r); });
  };
}

/* The week a row belongs to, by the same local rule the sections use. */
function weekKey_(item) {
  return weekKey(item.when || new Date(item.due_at));
}

function showBatchDrafts(r) {
  const host = $('#modalHost');
  const drafts = r.drafts || [];
  if (!drafts.length) {
    setStatus('nothing came back', 'err');
    return;
  }
  const card = (d, i) => `
    <div class="bdCard">
      <label class="bdPick">
        <input type="checkbox" class="bdTick" data-i="${i}" checked>
        <span class="cbadge" style="background:${esc(schedColour(d.course_id))}">${
          esc(d.course_label || '')}</span>
        <span class="bdAbout">${esc(d.name || '')}</span>
      </label>
      <input type="text" class="bdTitle" data-i="${i}" value="${esc(d.title || '')}">
      <textarea class="bdBody" data-i="${i}" rows="5">${esc(d.message || '')}</textarea>
      <div class="paperFrame canvasPage bdAnnPrev" data-i="${i}"></div>
    </div>`;

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>${drafts.length} reminder${drafts.length > 1 ? 's' : ''} to look over</h3>
      <div class="sub">${r.model ? 'drafted by ' + esc(r.model) : ''}${r.cost_usd
        ? ' · $' + Number(r.cost_usd).toFixed(3) : ''} · untick any you do not
        want, edit the rest</div>
      ${(r.failed || []).length ? `<div class="callout bad">${r.failed.length}
        could not be drafted: ${r.failed.map(f => esc(f.error)).join('; ')}</div>` : ''}
      <div class="bdList">${drafts.map(card).join('')}</div>
      <div class="callout">${r.writes_enabled
        ? 'Posting sends each ticked announcement to its own course. You will be '
          + 'asked to confirm once more before anything goes out.'
        : 'Canvas writes are locked off in config.json, so <b>Post</b> is '
          + 'disabled. Copy all still works.'}</div>
      <div class="foot">
        <button class="btn" id="bdCopy">Copy all</button>
        <span class="spacer"></span>
        <button class="btn" id="bdClose">Close</button>
        <button class="btn danger" id="bdPost" ${r.writes_enabled ? '' : 'disabled'}
          >Post ticked…</button>
      </div>
    </div></div>`;
  $('#bdClose').onclick = () => { host.innerHTML = ''; };

  const paintOne = i => paintAnnouncePreview(
    host.querySelector(`.bdTitle[data-i="${i}"]`),
    host.querySelector(`.bdBody[data-i="${i}"]`),
    host.querySelector(`.bdAnnPrev[data-i="${i}"]`),
    drafts[i].brand, drafts[i].look);
  drafts.forEach((_, i) => {
    paintOne(i);
    const title = host.querySelector(`.bdTitle[data-i="${i}"]`);
    const body = host.querySelector(`.bdBody[data-i="${i}"]`);
    if (title) title.oninput = () => paintOne(i);
    if (body) body.oninput = () => paintOne(i);
  });

  const ticked = () => [...host.querySelectorAll('.bdTick')]
    .filter(c => c.checked)
    .map(c => {
      const i = c.dataset.i;
      return {
        op: 'announce',
        course_id: drafts[i].course_id,
        title: host.querySelector(`.bdTitle[data-i="${i}"]`).value.trim(),
        message: wrapAnnouncement(
          host.querySelector(`.bdBody[data-i="${i}"]`).value.trim(),
          drafts[i].brand, drafts[i].look),
        _label: drafts[i].course_label,
      };
    })
    .filter(o => o.title && o.message);

  $('#bdCopy').onclick = () => {
    const text = drafts.map(d => `${d.course_label} — ${d.name}\n${d.title}\n\n`
      + `${d.message}`).join('\n\n----\n\n');
    navigator.clipboard.writeText(text)
      .then(() => setStatus(`${drafts.length} announcement(s) copied`, 'ok'))
      .catch(() => setStatus('could not copy', 'err'));
  };

  $('#bdPost').onclick = () => {
    const ops = ticked();
    if (!ops.length) { setStatus('nothing ticked, or a draft is empty', 'err'); return; }
    runJobConfirmed(`Posting ${ops.length} announcement(s)`,
      token => api('/schedule/apply', { body: { dry_run: false, confirm: token,
        operations: ops.map(({ _label, ...op }) => op) } }),
      res => { $('#modalHost').innerHTML = ''; showApplyResult(res); },
      { title: `Post ${ops.length} announcement(s)?`,
        verb: 'Yes, post them',
        note: 'Each goes to its own course, and students see it straight away.' });
  };
}

/* ==========================================================================
   Test administration: settings, and who gets longer
   ==========================================================================

   One dialog, one screen at a time. Clicking a test gives a short menu of what
   can be changed; picking an item replaces the body with just that, and a back
   arrow returns. Everything on one screen was the alternative, and a quiz has
   fifteen settings across four unrelated concerns -- dates, secrecy, timing,
   what students see afterwards. Shown together they are a wall; shown one
   concern at a time each screen is four fields and a Save.

   The state below is deliberately small: which quiz, which panel, and the
   loaded quiz itself. Everything else is read from the DOM at Save time. */
const TA = { cid: null, qid: null, panel: 'menu', data: null, busy: false };

/* The menu. Each entry is a panel name, a label, the fields it owns, and a line
   that says what it currently holds so the menu is readable without opening
   anything. */
const TA_PANELS = [
  {
    key: 'dates', icon: '📅', label: 'Dates and availability',
    fields: ['unlock_at', 'due_at', 'lock_at'],
    blurb: d => {
      const due = d.settings.due_at;
      const from = d.settings.unlock_at, until = d.settings.lock_at;
      const bits = [due ? `due ${fmtDay(new Date(due))}` : 'no due date'];
      if (from) bits.push(`opens ${fmtDay(new Date(from))}`);
      if (until) bits.push(`closes ${fmtDay(new Date(until))}`);
      return bits.join(' · ');
    },
  },
  {
    key: 'access', icon: '🔒', label: 'Password and access',
    fields: ['access_code', 'ip_filter', 'published'],
    blurb: d => [
      d.has_access_code ? 'password set' : 'no password',
      d.settings.ip_filter ? `IP filter ${d.settings.ip_filter}` : 'any address',
      d.settings.published ? 'published' : 'UNPUBLISHED',
    ].join(' · '),
  },
  {
    key: 'timing', icon: '⏱', label: 'Timing and attempts',
    fields: ['time_limit', 'allowed_attempts', 'scoring_policy',
             'one_question_at_a_time', 'cant_go_back', 'shuffle_answers'],
    blurb: d => [
      d.settings.time_limit ? `${d.settings.time_limit} minutes` : 'untimed',
      d.settings.allowed_attempts === -1 ? 'unlimited attempts'
        : `${d.settings.allowed_attempts || 1} attempt${
            (d.settings.allowed_attempts || 1) === 1 ? '' : 's'}`,
      d.settings.shuffle_answers ? 'answers shuffled' : 'fixed order',
    ].join(' · '),
  },
  {
    key: 'results', icon: '👁', label: 'What students see afterwards',
    fields: ['hide_results', 'show_correct_answers', 'one_time_results'],
    blurb: d => d.settings.hide_results
      ? 'results hidden'
      : (d.settings.show_correct_answers ? 'shows correct answers'
                                         : 'score only, no answers'),
  },
  {
    key: 'accom', icon: '♿', label: 'Accommodations',
    blurb: d => {
      const n = (d.accommodated || []).length;
      if (!n) return d.roster_size
        ? 'nobody on your list is in this course'
        : 'nobody on your accommodation list yet';
      const off = d.accommodated.filter(r => !r.in_sync).length;
      return `${n} student${n === 1 ? '' : 's'}`
        + (off ? ` · ${off} not set in Canvas yet` : ' · all set in Canvas');
    },
  },
];

async function openTestAdmin(courseId, quizId, panel) {
  TA.cid = String(courseId);
  TA.qid = String(quizId);
  TA.panel = panel || 'menu';
  TA.data = null;
  taShell('Loading…', `<p class="descWait"><span class="spin"></span>reading this
    test from Canvas…</p>`, '<button class="btn" id="taClose">Close</button>');
  $('#taClose').onclick = taDismiss;
  try {
    TA.data = await api(`/quiz/${TA.cid}/${TA.qid}`);
  } catch (err) {
    taShell('Could not read this test',
      `<div class="callout bad">${esc(err.message)}</div>
       <div class="cfNote">Only classic Canvas quizzes can be edited here. New
         Quizzes and publisher tests (Pearson, ALEKS) keep their own settings.</div>`,
      '<span class="spacer"></span><button class="btn" id="taClose">Close</button>');
    $('#taClose').onclick = taDismiss;
    return;
  }
  taRender();
}

function taDismiss() { $('#modalHost').innerHTML = ''; }

function taShell(title, body, foot, sub) {
  $('#modalHost').innerHTML = `<div class="modalBack"><div class="modal wide taModal">
      <div class="taHead">
        <button class="taBack" id="taBack" hidden title="Back to the menu">‹</button>
        <div class="taTitles">
          <h3 id="taTitle">${title}</h3>
          <div class="sub" id="taSub">${sub || ''}</div>
        </div>
      </div>
      <div class="taBody" id="taBody">${body}</div>
      <div class="foot" id="taFoot">${foot || ''}</div>
    </div></div>`;
}

function taRender() {
  const d = TA.data;
  if (!d) return;
  const where = [d.course_label, d.question_count != null
    ? `${d.question_count} question${d.question_count === 1 ? '' : 's'}` : '',
    d.points != null ? `${num(d.points)} points` : ''].filter(Boolean).join(' · ');

  if (TA.panel === 'menu') {
    taShell(esc(d.title), TA_PANELS.map(p => `
      <button class="taRow" data-panel="${p.key}">
        <span class="taIcon">${p.icon}</span>
        <span class="taRowText"><b>${esc(p.label)}</b>
          <span>${esc(p.blurb(d))}</span></span>
        <span class="taChev">›</span>
      </button>`).join(''),
      `${d.url ? `<a class="btn" href="${esc(d.url)}" target="_blank"
         rel="noopener">Open in Canvas ↗</a>` : ''}
       <span class="spacer"></span>
       <span class="descFetched">read just now</span>
       <button class="btn" id="taClose">Close</button>`, esc(where));
    $('#taClose').onclick = taDismiss;
    $('#taBody').querySelectorAll('.taRow').forEach(el => {
      el.onclick = () => { TA.panel = el.dataset.panel; taRender(); };
    });
    return;
  }

  const spec = TA_PANELS.find(p => p.key === TA.panel);
  if (!spec) { TA.panel = 'menu'; return taRender(); }

  if (spec.key === 'accom') return taAccom();

  taShell(esc(spec.label), `<div class="taForm">${
    spec.fields.map(f => taField(f, d)).join('')}</div>`,
    `<span class="spacer"></span>
     <button class="btn" id="taCancel">Back</button>
     <button class="btn danger" id="taSave">Save to Canvas…</button>`,
    esc(d.title));
  $('#taBack').hidden = false;
  $('#taBack').onclick = () => { TA.panel = 'menu'; taRender(); };
  $('#taCancel').onclick = () => { TA.panel = 'menu'; taRender(); };
  $('#taSave').onclick = () => taSave(spec);
  taWireForm();
}

/* One row per setting. The kind decides the control; the label and kind both
   come from the server, so the editor cannot drift from what it will accept. */
function taField(name, d) {
  const meta = (d.fields || {})[name];
  if (!meta) return '';
  const value = d.settings[name];
  const id = 'ta_' + name;
  const help = TA_HELP[name] ? `<span class="taHelp">${esc(TA_HELP[name])}</span>` : '';

  if (meta.kind === 'bool') {
    return `<label class="taSwitch">
      <input type="checkbox" id="${id}" data-f="${name}" ${value ? 'checked' : ''}>
      <span class="taLbl">${esc(meta.label)}${help}</span></label>`;
  }
  if (meta.kind === 'date') {
    return `<label class="taRowF"><span class="taLbl">${esc(meta.label)}${help}</span>
      <span class="taInline">
        <input type="datetime-local" id="${id}" data-f="${name}"
               value="${esc(utcToLocalInput(value))}">
        <button class="taMini" data-clear="${id}">clear</button>
      </span></label>`;
  }
  if (meta.kind === 'minutes') {
    return `<label class="taRowF"><span class="taLbl">${esc(meta.label)}${help}</span>
      <span class="taInline"><input type="number" min="0" max="1440" id="${id}"
        data-f="${name}" value="${value == null ? '' : esc(String(value))}"
        placeholder="no limit"><span class="taUnit">minutes</span></span></label>`;
  }
  if (meta.kind === 'attempts') {
    return `<label class="taRowF"><span class="taLbl">${esc(meta.label)}${help}</span>
      <span class="taInline"><input type="number" min="-1" max="100" id="${id}"
        data-f="${name}" value="${value == null ? 1 : esc(String(value))}">
        <span class="taUnit">−1 = unlimited</span></span></label>`;
  }
  if (meta.kind === 'text') {
    const isPw = name === 'access_code';
    return `<label class="taRowF"><span class="taLbl">${esc(meta.label)}${help}</span>
      <span class="taInline">
        <input type="text" id="${id}" data-f="${name}" autocomplete="off"
          data-secret="${isPw ? '1' : ''}"
          placeholder="${isPw && d.has_access_code
            ? 'a password is set — type to replace it' : 'none'}">
        ${isPw && d.has_access_code
          ? '<button class="taMini" data-clear="' + id + '">remove it</button>' : ''}
      </span></label>`;
  }
  if ((meta.kind || '').startsWith('choice:')) {
    const opts = meta.kind.split(':')[1].split(',');
    return `<label class="taRowF"><span class="taLbl">${esc(meta.label)}${help}</span>
      <select id="${id}" data-f="${name}">${opts.map(o => `<option value="${esc(o)}"
        ${String(value || '') === o ? 'selected' : ''}>${esc(TA_CHOICE[o] || o
          || 'No — they can see them')}</option>`).join('')}</select></label>`;
  }
  return '';
}

const TA_HELP = {
  access_code: 'never shown back to you once saved',
  ip_filter: 'a single address, or a range like 10.1.0.0/16',
  lock_at: 'after this, nobody can open or submit it',
  unlock_at: 'before this, students cannot start',
  cant_go_back: 'only available with one question at a time',
  time_limit: 'blank means untimed',
  one_time_results: 'they see their results once, then never again',
};
const TA_CHOICE = {
  keep_highest: 'the highest score', keep_latest: 'the most recent score',
  always: 'Always — never show results',
  until_after_last_attempt: 'Until they have used every attempt',
};

function taWireForm() {
  $('#taBody').querySelectorAll('[data-clear]').forEach(btn => {
    btn.onclick = ev => {
      ev.preventDefault();
      const el = document.getElementById(btn.dataset.clear);
      el.value = '';
      el.dataset.cleared = '1';           // blank means "clear it", not "untouched"
      btn.classList.add('on');
    };
  });
}

/* Only what actually moved gets sent. A password left blank means untouched --
   the field starts empty because the current one is never echoed back, so an
   empty box cannot be read as "remove the password" unless it was cleared on
   purpose with the button beside it. */
function taChanges(spec) {
  const out = {};
  spec.fields.forEach(name => {
    const el = document.getElementById('ta_' + name);
    if (!el) return;
    const meta = (TA.data.fields || {})[name];
    if (meta.kind === 'bool') { out[name] = el.checked; return; }
    if (meta.kind === 'date') {
      if (el.dataset.cleared === '1' && !el.value) { out[name] = null; return; }
      if (el.value) out[name] = localToUtc(el.value);
      return;
    }
    if (meta.kind === 'text') {
      if (el.dataset.cleared === '1') { out[name] = null; return; }
      if (el.value.trim()) out[name] = el.value.trim();
      return;
    }
    if (meta.kind === 'minutes') {
      out[name] = el.value.trim() === '' ? null : el.value.trim();
      return;
    }
    out[name] = el.value;
  });
  return out;
}

async function taSave(spec) {
  if (TA.busy) return;
  const changes = taChanges(spec);
  TA.busy = true;
  setStatus('checking…');
  try {
    const r = await postConfirmed(
      token => api(`/quiz/${TA.cid}/${TA.qid}/settings`,
        { body: { changes, confirm: token } }),
      { title: 'Change this test in Canvas?', verb: 'Save it',
        note: 'Students see these settings the moment they are saved.' });
    if (r === null) return;                    // cancelled at the confirmation
    (r.rejected || []).forEach(x => setStatus(firstLine(x.why), 'err'));
    if (!(r.changed || []).length && !(r.rejected || []).length) {
      setStatus('nothing had changed', 'ok');
    } else if ((r.changed || []).length) {
      setStatus(r.message, 'ok');
    }
    TA.data = await api(`/quiz/${TA.cid}/${TA.qid}?refresh=1`);
    // The schedule is showing dates that may have just moved.
    if (S.sched) { S.sched = null; }
    TA.panel = 'menu';
    taRender();
    if ((r.rejected || []).length) {
      $('#taBody').insertAdjacentHTML('afterbegin',
        `<div class="callout bad">${r.rejected.map(x =>
          esc(x.why)).join('<br>')}</div>`);
    }
  } catch (err) {
    setStatus(firstLine(err.message), 'err');
  } finally { TA.busy = false; }
}

/* --------------------------------------------------- accommodations, in place */
function taAccom() {
  const d = TA.data;
  const rows = d.accommodated || [];
  const body = `
    ${rows.length ? `<table class="acTable"><thead><tr>
        <th>Student</th><th>Approved</th><th>On this test</th><th>In Canvas</th>
      </tr></thead><tbody>${rows.map(r => `<tr class="${r.in_sync ? '' : 'off'}">
        <td>${esc(studentLabel(r))}</td>
        <td class="acWhat">${esc(r.accommodation)}</td>
        <td class="acMin">${r.would_be == null
          ? '<span class="muted">n/a — untimed</span>'
          : '+' + r.would_be + ' min'}</td>
        <td>${r.in_sync
          ? '<span class="pill good">set</span>'
          : (r.current_extra_time
              ? `<span class="pill warn">+${r.current_extra_time} min</span>`
              : '<span class="pill warn">not set</span>')}</td>
      </tr>`).join('')}</tbody></table>`
      : `<div class="callout">${d.roster_size
          ? 'Nobody on your accommodation list is enrolled in this course.'
          : 'Your accommodation list is empty. <b>Student roster</b> pulls '
            + 'students from every course you teach, so a college approval is '
            + 'entered once and applies everywhere.'}</div>`}

    ${(d.unlisted || []).length ? `<details class="acUnlisted">
      <summary>${d.unlisted.length} student(s) have an extension in Canvas but
        are not on your list</summary>
      <ul>${d.unlisted.map(u => `<li>${esc(studentLabel(u))} —
        ${u.extra_time ? '+' + u.extra_time + ' min' : ''}
        ${u.extra_attempts ? ' +' + u.extra_attempts + ' attempts' : ''}
        ${u.manually_unlocked ? ' can start while locked' : ''}</li>`).join('')}</ul>
      <div class="cfNote">Usually someone set up by hand. Adding them to the
        list means it happens by itself next time.</div>
    </details>` : ''}

    <div class="acScopes">
      <button class="acScope" id="acThis" ${rows.length ? '' : 'disabled'}>
        <b>Apply to this test</b><span>${esc(d.title)}</span></button>
      <button class="acScope" id="acCourse" ${rows.length ? '' : 'disabled'}>
        <b>Apply to every quiz in this course</b>
        <span>${esc(d.course_label || 'this course')} — blanket accommodations,
          one pass</span></button>
      <button class="acScope" id="acAll" ${d.roster_size ? '' : 'disabled'}>
        <b>Apply across every course I teach</b>
        <span>this term, for everyone on the list</span></button>
    </div>`;

  taShell('Accommodations', body,
    `<button class="btn" id="acRoster">Student roster…</button>
     <span class="spacer"></span>
     <button class="btn" id="taCancel">Back</button>`, esc(d.title));
  $('#taBack').hidden = false;
  $('#taBack').onclick = () => { TA.panel = 'menu'; taRender(); };
  $('#taCancel').onclick = () => { TA.panel = 'menu'; taRender(); };
  $('#acRoster').onclick = () => openRoster(() => openTestAdmin(TA.cid, TA.qid, 'accom'));
  const go = (scope) => applyAccommodations(scope, TA.cid, TA.qid,
    () => openTestAdmin(TA.cid, TA.qid, 'accom'));
  if (rows.length) {
    $('#acThis').onclick = () => go('quiz');
    $('#acCourse').onclick = () => go('course');
  }
  if (d.roster_size) $('#acAll').onclick = () => go('all');
}

/* ------------------------------------------------- applying, at any scope */
/* Plan first, always. The plan is a job because the term-wide scope reads every
   quiz in every course, and it comes back with the confirmation already
   attached: the review screen and the plan are the same step here. */
function applyAccommodations(scope, courseId, quizId, afterwards) {
  const label = { quiz: 'this test', course: 'this course',
                  all: 'every course this term' }[scope] || scope;
  runJob(`Working out what changes on ${label}`,
    () => api('/accommodations/plan', {
      body: { scope, course_id: courseId, quiz_id: quizId,
              term: (S.sched && S.sched.term) || null },
    }),
    plan => showAccomPlan(plan, scope, courseId, quizId, afterwards),
    { autoClose: true });
}

function showAccomPlan(plan, scope, courseId, quizId, afterwards) {
  const host = $('#modalHost');
  const rows = plan.rows || [];
  const already = (plan.skipped || []).filter(s => s.already);
  const noTime = (plan.skipped || []).filter(s => !s.quiet);

  if (!rows.length) {
    host.innerHTML = `<div class="modalBack"><div class="modal narrow">
        <h3>Nothing to change</h3>
        <div class="cfSummary">${esc(plan.summary)}</div>
        <div class="cfNote">${already.length} student-test pair(s) already hold
          the right value in Canvas${noTime.length
            ? `, and ${noTime.length} could not take extra time` : ''}.</div>
        <div class="foot"><span class="spacer"></span>
          <button class="btn" id="apOk">Close</button></div>
      </div></div>`;
    $('#apOk').onclick = () => { host.innerHTML = ''; if (afterwards) afterwards(); };
    return;
  }

  // Grouped by quiz, because that is the unit Canvas is written in and the unit
  // the instructor thinks in.
  const byQuiz = new Map();
  rows.forEach(r => {
    const key = r.course_id + ':' + r.quiz_id;
    if (!byQuiz.has(key)) byQuiz.set(key, []);
    byQuiz.get(key).push(r);
  });

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Check this before it happens</h3>
      <div class="sub">${esc(plan.summary)}${plan.quizzes_in_scope
        ? ` · ${plan.quizzes_in_scope} quiz(zes) looked at` : ''}</div>

      <div class="acPeople">${(plan.students || []).map(s =>
        `<span class="acChip">${esc(studentLabel(s))}
          <b>${esc(s.label)}</b></span>`).join('')}</div>

      <div class="apList">${[...byQuiz.values()].map(items => {
        const head = items[0];
        return `<div class="apQuiz">
          <div class="apQHead"><span class="cbadge" style="background:${
            esc(schedColour(head.course_id))}">${esc(head.course_label)}</span>
            <b>${esc(head.quiz_title)}</b>
            <span class="muted">${head.time_limit} min</span></div>
          <ul>${items.map(r => `<li>${esc(studentLabel(r))}
            <span class="apArrow">→</span>
            <b>+${r.extra_time} min</b>
            ${r.extra_attempts ? `<span class="apExtra">+${r.extra_attempts}
              attempt(s)</span>` : ''}
            ${r.manually_unlocked ? '<span class="apExtra">can start while '
              + 'locked</span>' : ''}
            ${(r.before || {}).extra_time
              ? `<span class="apWas">was +${r.before.extra_time}</span>` : ''}
          </li>`).join('')}</ul></div>`;
      }).join('')}</div>

      ${already.length ? `<details class="apSkipped">
        <summary>${already.length} already correct in Canvas — left alone</summary>
        <ul>${already.slice(0, 40).map(s => `<li>${esc(studentLabel(s))} · ${
          esc(s.quiz_title)}</li>`).join('')}</ul></details>` : ''}
      ${noTime.length ? `<details class="apSkipped">
        <summary>${noTime.length} could not take extra time</summary>
        <ul>${noTime.slice(0, 40).map(s => `<li>${esc(studentLabel(s))} · ${
          esc(s.quiz_title)} — ${esc(s.why)}</li>`).join('')}</ul></details>` : ''}

      <div class="cfNote">Canvas takes extra time as minutes, not a multiplier,
        so the numbers above are worked out per test from its own limit and
        rounded up.</div>

      <div class="foot">
        <span class="spacer"></span>
        <button class="btn" id="apCancel">Cancel</button>
        <button class="btn danger" id="apGo">Apply to Canvas…</button>
      </div>
    </div></div>`;
  $('#apCancel').onclick = () => { host.innerHTML = ''; if (afterwards) afterwards(); };
  $('#apGo').onclick = () => {
    host.innerHTML = '';
    askConfirm({ summary: plan.summary, confirm: plan.confirm,
                 detail: `${byQuiz.size} quiz(zes)` },
      token => runJob('Setting accommodations in Canvas',
        () => api('/accommodations/apply', {
          body: { scope, course_id: courseId, quiz_id: quizId,
                  term: (S.sched && S.sched.term) || null, confirm: token },
        }),
        res => {
          setStatus(res.message, (res.failed || []).length ? 'err' : 'ok');
          if (afterwards) afterwards();
        }, { autoClose: !((plan.batches || []).length > 6) }),
      { title: 'Set these accommodations?', verb: 'Yes, apply them',
        note: 'Students get the extra time immediately, on every test listed.',
        onCancel: () => showAccomPlan(plan, scope, courseId, quizId, afterwards) });
  };
}

function syncBanner(sync) {
  sync = sync || {};
  const state = sync.state || sync.did || '';
  if (state === 'diverged') {
    const who = (sync.remote_machine || (sync.machine && sync.machine.name) || 'another computer');
    return `<div class="callout warn" id="roSync">This list also changed on
      ${esc(String(who))}. Nothing was overwritten.
      <div class="foot" style="margin-top:.6rem">
        <button class="btn" id="roTakeRemote">Use the Canvas copy</button>
        <button class="btn" id="roTakeLocal">Keep this computer's list</button>
      </div></div>`;
  }
  if (state === 'error') {
    return `<div class="callout warn" id="roSync">Could not reach your Canvas
      files: ${esc(sync.detail || sync.error || 'unknown error')}.
      The list on this computer is unchanged.
      <button class="btn" id="roHydrate">Try again</button></div>`;
  }
  if (state === 'picked_up' || state === 'in_sync' || state === 'sent' || state === 'seeded') {
    const rev = sync.rev != null ? ` · rev ${esc(String(sync.rev))}` : '';
    return `<div class="sub" id="roSync">Synced through your Canvas files
      (courseforge-studio/state/accommodations.json)${rev}</div>`;
  }
  if (state === 'empty' || !state) {
    return `<div class="sub" id="roSync">Not in Canvas yet — saving the list
      uploads it so another computer can pick it up.</div>`;
  }
  return `<div class="sub" id="roSync">${esc(state)}</div>`;
}

function syncSaveMessage(r) {
  const n = `${r.count} student(s) on the accommodation list`;
  const st = (r.sync || {}).state || (r.sync || {}).did || '';
  if (st === 'in_sync' || st === 'sent' || st === 'seeded')
    return n + ' · saved to your Canvas files';
  if (st === 'diverged')
    return n + ' · Canvas has a different copy — open the roster to choose';
  if (st === 'error')
    return n + ' · saved here; Canvas upload failed';
  return n;
}

/* ---------------------------------------------------------- the saved roster */
/* The list itself: who has a standing accommodation, and what it is. Kept out
   of any one course on purpose -- a college approval applies to every course
   the student is in, and students repeat term after term. */
async function openRoster(afterwards) {
  const host = $('#modalHost');
  // Closing used to empty the dialog and leave #/roster in the address. A
  // refresh then opened it again, and the dim layer from that route sat over
  // the page so nothing else would take a click.
  let gone = false;
  const dismiss = () => {
    if (gone) return;
    gone = true;
    host.innerHTML = '';
    if (typeof afterwards === 'function') {
      afterwards();
      return;
    }
    const top = (location.hash || '').replace(/^#\/?/, '').split('/').filter(Boolean)[0];
    if (top === 'roster') location.hash = '#/';
  };
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Accommodation roster</h3>
      <div class="sub">loading…</div>
      <div class="foot"><span class="spacer"></span>
        <button class="btn" type="button" id="roClose">Close</button></div>
      </div></div>`;
  $('#roClose').onclick = dismiss;
  let saved, everyone;
  try {
    await api('/accommodations/sync').catch(() => null);
    if (gone) return;
    [saved, everyone] = await Promise.all([
      api('/accommodations'), api('/accommodations/students'),
    ]);
  } catch (err) {
    if (!gone) setStatus(firstLine(err.message), 'err');
    return;
  }
  if (gone) return;

  const state = {
    rows: (saved.students || []).map(s => ({ ...s })), query: '',
    // The add panel starts open on an empty list and, once opened, stays open:
    // it is a search box, and a search box that folds itself away between
    // keystrokes cannot be typed into.
    addOpen: !(saved.students || []).length,
  };

  /* Everyone not already on the list, matched against the search box. */
  const poolFor = () => {
    const listed = new Set(state.rows.map(r => String(r.user_id)));
    const q = state.query.toLowerCase();
    return (everyone.students || []).filter(s =>
      !listed.has(s.user_id)
      && (!q || s.name.toLowerCase().includes(q)
             || studentLabel(s).toLowerCase().includes(q)
             || (s.sis_user_id || '').toLowerCase().includes(q)));
  };

  const poolHtml = (pool) => `
    <div class="roPool">${pool.slice(0, 60).map(s => `
      <button class="roPick" data-uid="${esc(s.user_id)}">
        <b>${esc(studentLabel(s))}</b>
        <span>${esc(s.courses.slice(0, 3).join(', '))}${
          s.courses.length > 3 ? ` +${s.courses.length - 3} more` : ''}</span>
      </button>`).join('') || '<div class="muted">no match</div>'}</div>
    ${pool.length > 60 ? `<div class="muted roMore">${pool.length - 60}
      more — narrow the search</div>` : ''}`;

  const draw = () => {
    if (gone) return;
    const pool = poolFor();

    host.innerHTML = `<div class="modalBack"><div class="modal wide rosterModal">
        <h3>Accommodation roster</h3>
        <div class="sub">${state.rows.length} student(s) with a standing
          accommodation · ${everyone.count} students across
          ${everyone.courses} of your courses${
            saved.dropped ? ' · ' + saved.dropped + ' row(s) in the file could not be read'
                          : ''}</div>

        <div class="callout">Entered once, applied anywhere. A Canvas user id is
          the same person in every course, so a college approval set here reaches
          all of them. The list itself is kept in your Canvas files
          (Files → courseforge-studio → state) so another computer signed in
          as you can pick it up.</div>
        ${syncBanner(saved.sync)}

        ${state.rows.length ? `<table class="acTable rosterTable"><thead><tr>
            <th>Student</th><th>Type</th><th>Amount</th><th>Attempts</th>
            <th title="Can start a quiz that is locked for everyone else">Unlock</th>
            <th>Note</th><th></th></tr></thead>
          <tbody>${state.rows.map((r, i) => `<tr>
            <td class="acName"><a href="#/student/${esc(r.user_id)}">${esc(studentLabel(r))}</a>
              <span class="muted">${esc(r.sis_user_id || r.user_id)}</span>
              <label class="roNick">Nickname
                <input type="text" data-nick="${esc(r.user_id)}" maxlength="40"
                  autocomplete="off" placeholder="nickname"
                  value="${esc((typeof nickById !== 'undefined' && nickById[String(r.user_id)]) || '')}">
              </label></td>
            <td><select data-i="${i}" data-k="kind">
              <option value="percent" ${r.kind === 'percent' ? 'selected' : ''}
                >extra %</option>
              <option value="minutes" ${r.kind === 'minutes' ? 'selected' : ''}
                >extra minutes</option></select></td>
            <td class="acAmt">${r.kind === 'minutes'
              ? `<input type="number" min="1" max="10080" data-i="${i}"
                   data-k="minutes" value="${esc(String(r.minutes || 20))}">
                 <span class="taUnit">min</span>`
              : `<input type="number" min="1" max="400" step="5" data-i="${i}"
                   data-k="percent" value="${esc(String(r.percent || 50))}">
                 <span class="taUnit">% (${(1 + (+r.percent || 50) / 100)
                   .toFixed(2).replace(/0$/, '')}x)</span>`}</td>
            <td><input type="number" min="0" max="20" data-i="${i}"
              data-k="extra_attempts" value="${esc(String(r.extra_attempts || 0))}"></td>
            <td class="acTick"><input type="checkbox" data-i="${i}"
              data-k="manually_unlocked" ${r.manually_unlocked ? 'checked' : ''}></td>
            <td><input type="text" data-i="${i}" data-k="note" maxlength="120"
              value="${esc(r.note || '')}" placeholder="optional"></td>
            <td><button class="taMini" data-drop="${i}">remove</button></td>
          </tr>`).join('')}</tbody></table>`
        : '<div class="callout">Nobody on the list yet. Find a student below.</div>'}

        <details class="rosterAdd" id="roAdd" ${state.addOpen ? 'open' : ''}>
          <summary>Add a student from your courses</summary>
          <input type="search" id="roQuery" placeholder="Search by name or ID…"
            value="${esc(state.query)}" autocomplete="off">
          <div class="roPoolBox" id="roPoolBox">${poolHtml(pool)}</div>
        </details>

        <div class="foot">
          <span class="spacer"></span>
          <button class="btn" type="button" id="roClose">Close</button>
          <button class="btn" type="button" id="roSave">Save list</button>
          <button class="btn danger" type="button" id="roApply" ${state.rows.length ? '' : 'disabled'}
            title="Saves the list, then asks before it changes any quiz in Canvas"
            >Save, then apply everywhere…</button>
        </div>
      </div></div>`;

    const commit = (fn) => { fn(); draw(); };
    host.querySelectorAll('[data-k]').forEach(el => {
      const write = () => {
        const row = state.rows[+el.dataset.i];
        row[el.dataset.k] = el.type === 'checkbox' ? el.checked : el.value;
      };
      // Redraw only where the shape of the row changes; typing in a number must
      // not steal focus back to the top of the table.
      if (el.dataset.k === 'kind' || el.dataset.k === 'percent') {
        el.onchange = () => commit(write);
        if (el.dataset.k === 'percent') el.oninput = write;
      } else {
        el.oninput = write;
        el.onchange = write;
      }
    });
    host.querySelectorAll('[data-drop]').forEach(b => {
      b.onclick = () => commit(() => state.rows.splice(+b.dataset.drop, 1));
    });
    host.querySelectorAll('[data-nick]').forEach(el => {
      el.dataset.saved = el.value;
      el.onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); el.blur(); } };
      el.onblur = async () => {
        if (gone || el.value === el.dataset.saved) return;
        const uid = String(el.dataset.nick || '');
        const typed = el.value;
        if (!/^\d+$/.test(uid)) return;
        try {
          const saved = await saveNickname(uid, typed);
          if (gone) return;
          const live = host.querySelector('[data-nick="' + uid + '"]');
          if (live) {
            live.value = saved.nickname || '';
            live.dataset.saved = live.value;
          }
          const link = live && live.closest('td') && live.closest('td').querySelector('a');
          const row = state.rows.find(r => String(r.user_id) === uid);
          if (link && row) link.textContent = studentLabel(row);
          if (S.ws && S.ws.extracted && $('#roster')) renderRoster();
          setStatus(saved.nickname ? 'nickname saved' : 'nickname cleared', 'ok');
        } catch (err) {
          if (!gone) setStatus(firstLine(err.message), 'err');
        }
      };
    });
    const wirePool = () => {
      host.querySelectorAll('.roPick').forEach(b => {
        b.onclick = () => commit(() => {
          const s = (everyone.students || []).find(x => x.user_id === b.dataset.uid);
          if (s) state.rows.push({
            user_id: s.user_id, name: s.name, sis_user_id: s.sis_user_id,
            login_id: s.login_id, kind: 'percent', percent: 50,
            minutes: 0, extra_attempts: 0, manually_unlocked: false, note: '',
          });
        });
      });
    };
    wirePool();

    const details = $('#roAdd');
    if (details) details.ontoggle = () => { state.addOpen = details.open; };

    const search = $('#roQuery');
    if (search) {
      // Repaint only the list of matches. Redrawing the whole dialog on every
      // keystroke is what closed the panel and threw the caret away, which
      // made this typeable exactly one letter at a time.
      search.oninput = () => {
        state.query = search.value.trim();
        const box = $('#roPoolBox');
        if (box) { box.innerHTML = poolHtml(poolFor()); wirePool(); }
      };
      // Adding a student redraws the dialog; land back in the search box with
      // the last search selected. Any remaining matches for it are still on
      // screen (two students called Smith), and typing a new name replaces it
      // instead of running onto the end of the old one.
      if (state.addOpen && state.rows.length) {
        search.focus();
        try { search.select(); } catch (_) { /* older type=search */ }
      }
    }
    const hydrate = async (take) => {
      try {
        const r = take
          ? await api('/accommodations/resolve', { body: { take } })
          : await api('/accommodations/sync');
        setStatus(r.did === 'diverged' ? 'Still two copies — pick one'
                  : (r.did === 'picked_up' ? 'Loaded the list from Canvas'
                  : (r.did === 'sent' || r.did === 'seeded' ? 'Uploaded this list to Canvas'
                  : (r.did || 'synced'))), 'ok');
        if (gone) return;
        host.innerHTML = '';
        return openRoster(afterwards);
      } catch (err) { setStatus(firstLine(err.message), 'err'); }
    };
    const takeRemote = $('#roTakeRemote');
    const takeLocal = $('#roTakeLocal');
    const tryAgain = $('#roHydrate');
    if (takeRemote) takeRemote.onclick = () => hydrate('remote');
    if (takeLocal) takeLocal.onclick = () => hydrate('local');
    if (tryAgain) tryAgain.onclick = () => hydrate('');
    $('#roClose').onclick = dismiss;
    $('#roSave').onclick = () => save().then(r => {
      if (!r || gone) return;
      setStatus(syncSaveMessage(r), r.sync && r.sync.state === 'error' ? 'err' : 'ok');
      dismiss();
    });
    $('#roApply').onclick = () => save().then(r => {
      if (r) applyAccommodations('all', null, null, () => openRoster(afterwards));
    });
  };

  const save = async () => {
    try {
      const students = state.rows.map(row => {
        const copy = Object.assign({}, row);
        delete copy.nickname;
        return copy;
      });
      const r = await api('/accommodations', { body: { students } });
      (r.rejected || []).forEach(x => setStatus(firstLine(x.why), 'err'));
      return r;
    } catch (err) { setStatus(firstLine(err.message), 'err'); return null; }
  };

  if (typeof loadNicks === 'function') await loadNicks();
  if (gone) return;
  draw();
}

/* A UTC instant as the value a datetime-local input wants, in local time. */
function utcToLocalInput(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    + `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* --------------------------------------------- announcements & instructions */
/* Two ways to act from the schedule. Both end at the same place: a plan the
   instructor reads, then one confirmed call to /schedule/apply, which checks
   every operation again on the server and still has to get past
   allow_canvas_writes. Nothing here writes to Canvas on its own. */

/* A local wall-clock stamp ("2026-09-09T08:00:00") turned into a UTC instant.
   The browser is the only part of this that knows the timezone, and Date()
   parses an offset-less stamp as local, which is exactly what is wanted. */
function localToUtc(stamp) {
  if (!stamp) return null;
  const d = new Date(String(stamp).replace(' ', 'T'));
  return isNaN(d) ? null : d.toISOString();
}
function prettyLocal(stamp) {
  if (!stamp) return 'cleared';
  const d = new Date(String(stamp).replace(' ', 'T'));
  return isNaN(d) ? String(stamp) : `${fmtDay(d)} at ${fmtTime(d)}`;
}
function prettyUtc(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : `${fmtDay(d)} at ${fmtTime(d)}`;
}

/* ------------------------------------------------------------- announcement */
function openAnnounce(courseId, assignmentId) {
  const host = $('#modalHost');
  // Reached from the workspace too (View assignment, then Announcement), where
  // the schedule may never have been opened and S.sched is still null.
  const item = ((S.sched && S.sched.items) || []).find(
    i => String(i.assignment_id) === String(assignmentId)) || {};
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Announcement</h3>
      <div class="sub">${esc(item.course_label || '')}${item.name
        ? ' · ' + esc(item.name) : ''}</div>
      <label class="annLabel">Anything to add? <span>optional — "mention the
        review session", "keep it to two lines"</span>
        <input type="text" id="anExtra" autocomplete="off"
          placeholder="leave empty and it works from what Canvas knows">
      </label>
      <div class="foot" id="anFoot">
        <span class="spacer"></span>
        <button class="btn" id="anCancel">Cancel</button>
        <button class="btn ai" id="anDraft">Draft it</button>
      </div>
    </div></div>`;
  $('#anCancel').onclick = () => { host.innerHTML = ''; };
  $('#anExtra').focus();
  $('#anDraft').onclick = () => {
    const extra = $('#anExtra').value;
    runJob(`Drafting an announcement`,
      () => api('/schedule/announce', { body: {
        course_id: courseId, assignment_id: assignmentId, extra } }),
      draft => { $('#modalHost').innerHTML = ''; showAnnounceDraft(draft); });
  };
}

function looksLikeHtml(s) {
  return /^\s*<(div|p|h[1-6]|ul|ol|span|table|section|article)\b/i.test(s || '');
}

function wrapAnnouncement(message, brand, look) {
  const c = (brand && brand.colors) || {
    navy: '#061E3F', gold: '#E9A821', body_text: '#2c3a4d',
    hairline: '#d7dce3', card_fill: '#ffffff',
  };
  const f = (brand && brand.fonts) || {
    body: "Inter, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
  };
  look = look || 'hybrid';
  const raw = (message || '').trim();
  if (!raw) return '';
  if (looksLikeHtml(raw)) return raw;
  const paras = raw.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
  const inner = paras.map(p => {
    const lines = p.split('\n').map(line => esc(line)).join('<br>');
    return `<p style="margin: 0 0 12px; font-size: 15px; color: ${c.body_text};">${lines}</p>`;
  }).join('');
  const filled = look === 'hybrid' || look === 'rich';
  const band = `<div style="padding: 14px 20px; border-radius: 8px; ${
    filled ? 'background: ' + c.navy + '; ' : ''}border-top: 5px solid ${c.gold};">`
    + `<div style="font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; color: ${
      filled ? c.gold : c.navy}; font-weight: 700;">Announcement</div></div>`;
  return `<div style="max-width: 980px; margin: 0 auto; font-family: ${f.body}; line-height: 1.55; color: ${c.body_text};">`
    + band
    + `<div style="margin-top: 14px; padding: 18px 20px; border-radius: 8px; border: 1px solid ${c.hairline};">${inner}</div></div>`;
}

function paintAnnouncePreview(titleEl, bodyEl, paper, brand, look) {
  if (!paper) return;
  const title = (titleEl && titleEl.value) || '';
  const html = wrapAnnouncement((bodyEl && bodyEl.value) || '', brand, look);
  paper.innerHTML = `<div class="annCanvasTitle">${esc(title || '(no subject)')}</div>`
    + `<div class="canvasHtml asIs">${html || '<p class="muted">Nothing to show yet.</p>'}</div>`;
}

function showAnnounceDraft(d) {
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Announcement for ${esc(d.course_label || '')}</h3>
      <div class="sub">about ${esc(d.name || '')}${d.model
        ? ' · drafted by ' + esc(d.model) : ''}${d.cost_usd
        ? ' · $' + Number(d.cost_usd).toFixed(3) : ''} · edit anything before it
        goes out</div>
      ${d.parse_error ? `<div class="callout bad">Claude's reply was not clean
        JSON, so the text below is its raw answer. Read it before posting.</div>` : ''}
      <div class="annPanes">
        <section aria-labelledby="anPrevH">
          <h4 id="anPrevH" class="gwH">How it will read</h4>
          <div class="paperFrame canvasPage" id="anPreview"></div>
        </section>
        <section>
          <label class="annLabel">Subject
            <input type="text" id="anTitle" value="${esc(d.title || '')}">
          </label>
          <label class="annLabel">Message
            <span>plain sentences. The preview on the left is the Canvas page.</span>
            <textarea id="anBody" rows="9">${esc(d.message || '')}</textarea>
          </label>
        </section>
      </div>
      <div class="callout" id="anGate">${d.writes_enabled
        ? 'Posting puts this in front of every student in the course. You will be '
          + 'asked to confirm once more first.'
        : 'Canvas writes are locked off in config.json, so <b>Post</b> is '
          + 'disabled. Copy still works.'}</div>
      <div class="foot">
        <button class="btn" id="anCopy">Copy</button>
        <button class="btn" id="anRedo">Draft again</button>
        <span class="spacer"></span>
        <button class="btn" id="anClose">Close</button>
        <button class="btn danger" id="anPost" ${d.writes_enabled ? '' : 'disabled'}
          >Post to Canvas…</button>
      </div>
    </div></div>`;
  const refresh = () => paintAnnouncePreview($('#anTitle'), $('#anBody'),
    $('#anPreview'), d.brand, d.look);
  refresh();
  $('#anTitle').oninput = refresh;
  $('#anBody').oninput = refresh;
  $('#anClose').onclick = () => { host.innerHTML = ''; };
  $('#anRedo').onclick = () => openAnnounce(d.course_id, d.assignment_id);
  $('#anCopy').onclick = () => {
    navigator.clipboard.writeText(`${$('#anTitle').value}\n\n${$('#anBody').value}`)
      .then(() => setStatus('announcement copied', 'ok'))
      .catch(() => setStatus('could not copy', 'err'));
  };
  $('#anPost').onclick = () => {
    const title = $('#anTitle').value.trim();
    const message = $('#anBody').value.trim();
    if (!title || !message) { setStatus('needs a subject and a message', 'err'); return; }
    runJobConfirmed('Posting the announcement',
      token => api('/schedule/apply', { body: { dry_run: false, confirm: token,
        operations: [{ op: 'announce', course_id: d.course_id, title,
          message: wrapAnnouncement(message, d.brand, d.look) }] } }),
      r => {
        $('#modalHost').innerHTML = '';
        const ok = (r.applied || []).length;
        setStatus(ok ? 'announcement posted' :
          'not posted: ' + ((r.failed || [])[0]|| {}).error, ok ? 'ok' : 'err');
      });
  };
  $('#anTitle').focus();
}

/* -------------------------------------------------------- typed instruction */
const INSTRUCTION_EXAMPLES = [
  'unlock my Test 1 for Game Theory today at 8am',
  'push the College Algebra 1.1 homework to Friday at 11:59pm',
  'publish the Week 2 quiz in Game Theory',
  'close Test 2 in 3D Game Engine tonight and tell the class',
];

function openInstruct() {
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Tell it what to change</h3>
      <div class="sub">Plain sentences about the assignments on this schedule.
        It shows you a plan first and changes nothing until you say so.</div>
      <textarea id="inText" rows="3" placeholder="unlock my test today"></textarea>
      <div class="inExamples">${INSTRUCTION_EXAMPLES.map(x =>
        `<button class="chip" data-eg="${esc(x)}">${esc(x)}</button>`).join('')}</div>
      <p class="inNote">It can set the <b>opens</b>, <b>due</b> and <b>closes</b>
        dates, <b>publish</b> or <b>unpublish</b> an assignment, and <b>post an
        announcement</b>. It cannot change a grade, change points, or delete
        anything.</p>
      <div id="inResult"></div>
      <div class="foot">
        <span class="spacer"></span>
        <button class="btn" id="inCancel">Cancel</button>
        <button class="btn ai" id="inPlan">Work out a plan</button>
      </div>
    </div></div>`;
  $('#inCancel').onclick = () => { host.innerHTML = ''; };
  host.querySelectorAll('[data-eg]').forEach(el => {
    el.onclick = () => { $('#inText').value = el.dataset.eg; $('#inText').focus(); };
  });
  const go = () => {
    const instruction = $('#inText').value.trim();
    if (!instruction) { setStatus('type what you want changed', 'err'); return; }
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const now_local = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${
      pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
    runJob('Working out what to change',
      () => api('/schedule/instruct', { body: {
        instruction, now_local,
        zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        term: S.sched && S.sched.term } }),
      plan => { $('#modalHost').innerHTML = ''; showPlan(plan); });
  };
  $('#inPlan').onclick = go;
  $('#inText').onkeydown = ev => {
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); go(); }
  };
  $('#inText').focus();
}

function showPlan(p) {
  const host = $('#modalHost');
  const ops = p.operations || [];
  const rowFor = (op, i) => {
    const changes = Object.entries(op.dates || {}).map(([key, value]) => {
      const words = { unlock_at: 'opens', due_at: 'due', lock_at: 'closes' };
      const before = (op.before || {})[key];
      return `<div class="planChange"><span class="planField">${words[key] || key}</span>
        <span class="planWas">${prettyUtc(before)}</span>
        <span class="planArrow">→</span>
        <b>${esc(prettyLocal(value))}</b></div>`;
    }).join('');
    return `<label class="planOp">
      <input type="checkbox" class="planPick" data-i="${i}" checked>
      <span class="planBody">
        <span class="planHead">${esc(op.describe || '')}</span>
        ${changes}
        ${op.op === 'announce' ? `<span class="planAnn"><b>${esc(op.title)}</b>
          <span>${esc(op.message)}</span></span>` : ''}
        ${op.why ? `<span class="planWhy">${esc(op.why)}</span>` : ''}
      </span></label>`;
  };

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>${ops.length ? 'Check this before it happens' : 'Nothing to do'}</h3>
      <div class="sub">you asked: “${esc(p.instruction || '')}”${p.model
        ? ' · read by ' + esc(p.model) : ''}${p.cost_usd
        ? ' · $' + Number(p.cost_usd).toFixed(3) : ''}</div>

      ${p.understood ? `<p class="planUnderstood">${esc(p.understood)}</p>` : ''}
      ${p.refused ? `<div class="callout bad"><b>Not doing that.</b>
        ${esc(p.refused)}</div>` : ''}
      ${(p.questions || []).length ? `<div class="callout"><b>It needs to know:</b>
        <ul class="planQs">${p.questions.map(q => `<li>${esc(q)}</li>`).join('')}</ul>
        Answer in the box and try again.</div>` : ''}

      ${ops.length ? `<div class="planList">${ops.map(rowFor).join('')}</div>` : ''}

      ${(p.rejected || []).length ? `<details class="planRejected">
        <summary>${p.rejected.length} thing(s) it suggested were not allowed</summary>
        <ul>${p.rejected.map(r => `<li>${esc(r.why)}${r.raw
          ? ` <span class="muted">${esc(r.raw)}</span>` : ''}</li>`).join('')}</ul>
      </details>` : ''}

      ${ops.length ? `<div class="callout" id="planGate">${p.writes_enabled
        ? 'These are live courses. Check the dates above, then apply — there is '
          + 'one more confirmation after this.'
        : 'Canvas writes are locked off in config.json, so <b>Apply</b> is '
          + 'disabled. <b>Dry run</b> works either way and changes nothing.'}</div>` : ''}

      <div class="foot">
        <button class="btn" id="planRedo">Change the wording</button>
        <span class="spacer"></span>
        <button class="btn" id="planClose">Close</button>
        ${ops.length ? `<button class="btn" id="planDry">Dry run</button>
        <button class="btn danger" id="planApply" ${p.writes_enabled ? '' : 'disabled'}
          >Apply…</button>` : ''}
      </div>
    </div></div>`;
  $('#planClose').onclick = () => { host.innerHTML = ''; };
  $('#planRedo').onclick = () => { openInstruct(); $('#inText').value = p.instruction || ''; };

  const picked = () => [...host.querySelectorAll('.planPick')]
    .filter(c => c.checked)
    .map(c => {
      const op = ops[+c.dataset.i];
      if (op.op !== 'set_dates') return op;
      // The local wall clock the model returned, converted here where the
      // timezone is actually known.
      const utc = {};
      Object.entries(op.dates || {}).forEach(([key, value]) => {
        utc[key] = value === null ? null : localToUtc(value);
      });
      return { ...op, dates_utc: utc };
    });

  const send = (dry) => {
    const operations = picked();
    if (!operations.length) { setStatus('nothing ticked', 'err'); return; }
    runJobConfirmed(dry ? 'Dry run' : 'Applying the changes',
      token => api('/schedule/apply', { body: { dry_run: dry, operations,
        confirm: token, term: S.sched && S.sched.term } }),
      async r => {
        $('#modalHost').innerHTML = '';
        showApplyResult(r);
        if (!dry && (r.applied || []).length) {
          S.sched = await api('/schedule' + (S.sched && S.sched.term
            ? '?term=' + encodeURIComponent(S.sched.term) : ''));
          renderSchedule();
        }
      },
      { title: `Apply ${operations.length} change(s) to your live courses?`,
        verb: 'Yes, apply them',
        note: operations.map(o => '· ' + o.describe).join('\n') });
  };
  const dry = $('#planDry'); if (dry) dry.onclick = () => send(true);
  const apply = $('#planApply'); if (apply) apply.onclick = () => send(false);
}

function showApplyResult(r) {
  const host = $('#modalHost');
  const rows = (list, cls) => list.map(x =>
    `<li class="${cls}">${esc(x.describe || '')}${x.error
      ? ` — <span class="muted">${esc(x.error)}</span>` : ''}${x.sends
      ? ` <span class="muted">sends ${esc(JSON.stringify(x.sends))}</span>` : ''}</li>`
  ).join('');
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>${r.dry_run ? 'Dry run — nothing was changed'
        : (r.ok ? 'Done' : 'Finished with problems')}</h3>
      <div class="sub">${r.dry_run
        ? 'This is exactly what would be sent to Canvas.'
        : `${(r.applied || []).length} applied, ${(r.failed || []).length} failed`}</div>
      <ul class="applyList">
        ${rows(r.planned || [], 'planned')}
        ${rows(r.applied || [], 'done')}
        ${rows(r.failed || [], 'bad')}
      </ul>
      ${(r.rejected || []).length ? `<div class="callout bad">Not allowed:
        ${r.rejected.map(x => esc(x.why)).join('; ')}</div>` : ''}
      ${r.error ? `<div class="callout bad">${esc(r.error)}</div>` : ''}
      <div class="foot"><span class="spacer"></span>
        <button class="btn" id="apClose">Close</button></div>
    </div></div>`;
  $('#apClose').onclick = () => { host.innerHTML = ''; };
}

/* ------------------------------------------------- assignment description */
/* Clicking a row in the schedule opens what the assignment actually asks for.
   Reading the prompt is what you want from a schedule; grading is the step
   after, so it is a button in here rather than the thing a click does.

   The HTML arrives already sanitised by courseforge/htmlclean.py: a real
   Canvas description carries a remote <script> and stylesheet, and neither has
   any business running inside this page. */
async function openScheduleItem(courseId, assignmentId) {
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal wide descModal">
      <h3 id="dsTitle">Loading…</h3>
      <div class="sub" id="dsMeta"></div>
      <div class="descBody" id="dsBody"><p class="descWait">
        <span class="spin"></span>reading this assignment from Canvas…</p></div>
      <div class="foot" id="dsFoot">
        <button class="btn" id="dsClose">Close</button>
      </div>
    </div></div>`;
  $('#dsClose').onclick = () => { host.innerHTML = ''; };

  let d;
  try {
    d = await api(`/schedule/item/${encodeURIComponent(courseId)}/${
      encodeURIComponent(assignmentId)}`);
  } catch (err) {
    $('#dsTitle').textContent = 'Could not load this assignment';
    $('#dsBody').innerHTML = `<div class="callout bad">${esc(err.message)}</div>`;
    return;
  }
  if (!$('#dsTitle')) return;              // closed while it was loading

  const due = d.due_at ? new Date(d.due_at) : null;
  const bits = [
    (d.course_label || d.course_code)
      ? `<span class="cbadge" style="background:${esc(schedColour(d.course_id))}"
           title="${esc(d.course_name || '')}">${
           esc(d.course_label || d.course_code)}</span>` : '',
    due ? `due ${esc(fmtDay(due))} at ${esc(fmtTime(due))}` : 'no due date',
    d.points == null ? '' : `${num(d.points)} points`,
    `<span class="k ${KIND_CLASS[d.kind_label] || 'kind-asn'}">${esc(d.kind_label)}</span>`,
    d.proctored ? '<span class="flag">⚠ PROCTORED</span>' : '',
    d.published ? '' : '<span class="pill warn">unpublished</span>',
    d.needs_grading ? `<span class="tgBadge">${d.needs_grading}</span> waiting to grade` : '',
  ].filter(Boolean);

  $('#dsTitle').textContent = d.name;
  $('#dsMeta').innerHTML = bits.join('<span class="dsDot">·</span>');

  const submits = (d.submission_types || []).map(t => t.replace(/_/g, ' ')).join(', ');
  $('#dsBody').innerHTML = `
    ${d.has_description
      ? `<div class="canvasHtml">${d.description}</div>`
      : `<p class="descWait">This assignment has no description in Canvas.</p>`}
    ${(d.rubric || []).length ? `<details class="descRubric" open>
      <summary>Rubric (${d.rubric.length} rows, ${num(
        d.rubric.reduce((a, r) => a + (+r.points || 0), 0))} points)</summary>
      <table class="descRubricTable"><tbody>${d.rubric.map(r => `<tr>
        <td>${esc(r.label)}${r.detail && r.detail !== r.label
          ? `<div class="muted">${esc(r.detail)}</div>` : ''}</td>
        <td class="pts">${r.points == null ? '' : num(r.points)}</td>
      </tr>`).join('')}</tbody></table>
    </details>` : ''}
    ${submits ? `<p class="descSubmit">Submitted as: ${esc(submits)}${
      d.allowed_attempts && d.allowed_attempts > 0
        ? ` · ${d.allowed_attempts} attempt(s) allowed` : ''}</p>` : ''}`;

  $('#dsFoot').innerHTML = `
    <a class="btn ai" href="#/c/${esc(d.course_id)}/a/${esc(d.assignment_id)}"
       id="dsGrade">${d.needs_grading ? `Grade ${d.needs_grading} waiting`
         : 'Open in the grader'}</a>
    ${d.quiz_id ? `<button class="btn" id="dsEdit"
       title="Dates, password, timing, accommodations">Edit test…</button>` : ''}
    <button class="btn" id="dsAnnounce">Announcement…</button>
    ${d.url ? `<a class="btn" href="${esc(d.url)}" target="_blank"
       rel="noopener">Open in Canvas ↗</a>` : ''}
    <span class="spacer"></span>
    <span class="descFetched">read from Canvas just now</span>
    <button class="btn" id="dsClose2">Close</button>`;
  $('#dsClose2').onclick = () => { host.innerHTML = ''; };
  $('#dsGrade').onclick = () => { host.innerHTML = ''; };
  $('#dsAnnounce').onclick = () => openAnnounce(d.course_id, d.assignment_id);
  const de = $('#dsEdit');
  if (de) de.onclick = () => openTestAdmin(d.course_id, d.quiz_id);
}

/* The colour the schedule already assigned this course, so the badge in the
   reader matches the row it was opened from. */
function schedColour(courseId) {
  const found = ((S.sched && S.sched.courses) || [])
    .find(c => String(c.course_id) === String(courseId));
  return (found && found.colour) || 'var(--muted)';
}

/* ------------------------------------------------------------ remind missing */
/* Canvas's own "Message Students Who -> Haven't submitted yet", from here. One
   generic note, sent privately to every student with nothing turned in.

   The list is read live from Canvas, never from the last sync. This is offered
   from the term schedule, where an assignment may never have been synced at
   all, and a stale list means chasing someone who did hand their work in. The
   server re-reads it again at send time for the same reason. */
async function openRemind(courseId, assignmentId, only) {
  const host = $('#modalHost');
  const scope = (only && only.length) ? only.map(String) : null;
  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Remind students with nothing turned in</h3>
      <div class="sub" id="rmSub"><span class="spin"></span>reading the roster from Canvas…</div>
      <div id="rmBody"></div>
      <div class="foot" id="rmFoot"><button class="btn" id="rmClose">Close</button></div>
    </div></div>`;
  $('#rmClose').onclick = () => { host.innerHTML = ''; };

  let d;
  try {
    d = await api(`/a/${encodeURIComponent(courseId)}/${encodeURIComponent(assignmentId)}/missing`);
  } catch (err) {
    if (!$('#rmSub')) return;
    $('#rmSub').textContent = 'Could not read the roster.';
    $('#rmBody').innerHTML = `<div class="callout bad">${esc(err.message)}</div>`;
    return;
  }
  if (!$('#rmSub')) return;                     // closed while it was loading

  const rows = scope ? (d.missing || []).filter(r => scope.includes(String(r.user_id)))
                     : (d.missing || []);
  const due = d.due_phrase ? ` · due ${esc(d.due_phrase)}` : '';
  $('#rmSub').innerHTML = `<b>${esc(d.name)}</b>${due}`;

  if (!rows.length) {
    $('#rmBody').innerHTML = `<div class="callout">${scope
      ? 'This student has turned something in after all, so there is nothing to chase.'
      : `Everyone on the roster has turned something in for this. Nothing to send.`}</div>`;
    return;
  }

  $('#rmSub').innerHTML = `<b>${esc(d.name)}</b>${due} · <b>${rows.length}</b> of ${
    d.roster} student(s) have turned nothing in.${d.past_due ? ''
    : ' <span class="pill warn">this is not past due yet</span>'}`;

  $('#rmBody').innerHTML = `
    <div class="rmWho">${rows.map(r => `
      <label class="tick"><input type="checkbox" class="rmPick" value="${esc(r.user_id)}" checked>
        <span>${esc(studentLabel(r))}${r.graded
          ? ` <span class="rmNote">already scored ${num(r.score)}</span>` : ''}</span>
      </label>`).join('')}</div>
    <label class="rmField">Subject
      <input type="text" id="rmSubject" value="${esc(d.subject)}"></label>
    <label class="rmField">Message
      <textarea id="rmText" rows="7" spellcheck="true">${esc(d.body)}</textarea></label>
    <div class="callout">Every student gets their own private copy in the Canvas
      Inbox. Nobody can see who else was written to, and there is no reply-all.</div>`;

  $('#rmFoot').innerHTML = `
    <button class="btn sm" id="rmAll">Select all</button>
    <button class="btn sm" id="rmNone">Clear</button>
    <span class="spacer"></span>
    <button class="btn" id="rmCancel">Cancel</button>
    <button class="btn primary" id="rmGo">Send the message…</button>`;

  const picks = () => [...host.querySelectorAll('.rmPick')];
  const chosen = () => picks().filter(el => el.checked).map(el => el.value);
  const paint = () => {
    const n = chosen().length;
    $('#rmGo').disabled = !n;
    $('#rmGo').textContent = n ? `Send to ${n} student${n === 1 ? '' : 's'}…`
                               : 'Nobody selected';
  };
  picks().forEach(el => { el.onchange = paint; });
  $('#rmAll').onclick = () => { picks().forEach(el => { el.checked = true; }); paint(); };
  $('#rmNone').onclick = () => { picks().forEach(el => { el.checked = false; }); paint(); };
  $('#rmCancel').onclick = () => { host.innerHTML = ''; };
  paint();

  $('#rmGo').onclick = () => {
    const ids = chosen();
    if (!ids.length) return;
    const subject = $('#rmSubject').value.trim();
    const message = $('#rmText').value.trim();
    if (!message) { setStatus('the message is empty', 'err'); return; }
    runJobConfirmed(`Messaging ${ids.length} student(s)`,
      token => api(`/a/${courseId}/${assignmentId}/remind`,
        { body: { only: ids, subject, body: message, confirm: token } }),
      r => {
        $('#modalHost').innerHTML = '';
        const bad = (r.failed || []).reduce((a, f) => a + (f.names || []).length, 0);
        setStatus(`messaged ${r.count} student(s)` + (bad ? `, ${bad} failed` : ''),
          bad ? 'err' : 'ok');
      },
      { title: `Send this to ${ids.length} student${ids.length === 1 ? '' : 's'}?`,
        verb: 'Yes, send it',
        note: 'Each gets a private Canvas Inbox message. A sent message cannot be '
              + 'unsent, and students are usually notified by email.',
        // Cancelling rebuilds the dialog from markup alone, which would leave it
        // with no handlers, so put a live one back.
        onCancel: () => openRemind(courseId, assignmentId, only) });
  };
}

/* ------------------------------------------------------------- schedule */
/* Every dated assignment across the term, grouped by week.

   Weeks are grouped HERE, in the browser, not on the server. Canvas hands back
   due dates in UTC and an 11:59 PM Central deadline arrives as 04:59Z the next
   day: bucketing that server-side without a timezone database would file a
   Sunday-night deadline under the following week, which is the one mistake this
   view cannot afford. The browser already knows its own zone. */
const KIND_CLASS = {
  FINAL: 'kind-exam', EXAM: 'kind-exam', TEST: 'kind-test', QUIZ: 'kind-quiz',
  PRACTICE: 'kind-prac', DISCUSSION: 'kind-disc', UNGRADED: 'kind-ung',
  ASSIGNMENT: 'kind-asn',
};
const DAY_MS = 86400000;

function startOfWeek(d) {                    // Monday, local time
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return x;
}
const weekKey = d => startOfWeek(d).toISOString().slice(0, 10);
const fmtRange = (a, b) => `${a.toLocaleDateString([], { month: 'short', day: 'numeric' })} – ${
  b.toLocaleDateString([], { month: 'short', day: 'numeric' })}`;

/* Not `ago`: that name is taken further down by the version that reads an ISO
   timestamp, and two declarations of one name in this file means the later one
   wins everywhere. Fed seconds, it turned 2536 into a date in 1970 and the
   footer said "Canvas synced 2954 weeks ago". */
function agoSeconds(seconds) {
  if (seconds == null) return 'never';
  if (seconds < 60) return 'just now';
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} hour${h > 1 ? 's' : ''} ago`;
  const d = Math.round(h / 24);
  return `${d} day${d > 1 ? 's' : ''} ago`;
}

async function openSchedule(refresh) {
  showView('schedule');
  crumbs([{ label: 'Courses', href: '#/' }, { label: 'Schedule' }]);
  $('#headerActions').innerHTML = '';
  if (!S.sched) {
    $('#schedBody').innerHTML = '<p class="schedNote">Loading…</p>';
    $('#schedFoot').textContent = '';
  }
  try {
    S.sched = await api('/schedule' + (S.term && S.term !== '__all'
      ? '?term=' + encodeURIComponent(S.term) : ''));
  } catch (err) {
    $('#schedBody').innerHTML = `<p class="schedNote">Could not load: ${esc(err.message)}</p>`;
    return;
  }
  S.schedFilter = S.schedFilter || { off: new Set(), hidePast: true, examOnly: false, gradeOnly: false };
  renderSchedule();
  // "Fetch directly from Canvas to stay up to date": the cache paints first so
  // the view is instant, then a stale copy quietly refreshes behind it.
  if (refresh || S.sched.stale) refreshSchedule(!S.sched.items.length);
}

function refreshSchedule(blocking) {
  const body = { term: S.sched && S.sched.term ? S.sched.term : null };
  const done = async () => {
    S.sched = await api('/schedule' + (body.term ? '?term=' + encodeURIComponent(body.term) : ''));
    renderSchedule();
  };
  if (blocking) {
    runJob('Reading this term from Canvas',
      () => api('/schedule/refresh', { body }), () => { $('#modalHost').innerHTML = ''; done(); });
    return;
  }
  // Quiet background refresh: the footer says it is happening.
  const foot = $('#schedFoot');
  if (foot) foot.dataset.busy = '1';
  renderSchedFoot();
  api('/schedule/refresh', { body }).then(({ job }) => {
    const poll = async () => {
      const info = await api('/jobs/' + job);
      if (info.state === 'running') return setTimeout(poll, 900);
      if (foot) delete foot.dataset.busy;
      if (info.state === 'error') { renderSchedFoot(info.error); return; }
      await done();
    };
    poll();
  }).catch(err => {
    if (foot) delete foot.dataset.busy;
    renderSchedFoot(err.message);
  });
}

function schedPicked(it) {
  const f = S.schedFilter;
  if (f.off.has(it.course_code)) return false;
  if (f.examOnly && !it.exam) return false;
  if (f.gradeOnly && !it.needs_grading) return false;
  return true;
}

function schedRows() {
  const sc = S.sched, f = S.schedFilter;
  const now = Date.now();
  return (sc.items || []).map(it => {
    const when = new Date(it.due_at);
    return { ...it, when, past: when.getTime() < now };
  }).filter(it => {
    if (!schedPicked(it)) return false;
    // "Waiting to grade" is a work queue, not a calendar, and most of the
    // backlog is behind you: asking for it overrides hiding past due, or the
    // list would show a handful of items while 200 sat unseen.
    if (f.hidePast && it.past && !f.gradeOnly) return false;
    return true;
  });
}

function scheduleStats(items) {
  /* Same arithmetic as schedule.stats in Python: remaining / next 7 / exams
     are still-due; waiting to grade includes the past-due backlog. */
  const now = Date.now();
  const soon = now + 7 * DAY_MS;
  let remaining = 0, upcoming = 0, exams_left = 0, waiting = 0;
  for (const it of items) {
    const t = new Date(it.due_at).getTime();
    const past = !isNaN(t) && t < now;
    if (!past) {
      remaining++;
      if (!isNaN(t) && t <= soon) upcoming++;
      if (it.exam) exams_left++;
    }
    waiting += (+it.needs_grading || 0);
  }
  return { total: items.length, remaining, next_7_days: upcoming,
           exams_left, needs_grading: waiting };
}

function schedPad(n) { return String(n).padStart(2, '0'); }
function schedIso(d) {
  return d.getFullYear() + '-' + schedPad(d.getMonth() + 1) + '-' + schedPad(d.getDate());
}
function schedLocalDay(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return schedIso(d);
}

async function loadTermBreaks() {
  const term = (S.sched && S.sched.term) || '';
  const mine = term;
  try {
    const data = await api('/term-calendar?term=' + encodeURIComponent(term));
    if (!S.schedCalOn || ((S.sched && S.sched.term) || '') !== mine) return;
    S.termBreaks = data;
  } catch (err) {
    if (!S.schedCalOn) return;
    S.termBreaks = { term, days: [], live: false, note: firstLine(err.message) };
  }
  renderSchedule();
}

function renderTermCalendar() {
  const sc = S.sched || {};
  const pack = S.termBreaks || {};
  const breaks = {};
  (pack.days || []).forEach(row => {
    if (!row || !row.date) return;
    if (!breaks[row.date]) breaks[row.date] = [];
    if (!breaks[row.date].includes(row.name)) breaks[row.date].push(row.name);
  });
  const byDay = {};
  (sc.items || []).filter(schedPicked).forEach(it => {
    if (S.schedFilter.hidePast && new Date(it.due_at).getTime() < Date.now()
        && !S.schedFilter.gradeOnly) return;
    if (S.schedFilter.examOnly && !it.exam) return;
    if (S.schedFilter.gradeOnly && !it.needs_grading) return;
    const key = schedLocalDay(it.due_at);
    if (!key) return;
    if (!byDay[key]) byDay[key] = [];
    byDay[key].push(it);
  });
  const keys = Object.keys(byDay).concat(Object.keys(breaks)).sort();
  const today = schedIso(new Date());
  let startKey = keys[0] || today;
  let endKey = keys[keys.length - 1] || today;
  if (S.schedMonth && S.schedMonth >= startKey.slice(0, 7) && S.schedMonth <= endKey.slice(0, 7)) {
    /* keep the month the person is looking at */
  } else if (today >= startKey && today <= endKey) {
    S.schedMonth = today.slice(0, 7);
  } else {
    S.schedMonth = startKey.slice(0, 7);
  }
  const month = S.schedMonth;
  const [y, m] = month.split('-').map(Number);
  const firstDow = new Date(y, m - 1, 1).getDay();
  const count = new Date(y, m, 0).getDate();
  const label = new Date(y, m - 1, 1).toLocaleDateString([], { month: 'long', year: 'numeric' });
  let cells = '';
  for (let i = 0; i < firstDow; i++) cells += '<div></div>';
  for (let d = 1; d <= count; d++) {
    const iso = month + '-' + schedPad(d);
    const names = breaks[iso] || [];
    const items = byDay[iso] || [];
    const shown = items.slice(0, 3);
    const more = items.length - shown.length;
    const cls = ['scDay'];
    if (iso === today) cls.push('today');
    if (names.length) cls.push('break');
    cells += `<div class="${cls.join(' ')}">
      <span class="scNum">${d}</span>
      ${names.map(name => `<span class="scBreak">${esc(name)}</span>`).join('')}
      ${shown.map(it => `<button type="button" class="scItem" data-course="${esc(it.course_id)}"
        data-asg="${esc(it.assignment_id)}" title="${esc(it.name)}">
        <i style="background:${esc(it.colour || '#888')}"></i>${esc(it.name)}</button>`).join('')}
      ${more > 0 ? `<span class="scMore">+${more} more</span>` : ''}
    </div>`;
  }
  const prev = schedIso(new Date(y, m - 2, 1)).slice(0, 7);
  const next = schedIso(new Date(y, m, 1)).slice(0, 7);
  const host = $('#schedBody');
  host.innerHTML = `<div class="scCalHead">
      <button class="btn sm" type="button" id="scPrev">Previous</button>
      <h2>${esc(label)}</h2>
      <span class="spacer"></span>
      <button class="btn sm" type="button" id="scNext">Next</button>
    </div>
    <p class="hint">${esc(pack.note || 'Loading campus breaks…')}</p>
    <div class="scDow"><span>Sun</span><span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span></div>
    <div class="scCal" role="group" aria-label="${esc(label)}">${cells}</div>`;
  const shift = (ym) => { S.schedMonth = ym; renderTermCalendar(); };
  $('#scPrev').onclick = () => shift(prev);
  $('#scNext').onclick = () => shift(next);
  host.querySelectorAll('.scItem').forEach(btn => {
    btn.onclick = () => openScheduleItem(btn.dataset.course, btn.dataset.asg);
  });
}

function renderSchedule() {
  const sc = S.sched;
  const f = S.schedFilter;
  const picked = (sc.items || []).filter(schedPicked);
  const st = scheduleStats(picked);
  const nAll = (sc.courses || []).length;
  const nOn = (sc.courses || []).filter(c => !f.off.has(c.code)).length;
  const term = sc.term || 'this term';
  const where = nAll === 0 ? 'no courses'
    : nOn === nAll ? (nAll === 1 ? '1 course' : nAll + ' courses')
    : nOn + ' of ' + nAll + ' courses';
  $('#schedTitle').textContent = 'Term schedule';
  $('#schedSub').innerHTML = `${esc(term)} · dated assignments in ${esc(where)} ·
    times in your local timezone`;

  $('#schedStats').innerHTML = [
    [st.remaining, 'Still due this term',
     'Dated items not yet due, in the courses selected below'],
    [st.next_7_days, 'Due in the next 7 days',
     'Still-due items in the next seven days, in the courses selected below'],
    [st.exams_left, 'Tests &amp; exams remaining',
     'Tests, exams and finals still due in the courses selected below'],
    [st.needs_grading, 'Waiting to grade',
     'Submissions Canvas says are waiting, including past due, in the courses selected below'],
    [st.total, 'Dated items this term',
     'Every dated item in the courses selected below, past and upcoming'],
  ].map(([n, label, hint]) => `<div class="schedStat" title="${hint}"><b>${
      n == null ? '—' : n}</b><span>${label}</span></div>`).join('');

  $('#schedControls').innerHTML = `
    <div class="schedChips">
      <span class="ctlLabel">Courses</span>
      <button class="chip" id="scAll" aria-pressed="${!f.off.size}">All</button>
      ${(sc.courses || []).map(c => `<button class="chip cchip ${
        f.off.has(c.code) ? 'off' : ''}" data-code="${esc(c.code)}"
        title="${esc(c.name)}"><span class="cdot" style="background:${esc(c.colour)}"></span>
        ${esc(c.label || c.code)} <span class="cnum">(${c.count}${
          c.needs_grading ? ` · ${c.needs_grading} to grade` : ''})</span></button>`).join('')}
    </div>
    <div class="schedChips row2">
      <div class="ctlGroup">
        <span class="ctlLabel">Actions</span>
        <button class="btn sm ai" id="scInstruct"
          title="Say what you want changed, in plain words">Tell it what to change…</button>
        <button class="btn sm" id="scCal" type="button" aria-pressed="${!!S.schedCalOn}"
          title="Month view of this term, with campus breaks from the college calendar">Calendar</button>
      </div>
      <div class="ctlGroup ctlShow">
        <span class="ctlLabel">Show</span>
        <button class="chip" id="scPast" aria-pressed="${!!f.hidePast}">${
          f.hidePast ? 'Hiding past due' : 'Showing past due'}</button>
        <button class="chip" id="scExam" aria-pressed="${!!f.examOnly}">Tests &amp; exams only</button>
        <button class="chip" id="scGrade" aria-pressed="${!!f.gradeOnly}"
          title="Everything with submissions waiting, past due included">Waiting to grade</button>
      </div>
    </div>`;
  $('#scAll').onclick = () => {
    if (f.off.size) f.off.clear();
    else (sc.courses || []).forEach(c => f.off.add(c.code));
    renderSchedule();
  };
  $('#schedControls').querySelectorAll('.cchip').forEach(el => {
    el.onclick = () => {
      const code = el.dataset.code;
      if (f.off.has(code)) f.off.delete(code); else f.off.add(code);
      renderSchedule();
    };
  });
  $('#scInstruct').onclick = openInstruct;
  $('#scCal').onclick = () => {
    S.schedCalOn = !S.schedCalOn;
    if (!S.schedCalOn) { renderSchedule(); return; }
    $('#schedBody').innerHTML = '<p class="schedNote">Loading the college calendar…</p>';
    loadTermBreaks();
  };
  $('#scPast').onclick = () => { f.hidePast = !f.hidePast; renderSchedule(); };
  $('#scExam').onclick = () => { f.examOnly = !f.examOnly; renderSchedule(); };
  $('#scGrade').onclick = () => { f.gradeOnly = !f.gradeOnly; renderSchedule(); };

  if (S.schedCalOn) {
    renderTermCalendar();
    renderSchedFoot();
    return;
  }

  const rows = schedRows();
  if (!rows.length) {
    $('#schedBody').innerHTML = `<p class="schedNote">${(sc.items || []).length
      ? 'Nothing matches these filters.'
      : 'No dated assignments cached yet. Use Refresh to read this term from Canvas.'}</p>`;
    renderSchedFoot();
    return;
  }

  const weeks = new Map();
  rows.forEach(it => {
    const key = weekKey(it.when);
    if (!weeks.has(key)) weeks.set(key, []);
    weeks.get(key).push(it);
  });
  const thisWeek = weekKey(new Date());

  $('#schedBody').innerHTML = [...weeks.entries()].sort((a, b) => a[0] < b[0] ? -1 : 1)
    .map(([key, items]) => {
      const monday = new Date(key + 'T00:00:00');
      const sunday = new Date(monday.getTime() + 6 * DAY_MS);
      const pts = items.reduce((a, b) => a + (+b.points || 0), 0);
      const wait = items.reduce((a, b) => a + (b.needs_grading || 0), 0);
      // Only the assessments in this week, and only the ones the current
      // filters are actually showing: the button acts on what is on screen.
      const exams = items.filter(i => i.exam);
      return `<section class="week ${key === thisWeek ? 'now' : ''}">
        <div class="whead">
          <h2>Week of ${monday.toLocaleDateString([], { month: 'short', day: 'numeric' })}${
            key === thisWeek ? ' <span class="nowTag">this week</span>' : ''}</h2>
          <div class="wmeta">${fmtRange(monday, sunday)}
            <span class="load">${items.length} item${items.length > 1 ? 's' : ''} ·
              ${num(pts)} pts${wait ? ` · ${wait} to grade` : ''}</span>
            ${exams.length ? `<button class="weekRemind" data-week="${key}"
              title="Draft a reminder announcement for each of these ${
                exams.length} assessment(s)">Remind ${exams.length} test${
                exams.length > 1 ? 's' : ''}</button>` : ''}</div>
        </div>
        <table><thead><tr><th>Due</th><th>Course</th><th>Item</th><th>Type</th>
          <th class="tg">To grade</th><th class="pts">Pts</th></tr></thead><tbody>
        ${items.map(it => schedRow(it)).join('')}
        </tbody></table></section>`;
    }).join('');

  $('#schedBody').querySelectorAll('.itemName').forEach(el => {
    el.onclick = ev => {
      // Middle-click, Ctrl/Cmd-click and "open in new tab" must follow the
      // href to the assignment. preventDefault on every click is why those
      // landed on the home page: the name used to be href="#".
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button) return;
      ev.preventDefault();
      const [cid, aid] = el.dataset.open.split(':');
      openScheduleItem(cid, aid);
    };
  });
  $('#schedBody').querySelectorAll('[data-ann]').forEach(el => {
    el.onclick = ev => {
      ev.preventDefault();
      const [cid, aid] = el.dataset.ann.split(':');
      openAnnounce(cid, aid);
    };
  });
  $('#schedBody').querySelectorAll('[data-remind]').forEach(el => {
    el.onclick = ev => {
      ev.preventDefault();
      const [cid, aid] = el.dataset.remind.split(':');
      openRemind(cid, aid);
    };
  });
  $('#schedBody').querySelectorAll('[data-week]').forEach(el => {
    el.onclick = () => openWeekReminders(el.dataset.week);
  });
  $('#schedBody').querySelectorAll('[data-edit]').forEach(el => {
    el.onclick = ev => {
      ev.preventDefault();
      const [cid, qid] = el.dataset.edit.split(':');
      openTestAdmin(cid, qid);
    };
  });
  renderSchedFoot();

  const now = $('#schedBody .week.now');
  if (now && !S.schedScrolled) { now.scrollIntoView({ block: 'start' }); S.schedScrolled = true; }
}

function schedRow(it) {
  const today = new Date().toDateString() === it.when.toDateString();
  return `<tr class="${it.past ? 'past' : ''} ${today ? 'today' : ''}">
    <td class="daycell"><b>${fmtDay(it.when)}</b><span>${fmtTime(it.when)}</span></td>
    <td><span class="cbadge" style="background:${esc(it.colour)}"
      title="${esc(it.course_name || it.course_code)}">${
        esc(it.course_label || it.course_code)}</span></td>
    <td class="name">
      <a href="#/c/${esc(it.course_id)}/a/${esc(it.assignment_id)}" class="itemName"
         data-open="${esc(it.course_id)}:${esc(it.assignment_id)}"
         title="Read what this assignment asks for. Middle-click opens it in a new tab."
         >${esc(it.name)}</a>
      ${it.url ? `<a class="extLink" href="${esc(it.url)}" target="_blank"
         rel="noopener" title="Open in Canvas">↗</a>` : ''}
      <button class="rowAnnounce" data-ann="${esc(it.course_id)}:${
        esc(it.assignment_id)}"
        title="Draft a reminder announcement about this">announce</button>
      ${it.past ? `<button class="rowAnnounce rowRemind" data-remind="${
        esc(it.course_id)}:${esc(it.assignment_id)}"
        title="Message every student who has turned nothing in">remind</button>` : ''}
      ${it.quiz_id ? `<button class="rowEdit" data-edit="${esc(it.course_id)}:${
        esc(it.quiz_id)}"
        title="Dates, password, timing, accommodations">edit</button>` : ''}
      ${it.proctored ? '<span class="flag">⚠ PROCTORED</span>' : ''}
      ${it.published ? '' : '<span class="pill warn">unpublished</span>'}
    </td>
    <td class="kind"><span class="k ${KIND_CLASS[it.kind_label] || 'kind-asn'}">${
      esc(it.kind_label)}</span></td>
    <td class="tg">${it.needs_grading
      ? `<a class="tgBadge" href="#/c/${esc(it.course_id)}/a/${esc(it.assignment_id)}"
           title="${it.needs_grading} submission(s) Canvas says are waiting">${
             it.needs_grading}</a>`
      : '<span class="muted">—</span>'}</td>
    <td class="pts">${it.points == null ? '—' : num(it.points)}</td>
  </tr>`;
}

function renderSchedFoot(error) {
  const foot = $('#schedFoot');
  if (!foot) return;
  const sc = S.sched || {};
  const busy = foot.dataset.busy === '1';
  foot.innerHTML = `
    <span class="syncLine">
      ${busy ? '<span class="spin"></span>reading Canvas…'
        : `Canvas synced <b>${agoSeconds(sc.age_s)}</b>${sc.fetched_at
            ? ` (${new Date(sc.fetched_at).toLocaleString([], {
                month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })})` : ''}`}
      <button class="btn sm" id="scRefresh" ${busy ? 'disabled' : ''}>Refresh</button>
    </span>
    ${error ? `<span class="syncErr">refresh failed: ${esc(error)}</span>` : ''}
    ${(sc.partial || []).length ? `<span class="syncErr">${sc.partial.length}
      course(s) could not be read: ${sc.partial.map(p => esc(p.code)).join(', ')}</span>` : ''}
    <span class="footNote">Items with no due date in Canvas are not listed.
      Click a name to grade it here, or the arrow to open it in Canvas.
      Auto-refreshes when the cache is over ${sc.max_age_min || 30} minutes old.</span>`;
  const btn = $('#scRefresh');
  if (btn) btn.onclick = () => refreshSchedule(true);
}

/* --------------------------------------------------------------- courses */
/* Canvas file room. A corner chip rather than a panel: it is a number you
   glance at to know you are nowhere near the ceiling, and it earns about that
   much of the page. The detail lives in the tooltip for the day it matters. */
async function renderStorage() {
  const host = $('#storageBar');
  if (!host) return;
  let d;
  try { d = await api('/storage'); }
  catch (_) { host.innerHTML = ''; return; }      // offline: say nothing at all
  if (!$('#storageBar') || !d.quota) return;

  const mb = n => n / 1e6;
  const pct = Math.min(100, Math.max(0, 100 * d.used / d.quota));
  const level = pct >= 90 ? 'full' : pct >= 70 ? 'warm' : '';
  const carried = (d.handoffs || []).length;
  const tip = `Canvas file storage: ${mb(d.used).toFixed(1)} MB of ${
    Math.round(mb(d.quota))} MB used, ${mb(d.quota - d.used).toFixed(0)} MB free.
`
    + (carried
      ? `${carried} handoff file${carried === 1 ? '' : 's'} in ${d.folder}/ using ${
          mb(d.handoff_bytes).toFixed(1)} MB.`
      : `Nothing carried between machines yet; handoffs would live in ${d.folder}/.`);

  // A caption over the bar: on its own, "18 / 600 MB" in a corner does not say
  // what it is counting. No stray whitespace, because the indentation of a
  // template literal becomes text nodes inside the flex row and wraps the chip.
  host.innerHTML = `<span class="storeChip ${level}" title="${esc(tip)}">`
    + `<span class="storeCap">Canvas Storage</span>`
    + `<span class="storeRow">`
    + `<span class="storeTrack"><span class="storeFill" style="width:${pct.toFixed(2)}%">`
    + `</span></span><span>${Math.round(mb(d.used))} / ${Math.round(mb(d.quota))} MB</span>`
    + `</span></span>`;
}

/* The course list. Opens on the course you were last grading in, because that
   is what you came back for most mornings; the rest of the courses and the
   four cross-course tools share the row underneath.

   Everything on this screen is read off this machine. The counts beside a
   course come from the assignment list already cached for it, so a course you
   have never opened says so rather than the page making five Canvas calls to
   tell you something you are about to click into anyway. */
function homeListTools(on) {
  ['btnExpand', 'btnCollapse'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.hidden = !on;
  });
  if (!on) return;
  const expand = document.getElementById('btnExpand');
  const collapse = document.getElementById('btnCollapse');
  if (expand) expand.onclick = () => setAllGlance(true);
  if (collapse) collapse.onclick = () => setAllGlance(false);
}

/* The chevrons on the rows currently showing, including the resume band.
   Other terms keep whatever they had. A course that has never been opened
   has no figures to open. */
function setAllGlance(open) {
  const buttons = document.querySelectorAll('#pickerBody .pickFold, #pickerResume .pickFold');
  const all = glanceMap();
  buttons.forEach(btn => {
    const id = String(btn.dataset.cid || '');
    if (!id) return;
    if (open) all[id] = 1;
    else all[id] = 0;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.setAttribute('aria-label', open ? 'Collapse' : 'Expand');
    const box = btn.closest('.pickRow, .resumeBand');
    if (box) box.classList.toggle('open', open);
  });
  try { localStorage.setItem('cg.glance', JSON.stringify(all)); } catch (_) { /* private mode */ }
}

async function openCourses(refresh) {
  const tb = $('#teachBar'); if (tb) tb.classList.add('hidden');
  // The strip repaints before the new list arrives. Drop the previous
  // term's five so they do not sit under a different term's courses.
  S.behind = null;
  showView('picker');
  crumbs([{ label: 'Courses' }]);
  $('#pickerTitle').textContent = 'Your courses';
  $('#pickerResume').innerHTML = '';
  homeListTools(true);
  $('#btnRefresh').onclick = () => openCourses(true);
  $('#headerActions').innerHTML =
    '<a class="btn" href="#/schedule">Term schedule</a>'
    + '<button class="btn" id="btnToken" title="Replace the saved Canvas token">'
    + 'Canvas token…</button>';
  $('#btnToken').onclick = () => openSetup();
  if (S.health && S.health.canvas && S.health.canvas.reason === 'no_token') {
    $('#pickerHint').textContent = 'Paste your Canvas token to load your courses.';
    return;
  }
  const canvasDown = S.health && S.health.canvas && S.health.canvas.ok === false;
  $('#pickerHint').textContent = canvasDown
    ? 'Canvas did not answer. Looking for a course list already saved on this computer…'
    : 'Reading your courses from Canvas…';

  // A new computer has no saved list, so this wait is Canvas. Say so, and
  // do not leave the hint on "Loading…" if the request never comes back.
  const slow = setTimeout(() => {
    const hint = $('#pickerHint');
    if (hint && hint.textContent.indexOf('Reading your courses') === 0) {
      hint.textContent = 'Still reading your courses from Canvas. The first time on a computer has no saved list.';
    }
  }, 4000);
  const ctrl = new AbortController();
  const kill = setTimeout(() => ctrl.abort(), canvasDown ? 12000 : 40000);
  let picked;
  try {
    picked = await api('/picker' + (refresh ? '?refresh=1' : ''), { signal: ctrl.signal });
  } catch (err) {
    const aborted = err && err.name === 'AbortError';
    const msg = aborted
      ? 'Canvas did not answer with the course list. Wait a moment, then press Refresh.'
      : firstLine(err.message);
    $('#pickerHint').textContent = msg;
    setStatus(msg, 'err');
    return;
  } finally {
    clearTimeout(slow);
    clearTimeout(kill);
  }
  try {
    paintHome(picked);
  } catch (err) {
    const msg = 'Could not draw the course list: ' + firstLine(err.message);
    $('#pickerHint').textContent = msg;
    setStatus(msg, 'err');
  }
}

/* Drawn again after one course is re-read, without rebuilding the whole page. */
function paintHome(picked) {
  S.courses = picked.courses || [];
  S.termInfo = picked.terms || { terms: [] };

  // Default to the term we are actually in; remember an explicit choice.
  if (S.term == null) S.term = S.termInfo.default || '__all';
  const inTerm = c => S.term === '__all' || (c.term_label || '') === S.term;
  let list = S.courses.filter(inTerm);

  /* Only carry on with a course the filter is currently showing. Offering a
     Fall course while someone is looking at Spring would be answering a
     question they did not ask. */
  const resume = picked.resume
    && S.courses.some(c => String(c.id) === String(picked.resume.course_id) && inTerm(c))
    ? picked.resume : null;

  renderResume(resume);
  $('#pickerTitle').textContent = 'Your courses';
  const staleNote = $('#pickerThisPc');
  if (staleNote) staleNote.remove();

  const opts = (S.termInfo.terms || []).map(t =>
    `<option value="${esc(t.label)}"${t.label === S.term ? ' selected' : ''}>${esc(t.label)} (${t.count})</option>`).join('');
  $('#pickerHint').innerHTML =
    `<select class="termSel" id="selTerm" aria-label="Which term">${opts}
       <option value="__all"${S.term === '__all' ? ' selected' : ''}>All terms (${S.courses.length})</option>
     </select>
     <span style="margin-left:10px">${courseCount(list)}</span>`;

  $('#pickerBody').innerHTML = list.length
    ? '<div class="pickList">' + list.map(courseRow).join('') + '</div>'
    : '<p class="hint">No courses in this term.</p>';
  wireCourseTools($('#pickerBody'));
  S.behind = behindShown(picked.behind);
  if (typeof renderBehind === 'function') renderBehind($('#pickerStrip'), S.behind);

  renderStorage();
  $('#selTerm').onchange = ev => { S.term = ev.target.value; openCourses(false); };
}

/* The server ranks past-due work by days, not by how many are waiting.
   This screen shows five, and only for the term on screen. */
function behindShown(rows) {
  const all = Array.isArray(rows) ? rows : [];
  if (S.term === '__all') return all.slice(0, 5);
  const ids = new Set((S.courses || [])
    .filter(c => (c.term_label || '') === S.term)
    .map(c => String(c.id)));
  return all.filter(row => ids.has(String(row.course_id))).slice(0, 5);
}

function courseCount(list) {
  const n = list.length;
  const waiting = list.reduce((a, c) => a + (+c.waiting || 0), 0);
  return `${n} course${n === 1 ? '' : 's'}`
    + (waiting ? ` · ${waiting} submission${waiting === 1 ? '' : 's'} waiting` : '');
}

/* One row per course: the catalogue code, what the course is actually called,
   and the state this machine knows about. A course nobody has opened here says
   so plainly rather than showing a zero that would read as "nothing to do". */
/* The same four figures the course page shows in its band. Open unless this
   course was collapsed on purpose. A missing entry is the default, open, so
   a new course does not start folded. Collapse stores 0; deleting the entry
   would look like the default and spring open again. */
function glanceMap() {
  try {
    const raw = JSON.parse(localStorage.getItem('cg.glance') || '{}');
    return raw && typeof raw === 'object' ? raw : {};
  } catch (_) { return {}; }
}
function glanceIsOpen(id) {
  const key = String(id || '');
  const all = glanceMap();
  if (!Object.prototype.hasOwnProperty.call(all, key)) return true;
  return !!all[key];
}
function glanceCards(c, onBand) {
  const waiting = +c.waiting || 0;
  const cards = [
    [c.to_grade || 0, 'Assignments to Grade', (+c.to_grade) ? 'warn' : ''],
    [waiting, 'Waiting to grade', waiting ? 'warn' : ''],
    [c.graded || 0, 'Graded here', ''],
    [c.writes_today || 0, 'Canvas writes today', ''],
  ];
  const gid = 'glance-' + String(c.course_id || c.id || '');
  return `<span class="pickStats${onBand ? ' onBand' : ''}" id="${esc(gid)}">${cards.map(([n, label, kind]) =>
    `<span class="pickStat ${kind}"><b>${esc(n)}</b><span>${esc(label)}</span></span>`
  ).join('')}</span>`;
}
function glanceFold(c, onBand) {
  const id = String(c.course_id || c.id || '');
  const open = glanceIsOpen(id);
  return `<button type="button" class="pickFold${onBand ? ' onBand' : ''}" data-cid="${esc(id)}"
    aria-expanded="${open ? 'true' : 'false'}" aria-controls="glance-${esc(id)}"
    aria-label="${open ? 'Collapse' : 'Expand'}"><span class="pickChevron" aria-hidden="true">▾</span></button>`;
}
function wireGlance(root) {
  if (!root) return;
  root.querySelectorAll('.pickFold').forEach(btn => {
    btn.onclick = () => {
      const open = btn.getAttribute('aria-expanded') !== 'true';
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      btn.setAttribute('aria-label', open ? 'Collapse' : 'Expand');
      const box = btn.closest('.pickRow, .resumeBand');
      if (box) box.classList.toggle('open', open);
      const all = glanceMap();
      const id = String(btn.dataset.cid || '');
      if (open) all[id] = 1;
      else all[id] = 0;
      try { localStorage.setItem('cg.glance', JSON.stringify(all)); } catch (_) { /* private mode */ }
    };
  });
}

/* A circular arrow. One course, not the whole list: re-read its assignment
   list from Canvas and draw the figures again. Reads only. */
const REFRESH_ICON = `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
  <path d="M21 12a9 9 0 1 1-2.6-6.3" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>
  <path d="M21 3.5V9h-5.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>`;

function refreshButton(c, onBand) {
  const id = String(c.course_id || c.id || '');
  const name = c.title || c.name || 'this course';
  return `<button type="button" class="pickRefresh${onBand ? ' onBand' : ''}" data-cid="${esc(id)}"
    aria-label="Refresh ${esc(name)} from Canvas"
    title="Re-read this course's assignment list from Canvas. Reads only.">${REFRESH_ICON}</button>`;
}

function wireRefresh(root) {
  if (!root) return;
  root.querySelectorAll('.pickRefresh').forEach(btn => {
    btn.onclick = () => refreshOneCourse(btn.dataset.cid, btn);
  });
}

function wireCourseTools(root) {
  wireGlance(root);
  wireRefresh(root);
}

async function refreshOneCourse(cid, btn) {
  cid = String(cid || '');
  if (!cid || !btn || btn.dataset.busy === '1') return;
  btn.dataset.busy = '1';
  btn.disabled = true;
  btn.classList.add('spin');
  btn.setAttribute('aria-busy', 'true');
  try {
    const started = await api('/courses/' + encodeURIComponent(cid) + '/hub/refresh', { body: {} });
    let info = null;
    for (let i = 0; i < 180; i++) {
      info = await api('/jobs/' + started.job);
      if (!info || info.state !== 'running') break;
      if (S.view !== 'picker') return;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    if (S.view !== 'picker') return;
    if (!info || info.state === 'error') throw new Error((info && info.error) || 'The refresh failed.');
    if (info.state !== 'done') throw new Error('The refresh did not finish.');
    const picked = await api('/picker');
    if (S.view !== 'picker') return;
    paintHome(picked);
    setStatus('Refreshed from Canvas', 'ok');
  } catch (err) {
    setStatus(firstLine(err.message), 'err');
    btn.classList.remove('spin');
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    delete btn.dataset.busy;
  }
}

function courseRow(c) {
  const bits = [];
  if (c.students != null) bits.push(c.students + ' student' + (c.students === 1 ? '' : 's'));
  if (c.excluded) bits.push('excluded in config.json');
  else if (c.known && c.touched_at) bits.push(ago(c.touched_at));
  const right = [];
  if (c.excluded) right.push('<span class="pill warn">excluded</span>');
  else if (!c.known) right.push('<span class="pill">not opened yet</span>');
  else if (c.waiting) right.push(`<span class="pill warn">${c.waiting} to grade</span>`);

  const main = `<span class="pickCode">${esc(c.code || '')}</span>
    <span><span class="nm">${esc(c.title || c.name)}</span>
      <span class="sub">${esc(bits.join(' · '))}</span></span>
    ${right.length ? `<span class="rt">${right.join('')}</span>` : ''}`;
  if (c.excluded) {
    return `<div class="pickRow off" title="${esc(c.name)}"><div class="pickMain">${main}</div></div>`;
  }
  const show = c.known ? glanceIsOpen(c.id) : false;
  return `<div class="pickRow${show ? ' open' : ''}">
    <a class="pickMain" href="#/c/${esc(c.id)}" title="${esc(c.name)}">${main}</a>
    <div class="pickActs">${c.known ? glanceFold(c) : ''}${refreshButton(c)}</div>
    ${c.known ? glanceCards(c) : ''}
  </div>`;
}

/* Where you left off, or nothing at all. A band that said "no recent work"
   would be furniture on the one screen that should get out of the way. */
function renderResume(r) {
  const host = $('#pickerResume');
  if (!host) return;
  if (!r) { host.innerHTML = ''; return; }
  const href = r.assignment_id
    ? `#/c/${esc(r.course_id)}/a/${esc(r.assignment_id)}`
    : `#/c/${esc(r.course_id)}/grade`;
  host.innerHTML = `<section class="resumeBand" aria-labelledby="resumeH">
    <p class="resumeLine"><span class="kicker">Where you left off</span>
      <span class="resumeName" id="resumeH">${esc(r.title || '')}</span></p>
    <a class="btn primary sm" href="${href}">Carry on grading</a>
  </section>`;
}

/* "5 hours ago". The hub says the same thing about the same timestamps; this
   is the browser's copy so the picker does not wait on a round trip for it. */
function ago(iso) {
  if (!iso) return '';
  const when = new Date(iso);
  if (isNaN(when)) return '';
  const secs = (Date.now() - when.getTime()) / 1000;
  if (secs < 90) return 'just now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return mins + ' min ago';
  const hours = Math.round(mins / 60);
  if (hours < 36) return hours + ' hour' + (hours === 1 ? '' : 's') + ' ago';
  const days = Math.round(hours / 24);
  if (days < 14) return days + ' day' + (days === 1 ? '' : 's') + ' ago';
  return Math.round(days / 7) + ' weeks ago';
}

/* ------------------------------------------------------------ assignments */
async function openCourse(courseId, refresh) {
  const tb = $('#teachBar'); if (tb) tb.classList.add('hidden');
  showView('picker');
  homeListTools(false);
  // Arriving straight on this screen, the course list has not been read yet,
  // and the crumb and the tab would say "Course 734975" until it was.
  if (!S.courses.some(c => String(c.id) === String(courseId)) && typeof ensureCourse === 'function') {
    try { await ensureCourse(courseId); } catch (_) { /* the fallback name below */ }
  }
  S.course = S.courses.find(c => String(c.id) === String(courseId)) || { id: courseId, name: 'Course ' + courseId };
  crumbs([{ label: 'Courses', href: '#/' }, { label: courseTitle(S.course), href: '#/c/' + courseId }, { label: 'Grade' }]);
  $('#headerActions').innerHTML = '';
  $('#pickerTitle').textContent = courseTitle(S.course);
  $('#pickerTitle').title = S.course.name || '';
  document.title = courseTitle(S.course) + ' · CourseForge Studio';
  const sb = $('#storageBar'); if (sb) sb.innerHTML = '';
  $('#pickerHint').textContent = 'Loading assignments…';
  $('#btnRefresh').onclick = () => openCourse(courseId, true);
  try {
    S.assignments = await api(`/courses/${courseId}/assignments` + (refresh ? '?refresh=1' : ''));
  } catch (err) {
    $('#pickerHint').textContent = firstLine(err.message);
    return;
  }
  const waiting = S.assignments.reduce((a, x) => a + (x.graded ? 0 : (+x.needs_grading || 0)), 0);
  $('#pickerHint').textContent = `${S.assignments.length} assignments`
    + (waiting ? ` · ${waiting} submission${waiting === 1 ? '' : 's'} waiting to grade` : '');
  $('#pickerBody').innerHTML = `<table class="asgTable"><thead><tr>
      <th>Assignment</th><th>Due</th><th class="tg">To grade</th>
      <th style="text-align:right">Points</th>
      <th>Rubric</th><th>State</th></tr></thead><tbody>` +
    S.assignments.map(a => `<tr>
        <td><a class="asgLink" href="#/c/${esc(courseId)}/a/${esc(a.id)}">
          <b>${esc(a.name)}</b>${a.is_discussion ? ' <span class="pill">discussion</span>' : ''}
          ${a.published ? '' : ' <span class="pill warn">unpublished</span>'}</a></td>
        <td style="color:var(--muted)">${esc(fmtDate(a.due_at) || '—')}</td>
        <td class="tg">${a.graded
      ? '<span class="pill good gradedMark">Graded</span>'
      : (a.needs_grading
      ? `<span class="tgBadge" title="${a.needs_grading} submission(s) Canvas says are waiting">${
          a.needs_grading}</span>`
      : '<span class="muted">—</span>')}</td>
        <td class="num">${a.points_possible ?? '—'}</td>
        <td>${a.has_rubric ? '<span class="pill good">rubric</span>' : '<span class="pill warn">none</span>'}</td>
        <td>${a.graded_at ? '<span class="pill ai">graded ' + esc(fmtDate(a.graded_at)) + '</span>'
      : a.synced_at ? '<span class="pill">synced</span>' : ''}
            ${a.has_instructions ? ' <span class="pill">notes</span>' : ''}</td>
      </tr>`).join('') + '</tbody></table>';
}

/* ------------------------------------------------------------------ handoff */
/* No button. Opening an assignment is when the two machines can be compared, so
   that is when it happens: take a newer copy if this machine has nothing of its
   own to lose, otherwise make sure Canvas has what is here. The one case that
   cannot be decided without asking is both sides having moved, and that gets a
   banner rather than a permanent control nobody needs. */
async function handoffOnOpen(courseId, assignmentId) {
  let r;
  try {
    const { job } = await api(`/a/${courseId}/${assignmentId}/handoff`,
                              { body: { do: 'auto' } });
    for (let i = 0; i < 240; i++) {
      const info = await api('/jobs/' + job);
      if (info.state !== 'running') { r = info.result || {}; break; }
      await new Promise(s => setTimeout(s, 500));
    }
  } catch (_) { return; }             // offline, or Canvas is down: carry on
  if (!r || !S.ids || S.ids.assignmentId !== assignmentId) return;

  if (r.did === 'picked_up') {
    setStatus(`picked up revision ${r.rev} from ${
      (r.from || {}).name || 'another machine'}`
      + (r.attached ? ` · ${r.attached} Blender result(s) restored` : ''), 'ok');
    try {
      S.ws = await api(`/a/${courseId}/${assignmentId}`);
      renderHeaderActions(); render();
    } catch (_) { /* the next pull will pick it up */ }
  } else if (r.did === 'diverged') {
    showDivergedBanner(courseId, assignmentId, r);
  }
}

/* The only thing here worth interrupting for: grading on this machine that was
   never carried, and a newer copy in Canvas that has never seen it. Either
   answer loses something, so neither is chosen automatically. */
function showDivergedBanner(courseId, assignmentId, r) {
  if ($('#divergedBar')) return;
  const bar = document.createElement('div');
  bar.id = 'divergedBar';
  bar.className = 'staleBar';
  const where = ((r.remote || {}).machine || {}).name || 'another machine';
  bar.innerHTML = `<b>This assignment has moved in two places.</b>
    Grading here was never carried across, and ${esc(where)} has since handed off
    revision ${(r.remote || {}).rev}. Keeping one means losing the other.
    <button class="btn sm" id="dvMine">Keep this machine's</button>
    <button class="btn sm" id="dvTheirs">Take the Canvas copy</button>`;
  document.body.appendChild(bar);
  const close = () => bar.remove();
  $('#dvMine').onclick = () => {
    close();
    runJob('Carrying this machine\'s work to Canvas',
      () => api(`/a/${courseId}/${assignmentId}/handoff`, { body: { do: 'send' } }),
      out => setStatus(`handed off as revision ${out.rev}`, 'ok'));
  };
  $('#dvTheirs').onclick = () => {
    close();
    runJobConfirmed('Picking up from Canvas',
      token => api(`/a/${courseId}/${assignmentId}/handoff`,
        { body: { do: 'fetch', confirm: token } }),
      async out => {
        setStatus(`picked up revision ${out.rev}`, 'ok');
        try {
          S.ws = await api(`/a/${courseId}/${assignmentId}`);
          renderHeaderActions(); render();
        } catch (_) { /* the next pull will pick it up */ }
      },
      { title: 'Replace the grading on this machine?',
        verb: 'Yes, take the Canvas copy',
        note: 'Anything graded here and not carried across is lost.' });
  };
}

/* -------------------------------------------------------------- workspace */
async function openAssignment(courseId, assignmentId, opts = {}) {
  showView('work');
  S.ids = { courseId, assignmentId };
  setStatus('loading…');
  try {
    S.ws = await api(`/a/${courseId}/${assignmentId}`);
    if (typeof loadNicks === 'function') await loadNicks();
  } catch (err) { setStatus('failed: ' + err.message, 'err'); return; }
  setStatus('');
  const a = S.ws.assignment || {};
  S.assignment = a;
  crumbs([
    { label: 'Courses', href: '#/' },
    { label: courseTitle(S.course), href: '#/c/' + courseId + '/grade' },
    { label: a.name || 'Assignment' },
  ]);
  document.title = (a.name || 'Assignment') + ' · CourseForge Studio';
  renderHeaderActions();
  S.sel = 0;
  render();
  // Say this before the pull. A posted automatic score used to become
  // "in step" only after Canvas answered, and a quiet pull then left it there.
  if (S.ws.draft && S.ws.draft.synced_at) applyPullStatus(postingFromWorkspace(S.ws));
  // Choosing an assignment is the request to work on it, so fetch it rather
  // than waiting to be told again. The page is already drawn underneath, so
  // the sync reads as the page filling in. Only on a real arrival: the job
  // callbacks re-open the same assignment to pick up their own results, and
  // that must not kick off another sync.
  if (opts.arriving && staleSync(S.ws)) { doSync({ auto: true }); return; }
  startAutoPull();
  // Line up with the other machine. Fire and forget: it decides for itself and
  // only interrupts when both sides have moved.
  if (opts.arriving) handoffOnOpen(courseId, assignmentId);
}

/* ------------------------------------------------------------- pulling */
/* A pull re-reads the gradebook side of every student -- score, rubric, whether
   the student can see it -- and merges it into the draft without touching
   unpushed local work (gradesync.py has the rules). It runs when an assignment
   opens and then on a timer, so grading finished on another machine is waiting
   here. Quiet unless something changed. */
function stopAutoPull() {
  if (S.pullTimer) { clearInterval(S.pullTimer); S.pullTimer = null; }
}
function startAutoPull() {
  stopAutoPull();
  pullNow(true);
  const every = +((S.health || {}).pull_interval_s) || 0;
  if (every > 0) S.pullTimer = setInterval(() => pullNow(true), Math.max(15, every) * 1000);
}
/* Mid-edit? A pull that re-rendered the sliders under a hand on the mouse would
   lose the drag, so a timed pull waits for the next tick instead. */
function editingNow() {
  if (saveTimer) return true;
  const el = document.activeElement;
  return !!(el && /^(INPUT|TEXTAREA)$/.test(el.tagName) && el.closest('#detail'));
}
/* What a re-render would change: enough to skip the render when nothing did. */
function syncPrint(ws) {
  const ex = (ws && ws.extracted) || {}, st = ((ws && ws.draft) || {}).students || {};
  return JSON.stringify([
    Object.keys(ex).sort().map(u => [ex[u].canvas_score, ex[u].canvas_posted_at,
      (st[u] || {}).source, (st[u] || {}).total, (st[u] || {}).final_total, !!(st[u] || {}).conflict])]);
}
/* The grade Canvas is showing is the one held here, not only the automatic
   part. A quiz can stay "pending review" after that, because the essays were
   never marked inside the quiz tool. The posted total is still the grade. */
function gradeIsPosted(s) {
  if (!s || s.canvas_score == null || !s.canvas_posted_at || !S.ws) return false;
  const mine = finalOf(entryOf(s.user_id));
  if (mine == null) return false;
  return Math.abs(+mine - +s.canvas_score) < 0.05;
}
/* Canvas still wants a person to score the written questions. The number
   already posted can be the automatic part only, and students may already
   see it. pending_review is Canvas's own word for that. */
function answerIsBlank(q) {
  if (q && (q.type === 'file_upload_question' || q.type === 'file_upload')) return false;
  const t = String((q && q.response) || '').replace(/\s+/g, ' ').trim();
  return !t || /^\(no written answer\)$/i.test(t)
    || /^(n\s*\/\s*a|na|none|blank|skipped)\.?$/i.test(t);
}
function answersAllBlank(s) {
  const mans = ((s && s.quiz_review) || []).filter(q => q && q.manual);
  return mans.length > 0 && mans.every(answerIsBlank);
}
function writtenScoresRecorded(s) {
  const e = entryOf(s.user_id);
  if (!e || !e.scores) return false;
  const mans = (s.quiz_review || []).filter(q => q && q.manual);
  if (!mans.length) return false;
  return mans.every(q => {
    const v = e.scores['q' + q.id];
    return v != null && v !== '';
  });
}
/* A blank answer scored 0 is not a review hold. A real answer the model
   refused to grade still is, and that one stays on the to-grade list. */
function reviewHold(s) {
  if (!s || !S.ws) return false;
  const e = entryOf(s.user_id);
  if (!e || !e.needs_human || e.human_ok) return false;
  if (answersAllBlank(s) && writtenScoresRecorded(s)) return false;
  return true;
}
function writtenOpen(s) {
  if (!s || s.status === 'unsubmitted') return false;
  if (gradeIsPosted(s) || (writtenScoresRecorded(s) && !reviewHold(s))) return false;
  if (s.quiz_needs_written) return true;
  if (s.status === 'pending_review' || s.canvas_state === 'pending_review') return true;
  return !!(s.quiz && s.quiz.workflow === 'pending_review');
}
function writtenOpenCount(ws) {
  return Object.values((ws && ws.extracted) || {}).filter(writtenOpen).length;
}
function postingFromWorkspace(ws) {
  const rows = Object.values((ws && ws.extracted) || {});
  const students = Object.values(((ws && ws.draft) || {}).students || {});
  return {
    conflicts: students.filter(e => e && e.conflict),
    adopted: [],
    hidden: rows.filter(s => s && s.canvas_score != null && !s.canvas_posted_at),
    live: rows.filter(s => s && s.canvas_score != null && s.canvas_posted_at),
  };
}
function gradesLiveLine(n) {
  n = +n || 0;
  return `${n} grade${n === 1 ? '' : 's'} live`;
}
/* Every turned-in submission has a finished Canvas score. An automatic
   score with the written answers still open does not count. */
function gradingFinished(ws) {
  const people = Object.values((ws && ws.extracted) || {}).filter(s => s && typeof s === 'object');
  const submitted = people.filter(s => s.status && s.status !== 'unsubmitted'
    || (s.quiz_review && s.quiz_review.length));
  if (!submitted.length) return false;
  return submitted.every(s => !writtenOpen(s) && s.canvas_score != null);
}
function paintGradedStamp() {
  const host = $('#viewWork');
  if (!host) return;
  const el = $('#gradedStamp');
  if (!S.ws || !gradingFinished(S.ws)) { if (el) el.remove(); return; }
  if (el) return;
  const stamp = document.createElement('div');
  stamp.id = 'gradedStamp';
  stamp.className = 'gradedStamp';
  stamp.setAttribute('role', 'status');
  stamp.textContent = 'Graded';
  host.insertBefore(stamp, host.firstChild);
}
function applyPullStatus(r) {
  const c = (r.conflicts || []).length;
  const n = (r.adopted || []).length;
  const live = (r.live || []).length;
  const open = writtenOpenCount(S.ws);
  const liveLine = gradesLiveLine(live);
  if (c) {
    const tail = open ? ` · written answers still open for ${open}` : '';
    setStatus(`${c} conflict${c === 1 ? '' : 's'} with Canvas — see the Conflicts filter${tail} · ${liveLine}`, 'err');
  } else if (open) {
    setStatus(`${open} still to grade · written answers are not in the posted score · ${liveLine}`, 'err');
  } else if (n) {
    setStatus(`pulled ${n} grade${n === 1 ? '' : 's'} from Canvas · ${liveLine}`, 'ok');
  } else {
    setStatus(liveLine, 'ok');
  }
}
async function pullNow(quiet) {
  if (!S.ids || S.pulling) return;
  if (!(S.ws && S.ws.draft && S.ws.draft.synced_at)) return;   // nothing to pull into yet
  if ($('#modalHost').innerHTML) return;                       // a job or dialog owns the page
  if (quiet && editingNow()) return;
  const { courseId, assignmentId } = S.ids;
  S.pulling = true;
  if (!quiet) setStatus('checking Canvas…');
  try {
    const r = await api(`/a/${courseId}/${assignmentId}/pull`, { body: {} });
    if (!S.ids || S.ids.courseId !== courseId || S.ids.assignmentId !== assignmentId) return;
    const was = syncPrint(S.ws);
    S.ws = await api(`/a/${courseId}/${assignmentId}`);
    const changed = syncPrint(S.ws) !== was;
    if (changed) { renderHeaderActions(); render(); }
    // A quiet pull used to leave "in step" on screen after the numbers had
    // stopped changing, which read as "grading is finished".
    const cur = (($('#status') || {}).textContent || '').trim();
    if (changed || !quiet || !cur || cur.indexOf('in step with Canvas') === 0
        || cur.indexOf(' hidden, ') !== -1) applyPullStatus(r);
  } catch (err) {
    if (!quiet) setStatus('pull failed: ' + err.message, 'err');
  } finally { S.pulling = false; }
}

async function resolveConflicts(uids, choice) {
  const { courseId, assignmentId } = S.ids;
  try {
    const r = await api(`/a/${courseId}/${assignmentId}/resolve`, { body: { only: uids, choice } });
    S.ws = await api(`/a/${courseId}/${assignmentId}`);
    const n = (r.settled || []).length;
    setStatus(choice === 'canvas'
      ? `took Canvas for ${n}`
      : `kept the score on this computer for ${n} — the next push overwrites Canvas`, 'ok');
    render();
  } catch (err) { setStatus('could not resolve: ' + err.message, 'err'); }
}

function renderHeaderActions() {
  const synced = !!(S.ws.draft && S.ws.draft.synced_at);
  const canAI = !(S.health.claude && S.health.claude.logged_in === false);
  const models = S.health.models || [S.health.model];
  const hasBlend = Object.values(S.ws.extracted || {}).some(
    st => (st.filenames || []).some(f => /\.blend1?$/i.test(f)));
  const blenderOk = !!(S.health.blender && S.health.blender.ok);
  // Chasing missing work only makes sense once the deadline has gone.
  const dueAt = (S.ws.assignment || {}).due_at;
  const pastDue = !!(dueAt && new Date(dueAt).getTime() < Date.now());
  // Four groups, in the order the work happens, each an inline-flex unit so a
  // group wraps whole rather than splitting across rows and leaving its label
  // and its divider stranded. That is the one thing a plain row of thirteen
  // buttons could not do.
  $('#headerActions').innerHTML = `
    <span class="tbGroup"><span class="tbTag">Get</span>
      <button class="btn" id="btnSync">${synced ? 'Re-sync' : 'Sync from Canvas'}</button>
      ${hasBlend ? `<button class="btn" id="btnBlend" ${blenderOk ? '' : 'disabled'}
        title="${blenderOk ? 'Open every .blend in Blender and extract stats, renders and a 3D preview'
                           : 'Blender was not found on this machine'}">Blender pass</button>` : ''}
    </span>
    <span class="tbGroup"><span class="tbTag">Grade</span>
      <label class="modelPick" title="Which Claude model grades written work">Claude
        <select class="modelSel" id="selModel">
          ${models.map(m => `<option value="${esc(m)}"${m === S.health.model ? ' selected' : ''}>${esc(m)}</option>`).join('')}
        </select></label>
      <label class="modelPick${visionModel() === S.health.model ? '' : ' differs'}"
        title="Work with pictures in it is read by this one. Opus vision costs several
times more without judging a render any better, so it is set to sonnet out of the box.
Where every submission has images, this is the model that grades the whole class.">Images
        <select class="modelSel" id="selVision">
          ${models.map(m => `<option value="${esc(m)}"${m === visionModel() ? ' selected' : ''}>${esc(m)}</option>`).join('')}
        </select></label>
      <button class="btn" id="btnInstr"
        title="Standing notes for Claude about how to mark this assignment">Instructions${S.ws.instructions ? ' •' : ''}</button>
      <button class="btn ai" id="btnGrade" ${(!synced || !canAI) ? 'disabled' : ''}
        title="${canAI ? 'Grade every student with Claude against this rubric' : 'Claude CLI is not logged in'}">Auto-grade all</button>
    </span>
    <span class="tbGroup"><span class="tbTag">Read</span>
      <button class="btn" id="btnView"
        title="Read what this assignment asks for, as Canvas has it now">View assignment</button>
      <button class="btn" id="btnWork">${S.showWork ? 'Hide work' : 'Show work'}</button>
      <button class="btn ${S.view === 'insights' ? 'on' : ''}" id="btnCharts" ${synced ? '' : 'disabled'}>${S.view === 'insights' ? 'Back to grading' : 'Insights'}</button>
    </span>
    <span class="tbGroup"><span class="tbTag">Send</span>
      <button class="btn danger" id="btnPush" ${synced ? '' : 'disabled'}
        title="Write the grades into your Canvas gradebook. Students can see them. Asks first.">Push to Canvas…</button>
      ${pastDue ? `<button class="btn" id="btnRemind"
        title="Message every student who has turned nothing in for this">Remind missing</button>` : ''}
      <button class="btn" id="btnExport" ${synced ? '' : 'disabled'}
        title="Write every grade, rubric score and comment to a spreadsheet in this assignment's folder on this computer. Nothing leaves the machine">Export grades</button>
    </span>`;
  $('#btnSync').onclick = doSync;
  $('#btnGrade').onclick = () => doGrade(null);
  $('#selModel').onchange = ev => setModel(ev.target.value);
  $('#selVision').onchange = ev => setModel(ev.target.value, 'vision_model');
  const bb = $('#btnBlend');
  if (bb) bb.onclick = () => runJob('Reading .blend files with Blender',
    () => api(`/a/${S.ids.courseId}/${S.ids.assignmentId}/blend`, { body: {} }),
    () => openAssignment(S.ids.courseId, S.ids.assignmentId));
  $('#btnCharts').onclick = () => {
    S.view = S.view === 'insights' ? 'grade' : 'insights';
    renderHeaderActions(); render();
  };
  $('#btnView').onclick = () => openScheduleItem(S.ids.courseId, S.ids.assignmentId);
  $('#btnInstr').onclick = openInstructions;
  $('#btnWork').onclick = toggleWork;
  $('#btnExport').onclick = doExport;
  $('#btnPush').onclick = openPush;
  const rm = $('#btnRemind');
  if (rm) rm.onclick = () => openRemind(S.ids.courseId, S.ids.assignmentId);
}

/* ------------------------------------------------------------------ setup */
/* Connecting to Canvas is the one thing a new user has to do by hand, and
   asking them to create a file with an exact name containing exactly one line
   is a step that goes wrong quietly. They paste it here instead and the server
   writes the file, after checking the token actually works.

   The value goes one way only: it is posted to this machine's own server and
   never read back out of it. */
async function openSetup(afterwards) {
  const host = $('#modalHost');
  let state = {};
  try { state = await api('/setup'); } catch (err) { state = {}; }

  host.innerHTML = `<div class="modalBack"><div class="modal">
      <h3>Connect to Canvas</h3>
      <div class="sub">Two things, once. They are saved on this machine only.</div>

      <div class="setupForm">
        <label>Your Canvas web address
          <input type="text" id="suHost" spellcheck="false" autocomplete="off"
            value="${esc(state.base_url || 'https://')}"
            placeholder="https://yourschool.instructure.com">
          <span class="setupHint">The address you log in at, nothing after
            <code>.com</code>.</span>
        </label>

        <label>Access token
          <span class="setupToken">
            <input type="password" id="suToken" spellcheck="false"
              autocomplete="off" placeholder="paste the token from Canvas">
            <button type="button" class="btn sm" id="suShow"
              title="Show what you pasted">Show</button>
          </span>
          <span class="setupHint" id="suWhere"></span>
        </label>
      </div>

      <details class="setupHow" ${state.token_len ? '' : 'open'}>
        <summary>Where do I get a token?</summary>
        <ol>
          <li>Open <a id="suLink" href="#" target="_blank" rel="noopener">your
            Canvas account settings</a></li>
          <li>Scroll to <b>Approved Integrations</b> and click
            <b>+ New Access Token</b></li>
          <li>Purpose: anything, e.g. <code>courseforge-studio</code>. Leave the
            expiry blank.</li>
          <li><b>Generate Token</b>, then copy it straight away — Canvas will not
            show it again</li>
        </ol>
      </details>

      <div id="suResult"></div>

      <div class="foot">
        <span class="spacer"></span>
        <button class="btn" id="suCancel">Cancel</button>
        <button class="btn ai" id="suSave">Check and save</button>
      </div>
    </div></div>`;

  const setWhere = () => {
    $('#suWhere').innerHTML = state.token_len
      ? `A token is already saved (${state.token_len} characters). Pasting a new
         one replaces it.`
      : `Saved to <code>${esc(state.token_path || 'canvas.token')}</code>, which
         is gitignored. It is only ever sent to your own Canvas.`;
  };
  setWhere();

  const syncLink = () => {
    const base = ($('#suHost').value || '').trim().replace(/\/+$/, '');
    $('#suLink').href = (base.startsWith('http') ? base : 'https://' + base)
      + '/profile/settings';
  };
  syncLink();
  $('#suHost').oninput = syncLink;

  $('#suShow').onclick = () => {
    const field = $('#suToken');
    const shown = field.type === 'text';
    field.type = shown ? 'password' : 'text';
    $('#suShow').textContent = shown ? 'Show' : 'Hide';
  };
  $('#suCancel').onclick = () => { host.innerHTML = ''; };

  const save = async (force) => {
    const token = $('#suToken').value;
    const base_url = $('#suHost').value;
    $('#suSave').disabled = true;
    $('#suResult').innerHTML = '<div class="setupBusy"><span class="spin"></span>' +
      'asking Canvas whether that token works…</div>';
    let r;
    try {
      r = await api('/setup/token', { body: { token, base_url, force } });
    } catch (err) {
      r = { ok: false, error: err.message };
    }
    $('#suSave').disabled = false;
    if (!r.ok) {
      $('#suResult').innerHTML = `<div class="callout bad">${esc(r.error || 'failed')}
        ${r.reason === 'unreachable'
          ? '<button class="btn sm" id="suForce">Save it anyway</button>' : ''}</div>`;
      const forceBtn = $('#suForce');
      if (forceBtn) forceBtn.onclick = () => save(true);
      return;
    }
    $('#suResult').innerHTML = `<div class="callout ok">
      <b>${r.verified ? 'Connected as ' + esc(r.name) + '.' : 'Saved.'}</b>
      Written to <code>${esc(r.path)}</code>.
      ${r.env_override ? '<br><b>Note:</b> a CANVAS_TOKEN environment variable is '
        + 'set on this machine and takes priority over this file, so the app will '
        + 'keep using that one until you clear it.' : ''}</div>`;
    // Let them read the confirmation, then carry on into the app.
    setTimeout(async () => {
      host.innerHTML = '';
      try { S.health = await api('/health'); } catch (err) { /* banner covers it */ }
      renderBanners();
      if (afterwards) afterwards(); else openCourses(true);
    }, 1200);
  };
  $('#suSave').onclick = () => save(false);
  $('#suToken').onkeydown = ev => { if (ev.key === 'Enter') save(false); };
  $('#suToken').focus();
}

/* Never synced means there is nothing on screen to work with, so it always
   syncs. After that, a copy younger than assignment_max_age_min is taken as
   current: re-syncing is not free, and the grade pull on a timer already keeps
   scores in step. Set that to 0 in config.json to sync on every open. */
function staleSync(ws) {
  const at = ws && ws.draft && ws.draft.synced_at;
  if (!at) return true;
  const maxMin = (S.health || {}).assignment_max_age_min;
  const limit = (maxMin == null ? 15 : +maxMin);
  if (!(limit > 0)) return true;
  const age = (Date.now() - new Date(at).getTime()) / 60000;
  return !(age >= 0) || age > limit;      // an unreadable date counts as stale
}

function doSync(opts = {}) {
  const { courseId, assignmentId } = S.ids;
  runJob('Syncing from Canvas', () => api(`/a/${courseId}/${assignmentId}/sync`, { body: {} }),
    () => {
      openAssignment(courseId, assignmentId);
      if (opts.auto) {
        setStatus('synced from Canvas just now', 'ok');
        // Arriving on a stale assignment went straight to this sync and
        // returned before the handoff check, so a fresh machine never lined
        // up with the other one until the next open. Do it here instead.
        handoffOnOpen(courseId, assignmentId);
      }
    },
    { autoClose: !!opts.auto });
}
/* Which model reads a submission that has pictures in it. Falls back to the
   grading model, which is what the server does when the setting is empty. */
function visionModel() {
  return (S.health && (S.health.vision_model || S.health.model)) || '';
}

function doGrade(only) {
  const { courseId, assignmentId } = S.ids;
  const n = only ? only.length : Object.keys(S.ws.extracted || {}).length;
  // Naming one model here was a claim the run could not keep: work with images
  // goes to the vision model, and in a course of renders that is every student.
  // Say both, or say neither, rather than the one that may never run.
  const pair = visionModel() === S.health.model ? S.health.model
    : `${S.health.model}, ${visionModel()} for work with images`;
  const label = only ? `Grading ${n} student${n === 1 ? '' : 's'} with ${pair}`
    : `Auto-grading ${n} students with ${pair}`;
  runJob(label, () => api(`/a/${courseId}/${assignmentId}/grade`, { body: { only } }),
    () => openAssignment(courseId, assignmentId), { stoppable: true });
}
async function setModel(model, key = 'model') {
  const previous = S.health[key];
  const sel = $(key === 'model' ? '#selModel' : '#selVision');
  try {
    const r = await api('/settings', { body: { [key]: model } });
    S.health.model = r.model;
    S.health.vision_model = r.vision_model || r.model;
    renderHeaderActions();
    setStatus(key === 'model'
      ? 'written work is graded by ' + r.model
      : 'work with images is read by ' + (r.vision_model || r.model), 'ok');
  } catch (err) {
    S.health[key] = previous;
    if (sel) sel.value = previous;
    setStatus('could not switch model: ' + firstLine(err.message), 'err');
  }
}

/* "exported 23 rows" said nothing anybody could act on: not what a row was,
   not what was in it, and not where the file went. The answer to all three is
   short enough to just show. */
function doExport() {
  const { courseId, assignmentId } = S.ids;
  api(`/a/${courseId}/${assignmentId}/export`, { body: {} })
    .then(r => {
      setStatus(`${r.rows} grade${r.rows === 1 ? '' : 's'} written to grades.csv`, 'ok');
      showExported(r);
    })
    .catch(err => setStatus('export failed: ' + firstLine(err.message), 'err'));
}

function showExported(r) {
  const folder = String(r.csv || '').replace(/[\\/][^\\/]*$/, '');
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal narrow">
      <h3>${esc(r.rows)} grade${r.rows === 1 ? '' : 's'} exported</h3>
      <div class="sub">Written to this computer. Nothing was sent anywhere, and
        nothing in Canvas changed.</div>
      <dl class="exList">
        <dt>grades.csv</dt>
        <dd>One row per student: name, status, points earned, any curve, the total,
          the percentage and letter, every rubric criterion as its own column, and
          the comment. Opens in Excel.</dd>
        <dt>grades.json</dt>
        <dd>The same thing as the Studio holds it, including the rubric and what
          Claude flagged. For a script, or for a colleague's tool.</dd>
      </dl>
      <label class="annLabel">Where they are
        <input type="text" id="exPath" readonly value="${esc(folder)}"></label>
      <div class="foot">
        <button class="btn" id="exCopy">Copy the folder path</button>
        <span class="spacer"></span>
        <button class="btn primary" id="exClose">Close</button>
      </div></div></div>`;
  $('#exClose').onclick = () => { host.innerHTML = ''; };
  $('#exCopy').onclick = () => {
    const box = $('#exPath');
    box.select();
    navigator.clipboard.writeText(folder)
      .then(() => setStatus('folder path copied', 'ok'))
      .catch(() => setStatus('could not copy; the path is selected, press Ctrl+C', 'err'));
  };
  $('#exPath').focus();
}

/* ---------------------------------------------------------- instructions */
function openInstructions() {
  const { courseId, assignmentId } = S.ids;
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal">
      <h3>Custom grading instructions</h3>
      <div class="sub">Anything Claude should know that is not in the Canvas description or rubric —
        a requirement you changed in class, an extension you granted, a common misreading to be lenient about,
        how strictly to treat length floors. These override the rubric where they conflict.
        They also override the syllabus late penalty: “ignore late grades for this assignment”
        lifts it for the class, and “ignore Jane Doe’s tardy submission” lifts it for that student only.
        Saving applies that to grades already on this screen. You do not have to re-grade.</div>
      <textarea class="instructions" id="instrText" placeholder="e.g. Ignore late grades for this assignment. Or: ignore Jane Doe’s tardy submission — do not dock points for it.">${esc(S.ws.instructions || '')}</textarea>
      <div class="foot">
        <button class="btn" id="instrCancel">Cancel</button>
        <button class="btn primary" id="instrSave">Save</button>
      </div></div></div>`;
  $('#instrCancel').onclick = () => { host.innerHTML = ''; };
  $('#instrSave').onclick = async () => {
    const text = $('#instrText').value;
    try {
      const saved = await api(`/a/${courseId}/${assignmentId}/instructions`, { body: { text } });
      S.ws = await api(`/a/${courseId}/${assignmentId}`);
      host.innerHTML = '';
      render();
      const n = (saved && saved.late_waived) || 0;
      setStatus(n ? `instructions saved — late penalty changed for ${n}` : 'instructions saved', 'ok');
    } catch (err) { setStatus('save failed: ' + err.message, 'err'); }
  };
}

/* ------------------------------------------------------------------- ask */
const PROMPTS = [
  'Did they actually cite a source, or just name one?',
  'Is there anything here that reads like it was written by AI?',
  'Quote every place they name a specific rule or mechanic.',
  'Did they answer every part of the prompt? List what is missing.',
  'Is my score for the weakest criterion defensible? Argue the other side.',
];

function openAsk(student) {
  const { courseId, assignmentId } = S.ids;
  const host = $('#modalHost');
  S.askHistory = S.askHistory || {};
  const key = assignmentId + ':' + student.user_id;
  const history = S.askHistory[key] = S.askHistory[key] || [];

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Ask about ${esc(studentLabel(student))}'s work</h3>
      <div class="sub">Claude answers from this submission only, and says so when the
        answer is not in there. Nothing here changes the grade.</div>
      <div class="askThread" id="askThread"></div>
      <div class="askChips">${PROMPTS.map((p, i) =>
    `<button class="chip" data-p="${i}">${esc(p)}</button>`).join('')}</div>
      <textarea id="askText" class="askBox" rows="3"
        placeholder="Ask anything about this submission…"></textarea>
      <div class="foot">
        <span class="sub" id="askState" style="margin:0;flex:1;text-align:left"></span>
        <button class="btn" id="askClose">Close</button>
        <button class="btn ai" id="askGo">Ask</button>
      </div></div></div>`;

  const thread = $('#askThread');
  const paint = () => {
    thread.innerHTML = history.length ? history.map(t =>
      `<div class="askTurn ${t.role}"><b>${t.role === 'user' ? 'You' : 'Claude'}</b>
        <div>${esc(t.text)}</div></div>`).join('')
      : '<div class="askEmpty">No questions yet.</div>';
    thread.scrollTop = 1e6;
  };
  paint();

  $('#askClose').onclick = () => { host.innerHTML = ''; };
  host.querySelectorAll('.askChips .chip').forEach(b => b.onclick = () => {
    $('#askText').value = PROMPTS[+b.dataset.p]; $('#askText').focus();
  });

  const ask = async () => {
    const q = $('#askText').value.trim();
    if (!q) return;
    history.push({ role: 'user', text: q });
    $('#askText').value = '';
    paint();
    $('#askGo').disabled = true;
    $('#askState').textContent = 'thinking…';
    try {
      busyOn();
      const { job } = await api(`/a/${courseId}/${assignmentId}/ask/${student.user_id}`,
        { body: { question: q, history: history.slice(0, -1) } });
      const poll = async () => {
        const info = await api('/jobs/' + job);
        if (info.state === 'running') {
          const live = (info.items || [])[0];
          $('#askState').textContent = live
            ? `${live.state}${live.detail ? ' · ' + live.detail : ''} · ${clock(live.elapsed_s)}`
            : 'thinking…';
          return setTimeout(poll, 700);
        }
        busyOff();
        $('#askGo').disabled = false;
        $('#askState').textContent = '';
        if (info.state === 'error') {
          history.push({ role: 'error', text: info.error || 'failed' });
        } else {
          history.push({ role: 'claude', text: (info.result || {}).answer || '(no answer)' });
        }
        paint();
      };
      poll();
    } catch (err) {
      busyOff();
      $('#askGo').disabled = false;
      $('#askState').textContent = '';
      history.push({ role: 'error', text: err.message });
      paint();
    }
  };
  $('#askGo').onclick = ask;
  $('#askText').addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); ask(); }
  });
  $('#askText').focus();
}

/* --------------------------------------------------------------- overlap */
function pctText(v) { return (100 * (v || 0)).toFixed(0) + '%'; }

function openOverlapReport(r) {
  const host = $('#modalHost');
  if (!r) return;
  const notable = (r.pairs || []).filter(p => p.notable);
  const rest = (r.pairs || []).filter(p => !p.notable);

  const pairCard = p => `
    <div class="ovPair${p.worth_a_conversation ? ' hot' : ''}">
      <div class="ovWho">
        <b>${esc(p.a_name || p.a)}</b> <span class="ovAmp">and</span>
        <b>${esc(p.b_name || p.b)}</b>
        <span class="ovNums">${p.longest_words} words in a row ·
          ${pctText(p.containment)} of the shorter one</span>
      </div>
      ${p.reading ? `<div class="ovTags"><span class="tag">${esc(p.reading)}</span>
        ${p.worth_a_conversation ? '<span class="tag warn">worth a conversation</span>' : ''}</div>` : ''}
      ${p.what_i_see ? `<p class="ovSee">${esc(p.what_i_see)}</p>` : ''}
      ${p.innocent_explanation
        ? `<p class="ovBenign"><b>Most likely innocent explanation.</b>
             ${esc(p.innocent_explanation)}</p>` : ''}
      <div class="ovPassages">
        ${(p.passages || []).slice(0, 3).map(x =>
          `<div class="ovQuote"><span class="ovLen">${x.words}w</span>${esc(x.text)}</div>`).join('')}
      </div>
    </div>`;

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Overlap check</h3>
      <div class="sub">${r.n} submissions · ${r.compared != null ? r.compared
        : (r.pairs || []).length} pairs compared ·
        ${r.window_words}-word windows${r.model ? ' · read by ' + esc(r.model) : ''}
        ${r.cost_usd ? ' · $' + Number(r.cost_usd).toFixed(3) : ''}</div>

      <div class="callout" style="margin:12px 0">
        <b>This measures shared wording. It is not a finding of misconduct.</b>
        Wording the whole group shares (the prompt, the rubric, your instructions,
        anything common to more than a third of these submissions) was removed
        before measuring, so what is quoted below is wording these submissions
        share and the others do not. Students still overlap for innocent reasons:
        the same source, a narrow prompt, permitted collaboration, the same
        tutorial. Read the passages yourself before drawing any conclusion, and
        take anything further through your institution's process.
      </div>

      ${r.read ? `<p class="ovNote">${esc(r.read)}</p>` : ''}

      ${notable.length ? notable.map(pairCard).join('')
        : '<p class="ovNote">No pair stood out enough to quote.</p>'}

      ${(r.no_text || []).length ? `<div class="ovAside">
        <b>Not compared — nothing the student wrote:</b>
        ${(r.no_text || []).map(x => esc(x.label) + ' (' + esc(x.why) + ')').join('; ')}
      </div>` : ''}
      ${(r.skipped_short || []).length ? `<div class="ovAside">
        <b>Too short to compare:</b> ${(r.skipped_short || []).map(esc).join(', ')}
      </div>` : ''}

      ${rest.length ? `<details class="ovRest"><summary>${rest.length} pair(s)
        below the threshold</summary>
        <table class="ovTable"><tbody>
        ${rest.map(p => `<tr><td>${esc(p.a_name || p.a)}</td><td>${esc(p.b_name || p.b)}</td>
          <td>${p.longest_words}w</td><td>${pctText(p.containment)}</td></tr>`).join('')}
        </tbody></table></details>` : ''}

      <div class="foot">
        <button class="btn" id="ovCopy">Copy as text</button>
        <button class="btn" id="ovClose">Close</button>
      </div>
    </div></div>`;
  $('#ovClose').onclick = () => { host.innerHTML = ''; };
  $('#ovCopy').onclick = () => {
    const lines = [`Overlap check - ${r.n} submissions, ${r.compared != null ? r.compared
      : (r.pairs || []).length} pairs`,
      'This measures shared wording. It is not a finding of misconduct.', ''];
    if (r.read) lines.push(r.read, '');
    notable.forEach(p => {
      lines.push(`${p.a_name || p.a} and ${p.b_name || p.b}: ${p.longest_words} words in a row, ` +
        `${pctText(p.containment)} of the shorter submission` +
        (p.reading ? ` - ${p.reading}` : ''));
      if (p.what_i_see) lines.push('  ' + p.what_i_see);
      if (p.innocent_explanation) lines.push('  Innocent explanation: ' + p.innocent_explanation);
      (p.passages || []).slice(0, 3).forEach(x => lines.push(`  (${x.words}w) "${x.text}"`));
      lines.push('');
    });
    navigator.clipboard.writeText(lines.join('\n'))
      .then(() => setStatus('overlap report copied', 'ok'))
      .catch(() => setStatus('could not copy', 'err'));
  };
}

/* ------------------------------------------------------------- teaching zone */
/* The bar along the bottom is a different job from the bar along the top. The
   top bar acts on grades: sync, grade, curve, push. Nothing down here changes a
   grade or touches Canvas. It reads the same results back as evidence about the
   teaching and the assignment, which is a question the instructor asks after the
   grading is done, so it gets its own zone and its own colour. */
function teachSignal() { return (S.ws && S.ws.teaching_signal) || null; }

function renderTeachBar() {
  const bar = $('#teachBar');
  if (!bar) return;
  // S.view is only set once someone toggles to insights, so it says nothing
  // about whether an assignment is open. The workspace payload does.
  if (!S.ws || !S.ids) { bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');

  const sig = teachSignal();
  const graded = sig ? sig.n_graded : 0;
  const scoped = S.picked.size ? ` · ${S.picked.size} selected` : '';
  const un = (sig && sig.unscored) || {};
  const nMissing = (un.no_submission || []).length;

  if (!graded) {
    bar.innerHTML = `<div class="teachLabel">Teaching</div>
      <span class="teachIdle">Grade a few students and this bar starts reading the
        results back as feedback on the assignment.</span>`;
    return;
  }

  // The chips are measured locally, so they are already true before anyone asks.
  const chips = [];
  const weakest = (sig.health || [])[0];
  if (weakest && weakest.mean_pct < 70) {
    chips.push({ cls: 'warn', text: `weakest row: ${esc(weakest.label)}
      ${weakest.mean_pct}%`, go: 'rubric' });
  }
  const notDoingJob = (sig.health || []).filter(h =>
    h.findings.some(f => ['ceiling', 'no_spread', 'weak_link'].includes(f.kind))).length;
  if (notDoingJob) {
    chips.push({ cls: '', text: `${notDoingJob} rubric row${notDoingJob > 1 ? 's' : ''}
      not separating students`, go: 'rubric' });
  }
  const voice = sig.voice || { n_students: 0, by_category: [] };
  if (voice.n_students) {
    const top = voice.by_category[0];
    chips.push({ cls: 'voice', text: `${voice.n_students} student${voice.n_students > 1 ? 's' : ''}
      told you something${top ? ` · ${top.count} ${esc(top.label)}` : ''}`, go: 'voice' });
  }
  if ((sig.patterns || []).length) {
    chips.push({ cls: 'warn', text: `${sig.patterns.length} weakness${
      sig.patterns.length > 1 ? 'es' : ''} recurring across the course`, go: 'patterns' });
  }
  if (!chips.length) {
    chips.push({ cls: 'good', text: 'nothing measured stands out on this one',
      go: 'rubric' });
  }

  bar.innerHTML = `
    <div class="teachLabel">Teaching</div>
    <div class="teachChips">${chips.map((c, i) =>
      `<button class="teachChip ${c.cls}" data-go="${c.go}" data-i="${i}">${c.text}</button>`).join('')}</div>
    <span class="spacer"></span>
    <span class="teachMeta">${graded} graded${
      nMissing ? ` · ${nMissing} did not submit` : ''}${scoped}</span>
    <button class="btn sm" id="tbRubric">Rubric health</button>
    <button class="btn sm" id="tbVoice" ${voice.n_students ? '' : 'disabled'}
      title="${voice.n_students ? 'What students said about the assignment in their own words'
                                : 'No student wrote anything about their own experience'}">Student voice</button>
    <button class="btn sm" id="tbPatterns" ${(sig.patterns || []).length ? '' : 'disabled'}
      title="${(sig.patterns || []).length ? 'Rubric rows that keep coming out weak in this course'
                                           : 'Needs more than one graded assignment in this course'}">Course patterns</button>
    <button class="btn sm ai" id="tbRead">${sig.has_read ? 'Teaching read ✓' : 'What should I change?'}</button>`;

  bar.querySelectorAll('.teachChip').forEach(el => {
    el.onclick = () => openTeaching(el.dataset.go);
  });
  $('#tbRubric').onclick = () => openTeaching('rubric');
  $('#tbVoice').onclick = () => openTeaching('voice');
  $('#tbPatterns').onclick = () => openTeaching('patterns');
  $('#tbRead').onclick = doTeachingRead;
}

function doTeachingRead() {
  const { courseId, assignmentId } = S.ids;
  const only = S.picked.size ? pickedIds() : null;
  runJob(only ? `Reading ${only.length} selected results` : 'Reading the results back',
    () => api(`/a/${courseId}/${assignmentId}/teaching`, { body: { only } }),
    async read => {
      S.ws = await api(`/a/${courseId}/${assignmentId}`);
      $('#modalHost').innerHTML = '';
      render();
      openTeachingRead(read);
    });
}

/* ------------------------------------------------------- the measured panels */
function openTeaching(which) {
  const sig = teachSignal();
  if (!sig) return;
  const host = $('#modalHost');
  const draft = S.ws.draft || {};
  const saved = draft.teaching;

  const tabs = [
    ['rubric', 'Rubric health'],
    ['voice', `Student voice${sig.voice.n_students ? ` (${sig.voice.n_students})` : ''}`],
    ['patterns', `Course patterns${(sig.patterns || []).length ? ` (${sig.patterns.length})` : ''}`],
  ];

  host.innerHTML = `<div class="modalBack"><div class="modal wide teachModal">
      <h3>Teaching signal</h3>
      <div class="sub">Measured from ${sig.n_graded} graded submissions. Nothing
        here changes a grade.</div>
      <div class="teachTabs">${tabs.map(([id, label]) =>
        `<button class="chip" data-tab="${id}" aria-pressed="${id === which}">${label}</button>`).join('')}</div>
      <div id="teachBody"></div>
      <div class="foot">
        ${saved ? '<button class="btn" id="tShowRead">Show the teaching read</button>' : ''}
        <span class="spacer"></span>
        <button class="btn" id="tClose">Close</button>
      </div>
    </div></div>`;

  const paint = id => {
    $('#teachBody').innerHTML =
      id === 'voice' ? voiceHTML(sig.voice)
        : id === 'patterns' ? patternsHTML(sig.patterns)
          : rubricHealthHTML(sig.health, sig.n_graded);
    host.querySelectorAll('.teachTabs .chip').forEach(c =>
      c.setAttribute('aria-pressed', String(c.dataset.tab === id)));
  };
  host.querySelectorAll('.teachTabs .chip').forEach(c => {
    c.onclick = () => paint(c.dataset.tab);
  });
  $('#tClose').onclick = () => { host.innerHTML = ''; };
  const sr = $('#tShowRead');
  if (sr) sr.onclick = () => openTeachingRead(saved);
  paint(which || 'rubric');
}

const FINDING_WORDS = {
  ceiling: 'not separating anyone', no_spread: 'no spread',
  floor: 'almost nobody scored', weak: 'class did not clear it',
  weak_link: 'measures something else',
};

function rubricHealthHTML(health, n) {
  if (!health || !health.length) {
    return '<p class="teachNote">This assignment has no rubric to analyse.</p>';
  }
  return `
    <p class="teachNote">Each row of your rubric, worst average first. The point of
      this table is the rows that are not doing their job: a row everyone aces is
      not measuring anything, and a row nobody clears is usually about the
      teaching or the wording rather than the class.</p>
    <table class="teachTable">
      <thead><tr><th>Rubric row</th><th>Average</th><th>Spread</th>
        <th>Full marks</th><th>Near zero</th><th>Tracks grade</th></tr></thead>
      <tbody>${health.map(h => `
        <tr class="${h.findings.length ? 'flagged' : ''}">
          <td><b>${esc(h.label)}</b> <span class="muted">${num(h.points)} pts</span>
            ${h.findings.map(f => `<div class="teachFinding">
              <span class="tag ${f.kind === 'ceiling' || f.kind === 'no_spread' ? '' : 'warn'}">${
                FINDING_WORDS[f.kind] || f.kind}</span> ${esc(f.text)}</div>`).join('')}</td>
          <td class="mono">${num(h.mean)}<span class="muted">/${num(h.points)}</span>
            <div class="muted">${h.mean_pct}%</div></td>
          <td class="mono">${h.spread_pct}%</td>
          <td class="mono">${h.at_ceiling}/${h.n}</td>
          <td class="mono">${h.at_floor}/${h.n}</td>
          <td class="mono">${h.r_with_rest == null
            ? `<span class="muted" title="needs at least 8 graded students">—</span>`
            : h.r_with_rest}</td>
        </tr>`).join('')}</tbody>
    </table>`;
}

function voiceHTML(voice) {
  if (!voice || !voice.n_students) {
    return `<p class="teachNote">No student wrote anything about their own
      experience of this assignment, in their submission or in a Canvas comment.
      That is not the same as nothing being wrong; it usually means there was
      nowhere obvious to say it.</p>`;
  }
  return `
    <p class="teachNote">Sentences where a student is talking about their own
      experience rather than about the subject. Pulled from what they wrote and
      from their Canvas comments, never from anything this tool generated.</p>
    <div class="voiceBands">${voice.by_category.map(b =>
      `<span class="tag">${b.count} × ${esc(b.label)}</span>`).join('')}</div>
    <div class="voiceList">${voice.students.map(p => `
      <div class="voicePerson">
        <div class="voiceWho">${esc(studentLabel(p))}</div>
        ${p.items.map(i => `<div class="voiceQuote">
          <span class="voiceCat">${esc(i.category)}${
            i.source === 'comment' ? ' · Canvas comment' : ''}</span>
          ${esc(i.text)}</div>`).join('')}
      </div>`).join('')}</div>`;
}

function patternsHTML(patterns) {
  if (!patterns || !patterns.length) {
    return `<p class="teachNote">Nothing recurring yet. This compares rubric rows
      across every graded assignment in this course and needs the same row to come
      out weak in at least two of them.</p>`;
  }
  return `
    <p class="teachNote">Rubric rows that came out weak in more than one assignment
      in this course, matched on their wording. One weak row is an assignment; the
      same row weak repeatedly is the course.</p>
    <div class="patList">${patterns.map(p => `
      <div class="patItem">
        <div class="patHead"><b>${esc(p.label)}</b>
          <span class="muted">weak in ${p.times_weak} of ${p.times_seen}
            assignments · ${p.mean_pct}% average where weak</span></div>
        <ul>${p.where.map(w => `<li>${esc(w.assignment)} —
          <span class="mono">${w.mean_pct}%</span></li>`).join('')}</ul>
      </div>`).join('')}</div>`;
}

/* ------------------------------------------------------------ the model read */
const CAUSE_WORDS = {
  'not taught': 'not taught',
  'taught but not practised': 'taught, not practised',
  'assignment was unclear': 'the assignment was unclear',
  'tooling or logistics': 'tooling or logistics',
};

function openTeachingRead(r) {
  if (!r) return;
  const host = $('#modalHost');
  host.innerHTML = `<div class="modalBack"><div class="modal wide teachModal">
      <h3>What to change</h3>
      <div class="sub">${r.n_graded} graded submissions, class average
        ${r.mean_pct}%${r.model ? ' · read by ' + esc(r.model) : ''}${
        r.cost_usd ? ' · $' + Number(r.cost_usd).toFixed(3) : ''}${
        r.scope === 'selection' ? ' · selected students only' : ''}</div>

      ${r.headline ? `<p class="teachHeadline">${esc(r.headline)}</p>` : ''}

      ${(r.reteach || []).length ? `<h4 class="teachH">Worth going back over</h4>
        <div class="reteachList">${r.reteach.map((t, i) => `
          <div class="reteachItem">
            <div class="reteachTop">
              <span class="reteachNum">${i + 1}</span>
              <b>${esc(t.what)}</b>
              ${t.cause ? `<span class="tag ${
                t.cause === 'assignment was unclear' ? 'warn' : ''}">${
                esc(CAUSE_WORDS[t.cause] || t.cause)}</span>` : ''}
              ${t.students_affected != null
                ? `<span class="muted">${t.students_affected} students</span>` : ''}
            </div>
            ${t.evidence ? `<p class="reteachWhy"><b>Evidence.</b> ${esc(t.evidence)}</p>` : ''}
            ${t.action ? `<p class="reteachDo"><b>Next class.</b> ${esc(t.action)}</p>` : ''}
          </div>`).join('')}</div>` : ''}

      ${(r.assignment_fixes || []).length ? `<h4 class="teachH">Changes to the
        assignment itself</h4>
        <div class="fixList">${r.assignment_fixes.map(f => `
          <div class="fixItem"><b>${esc(f.what)}</b>
            ${f.why ? `<span class="muted">${esc(f.why)}</span>` : ''}</div>`).join('')}</div>` : ''}

      ${r.worked ? `<h4 class="teachH">What clearly landed</h4>
        <p class="teachNote">${esc(r.worked)}</p>` : ''}
      ${r.watch_next_time ? `<h4 class="teachH">What to watch next time</h4>
        <p class="teachNote">${esc(r.watch_next_time)}</p>` : ''}

      <div class="foot">
        <button class="btn" id="trCopy">Copy as text</button>
        <button class="btn" id="trEvidence">Show the measured evidence</button>
        <span class="spacer"></span>
        <button class="btn" id="trClose">Close</button>
      </div>
    </div></div>`;
  $('#trClose').onclick = () => { host.innerHTML = ''; };
  $('#trEvidence').onclick = () => openTeaching('rubric');
  $('#trCopy').onclick = () => {
    const lines = [`What to change - ${r.n_graded} graded, average ${r.mean_pct}%`, ''];
    if (r.headline) lines.push(r.headline, '');
    (r.reteach || []).forEach((t, i) => {
      lines.push(`${i + 1}. ${t.what}` + (t.cause ? ` [${t.cause}]` : ''));
      if (t.evidence) lines.push(`   Evidence: ${t.evidence}`);
      if (t.action) lines.push(`   Next class: ${t.action}`);
      lines.push('');
    });
    if ((r.assignment_fixes || []).length) {
      lines.push('Changes to the assignment:');
      r.assignment_fixes.forEach(f => lines.push(`- ${f.what}${f.why ? ` (${f.why})` : ''}`));
      lines.push('');
    }
    if (r.worked) lines.push('What landed: ' + r.worked, '');
    if (r.watch_next_time) lines.push('Watch next time: ' + r.watch_next_time);
    navigator.clipboard.writeText(lines.join('\n'))
      .then(() => setStatus('teaching read copied', 'ok'))
      .catch(() => setStatus('could not copy', 'err'));
  };
}

/* ----------------------------------------------------------------- curves */
const CURVE_KINDS = [
  { id: 'target_mean', label: 'Raise the average to…',
    hint: 'Everyone gains the same number of points, enough to move the average to your target. This is the one for a criterion the class did badly on.',
    needs: 'target', suffix: '%', dflt: 75 },
  { id: 'flat', label: 'Add a flat number of points',
    hint: 'Everyone gains the same points, no matter where they started.',
    needs: 'amount', suffix: 'points', dflt: 5 },
  { id: 'sqrt', label: 'Square-root curve',
    hint: 'Helps the bottom of the class most and the top barely at all: a 49% becomes a 70%, a 90% becomes a 95%. Use this when the whole class did poorly across the board.',
    needs: null },
  { id: 'floor', label: 'Lift everyone below…',
    hint: 'Nobody ends below your floor. Students already above it are untouched.',
    needs: 'target', suffix: '%', dflt: 60 },
  { id: 'to_top', label: 'Curve to the top score',
    hint: 'Adds whatever the highest scorer was missing to everyone, so the best paper becomes full marks.',
    needs: null },
  { id: 'scale', label: 'Scale every score up by…',
    hint: 'Multiplies each score, so it gives more points to students who already had more.',
    needs: 'amount', suffix: '%', dflt: 10 },
];

function openCurve(only) {
  const { courseId, assignmentId } = S.ids;
  const host = $('#modalHost');
  const scope = (only && only.length) ? only.slice() : null;
  const crits = rubric();
  const anyCurve = Object.values((S.ws.draft || {}).students || {})
    .some(e => e && (e.curve || e.final_total != null));

  host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>Curve grades${scope ? ` — ${scope.length} selected` : ' — whole class'}</h3>
      <div class="sub">Nothing changes until you press Apply. The earned score is
        always kept underneath, so a curve can be removed exactly.</div>

      <div class="curveForm">
        <label>Apply to
          <select id="cvScope">
            <option value="total">the whole score</option>
            ${crits.map(c => `<option value="${esc(c.id)}">${esc(c.label)}
              (${num(c.points)} pts)</option>`).join('')}
          </select>
        </label>
        <label>Curve
          <select id="cvKind">
            ${CURVE_KINDS.map(k => `<option value="${k.id}">${esc(k.label)}</option>`).join('')}
          </select>
        </label>
        <label id="cvValWrap">Value
          <span class="cvVal"><input type="number" id="cvVal" step="0.5" value="75">
            <span id="cvSuffix">%</span></span>
        </label>
        <label>Note (optional)
          <input type="text" id="cvLabel" maxlength="80"
            placeholder="why you are curving, for your own record">
        </label>
      </div>
      <p class="cvHint" id="cvHint"></p>

      <div id="cvPreview" class="cvPreview">Working out the preview…</div>

      <div class="foot">
        ${anyCurve ? `<button class="btn danger" id="cvRemove">Remove
          ${scope ? 'their' : 'all'} curves</button>` : ''}
        <span class="spacer"></span>
        <button class="btn" id="cvCancel">Cancel</button>
        <button class="btn ai" id="cvApply" disabled>Apply curve</button>
      </div>
    </div></div>`;

  const kindOf = () => CURVE_KINDS.find(k => k.id === $('#cvKind').value);
  let latest = null;

  function syncForm() {
    const k = kindOf();
    $('#cvHint').textContent = k.hint;
    $('#cvValWrap').hidden = !k.needs;
    if (k.needs) {
      $('#cvSuffix').textContent = k.suffix;
      if (document.activeElement !== $('#cvVal')) $('#cvVal').value = k.dflt;
    }
  }

  async function preview() {
    const k = kindOf();
    const body = {
      kind: k.id, scope: $('#cvScope').value, only: scope,
      amount: k.needs === 'amount' ? +$('#cvVal').value : 0,
      target: k.needs === 'target' ? +$('#cvVal').value : null,
    };
    $('#cvApply').disabled = true;
    try {
      latest = await api(`/a/${courseId}/${assignmentId}/curve`, { body });
      $('#cvPreview').innerHTML = curvePreviewHTML(latest);
      $('#cvApply').disabled = !latest.n_changed;
    } catch (err) {
      latest = null;
      $('#cvPreview').innerHTML = `<div class="callout bad">${esc(err.message)}</div>`;
    }
  }

  const debounced = (() => {
    let t;
    return () => { clearTimeout(t); t = setTimeout(preview, 250); };
  })();

  $('#cvKind').onchange = () => { syncForm(); preview(); };
  $('#cvScope').onchange = preview;
  $('#cvVal').oninput = debounced;
  $('#cvCancel').onclick = () => { host.innerHTML = ''; };
  // A curve changes nothing in Canvas, so there is no server token to spend;
  // the shell's own dialog still asks, because the browser's built-in prompt
  // is the one thing the front-end contract rules out. Cancel rebuilds this
  // dialog from markup alone, which leaves it with no handlers, so reopen it.
  $('#cvApply').onclick = () => {
    if (!latest || !latest.n_changed) return;
    const k = kindOf();
    const what = latest.criterion_label === 'whole score'
      ? 'the whole score' : `"${latest.criterion_label}"`;
    const body = {
      kind: k.id, scope: $('#cvScope').value, only: scope,
      amount: k.needs === 'amount' ? +$('#cvVal').value : 0,
      target: k.needs === 'target' ? +$('#cvVal').value : null,
      label: $('#cvLabel').value.trim(), apply: true,
    };
    askConfirm({
      summary: `Apply ${k.label.replace(/…$/, '')} to ${what} for `
        + `${latest.n_changed} student(s).`,
      detail: `Class average ${latest.before.mean} to ${latest.after.mean} `
        + `(${latest.before.mean_pct}% to ${latest.after.mean_pct}%).`,
    }, async () => {
      try {
        const done = await api(`/a/${courseId}/${assignmentId}/curve`, { body });
        S.ws = await api(`/a/${courseId}/${assignmentId}`);
        host.innerHTML = '';
        setStatus(`curve applied to ${done.n_changed} student(s)`, 'ok');
        render();
      } catch (err) { setStatus('curve failed: ' + err.message, 'err'); }
    }, { title: 'Apply this curve?', verb: 'Apply curve',
         note: 'Nothing is sent to Canvas. The earned scores are kept underneath, '
               + 'and Remove curves puts them back exactly.',
         onCancel: () => openCurve(only) });
  };
  const rm = $('#cvRemove');
  if (rm) rm.onclick = () => {
    askConfirm({
      summary: scope
        ? `Remove any curve from the ${scope.length} selected student(s).`
        : 'Remove every curve on this assignment.',
      detail: 'Scores go back to what was earned.',
    }, async () => {
      try {
        const done = await api(`/a/${courseId}/${assignmentId}/curve`,
          { body: { remove: true, only: scope } });
        S.ws = await api(`/a/${courseId}/${assignmentId}`);
        host.innerHTML = '';
        setStatus(`curve removed from ${(done.removed || []).length} student(s)`, 'ok');
        render();
      } catch (err) { setStatus('could not remove: ' + err.message, 'err'); }
    }, { title: 'Remove the curve?', verb: 'Remove it',
         note: 'Nothing is sent to Canvas.',
         onCancel: () => openCurve(only) });
  };

  syncForm();
  preview();
}

function curvePct(score, max) {
  const n = Number(score), m = Number(max);
  if (!m || !isFinite(n)) return null;
  return Math.round(1000 * n / m) / 10;
}
function curvePassLine(scale) {
  const cuts = Object.values(scale || {}).map(Number).filter(v => isFinite(v));
  return cuts.length ? Math.min(...cuts) : 60;
}
function noWrittenWork(uid) {
  const s = (S.ws && S.ws.extracted && S.ws.extracted[String(uid)]) || null;
  const mans = ((s && s.quiz_review) || []).filter(q => q && q.manual);
  if (!mans.length) return false;
  return mans.every(q => {
    const t = String(q.response || '').replace(/\s+/g, ' ').trim();
    return !t || /^\(no written answer\)$/i.test(t)
      || /^(n\s*\/\s*a|na|none|blank|skipped)\.?$/i.test(t);
  });
}
function curvePreviewHTML(p) {
  const bandRow = (before, after) => {
    const rows = before.distribution.map((b, i) => {
      const a = after.distribution[i];
      const moved = a.count - b.count;
      // Fewer F's is students moving up, not a loss. More A's is the same
      // direction. The sign on the count is not the direction of the grade.
      const failing = b.letter === 'F';
      const up = failing ? moved < 0 : moved > 0;
      const down = failing ? moved > 0 : moved < 0;
      const label = !moved ? '—'
        : (failing && moved < 0) ? `${-moved} up`
        : (failing && moved > 0) ? `${moved} more`
        : (moved > 0 ? '+' + moved : String(moved));
      return `<tr>
        <td class="ltrCell">${b.letter}</td>
        <td>${b.count} <span class="muted">(${(100 * b.share).toFixed(0)}%)</span></td>
        <td class="arrowCell">→</td>
        <td>${a.count} <span class="muted">(${(100 * a.share).toFixed(0)}%)</span></td>
        <td class="${up ? 'up' : down ? 'down' : 'muted'}">${label}</td>
      </tr>`;
    }).join('');
    return `<table class="cvBands"><thead><tr><th></th><th>now</th><th></th>
      <th>after</th><th>change</th></tr></thead><tbody>${rows}</tbody></table>`;
  };

  const rows = (p.rows || []).filter(r => r.delta);
  const nudge = [];
  if (p.n_lowered_blocked) {
    nudge.push(`<div class="callout bad"><b>This curve would have lowered
      ${p.n_lowered_blocked} student(s), so it does nothing for them.</b>
      A curve here never takes points away. If you meant to raise the average,
      check that your target is above the current one.</div>`);
  }
  if (p.n_capped) {
    nudge.push(`<div class="callout"><b>${p.n_capped} student(s) hit the maximum</b>
      and gained less than the others, because a curve cannot push a score above
      full marks.</div>`);
  }
  if ((p.ungraded_skipped || []).length) {
    nudge.push(`<div class="callout">${p.ungraded_skipped.length} selected
      student(s) are not graded yet, so there is nothing to curve for them.</div>`);
  }

  return `
    ${nudge.join('')}
    <div class="cvStats">
      <div><span class="cvBig">${p.n_changed}</span><span>of ${p.n_selected}
        students change</span></div>
      <div><span class="cvBig">${p.before.mean} → ${p.after.mean}</span>
        <span>class average (${p.before.mean_pct}% → ${p.after.mean_pct}%)</span></div>
      <div><span class="cvBig">+${num(p.mean_gain)}</span><span>average gain,
        biggest +${num(p.biggest_gain)}</span></div>
    </div>
    <div class="cvSplit">
      <div>
        <h4>Letter grades</h4>
        ${bandRow(p.before, p.after)}
      </div>
      <div>
        <h4>Who changes${rows.length > 12 ? ` (top 12 of ${rows.length})` : ''}</h4>
        <table class="cvRows"><tbody>
        ${rows.slice(0, 12).map(r => `<tr>
          <td>${esc(studentLabel(r))}</td>
          <td class="mono">${num(r.before)} → ${num(r.after)}</td>
          <td class="mono up">+${num(r.delta)}</td>
          <td>${r.letter_before !== r.letter_after
            ? `<span class="ltrMove">${esc(r.letter_before)}<svg class="ltrIco" viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M2 6.2h6.2V3.2L14 8l-5.8 4.8V9.8H2z"/></svg>${esc(r.letter_after)}</span>`
            : (r.letter_after === 'F' && r.delta > 0 && noWrittenWork(r.user_id)
              ? `<span class="ltrHold" title="Nothing was turned in for the written questions, so those points are 0. ${num(r.after)} of ${num(p.scope_max)} is ${curvePct(r.after, p.scope_max)}%, and a D starts at ${curvePassLine(p.scale)}%.">F <span>no writing</span></span>`
              : (r.letter_after === 'F' && r.delta > 0
              ? `<span class="ltrHold" title="${curvePct(r.after, p.scope_max)}% of ${num(p.scope_max)}. A D starts at ${curvePassLine(p.scale)}%.">F <span>${curvePct(r.after, p.scope_max)}%</span></span>`
              : `<span class="muted">${esc(r.letter_after)}</span>`))}</td>
        </tr>`).join('')}
        ${!rows.length ? '<tr><td colspan="4" class="muted">Nobody changes.</td></tr>' : ''}
        </tbody></table>
      </div>
    </div>
    <p class="cvFoot">Scope: <b>${esc(p.criterion_label)}</b>
      (max ${num(p.scope_max)}). Letter bands come from
      <code>grade_scale</code> in config.json.</p>`;
}

/* ------------------------------------------------------------------ push */
function openPush(only) {
  const { courseId, assignmentId } = S.ids;
  const host = $('#modalHost');
  const scope = (only && only.length) ? only : null;
  const inScope = scope ? new Set(scope.map(String)) : null;
  const nTicked = Object.entries((S.ws.draft && S.ws.draft.students) || {})
    .filter(([uid, e]) => e && e.post_comment && (e.comment || '').trim()
      && (!inScope || inScope.has(String(uid)))).length;
  host.innerHTML = `<div class="modalBack"><div class="modal">
      <h3>Push grades to Canvas${scope ? ` — ${scope.length} selected` : ''}</h3>
      <div class="sub">This writes into your gradebook. The plan below is worked
        out read-only and changes nothing; nothing reaches Canvas until you press
        <b>Post for real</b> and confirm.${scope
          ? ' Only the students you selected are included.' : ''}</div>
      <label class="fieldLabel" for="pushComments" style="margin:12px 0 6px">Comments to Canvas</label>
      <select id="pushComments" style="margin-bottom:12px">
        <option value="none" selected>Don't post comments (scores only)</option>
        <option value="selected">Only comments I ticked on the student panel${
          nTicked ? ` (${nTicked})` : ''}</option>
        <option value="all">Every student's comment</option>
      </select>
      <p class="hint" style="margin:-6px 0 12px">Claude's comments stay off unless you tick
        them on a student. "Every student's comment" is the old all-or-nothing dump.</p>
      <div class="log" id="pushLog">Working out what would be written…</div>
      <div class="foot">
        <button class="btn" id="pushCancel">Cancel</button>
        <button class="btn danger" id="pushGo" disabled>Post for real</button>
      </div></div></div>`;
  $('#pushCancel').onclick = () => { host.innerHTML = ''; };
  // Turning comments on or off changes what would be written, so the plan is
  // worked out again rather than left on screen describing the other setting.
  $('#pushComments').onchange = () => { $('#pushGo').disabled = true; plan(); };

  /* The plan, every time this opens. It is the dry run under another name --
     the same read-only call, which is why it can just happen: what the roster
     cannot tell you is which students are about to be left out and why, and a
     score you can see is not proof it will be written (one still flagged for
     review is held back). Asking for that by button meant it could be skipped
     on the way to a live gradebook write. */
  const plan = async () => {
    const log = $('#pushLog');
    if (!log) return;                       // closed while this was in flight
    log.textContent = 'Working out what would be written…';
    try {
      const { job } = await api(`/a/${courseId}/${assignmentId}/push`,
        { body: { dry_run: true, comments: $('#pushComments').value, only: scope } });
      const poll = async () => {
        const info = await api('/jobs/' + job);
        const box = $('#pushLog');
        if (!box) return;                   // closed while this was in flight
        if (info.state === 'running') return setTimeout(poll, 500);
        if (info.state === 'error') { box.textContent = 'Failed: ' + info.error; return; }
        const r = info.result || {};
        const write = r.would_post || [], held = r.skipped || [];
        // Held back first: the roster already shows every score, so the part
        // worth reading here is who is about to be left out -- a score you can
        // see is not proof it will be written. A curved score shows its
        // arithmetic, because writing a number the instructor never saw would
        // be the worst surprise of the lot.
        box.textContent = [
          (r.comment_mode === 'all' ? 'SCORES + ALL COMMENTS'
            : r.comment_mode === 'selected'
              ? `SCORES + ${r.comments_n || 0} SELECTED COMMENT${(r.comments_n || 0) === 1 ? '' : 'S'}`
              : 'SCORES ONLY (no comments)'),
          `WILL WRITE ${write.length} · HELD BACK ${held.length}`,
          r.late_cleared_n ? `\nLATE: ${r.late_note}` : '',
          held.length ? '\nHELD BACK - nothing is written for these:\n'
            + held.map(s => `  ${studentLabel(s)}: ${s.why}`).join('\n') : '',
          write.length ? '\nWILL WRITE:\n'
            + write.map(s => `  ${studentLabel(s)}: ${s.score}`
              + (s.curved_by ? `   (${s.earned} earned ${s.curved_by > 0 ? '+' : ''}${
                  s.curved_by}${s.curved_by > 0 ? ' curve' : ' late'})` : '')
              + (s.comment ? '   + comment' : '')
              + (s.clear_late ? '   + Canvas late status set to None' : '')).join('\n') : '',
        ].filter(Boolean).join('\n');
        $('#pushGo').disabled = !write.length;
      };
      poll();
    } catch (err) {
      const box = $('#pushLog');
      if (box) box.textContent = 'Failed: ' + err.message;
    }
  };
  // Worked out as soon as the dialog opens. It changes nothing, so there is
  // nothing to press: the plan is not a step, it is the dialog's content.
  plan();

  $('#pushGo').onclick = () => {
    const commentMode = ($('#pushComments') || {}).value || 'none';

    // The write runs as a job, and the server refuses the first attempt from
    // inside that job -- so the refusal arrives on the finished job, not on the
    // HTTP response. postConfirmed only ever sees the HTTP side, which is why
    // it never asked and every real push died reporting its own confirmation
    // summary as the error. This is the job-level handshake instead, the same
    // one every other confirmed write in the app uses.
    runJobConfirmed(
      scope ? `Pushing ${scope.length} grade(s) to Canvas` : 'Pushing grades to Canvas',
      token => api(`/a/${courseId}/${assignmentId}/push`,
        { body: { dry_run: false, comments: commentMode, only: scope,
                  show: true, confirm: token } }),
      async r => {
        const n = (r.posted || []).length, bad = (r.failed || []).length;
        setStatus(`pushed ${n} grade(s)`
          + (bad ? `, ${bad} failed` : '')
          + (n ? ' · students can see them' : ''),
          bad ? 'err' : 'ok');
        // The push recorded what Canvas now holds; show it without waiting for
        // the pull timer.
        try {
          S.ws = await api(`/a/${courseId}/${assignmentId}`);
          renderHeaderActions();
          render();
        } catch (_) { /* the job dialog still holds the result */ }
      },
      { title: scope ? `Push grades for ${scope.length} student(s)?`
                     : 'Push these grades to Canvas?',
        verb: 'Post grades',
        note: (commentMode === 'all' ? 'Every student comment will be written as well.'
              : commentMode === 'selected' ? 'Only comments you ticked on the student panel will be written.'
                            : 'Scores only, no comments.')
              + ' Students can see these grades as soon as they are posted.',
        // Cancelling restores the dialog underneath from markup alone, which
        // leaves it with no handlers at all, so put a live one back.
        onCancel: () => openPush(only) });
  };
}

/* -------------------------------------------------------- letters & curves */
/* Mirrors courseforge/curve.py: cutoffs are the minimum percent per letter,
   and anything under the lowest is an F. The scale comes from config.json via
   /health so the charts match the institution's own bands. */
function gradeScale() {
  return (S.health && S.health.grade_scale) || { A: 90, B: 80, C: 70, D: 60 };
}
function gradeLadder() {
  return Object.entries(gradeScale())
    .map(([name, cut]) => [name, +cut])
    .sort((a, b) => b[1] - a[1]);
}
function letterOf(pct) {
  if (pct == null || isNaN(pct)) return '';
  for (const [name, cut] of gradeLadder()) if (pct >= cut) return name;
  return 'F';
}
function letterBands(percents) {
  const steps = gradeLadder();
  const names = steps.map(([n]) => n).concat('F');
  const counts = Object.fromEntries(names.map(n => [n, 0]));
  percents.forEach(p => { counts[letterOf(p)] += 1; });
  const total = percents.length;
  return names.map((name, i) => ({
    letter: name, count: counts[name],
    share: total ? counts[name] / total : 0,
    low: name === 'F' ? 0 : steps[i][1],
    high: name === 'F' ? (steps.length ? steps[steps.length - 1][1] : 0)
      : (i ? steps[i - 1][1] : null),
  }));
}

/* Mirrors curve.is_scored in Python. A missing submission is not a zero: it has
   no score, it is reported as a non-submission, and it stays out of every
   average and every letter band. Older drafts stored a fabricated 0 for these,
   so the marker is checked as well as the total. */
const UNSCORED_SOURCES = ['auto-skip', 'no-submission'];
function isScored(e) {
  return !!e && e.total != null && !UNSCORED_SOURCES.includes(e.source);
}
/* Students could see a finished grade when this assignment was last scanned.
   Scans from before scan_live existed fall back to what Canvas shows now. */
function liveAtScan(s) {
  if (!s) return false;
  if (typeof s.scan_live === 'boolean') return s.scan_live;
  return s.canvas_score != null && !!s.canvas_posted_at;
}
function unscoredReason(s, e) {
  if (isScored(e)) return '';
  if (s && s.status === 'unsubmitted') return 'no submission';
  if (e && e.unscored_reason) return e.unscored_reason;
  if (e) return 'nothing readable';
  return 'not graded yet';
}

/* The score that counts: earned, minus a syllabus late dock, plus any curve.
   The server keeps final_total in step whenever those change. */
function lateOff(e) {
  const full = lateFull(e);
  if (!full) return 0;
  return Math.round(full * lateShareOf(e)) / 100;
}
/* The whole syllabus penalty, before the late slider scales it. */
function lateFull(e) {
  const lp = e && e.late_penalty;
  if (!lp || !lp.applied) return 0;
  return +(lp.points || 0);
}
/* Mirrors latepolicy.share_of: how much of the penalty is applied, 0 to 100.
   No penalty attached reads as 0, because that is what the score carries. */
function lateShareOf(e) {
  const lp = e && e.late_penalty;
  if (!lp || !lp.applied) return 0;
  const raw = e.late_share;
  if (raw == null || !isFinite(+raw)) return 100;
  return Math.max(0, Math.min(100, Math.round(+raw / 10) * 10));
}
const LATE_KINDS = ['percent_per_day', 'percent_per_hour', 'flat_percent', 'none_accepted'];
/* The late-penalty slider under the score. Only for late work, and only when
   Studio applies the syllabus rule itself; Canvas's own late policy is
   Canvas's to apply. */
function lateBox(s, e) {
  if (!s || !(s.late || (e && e.late_penalty && e.late_penalty.applied))) return '';
  const policy = (S.ws && (S.ws.late_policy || S.ws.draft.late_policy)) || {};
  if (policy.canvas_applies || policy.kind === 'canvas') return '';
  if (!LATE_KINDS.includes(policy.kind)) {
    return `<div class="lateBox muted"><b>Late penalty</b> · ${esc(policy.summary
      || 'No late-work rule found in the syllabus.')} There is no penalty to scale.</div>`;
  }
  const share = lateShareOf(e);
  const lp = (e && e.late_penalty) || {};
  const note = !isScored(e)
    ? 'Takes effect once this student has a score.'
    : (lp.waived && !lp.applied
      ? esc(lp.summary || 'Waived by your instructions.') + ' Move the slider to put some of it back.'
      : (!lp.applied && lp.summary ? esc(lp.summary)
        : `Syllabus: ${esc(policy.summary || '')}`));
  const ticks = Array.from({ length: 11 }, (_, i) => `<span>${i * 10}</span>`).join('');
  return `<div class="lateBox">
      <div class="critHead">
        <label class="lbl" for="lateShare">Late penalty applied</label>
        <span class="val" id="lateVal">${lateValText(e, share)}</span>
      </div>
      <input type="range" id="lateShare" min="0" max="100" step="10" value="${share}"
        aria-describedby="lateNote">
      <div class="ticks">${ticks}</div>
      <div class="lateNote" id="lateNote">${note}</div>
    </div>`;
}
function lateValText(e, share) {
  const full = lateFull(e);
  const off = full ? Math.round(full * share) / 100 : 0;
  const pts = full
    ? ` <span class="sub">−${num(off)} of ${num(full)} pts</span>` : '';
  return `${share}%${pts}`;
}
function wholePoints(n) {
  const x = Number(n);
  if (!isFinite(x)) return n;
  return Math.floor(x + 0.5);
}
function finalOf(e) {
  if (!isScored(e)) return null;
  let value;
  if (e.final_total != null) value = +e.final_total;
  else {
    const off = lateOff(e);
    value = off ? (+e.total - off) : +e.total;
  }
  const curved = e.curve && (e.curve.flat || (e.curve.by_criterion
    && Object.keys(e.curve.by_criterion).length));
  if (e.total_only && !curved) return value;
  return wholePoints(value);
}
function earnedOf(e) { return isScored(e) ? e.total : null; }
function scoreAdjustNote(e, earned, bump) {
  const off = lateOff(e);
  if (!off && !bump) return aiDeltaNote(e);
  const bits = [`earned ${num(earned)}`];
  if (off) bits.push(`late −${num(off)}`);
  if (bump) bits.push(`curved +${num(bump)}`);
  return bits.join(', ');
}
function curveDelta(e) {
  const f = finalOf(e), b = earnedOf(e);
  if (f == null || b == null) return 0;
  return Math.round((f - b + lateOff(e)) * 100) / 100;
}
/* A criterion's score including any curve aimed at that criterion, so the
   per-criterion charts show the effect of a curve instead of the old numbers. */
function critScore(e, cid) {
  const raw = +((e.scores || {})[cid] || 0);
  const add = +(((e.curve || {}).by_criterion || {})[cid] || 0);
  const crit = rubric().find(c => c.id === cid);
  const top = crit ? +crit.points : Infinity;
  return Math.max(0, Math.min(raw + add, top));
}

/* How late, not just whether. A paper six hours past the deadline and one 27
   days past are different situations to grade, and both were showing the same
   word. Canvas's own `late` flag decides IF it counts as late: seconds_late is
   filled in for a student who never submitted at all, so reading it alone would
   label every missing submission "7.7 days late". The flag gates it; the number
   only supplies the size. */
function lateText(s) {
  if (!s || !s.late) return '';
  const secs = +(s.seconds_late || 0);
  if (secs <= 0) return 'late';                 // flagged, but no figure given
  const days = secs / 86400;
  if (days >= 10) return `${Math.round(days)} days late`;
  const shown = Math.round(days * 10) / 10;
  // Below a tenth of a day the decimal reads 0.0, which says nothing.
  if (shown >= 0.1) return `${shown} day${shown === 1 ? '' : 's'} late`;
  return `${Math.max(1, Math.round(secs / 60))} min late`;
}

/* ---------------------------------------------------------------- roster */
function rubric() { return (S.ws.draft && S.ws.draft.rubric) || []; }
function entryOf(uid) { return ((S.ws.draft && S.ws.draft.students) || {})[String(uid)] || null; }
function infoOf(uid) { return (S.ws.extracted || {})[String(uid)] || {}; }

/* Surname, the way a roster is usually read. */
function sortKey(s) { return String(s.name || '').trim().split(/\s+/).pop(); }

const ROSTER_SORTS = ['submitted', 'name', 'name-desc', 'grade-desc', 'grade-asc', 'late'];
S.sort = S.sort || 'submitted';

function byName(a, b) {
  return sortKey(a).localeCompare(sortKey(b))
    || String(a.name || '').localeCompare(String(b.name || ''));
}

/* A missing score is not a zero. It sorts after every real grade, either way. */
function gradeOf(s) {
  const n = finalOf(entryOf(s.user_id));
  return n == null ? null : +n;
}

function rosterOrder(a, b) {
  const mode = S.sort || 'submitted';
  if (mode === 'name') return byName(a, b);
  if (mode === 'name-desc') return byName(b, a);
  if (mode === 'grade-desc' || mode === 'grade-asc') {
    const ga = gradeOf(a), gb = gradeOf(b);
    if (ga == null && gb == null) return byName(a, b);
    if (ga == null) return 1;
    if (gb == null) return -1;
    if (ga !== gb) return mode === 'grade-desc' ? gb - ga : ga - gb;
    return byName(a, b);
  }
  if (mode === 'late') {
    const rank = s => s.status === 'unsubmitted' ? 2 : (s.late ? 0 : 1);
    const gap = rank(a) - rank(b);
    if (gap) return gap;
    const lateGap = (+(b.seconds_late || 0)) - (+(a.seconds_late || 0));
    if (a.late && b.late && lateGap) return lateGap;
    return byName(a, b);
  }
  // Turned in first, then last name. Empty rows stay at the bottom so grading
  // can run top to bottom without skipping past people who handed nothing in.
  return ((a.status === 'unsubmitted') - (b.status === 'unsubmitted')) || byName(a, b);
}

function students() {
  const all = Object.values(S.ws.extracted || {});
  all.sort(rosterOrder);
  return all.filter(s => {
    if (S.query && ![s.name, s.sortable_name, studentLabel(s)].join(' ')
      .toLowerCase().includes(S.query)) return false;
    const e = entryOf(s.user_id);
    if (S.filter === 'review') return reviewHold(s);
    if (S.filter === 'human') return !!(e && (e.source === 'human' || e.source === 'canvas'));
    // Not live on Canvas when the assignment was scanned. A slider score or a
    // push since then does not take anyone off this list; a re-sync does.
    if (S.filter === 'ungraded') return !liveAtScan(s);
    // The working set most of the time: whoever handed something in. Everyone
    // else is a roster entry with nothing to read, and scrolling past them is
    // the whole reason this chip exists.
    if (S.filter === 'submitted') return s.status !== 'unsubmitted';
    if (S.filter === 'missing') return s.status === 'unsubmitted';
    if (S.filter === 'conflict') return !!(e && e.conflict);
    return true;
  });
}

/* ------------------------------------------------------------- selection */
function closeRosterMenu() {
  const m = $('#rosterMenu');
  if (m) m.remove();
  document.removeEventListener('pointerdown', _rosterMenuAway, true);
  document.removeEventListener('keydown', _rosterMenuKey, true);
}
function _rosterMenuAway(ev) {
  const m = $('#rosterMenu');
  if (m && !m.contains(ev.target)) closeRosterMenu();
}
function _rosterMenuKey(ev) {
  if (ev.key === 'Escape') { ev.preventDefault(); closeRosterMenu(); }
}
function menuIdsFor(uid) {
  const key = String(uid);
  if (S.picked.size > 1 && S.picked.has(key)) return pickedIds();
  return [key];
}
/* The conflict buttons sit on one student, but a multi-select means the
   choice is for every selected student who actually disagrees with Canvas. */
function conflictChoiceIds(uid) {
  return menuIdsFor(uid).filter(id => {
    const row = entryOf(id);
    return row && row.conflict;
  });
}
function selectionState(ids) {
  ids = (ids || []).map(String);
  const n = ids.length;
  const entries = ids.map(uid => entryOf(uid)).filter(Boolean);
  const canAI = !(S.health && S.health.claude && S.health.claude.logged_in === false);
  const allOk = n > 0 && entries.length === n && entries.every(e => e && e.human_ok);
  return {
    ids, n, entries, canAI, allOk,
    flagged: ids.filter(uid => reviewHold((S.ws.extracted || {})[String(uid)] || { user_id: uid })).length,
    clashes: entries.filter(e => e && e.conflict).length,
  };
}
function selectionItems(st) {
  /* One list for the bulk bar and the right-click menu, so a new action cannot
     land on shift-click and be missing from the context menu (or the other way
     around). `label` is the menu wording; `bar` is the shorter bulk-bar label. */
  const one = st.n === 1;
  const items = [];
  if (st.clashes) {
    items.push({ id: 'takeCanvas', label: 'Take Canvas score', bar: 'Take Canvas',
                 title: 'Replace the local score with what Canvas holds, for these students in conflict' });
    items.push({ id: 'keepMine', label: 'Keep my score', bar: 'Keep mine',
                 title: 'Keep the local score for these; the next push overwrites Canvas' });
    items.push({ sep: true });
  }
  items.push({ id: 'regrade',
               label: one ? 'Re-grade this student' : 'Re-grade ' + st.n + ' students',
               bar: 'Re-grade', cls: 'ai', disabled: !st.canAI,
               title: 'Re-grade just these students, using the custom instructions' });
  items.push({ id: 'review',
               label: st.allOk ? 'Un-mark reviewed' : 'Mark as reviewed',
               bar: st.allOk ? 'Un-mark reviewed' : 'Mark reviewed',
               title: st.allOk ? 'Put the review flag back on these'
                               : 'Clears the review block so these can be pushed to Canvas' });
  items.push({ id: 'insights', label: 'Insights',
               title: 'Charts and a written read for just these students' });
  items.push({ id: 'overlap', label: 'Overlap check',
               disabled: !(st.canAI && st.n >= 2),
               title: st.n >= 2 ? 'Compare what these students wrote for shared wording'
                                : 'Pick at least two students' });
  items.push({ id: 'curve', label: 'Curve…',
               title: 'Curve just these students' });
  items.push({ sep: true });
  items.push({ id: 'push',
               label: one ? 'Push grade to Canvas…' : 'Push ' + st.n + ' grades to Canvas…',
               bar: 'Push…', danger: true,
               title: 'Push only these students to Canvas. Asks first, and lets you choose whether they can see the grades' });
  return items;
}
function runSelectionAction(name, ids) {
  closeRosterMenu();
  const st = selectionState(ids);
  if (name === 'takeCanvas') return resolveConflicts(st.ids, 'canvas');
  if (name === 'keepMine') return resolveConflicts(st.ids, 'mine');
  if (name === 'regrade') return doGrade(st.ids);
  if (name === 'review') return doMarkReviewed(!st.allOk, st.ids);
  if (name === 'insights') {
    S.picked = new Set(st.ids);
    S.insightsScope = 'selection'; S.view = 'insights';
    renderHeaderActions(); render();
    return;
  }
  if (name === 'overlap') return doOverlap(st.ids);
  if (name === 'curve') return openCurve(st.ids);
  if (name === 'push') return openPush(st.ids);
}
function onRosterContext(ev) {
  const row = ev.target.closest && ev.target.closest('#roster .rrow');
  if (!row || !row.dataset.uid) return;
  ev.preventDefault();
  ev.stopPropagation();
  if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
  openRosterMenu(ev, menuIdsFor(row.dataset.uid));
}
function openRosterMenu(ev, ids) {
  closeRosterMenu();
  const st = selectionState(ids);
  const items = selectionItems(st);
  const one = st.n === 1;
  const head = one
    ? esc(studentLabel(Object.values(S.ws.extracted || {}).find(s => String(s.user_id) === st.ids[0]) || { name: st.ids[0] }))
    : esc(st.n + ' students');
  const menu = document.createElement('div');
  menu.id = 'rosterMenu';
  menu.className = 'rosterMenu';
  menu.setAttribute('role', 'menu');
  menu.innerHTML = `<div class="rosterMenuHead">${head}</div>` + items.map(it => {
    if (it.sep) return '<div class="sep"></div>';
    return `<button type="button" role="menuitem" data-act="${it.id}"${
      it.disabled ? ' disabled' : ''}${it.danger ? ' class="danger"' : ''}${
      it.title ? ` title="${esc(it.title)}"` : ''}>${esc(it.label)}</button>`;
  }).join('');
  document.body.appendChild(menu);
  const pad = 8;
  const x = Math.min(ev.clientX, window.innerWidth - menu.offsetWidth - pad);
  const y = Math.min(ev.clientY, window.innerHeight - menu.offsetHeight - pad);
  menu.style.left = Math.max(pad, x) + 'px';
  menu.style.top = Math.max(pad, y) + 'px';
  menu.querySelectorAll('[data-act]').forEach(btn => {
    btn.onclick = () => { if (!btn.disabled) runSelectionAction(btn.dataset.act, st.ids); };
  });
  const first = menu.querySelector('[data-act]:not([disabled])');
  if (first) first.focus();
  setTimeout(() => {
    document.addEventListener('pointerdown', _rosterMenuAway, true);
    document.addEventListener('keydown', _rosterMenuKey, true);
  }, 0);
}

function pickedIds() { return [...S.picked]; }

/* Shift-click takes a range, ctrl/cmd-click toggles one, a plain click goes back
   to single-student review. The range runs over the rows as currently filtered,
   which is what you see and therefore what you mean. */
function onRowClick(ev, index, uid) {
  const list = students();
  if (ev.shiftKey && list.length) {
    const from = Math.min(S.anchor, index), to = Math.max(S.anchor, index);
    for (let i = from; i <= to; i++) S.picked.add(String(list[i].user_id));
    S.sel = index;
  } else if (ev.ctrlKey || ev.metaKey) {
    const key = String(uid);
    if (S.picked.has(key)) S.picked.delete(key); else S.picked.add(key);
    S.sel = index; S.anchor = index;
  } else {
    S.picked.clear();
    S.sel = index; S.anchor = index;
  }
  render();
}

function selectAllShown() {
  students().forEach(s => S.picked.add(String(s.user_id)));
  render();
}
function clearPicked() {
  if (!S.picked.size) return;
  S.picked.clear();
  if (S.insightsScope === 'selection') S.insightsScope = 'class';
  render();
}

function renderBulkBar() {
  const bar = $('#bulkBar');
  if (!bar) return;
  const n = S.picked.size;
  if (!n) {
    bar.className = 'bulkBar';
    bar.innerHTML = `<span class="bulkHint">Shift-click for a range, ctrl-click to add one. Right-click a name for the same actions, including push.</span>`;
    return;
  }
  const st = selectionState(pickedIds());
  const acts = selectionItems(st).filter(it => !it.sep);
  bar.className = 'bulkBar show';
  bar.innerHTML = `
    <div class="bulkHead">
      <b>${n} selected</b>
      ${st.flagged ? `<span class="bulkNote">${st.flagged} flagged for review</span>` : ''}
      ${st.clashes ? `<span class="bulkNote">${st.clashes} in conflict with Canvas</span>` : ''}
      <button class="btn sm" id="bulkAll">Select all shown</button>
      <button class="btn sm" id="bulkNone">Clear</button>
    </div>
    <div class="bulkActs">${acts.map(it =>
      `<button type="button" class="btn sm${it.cls ? ' ' + it.cls : ''}${
        it.danger ? ' danger' : ''}" data-act="${it.id}"${
        it.disabled ? ' disabled' : ''}${
        it.title ? ` title="${esc(it.title)}"` : ''}>${esc(it.bar || it.label)}</button>`
    ).join('')}</div>`;
  $('#bulkAll').onclick = selectAllShown;
  $('#bulkNone').onclick = clearPicked;
  bar.querySelectorAll('[data-act]').forEach(btn => {
    btn.onclick = () => { if (!btn.disabled) runSelectionAction(btn.dataset.act, st.ids); };
  });
}

async function doMarkReviewed(reviewed, only) {
  const { courseId, assignmentId } = S.ids;
  const ids = (only && only.length) ? only.map(String) : pickedIds();
  try {
    const r = await api(`/a/${courseId}/${assignmentId}/review`,
      { body: { only: ids, reviewed } });
    S.ws = await api(`/a/${courseId}/${assignmentId}`);
    const n = (r.marked || []).length;
    let msg = reviewed
      ? `${n} marked reviewed — they can be pushed now`
      : `${n} put back to needs-review`;
    if ((r.no_score || []).length) {
      msg += `; ${r.no_score.length} still has no score to push`;
    }
    if ((r.not_graded || []).length) {
      msg += `; ${r.not_graded.length} not graded yet, nothing to mark`;
    }
    setStatus(msg, 'ok');
    render();
  } catch (err) { setStatus('could not mark: ' + err.message, 'err'); }
}

function doOverlap(only) {
  const { courseId, assignmentId } = S.ids;
  only = (only && only.length) ? only.map(String) : pickedIds();
  runJob(`Overlap check on ${only.length} submissions`,
    () => api(`/a/${courseId}/${assignmentId}/overlap`, { body: { only } }),
    report => { $('#modalHost').innerHTML = ''; openOverlapReport(report); });
}

function renderRoster() {
  closeRosterMenu();
  const list = students(), host = $('#roster'), keep = host.scrollTop;
  host.innerHTML = '';
  const policy = S.ws.late_policy || (S.ws.draft || {}).late_policy || {};
  if (policy.summary && policy.kind && policy.kind !== 'none') {
    const note = document.createElement('p');
    note.className = 'sub';
    note.style.cssText = 'margin:0 0 8px;padding:0 4px';
    note.textContent = S.ws.late_waiver
      ? policy.summary + ' Your instructions waive that for this assignment.'
      : policy.summary;
    host.appendChild(note);
  }
  list.forEach((s, i) => {
    const e = entryOf(s.user_id);
    const shown = finalOf(e);
    const total = shown != null ? num(shown) : '—';
    const bump = curveDelta(e);
    const noScore = !!e && !isScored(e);
    const dot = e && e.source === 'human' ? '<span class="dot human"></span>'
      : e && e.source === 'canvas' ? '<span class="dot canvas"></span>'
        : reviewHold(s) ? '<span class="dot flag"></span>'
          : s.status === 'unsubmitted' ? '<span class="dot none"></span>' : '';
    const sub = s.status === 'unsubmitted' ? 'no submission'
      : noScore ? esc(unscoredReason(s, e))
        : (e && e.conflict) ? (writtenOpen(s)
          ? `Canvas auto ${num(e.conflict.canvas_score)} · written open`
          : `Canvas has ${num(e.conflict.canvas_score)}`)
          : writtenOpen(s) ? 'written answers not graded'
            : (reviewHold(s) ? 'needs review' : (lateText(s) || `${s.words || 0} words`));
    const picked = S.picked.has(String(s.user_id));
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'rrow' + (picked ? ' picked' : '');
    b.dataset.uid = String(s.user_id);
    b.setAttribute('aria-current', String(i === S.sel));
    b.setAttribute('aria-pressed', String(picked));
    const ok = e && e.human_ok ? '<span class="okTick" title="marked reviewed">✓</span>' : '';
    const clash = e && e.conflict
      ? `<span class="clashMark" title="Canvas holds ${num(e.conflict.canvas_score)}; you have ${total}. Nothing was overwritten.">!</span>` : '';
    const curveMark = bump
      ? `<span class="curveMark" title="includes a curve of +${num(bump)}">↑</span>` : '';
    const lateMark = lateOff(e)
      ? `<span class="tag warn" title="${esc((e.late_penalty && e.late_penalty.summary) || '')}">−${num(lateOff(e))}</span>`
      : ((e && e.late_penalty && e.late_penalty.waived)
        ? `<span class="tag" title="${esc(e.late_penalty.summary || '')}">late waived</span>` : '');
    // Video never gets an automatic score, so the roster has to say which rows
    // are waiting on someone to sit and watch them.
    const vidMark = (s.videos || []).length
      ? `<span class="vidMark" title="${esc((s.videos || []).join(', '))} — plays in the work pane">▶</span>` : '';
    // Is this grade in Canvas, and can the student see it? Quiet glyph: the
    // detail pane has the words.
    const postMark = (s.canvas_score == null || !s.canvas_posted_at) ? ''
      : writtenOpen(s)
        ? '<span class="postMark live" title="Automatic score only. Written answers are still to grade.">●</span>'
        : '<span class="postMark live" title="students can see this score">●</span>';
    const cmtMark = e && e.post_comment && (e.comment || '').trim()
      ? '<span class="cmtMark" title="this comment will be included on the next push">cmt</span>' : '';
    b.innerHTML = `<span><span class="nm">${dot}${esc(studentLabel(s))}${ok}${clash}${vidMark}${cmtMark}</span><span class="sub">${esc(sub)}</span></span>
                   <span class="sc">${postMark}${lateMark}${curveMark}${total}</span>`;
    b.onclick = ev => onRowClick(ev, i, s.user_id);
    host.appendChild(b);
  });
  if (!list.length) {
    const empty = document.createElement('div');
    empty.style.cssText = 'padding:18px;color:var(--muted);font-size:12.5px';
    empty.textContent = 'No students match.';
    host.appendChild(empty);
  }
  host.scrollTop = keep;
  S.anchor = Math.max(0, Math.min(S.anchor, list.length - 1));
  renderBulkBar();
}

/* --------------------------------------------------------------- insights */
const CHART = {
  pad: 34,
  ring: [0.25, 0.5, 0.75, 1],
};

/* Students with no score, split by why, so the dashboard can report them as
   their own category instead of burying them in the F band. */
function unscoredRows() {
  const scoped = S.insightsScope === 'selection' && S.picked.size;
  const out = { no_submission: [], nothing_readable: [], not_graded_yet: [] };
  Object.values(S.ws.extracted || {}).forEach(s => {
    if (scoped && !S.picked.has(String(s.user_id))) return;
    const e = entryOf(s.user_id);
    if (isScored(e)) return;
    const why = unscoredReason(s, e);
    const bucket = why === 'no submission' ? 'no_submission'
      : why === 'not graded yet' ? 'not_graded_yet' : 'nothing_readable';
    out[bucket].push(studentLabel(s));
  });
  return out;
}

function statsByCriterion(includeMissing) {
  const crits = rubric();
  const scoped = S.insightsScope === 'selection' && S.picked.size;
  const rows = Object.values(S.ws.extracted || {}).map(s => ({ s, e: entryOf(s.user_id) }));
  const pool = rows.filter(({ s, e }) => {
    if (scoped && !S.picked.has(String(s.user_id))) return false;
    // A non-submission only enters the arithmetic if the instructor explicitly
    // asks for the "what if I zero these" view, and then as a synthetic 0.
    if (!isScored(e)) return includeMissing && s.status === 'unsubmitted';
    return true;
  });
  const per = crits.map(c => {
    const vals = pool.map(({ e }) => isScored(e) ? critScore(e, c.id) : 0);
    const mean = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
    const sorted = vals.slice().sort((a, b) => a - b);
    const median = sorted.length ? (sorted.length % 2 ? sorted[(sorted.length - 1) / 2]
      : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2) : 0;
    return {
      ...c, n: vals.length, mean, median,
      min: sorted[0] ?? 0, max: sorted[sorted.length - 1] ?? 0,
      pct: c.points ? mean / c.points : 0,
    };
  });
  return { crits, per, pool };
}

function svgWrap(w, h, inner, title) {
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" xmlns="http://www.w3.org/2000/svg"
    font-family="system-ui,-apple-system,Segoe UI,Roboto,sans-serif" role="img"
    aria-label="${esc(title)}"><title>${esc(title)}</title>${inner}</svg>`;
}

/* radar: one axis per rubric criterion, class mean plus optional student */
function radarSVG(per, student) {
  const size = 420, cx = size / 2, cy = size / 2 + 6, R = size / 2 - 74;
  const n = per.length;
  if (n < 3) return '<p class="chartNote">A radar needs at least three criteria — see the bars below.</p>';
  const ang = i => (-Math.PI / 2) + (i * 2 * Math.PI / n);
  const pt = (i, r) => [cx + r * Math.cos(ang(i)), cy + r * Math.sin(ang(i))];
  const poly = vals => vals.map((v, i) => pt(i, Math.max(0, Math.min(1, v)) * R).map(x => x.toFixed(1)).join(',')).join(' ');

  let g = '';
  CHART.ring.forEach(r => {
    g += `<polygon points="${poly(per.map(() => r))}" fill="none"
      stroke="var(--line)" stroke-width="1"/>`;
  });
  per.forEach((c, i) => {
    const [x, y] = pt(i, R);
    g += `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="var(--line)"/>`;
    const [lx, ly] = pt(i, R + 22);
    const anchor = Math.abs(lx - cx) < 6 ? 'middle' : (lx > cx ? 'start' : 'end');
    g += `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="${anchor}"
      dominant-baseline="middle" font-size="12" font-weight="600" fill="var(--muted)">C${i + 1}
      <title>${esc(c.label)}</title></text>`;
  });
  CHART.ring.forEach(r => {
    g += `<text x="${cx + 4}" y="${(cy - r * R).toFixed(1)}" font-size="9"
      fill="var(--muted)" opacity="0.75">${Math.round(r * 100)}%</text>`;
  });

  g += `<polygon points="${poly(per.map(c => c.pct))}" fill="var(--accent)" fill-opacity="0.22"
    stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>`;
  per.forEach((c, i) => {
    const [x, y] = pt(i, c.pct * R);
    g += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.5" fill="var(--accent)"/>`;
  });

  if (student) {
    const vals = per.map(c => c.points ? (+(student.scores || {})[c.id] || 0) / c.points : 0);
    g += `<polygon points="${poly(vals)}" fill="var(--ai)" fill-opacity="0.16"
      stroke="var(--ai)" stroke-width="2" stroke-dasharray="5 3" stroke-linejoin="round"/>`;
    vals.forEach((v, i) => {
      const [x, y] = pt(i, v * R);
      g += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="var(--ai)"/>`;
    });
  }
  return svgWrap(size, size + 10, g, 'Rubric criteria radar');
}

/* horizontal bars: mean % per criterion, weakest first */
function barsSVG(per) {
  const rowH = 44, w = 560, left = 46, right = 58;
  const h = per.length * rowH + 12;
  const sorted = per.map((c, i) => ({ ...c, idx: i })).sort((a, b) => a.pct - b.pct);
  const barW = w - left - right;
  let g = '';
  sorted.forEach((c, row) => {
    const y = row * rowH + 12;
    const fill = c.pct < 0.6 ? 'var(--danger)' : c.pct < 0.78 ? 'var(--warn)' : 'var(--accent)';
    g += `<text x="0" y="${y + 11}" font-size="12" font-weight="700" fill="var(--muted)">C${c.idx + 1}</text>`;
    g += `<rect x="${left}" y="${y}" width="${barW}" height="15" rx="3" fill="var(--line)" opacity="0.55"/>`;
    g += `<rect x="${left}" y="${y}" width="${(barW * c.pct).toFixed(1)}" height="15" rx="3" fill="${fill}"><title>${esc(c.label)}</title></rect>`;
    g += `<text x="${w - right + 8}" y="${y + 12}" font-size="12" font-weight="600"
      fill="var(--ink)">${Math.round(c.pct * 100)}%</text>`;
    g += `<text x="${left}" y="${y + 29}" font-size="10.5" fill="var(--muted)">${esc(
      c.label.length > 62 ? c.label.slice(0, 60) + '…' : c.label)} · mean ${c.mean.toFixed(1)} of ${num(c.points)}</text>`;
  });
  return svgWrap(w, h + 14, g, 'Mean achievement per criterion');
}

/* heatmap: students down, criteria across */
function heatSVG(per, pool) {
  if (!pool.length) return '';
  const cw = 62, ch = 20, left = 132, top = 40;
  const w = left + per.length * cw + 46;
  const h = top + pool.length * ch + 10;
  const rows = pool.slice().sort((a, b) => (finalOf(b.e) || 0) - (finalOf(a.e) || 0));
  let g = '';
  per.forEach((c, i) => {
    g += `<text x="${left + i * cw + cw / 2}" y="${top - 12}" text-anchor="middle"
      font-size="11" font-weight="700" fill="var(--muted)">C${i + 1}<title>${esc(c.label)}</title></text>`;
  });
  g += `<text x="${left + per.length * cw + 8}" y="${top - 12}" font-size="11"
    font-weight="700" fill="var(--muted)">Tot</text>`;
  rows.forEach((r, row) => {
    const y = top + row * ch;
    const label = studentLabel(r.s);
    const name = label.length > 20 ? label.slice(0, 19) + '…' : label;
    g += `<text x="0" y="${y + 14}" font-size="11" fill="var(--ink)">${esc(name)}</text>`;
    per.forEach((c, i) => {
      const v = +(r.e.scores || {})[c.id] || 0;
      const p = c.points ? v / c.points : 0;
      g += `<rect x="${left + i * cw}" y="${y + 2}" width="${cw - 3}" height="${ch - 4}" rx="2"
        fill="var(--accent)" fill-opacity="${(0.10 + 0.85 * p).toFixed(3)}"/>`;
      g += `<text x="${left + i * cw + (cw - 3) / 2}" y="${y + 15}" text-anchor="middle"
        font-size="10" font-weight="600" fill="${p > 0.55 ? '#07130d' : 'var(--muted)'}">${num(v)}</text>`;
    });
    g += `<text x="${left + per.length * cw + 8}" y="${y + 15}" font-size="11"
      font-weight="700" fill="var(--ink)">${num(finalOf(r.e))}</text>`;
  });
  return svgWrap(w, h, g, 'Per-student criterion heatmap');
}

/* histogram of totals */
/* Letter bands, lowest first, each labelled with its count and its share of the
   graded pool. The count alone makes a class of 12 and a class of 40 look the
   same, so both numbers are on the chart rather than only in a tooltip. */
function histSVG(pool, possible) {
  const percents = pool.map(({ e }) =>
    possible ? 100 * (isScored(e) ? finalOf(e) : 0) / possible : 0);
  const bands = letterBands(percents).slice().reverse();      // F .. A
  const peak = Math.max(1, ...bands.map(b => b.count));
  const w = 460, h = 210, bw = 66, gap = 18, base = h - 50;
  const colourFor = name => name === 'F' ? 'var(--danger)'
    : name === 'D' ? 'var(--warn)' : 'var(--accent)';
  let g = '';
  bands.forEach((b, i) => {
    const x = 26 + i * (bw + gap);
    // Leave room above the tallest bar for its two stacked labels, or the
    // count on the biggest band gets clipped by the top of the viewBox.
    const bh = Math.round((b.count / peak) * (base - 46));
    g += `<rect x="${x}" y="${base - bh}" width="${bw}" height="${Math.max(bh, 2)}" rx="4"
      fill="${colourFor(b.letter)}"><title>${b.letter}: ${b.count} of ${percents.length}
      (${(100 * b.share).toFixed(1)}%)</title></rect>`;
    g += `<text x="${x + bw / 2}" y="${base - bh - 21}" text-anchor="middle" font-size="13"
      font-weight="700" fill="var(--ink)">${b.count}</text>`;
    g += `<text x="${x + bw / 2}" y="${base - bh - 7}" text-anchor="middle" font-size="10.5"
      font-weight="600" fill="var(--muted)">${(100 * b.share).toFixed(0)}%</text>`;
    g += `<text x="${x + bw / 2}" y="${base + 18}" text-anchor="middle" font-size="14"
      font-weight="700" fill="var(--ink)">${b.letter}</text>`;
    g += `<text x="${x + bw / 2}" y="${base + 33}" text-anchor="middle" font-size="10"
      fill="var(--muted)">${b.letter === 'F' ? 'under ' + num(b.high)
        : num(b.low) + (b.high == null ? '+' : '–' + (b.high - 0.1).toFixed(1))}</text>`;
  });
  return svgWrap(w, h, g, 'Letter grade distribution');
}

/* The same numbers as text, because a chart is not a table and the exact counts
   are what get read out in a meeting. */
function letterLegend(pool, possible) {
  const percents = pool.map(({ e }) =>
    possible ? 100 * (isScored(e) ? finalOf(e) : 0) / possible : 0);
  const bands = letterBands(percents);
  const n = percents.length;
  const un = unscoredRows();
  // Non-submissions get their own line, outside the letters. They are not an F,
  // and they are not in the percentages above.
  const extra = [
    ['No submission', un.no_submission],
    ['Nothing readable', un.nothing_readable],
    ['Not graded yet', un.not_graded_yet],
  ].filter(([, list]) => list.length);
  return `<div class="legend letterLegend">${bands.map(b =>
    `<div><b>${b.letter}</b> <span>${b.count} of ${n} ·
      ${(100 * b.share).toFixed(1)}% · ${b.letter === 'F'
        ? 'under ' + num(b.high) + '%'
        : num(b.low) + '%' + (b.high == null ? ' and up' : '–' + (b.high - 0.1).toFixed(1) + '%')}
    </span></div>`).join('')}</div>
    ${extra.length ? `<div class="unscoredNote">
      ${extra.map(([label, list]) => `<div><b>${label}</b>
        <span>${list.length} student${list.length > 1 ? 's' : ''} ·
        not counted in the letters or the average${
          list.length <= 6 ? ' · ' + list.map(esc).join(', ') : ''}</span></div>`).join('')}
      <p>These have no score. ${S.includeMissing
        ? 'The charts above are currently counting non-submissions as zero, because you asked for that view.'
        : 'Nothing above includes them.'}</p>
    </div>` : ''}`;
}

function renderInsights() {
  const host = $('#detail');
  const draft = S.ws.draft || {};
  const includeMissing = !!S.includeMissing;
  const { crits, per, pool } = statsByCriterion(includeMissing);
  const possible = draft.points_possible || 0;

  if (!pool.length) {
    host.innerHTML = `<div class="who"><h2>Insights</h2></div>
      <p style="color:var(--muted);max-width:60ch">Nothing is graded yet. Run
      <b>Auto-grade all</b>, or score a few students by hand, and the charts will fill in.</p>`;
    disposeDocx();
    disposePdf();
    $('#workPane').innerHTML = '';
    return;
  }

  const list = students();
  const selected = list[Math.min(S.sel, list.length - 1)];
  const selEntry = selected ? entryOf(selected.user_id) : null;
  const useStudent = selEntry && selEntry.total != null ? selEntry : null;

  const totals = pool.map(({ e }) => isScored(e) ? finalOf(e) : 0).sort((a, b) => a - b);
  const mean = totals.reduce((a, b) => a + b, 0) / totals.length;
  const curvedCount = pool.filter(({ e }) => curveDelta(e)).length;
  const weakest = per.slice().sort((a, b) => a.pct - b.pct)[0];
  const summary = draft.class_summary;

  host.innerHTML = `
    <div class="who"><h2>Insights</h2>
      <span class="id">${esc(draft.assignment_name || '')} · ${pool.length} graded of ${
        (S.insightsScope === 'selection' && S.picked.size) ? S.picked.size + ' selected'
          : Object.keys(S.ws.extracted).length}</span></div>
    ${(S.insightsScope === 'selection' && S.picked.size) ? `<div class="scopeBar">
      Showing <b>${S.picked.size} selected students</b> only.
      <button class="btn sm" id="scopeAll">Show the whole class</button></div>` : ''}

    <div class="insightBar">
      <label class="tick" title="Off by default: a non-submission has no score and
        is reported separately rather than averaged in as a zero.">
        <input type="checkbox" id="chkMissing" ${includeMissing ? 'checked' : ''}>
        also count non-submissions as zero</label>
      <span class="spacer"></span>
      <button class="btn sm ai" id="btnCurve">Curve grades…</button>
      <button class="btn sm" id="btnPrint">Print / Save as PDF</button>
      <button class="btn sm" id="dlAll">Download charts (SVG)</button>
    </div>

    <div class="takeaway"><b>Weakest criterion:</b> ${esc(weakest.label)}.
      Class mean ${weakest.mean.toFixed(1)} of ${num(weakest.points)}
      (${Math.round(weakest.pct * 100)}%). Class average
      ${mean.toFixed(1)} / ${possible}.</div>

    <figure class="chart summaryCard" id="fig-summary">
      <figcaption>How the class did
        <span>${summary ? `written by ${esc(summary.model || 'claude')} from ${summary.n} graded submissions` : 'a written read of the whole class'}</span>
        <span class="spacer" style="flex:1"></span>
        <button class="btn sm ai" id="btnSummary">${summary ? 'Rewrite' : 'Write class summary'}</button>
      </figcaption>
      ${summary
      ? `<div class="summaryText">${esc(summary.text)}</div>
           <div class="summaryMeta">generated ${esc(fmtDate(summary.generated_at))}${summary.include_missing ? ' · counting unsubmitted as zero' : ''}</div>`
      : `<p class="chartNote">Claude reads the per-criterion results and the notes it wrote
           while grading, then tells you where the class struggled and what to reteach.
           Needs at least three graded students.</p>`}
    </figure>

    <div class="chartGrid">
      <figure class="chart" id="fig-radar">
        <figcaption>Criterion achievement
          <span>class mean${useStudent ? `, dashed = ${esc(studentLabel(selected))}` : ''}</span></figcaption>
        ${radarSVG(per, useStudent)}
      </figure>
      <figure class="chart" id="fig-hist">
        <figcaption>Letter grades<span>count and share of the ${pool.length}
          graded</span></figcaption>
        ${histSVG(pool, possible)}
        ${letterLegend(pool, possible)}
      </figure>
    </div>

    <figure class="chart" id="fig-bars">
      <figcaption>Where the points went<span>weakest criterion first</span></figcaption>
      ${barsSVG(per)}
    </figure>

    <div class="legend">${per.map((c, i) =>
    `<div><b>C${i + 1}</b> ${esc(c.label)} <span>· mean ${c.mean.toFixed(1)} / ${num(c.points)}
      · median ${num(c.median)} · range ${num(c.min)}–${num(c.max)}</span></div>`).join('')}</div>

    <figure class="chart scrollX" id="fig-heat">
      <figcaption>Per-student breakdown<span>darker is closer to full marks</span></figcaption>
      ${heatSVG(per, pool)}
    </figure>`;

  host.scrollTop = 0;
  disposeDocx();
  disposePdf();
  $('#workPane').innerHTML = '';
  const scopeBtn = $('#scopeAll');
  if (scopeBtn) scopeBtn.onclick = () => { S.insightsScope = 'class'; render(); };
  $('#chkMissing').onchange = ev => { S.includeMissing = ev.target.checked; renderInsights(); };
  $('#dlAll').onclick = downloadCharts;
  $('#btnCurve').onclick = () => openCurve(
    S.insightsScope === 'selection' && S.picked.size ? pickedIds() : null);
  $('#btnPrint').onclick = () => window.print();
  const scopeSel = S.insightsScope === 'selection' && S.picked.size;
  $('#btnSummary').onclick = () => {
    const { courseId, assignmentId } = S.ids;
    runJob(scopeSel ? `Reading ${S.picked.size} selected students` : 'Reading the whole class',
      () => api(`/a/${courseId}/${assignmentId}/summary`,
        { body: { include_missing: !!S.includeMissing,
                  only: scopeSel ? pickedIds() : null } }),
      async () => {
        S.ws = await api(`/a/${courseId}/${assignmentId}`);
        $('#modalHost').innerHTML = '';
        renderInsights();
      });
  };
}

function downloadCharts() {
  const stamp = (S.ws.draft.assignment_name || 'assignment')
    .replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 50);
  let n = 0;
  ['fig-radar', 'fig-bars', 'fig-heat', 'fig-hist'].forEach(id => {
    const svg = document.querySelector('#' + id + ' svg');
    if (!svg) return;
    // Inline the computed theme colours so the file stands alone outside this page.
    const clone = svg.cloneNode(true);
    const css = getComputedStyle(document.body);
    ['--accent', '--ai', '--line', '--muted', '--ink', '--warn', '--danger'].forEach(v => {
      const val = css.getPropertyValue(v).trim();
      clone.innerHTML = clone.innerHTML.split(`var(${v})`).join(val);
    });
    clone.setAttribute('style', 'background:' + css.getPropertyValue('--panel').trim());
    const blob = new Blob([clone.outerHTML], { type: 'image/svg+xml' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${stamp}-${id.replace('fig-', '')}.svg`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    n++;
  });
  setStatus(`downloaded ${n} chart${n === 1 ? '' : 's'}`, 'ok');
}

/* ---------------------------------------------------------------- detail */
function render() {
  renderRoster();
  if (S.view === 'insights') renderInsights();
  else renderDetail();
  renderTeachBar();
  paintGradedStamp();
}

function renderDetail() {
  const list = students(), host = $('#detail');
  const draft = S.ws.draft || {};
  if (!Object.keys(S.ws.extracted || {}).length) {
    host.innerHTML = `<div class="wrap" style="padding:0">
      <h2 style="margin-top:0">${esc(S.assignment.name || '')}</h2>
      <p style="color:var(--muted);max-width:60ch">Nothing has been pulled from Canvas for this assignment yet.
      Hit <b>Sync from Canvas</b> to download submissions and extract their text, then <b>Auto-grade all</b>.</p></div>`;
    disposeDocx();
    disposePdf();
    $('#workPane').innerHTML = '';
    return;
  }
  if (!list.length) { host.innerHTML = ''; return; }
  S.sel = Math.min(S.sel, list.length - 1);
  const s = list[S.sel];
  const e = entryOf(s.user_id) || {};
  const crits = rubric();
  const possible = draft.points_possible || 0;
  const scores = e.scores || {};
  const total = finalOf(e);
  const earned = earnedOf(e);
  const bump = curveDelta(e);

  let tags = '';
  if (e.total !== undefined && !isScored(e)) {
    tags += `<span class="tag">no score · ${esc(unscoredReason(s, e))}</span>`;
  }
  if (bump) tags += `<span class="tag ai">curved +${num(bump)}</span>`;
  if (lateOff(e)) tags += `<span class="tag warn" id="lateTag" title="${esc((e.late_penalty && e.late_penalty.summary) || '')}">late −${num(lateOff(e))}</span>`;
  else if (e.late_penalty && e.late_penalty.waived) tags += `<span class="tag" id="lateTag" title="${esc(e.late_penalty.summary || '')}">late penalty waived</span>`;
  else if (lateFull(e)) tags += `<span class="tag" id="lateTag">late penalty off</span>`;
  if (e.human_ok) tags += '<span class="tag ok">reviewed by you</span>';
  if (e.source === 'human') tags += '<span class="tag edit">edited by you</span>';
  else if (e.source === 'canvas') tags += '<span class="tag edit">pulled from Canvas</span>';
  else if (e.source === 'claude') tags += `<span class="tag ai">graded by ${esc(e.model || 'claude')}</span>`;
  if (s.status === 'unsubmitted') tags += '<span class="tag danger">no submission</span>';
  if (s.late) tags += `<span class="tag warn">${esc(lateText(s))}</span>`;
  tags += canvasTag(s, e);
  (e.flags || []).forEach(f => { tags += `<span class="tag warn">${esc(f)}</span>`; });
  if (e.confidence && e.confidence !== 'high') tags += `<span class="tag">confidence: ${esc(e.confidence)}</span>`;

  let html = `<div class="who"><h2>${esc(studentLabel(s))}</h2>
      <span class="id">user ${esc(s.user_id)}${S.ws.pseudonymize ? ' · sent as ' + esc(s.pseudonym) : ''}
        · <a href="#/student/${esc(s.user_id)}">Full record</a></span>
    </div>
    <div class="tags">${tags || '<span class="tag">not graded yet</span>'}</div>`;

  if (e.total !== undefined && !isScored(e)) {
    html += noScoreNote(s, e);
  } else if (reviewHold(s)) {
    html += `<div class="callout bad" style="margin-bottom:14px"><b>Claude flagged this for you.</b>
      ${esc(e.needs_human_reason || 'It could not grade this fairly.')}</div>`;
  }

  if (e.conflict) {
    const c = e.conflict;
    const proposal = writtenOpen(s) && e.source !== 'human';
    const choiceIds = conflictChoiceIds(s.user_id);
    const many = choiceIds.length > 1;
    const canvasBit = writtenOpen(s)
      ? `Canvas holds <b>${num(c.canvas_score)}</b>, the automatic score${c.canvas_posted_at
          ? ' students can already see' : ''}. The written answers are still ungraded there.`
      : `Canvas holds <b>${num(c.canvas_score)}</b>${c.canvas_graded_at
          ? ' (graded ' + esc(fmtDate(c.canvas_graded_at)) + ')' : ''}.`;
    const mineBit = proposal
      ? ` A proposal on this computer is <b>${num(total)}</b> and has not been posted.`
      : ` You have <b>${num(total)}</b> here and it has not been pushed.`;
    const who = many
      ? ` This applies to all ${choiceIds.length} selected students who disagree with Canvas, not only this one.`
      : '';
    html += `<div class="callout bad conflictBox" style="margin-bottom:14px">
      <b>Canvas and this machine disagree.</b> ${canvasBit}${mineBit}
      Nothing was overwritten. Pick one.${who}
      <div class="actionRow" style="margin:10px 0 0">
        <button class="btn sm" id="cfCanvas">${many
          ? `Take Canvas for ${choiceIds.length}`
          : `Take Canvas (${num(c.canvas_score)})`}</button>
        <button class="btn sm" id="cfMine">${many
          ? `${proposal ? 'Keep the proposal' : 'Keep mine'} for ${choiceIds.length}`
          : `${proposal ? 'Keep the proposal' : 'Keep mine'} (${num(total)})`}</button>
      </div></div>`;
  }
  if (e.total_only && isScored(e)) {
    html += `<div class="callout" style="margin-bottom:14px">Canvas holds a total for this student
      with no rubric breakdown, so the sliders below start at zero. Moving one replaces the
      Canvas total with your rubric score.</div>`;
  }

  const gradeIds = menuIdsFor(s.user_id);
  html += `<div class="actionRow">
      <button class="btn on" id="btnWorkInline">${S.showWork ? 'Hide' : 'View'} submitted work</button>
      <button class="btn ai" id="btnGradeOne">${gradeIds.length > 1
        ? `Re-grade ${gradeIds.length} students` : 'Re-grade this student'}</button>
      <button class="btn" id="btnAsk">Ask about this work…</button>
      ${e.needs_human || e.human_ok ? `<button class="btn" id="btnReviewOne"
        title="${e.human_ok ? 'Put the review flag back'
                            : 'Clears the review block so this can be pushed'}">${
        e.human_ok ? 'Un-mark reviewed' : 'Mark reviewed'}</button>` : ''}
      ${s.status === 'unsubmitted' ? `<button class="btn" id="btnRemindOne"
        title="Send this student a Canvas message about the missing work">Remind this student</button>` : ''}
      <a class="btn" target="_blank" rel="noopener" href="${sgUrl(s.user_id)}">SpeedGrader ↗</a>
    </div>
    <div class="totalCard">
      <span class="bigScore">${total == null ? '—' : num(total)}<span class="of"> / ${possible}</span></span>
      <span class="pct">${(total != null && possible)
        ? (100 * total / possible).toFixed(1) + '%'
          + ` <b class="ltr">${letterOf(100 * total / possible)}</b>` : ''}</span>
      <span class="origNote">${scoreAdjustNote(e, earned, bump)}</span>
    </div>
    ${lateBox(s, e)}
    ${bump ? `<div class="curveNote">
      This score includes a curve you applied${curveSteps(e)}.
      The earned score, ${num(earned)}, is kept underneath and is what comes back
      if you remove the curve.</div>` : ''}`;

  const autoShown = e.quiz_auto_score != null ? e.quiz_auto_score : s.quiz_auto_score;
  if (gradeIsPosted(s)) {
    html += `<div class="callout">This grade is in Canvas and students can see it.
      The bars below are the written part of that score.</div>`;
  } else if (writtenOpen(s)) {
    const autoBit = autoShown != null
      ? `Canvas posted the automatic score, <b>${num(autoShown)}</b>. That is the number students can see. `
      : '';
    const here = !isScored(e)
      ? 'The written answers still need a grade.'
      : (e.source === 'human'
        ? 'The bars below are your score on this computer. They have not been posted.'
        : 'The bars below are a proposal on this computer. They have not been posted, and they are not the grade in Canvas.');
    html += `<div class="callout">${autoBit}${here}</div>`;
  } else if (autoShown != null) {
    html += `<div class="callout">Canvas already scored the multiple choice at
      <b>${num(autoShown)}</b>. The bars below are only the written answers,
      and the total adds the two together.</div>`;
  }

  crits.forEach(c => {
    const top = +c.points || 0;
    const v = Math.max(0, Math.min(top, Math.round(+(scores[c.id] || 0))));
    let input, ticks;
    if (c.locked) {
      input = `<p class="muted">Already scored by Canvas. Not graded again.</p>`;
      ticks = '';
    } else {
      // Whole points, one at a time. The Canvas ratings stay as marks under the
      // bar so you can see them, but the bar is not limited to those jumps.
      const tiers = (c.ratings || []).map(r => Math.round(+r.points))
        .filter(p => p >= 0 && p <= top);
      const marks = [...new Set([0, ...tiers, top])].sort((a, b) => a - b);
      input = `<input type="range" min="0" max="${top}" step="1" value="${v}"
                 data-cid="${esc(c.id)}">`;
      ticks = `<div class="ticks">${marks.map(t => '<span>' + num(t) + '</span>').join('')}</div>`;
    }
    const why = (e.rationales || {})[c.id];
    html += `<div class="crit">
        <div class="critHead"><span class="lbl">${esc(c.label)}</span>
          <span class="val">${num(v)}<span style="color:var(--muted);font-weight:400"> / ${num(c.points)}</span></span></div>
        ${input}${ticks}
        ${why ? `<div class="why"><b>Claude's reasoning</b><br>${esc(why)}</div>` : ''}
      </div>`;
  });

  html += `<label class="fieldLabel" for="cmt">Comment to student
      <span class="hint">— stays here unless you tick the box</span></label>
    <textarea class="comment" id="cmt">${esc(e.comment || '')}</textarea>
    <label class="tick" style="margin-top:8px">
      <input type="checkbox" id="postCmt"${e.post_comment ? ' checked' : ''}>
      Include this comment when pushing to Canvas
    </label>
    <div class="actionRow" style="margin-top:16px">
      <button class="btn" id="prevS">← Previous</button>
      <button class="btn" id="nextS">Next →</button>
    </div>`;

  host.innerHTML = html;
  host.scrollTop = 0;

  const lateEl = $('#lateShare');
  if (lateEl) lateEl.addEventListener('input', () => {
    lateEl.dataset.touched = '1';
    const share = Math.max(0, Math.min(100, Math.round(+lateEl.value / 10) * 10));
    // The points follow once the server has priced the penalty; until then
    // the percent is the part that is already certain.
    const full = lateFull(entryOf(s.user_id));
    $('#lateVal').innerHTML = full ? lateValText(entryOf(s.user_id), share) : `${share}%`;
    queueSave(s.user_id);
  });
  host.querySelectorAll('input[type=range][data-cid]').forEach(r => {
    r.addEventListener('input', ev => {
      const el = ev.target;
      const c = crits.find(x => x.id === el.dataset.cid);
      const val = Math.max(0, Math.min(+(c && c.points) || 0, Math.round(+el.value)));
      const head = el.closest('.crit').querySelector('.val');
      head.innerHTML = `${num(val)}<span style="color:var(--muted);font-weight:400"> / ${num(c.points)}</span>`;
      queueSave(s.user_id);
    });
  });
  $('#cmt').addEventListener('input', () => queueSave(s.user_id));
  const postCmt = $('#postCmt');
  if (postCmt) postCmt.addEventListener('change', () => {
    const on = !!postCmt.checked;
    api(`/a/${S.ids.courseId}/${S.ids.assignmentId}/student/${s.user_id}`,
      { body: { post_comment: on } }).then(r => {
        if (r && r.student) S.ws.draft.students[String(s.user_id)] = r.student;
        setStatus(on ? 'this comment will be pushed' : 'this comment stays here', 'ok');
        renderRoster();
      }).catch(err => setStatus('save failed: ' + err.message, 'err'));
  });
  $('#prevS').onclick = () => { S.sel = Math.max(0, S.sel - 1); render(); };
  $('#nextS').onclick = () => { S.sel = Math.min(list.length - 1, S.sel + 1); render(); };
  $('#btnWorkInline').onclick = toggleWork;
  $('#btnGradeOne').onclick = () => doGrade(gradeIds);
  const remindOne = $('#btnRemindOne');
  if (remindOne) remindOne.onclick = () =>
    openRemind(S.ids.courseId, S.ids.assignmentId, [String(s.user_id)]);
  const one = $('#btnReviewOne');
  if (one) one.onclick = () => doMarkReviewed(!e.human_ok, [String(s.user_id)]);
  $('#btnAsk').onclick = () => openAsk(s);
  const choiceIds = conflictChoiceIds(s.user_id);
  const cfC = $('#cfCanvas'); if (cfC) cfC.onclick = () => resolveConflicts(choiceIds, 'canvas');
  const cfM = $('#cfMine'); if (cfM) cfM.onclick = () => resolveConflicts(choiceIds, 'mine');

  renderWork(s, e);
}

/* What the gradebook holds for this student. A posted automatic score on an
   open written question is not a finished grade. */
function canvasTag(s, e) {
  if (s.canvas_score == null) return '';
  if (writtenOpen(s)) {
    return `<span class="tag warn" title="Canvas scored the automatic questions. The written ones are still waiting.">Canvas: ${num(s.canvas_score)} posted · written answers not graded</span>`;
  }
  const mine = finalOf(e);
  const differs = mine != null && Math.abs(mine - s.canvas_score) >= 0.005;
  const when = fmtDate(s.canvas_graded_at);
  const seen = s.canvas_posted_at ? ' · students can see it' : '';
  // canvas_score is what was entered. Canvas's own late policy may have taken
  // points off what students see; the next push sets Status: None to undo it.
  const cut = +(s.canvas_points_deducted || 0);
  const cutTag = cut > 0 && s.canvas_late_status !== 'none'
    ? `<span class="tag warn" title="Canvas's late policy took this off the score students see. Pushing again sets this submission's Canvas status to None, so only Studio's late penalty counts.">Canvas took −${num(cut)} late · students see ${num(s.canvas_shown_score)}</span>`
    : '';
  return `<span class="tag ${s.canvas_posted_at ? 'ok' : ''}" title="${when ? 'graded in Canvas ' + esc(when) : 'in the Canvas gradebook'}">Canvas: ${num(s.canvas_score)}${seen}${differs ? ' · differs from yours' : ''}</span>${cutTag}`;
}

/* Names the curve steps in the order they were applied, so a stacked curve is
   legible rather than just a number. */
function curveSteps(e) {
  const steps = ((e.curve || {}).steps) || [];
  if (!steps.length) return '';
  const words = steps.map(st => {
    const where = st.scope && st.scope !== 'total'
      ? (rubric().find(c => c.id === st.scope) || {}).label || st.scope
      : 'the whole score';
    const how = st.kind === 'flat' ? `+${num(st.amount)} points`
      : st.kind === 'scale' ? `+${num(st.amount)}%`
        : st.kind === 'target_mean' ? `mean to ${num(st.target)}%`
          : st.kind === 'floor' ? `floor at ${num(st.target)}%`
            : st.kind === 'to_top' ? 'curved to the top score'
              : st.kind === 'sqrt' ? 'square-root curve' : st.kind;
    return `${how} on ${where}${st.label ? ` (${st.label})` : ''}`;
  });
  return ': ' + words.join(', then ');
}

function noScoreNote(s, e) {
  const why = unscoredReason(s, e);
  return `<div class="callout" style="margin-bottom:14px"><b>No score:
    ${esc(why)}.</b> This is not a zero. It is left out of the class average, the
    letter grades and the rubric analysis, and it is not pushed to Canvas.
    ${why === 'no submission'
      ? 'Whether a missing submission earns a zero is your call: set it below, or let your Canvas missing-submission policy handle it.'
      : 'Open the submitted work and score it by hand, or re-sync if the file should have been readable.'}</div>`;
}

function aiDeltaNote(e) {
  if (!isScored(e)) return 'not graded yet';
  const ai = e.ai && e.ai.total;
  if (e.source === 'human' && ai != null && ai !== e.total) {
    const d = e.total - ai;
    return `Claude proposed ${ai} · <b>${d > 0 ? '+' : ''}${d}</b>`;
  }
  if (e.source === 'human') return 'set by you';
  if (e.source === 'canvas') {
    return 'pulled from Canvas' + (ai != null && ai !== e.total ? ` · Claude proposed ${ai}` : '');
  }
  return 'proposed by Claude — adjust the sliders to override';
}

function sgUrl(uid) {
  const base = (S.health.base_url || '').replace(/\/$/, '');
  return `${base}/courses/${S.ids.courseId}/gradebook/speed_grader?assignment_id=${S.ids.assignmentId}&student_id=${uid}`;
}

let saveTimer = null;
function queueSave(uid) {
  setStatus('unsaved…');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveStudent(uid), 700);
}
async function saveStudent(uid) {
  const crits = rubric();
  const scores = {};
  document.querySelectorAll('#detail input[type=range][data-cid]').forEach(el => {
    const cap = +((crits.find(c => c.id === el.dataset.cid) || {}).points || 0);
    scores[el.dataset.cid] = Math.max(0, Math.min(cap, Math.round(+el.value)));
  });
  const comment = ($('#cmt') || {}).value || '';
  const postComment = !!($('#postCmt') || {}).checked;
  const body = { scores, comment, post_comment: postComment, source: 'human' };
  // Only a slider the instructor moved: sending it on every rubric save would
  // undo a waiver from the instructions box.
  const lateEl = $('#lateShare');
  if (lateEl && lateEl.dataset.touched) body.late_share = Math.round(+lateEl.value / 10) * 10;
  try {
    const r = await api(`/a/${S.ids.courseId}/${S.ids.assignmentId}/student/${uid}`, { body });
    S.ws.draft.students[String(uid)] = r.student;
    setStatus('saved', 'ok');
    renderRoster();
    // The curved total, when there is one: the same number the roster shows.
    const card = $('.bigScore');
    if (card) card.innerHTML = `${num(finalOf(r.student))}<span class="of"> / ${S.ws.draft.points_possible || 0}</span>`;
    const pct = $('.totalCard .pct'), now = finalOf(r.student), max = +S.ws.draft.points_possible || 0;
    if (pct) pct.innerHTML = (now != null && max)
      ? (100 * now / max).toFixed(1) + `% <b class="ltr">${letterOf(100 * now / max)}</b>` : '';
    const note = $('.origNote'); if (note) note.innerHTML = scoreAdjustNote(r.student, earnedOf(r.student), curveDelta(r.student));
    paintLate(r.student);
  } catch (err) { setStatus('save failed: ' + err.message, 'err'); }
}
/* After a save: the slider's points and the late tag, without a re-render
   that would scroll the pane back to the top. */
function paintLate(e) {
  const el = $('#lateShare');
  if (!el) return;
  const share = lateShareOf(e);
  if (!el.matches(':active')) el.value = String(share);
  const val = $('#lateVal'); if (val) val.innerHTML = lateValText(e, share);
  const lp = e.late_penalty || {};
  const noteEl = $('#lateNote');
  if (noteEl && lp.applied && isScored(e)) {
    const policy = (S.ws && (S.ws.late_policy || S.ws.draft.late_policy)) || {};
    noteEl.textContent = `Syllabus: ${policy.summary || lp.summary || ''}`;
  }
  const tag = $('#lateTag');
  const off = lateOff(e);
  if (tag) {
    tag.textContent = off ? `late −${num(off)}` : 'late penalty off';
    tag.className = off ? 'tag warn' : 'tag';
  }
}

/* -------------------------------------------------------------- work pane */
function toggleWork() {
  S.showWork = !S.showWork;
  document.body.classList.toggle('showWork', S.showWork);
  const b = $('#btnWork'); if (b) b.textContent = S.showWork ? 'Hide work' : 'Show work';
  const i = $('#btnWorkInline'); if (i) i.textContent = (S.showWork ? 'Hide' : 'View') + ' submitted work';
  if (!S.showWork || !S.ws) return;
  const list = students();
  const s = list[Math.min(S.sel, Math.max(list.length - 1, 0))];
  if (s) renderWork(s, entryOf(s.user_id) || {});
}

/* -------------------------------------------------------- code rendering */
// extract.py wraps source files in a language-tagged fence. Split on those so
// code becomes a highlighted block and surrounding prose stays prose.
const FENCE = /```([A-Za-z0-9_+-]*)\n([\s\S]*?)```/g;

// Our file extensions mapped onto highlight.js language names. Anything absent
// falls through to auto-detection, which is fine for the long tail.
const HLJS_LANG = {
  cs: 'csharp', py: 'python', js: 'javascript', ts: 'typescript',
  jsx: 'javascript', tsx: 'typescript', java: 'java', kt: 'kotlin',
  c: 'c', h: 'c', cpp: 'cpp', hpp: 'cpp', cc: 'cpp', m: 'objectivec',
  swift: 'swift', go: 'go', rs: 'rust', rb: 'ruby', php: 'php', lua: 'lua',
  sql: 'sql', html: 'xml', htm: 'xml', xml: 'xml', css: 'css', scss: 'scss',
  yaml: 'yaml', yml: 'yaml', toml: 'ini', ini: 'ini', cfg: 'ini',
  sh: 'bash', bat: 'dos', json: 'json', md: 'markdown',
  shader: 'glsl', hlsl: 'glsl', glsl: 'glsl',
};

function codeBlock(lang, code) {
  const mapped = HLJS_LANG[(lang || '').toLowerCase()] || '';
  const body = code.replace(/\s+$/, '');
  let out = esc(body);
  const hl = window.hljs;
  if (hl) {
    try {
      out = (mapped && hl.getLanguage(mapped))
        ? hl.highlight(body, { language: mapped, ignoreIllegals: true }).value
        : hl.highlightAuto(body).value;
    } catch { /* keep the escaped source */ }
  }
  const lines = body.split('\n').length;
  return `<div class="codeBlock"><div class="codeBar">
      <span>${esc(mapped || lang || 'text')}</span>
      <span style="flex:1"></span>
      <span>${lines} line${lines === 1 ? '' : 's'}</span></div>
    <pre class="hljs"><code>${out}</code></pre></div>`;
}

function renderProse(text) {
  let html = '', last = 0, m;
  FENCE.lastIndex = 0;
  while ((m = FENCE.exec(text)) !== null) {
    const before = text.slice(last, m.index).trim();
    if (before) html += `<div class="prose">${esc(before)}</div>`;
    html += codeBlock(m[1], m[2]);
    last = m.index + m[0].length;
  }
  const rest = text.slice(last).trim();
  if (rest) html += `<div class="prose">${esc(rest)}</div>`;
  return html || `<div class="prose">${esc(text)}</div>`;
}

/* ---------------------------------------------------------- blend / 3D */
function fileUrl(name) {
  return `/api/a/${S.ids.courseId}/${S.ids.assignmentId}/file?name=${encodeURIComponent(name)}`;
}

/* Pictures pulled out of a Word file, and pages of a scanned PDF, sit one
   folder under files/. The name has to keep that folder or the picture 404s. */
/* A picture pasted into the text box is drawn inside the entry once the HTML
   is shown. The separate image part would put the same picture again below. */
function inlinedInEntry(s, path) {
  const name = String(path || '').split(/[\\/]/).pop();
  if (!name || !/_rce\d+_/.test(name)) return false;
  return (s.parts || []).some(p => p && p.html && p.html.indexOf(name) >= 0);
}

function fileRel(path) {
  const norm = String(path || '').replace(/\\/g, '/');
  const mark = '/files/';
  const at = norm.toLowerCase().lastIndexOf(mark);
  if (at >= 0) {
    const rel = norm.slice(at + mark.length);
    if (rel && !rel.includes('..') && rel.split('/').length <= 2) return rel;
  }
  return norm.split('/').pop() || '';
}

const CHIPS = [
  ['objects', 'objects', v => v > 0],
  ['faces', 'faces', v => v > 0],
  ['tris', 'tris', v => v > 0],
  ['quad_pct', '% quads', v => v >= 90],
  ['ngons', 'n-gons', v => v === 0],
  ['unapplied_scale', 'unapplied scale', v => v === 0],
  ['unapplied_rotation', 'unapplied rot', v => v === 0],
  ['default_names', 'default names', v => v === 0],
  ['no_uvs', 'meshes w/o UVs', v => v === 0],
  ['missing_textures', 'missing textures', v => v === 0],
  ['non_manifold', 'non-manifold', v => v === 0],
];

function blendCard(p) {
  const d = p.data || {};
  const st = d.stats || {};
  let html = `<div class="workSec blendCard"><h4>${esc(p.label)}
      <span class="wc">Blender ${esc(st.saved_with || '?')}</span></h4>`;

  if (d.empty_scene) html += '<div class="callout bad">This file opens but has no mesh objects at all.</div>';
  if (d.default_scene) html += '<div class="callout">This looks like Blender\'s untouched startup file.</div>';

  html += '<div class="chips blendChips">' + CHIPS.map(([k, label, good]) => {
    const v = st[k];
    if (v === undefined || v === null) return '';
    const cls = good(v) ? 'ok' : 'bad';
    return `<span class="statChip ${cls}"><b>${esc(String(v))}</b> ${esc(label)}</span>`;
  }).join('') + '</div>';

  if (d.model) {
    const mb = (d.glb_bytes || 0) / 1e6;
    html += `<div class="viewerWrap" data-glb="${esc(fileUrl(d.model))}" data-bytes="${d.glb_bytes || 0}">
        <div class="viewerBar">
          <button class="btn sm" data-vw="load">Load 3D model${mb ? ` (${mb.toFixed(1)} MB)` : ''}</button>
          <span class="viewerTools hidden">
            <button class="btn sm" data-vw="textured">Textured</button>
            <button class="btn sm" data-vw="clay">Clay</button>
            <button class="btn sm" data-vw="normals">Normals</button>
            <button class="btn sm" data-vw="wire">Wireframe</button>
            <button class="btn sm" data-vw="grid">Grid</button>
            <button class="btn sm" data-vw="reset">Reset view</button>
          </span>
          <span class="spacer" style="flex:1"></span>
          <span class="viewerInfo"></span>
        </div>
        <div class="viewerHost"></div>
      </div>`;
  } else if (d.glb_bytes === 0 && d.status) {
    html += '<div class="callout">No 3D preview was produced for this file. The report above still applies.</div>';
  }

  if (d.sheet) {
    html += `<img class="workImg" loading="lazy" alt="rendered views of the model"
       src="${fileUrl(d.sheet)}">
      <div class="sheetNote">Rendered by this tool with neutral solid shading, not by the
        student. Judge form and proportion here, not lighting or presentation.</div>`;
  }

  html += `<details class="blendReport"><summary>Full scene report</summary>
      <div class="prose small">${esc(p.text || '')}</div></details>`;
  if ((d.notes || []).length) {
    html += `<details class="blendReport"><summary>Safety changes made before reading
      (${d.notes.length})</summary><div class="prose small">${esc(d.notes.join('\n'))}</div></details>`;
  }
  return html + '</div>';
}

/* --------------------------------------------------------------- video */
/* A screen recording is graded by watching it, not by reading it, so the only
   job here is to make watching fast: it plays in place, seeks against a byte
   range off local disk, and remembers nothing about the grade. Nothing on this
   card is ever sent to Claude -- video is by far the most expensive thing a
   student can submit and the cheapest thing to watch, so the tool watches
   nothing and says so. */
const VIDEO_RATES = [1, 1.25, 1.5, 2];

/* Mirrors VIDEO_EXT and VIDEO_PLAYABLE in extract.py. Kept here as well because
   an assignment synced before video was understood has the file sitting on disk
   but a part that still reads "no text extractor for .mov": the extension is
   enough to put a player on it, which beats re-syncing to watch something that
   was already downloaded. */
const VIDEO_RE = /\.(mov|mp4|m4v|webm|avi|mkv|mpg|mpeg|wmv|ogv|3gp|mts|flv)$/i;
const VIDEO_PLAYABLE_RE = /\.(mov|mp4|m4v|webm|ogv)$/i;
function isVideoPart(p) {
  return p.kind === 'video' || (!!p.path && VIDEO_RE.test(p.path));
}

function videoCard(p) {
  const d = p.data || {};
  const name = (p.path || '').split(/[\\/]/).pop();
  const url = fileUrl(name);
  const mb = (d.bytes || 0) / 1e6;
  const size = mb ? `${mb.toFixed(mb < 10 ? 1 : 0)} MB` : '';
  // A part cached before video was understood carries no data payload, so
  // everything below falls back to the filename.
  const ext = (d.ext || (name.match(/\.[^.]+$/) || [''])[0] || '').toLowerCase();
  const playable = d.playable != null ? d.playable : VIDEO_PLAYABLE_RE.test(name);
  const head = `<h4>${esc(p.label)} <span class="wc">video${
    size ? ' · ' + size : ''}</span></h4>`;

  if (!playable) {
    return `<div class="workSec videoCard">${head}
      <div class="callout">${esc(ext || 'this format')} is one most browsers
        cannot play. Download it and open it in a desktop player, or watch it in
        SpeedGrader.
        <div class="videoBar" style="margin-top:9px">
          <a class="btn sm" href="${url}&amp;dl=1" download>Download</a>
        </div></div></div>`;
  }

  return `<div class="workSec videoCard">${head}
    <div class="videoWrap" data-dl="${url}&amp;dl=1">
      <video class="workVideo" controls preload="metadata" playsinline
        src="${url}"></video>
      <div class="videoBar">
        <span class="videoSpeed">${VIDEO_RATES.map(r =>
          `<button class="btn sm${r === 1 ? ' on' : ''}" data-rate="${r}"
            >${r}x</button>`).join('')}</span>
        <span class="spacer" style="flex:1"></span>
        <span class="videoNote">not graded automatically — watch it and score by hand</span>
        <a class="btn sm" href="${url}&amp;dl=1" download>Download</a>
      </div>
    </div></div>`;
}

/* Speed is the one control a browser does not put on screen, and it is the one
   that matters when there are forty recordings to get through. */
function wireVideos(host) {
  host.querySelectorAll('.videoWrap').forEach(wrap => {
    const vid = wrap.querySelector('video');
    if (!vid) return;

    // A .mov can hold anything. H.264 plays everywhere; ProRes and some HEVC
    // phone recordings do not decode in a browser at all, and an empty black
    // box with no explanation is the worst version of that. Say what happened
    // and give them the file.
    vid.onerror = () => {
      const dl = wrap.dataset.dl || '';
      wrap.innerHTML = `<div class="callout" style="margin:0">This file will not
        play in the browser — usually a codec it cannot decode (ProRes, or some
        phone HEVC). Download it and open it in a desktop player, or watch it in
        SpeedGrader.
        <div class="videoBar" style="margin-top:9px;border:0;padding:0;background:none">
          <a class="btn sm" href="${dl}" download>Download</a>
        </div></div>`;
    };

    wrap.querySelectorAll('[data-rate]').forEach(btn => {
      btn.onclick = () => {
        vid.playbackRate = +btn.dataset.rate;
        wrap.querySelectorAll('[data-rate]').forEach(b => b.classList.remove('on'));
        btn.classList.add('on');
      };
    });
  });
}

// Models auto-load, but after a beat. The roster is keyboard-navigable with j/k,
// so mounting instantly would fetch and build a scene for every student you pass
// through. The timer is cleared on the next render, so only the student you
// actually stop on gets loaded.
const AUTO_LOAD_DELAY_MS = 280;
const AUTO_LOAD_MAX_BYTES = 25e6;
let autoLoadTimer = null;

function wireViewers(host) {
  host.querySelectorAll('.viewerWrap').forEach(wrap => {
    const url = wrap.dataset.glb;
    const bytes = +(wrap.dataset.bytes || 0);
    const hostEl = wrap.querySelector('.viewerHost');
    const tools = wrap.querySelector('.viewerTools');
    const info = wrap.querySelector('.viewerInfo');
    const loadBtn = wrap.querySelector('[data-vw="load"]');

    const start = () => {
      const V = window.BlendViewer;
      if (!V) { info.textContent = '3D viewer failed to load'; return; }
      if (loadBtn) { loadBtn.disabled = true; loadBtn.classList.add('hidden'); }
      V.mount(hostEl, url, { onReady: r => {
        info.textContent = `${r.triangles.toLocaleString()} triangles`;
        tools.classList.remove('hidden');
      }});
    };

    // Anything unusually large still waits for a deliberate click.
    if (bytes && bytes <= AUTO_LOAD_MAX_BYTES) {
      autoLoadTimer = setTimeout(start, AUTO_LOAD_DELAY_MS);
    }

    wrap.querySelectorAll('[data-vw]').forEach(btn => btn.onclick = () => {
      const V = window.BlendViewer;
      if (!V) { info.textContent = '3D viewer failed to load'; return; }
      const act = btn.dataset.vw;
      if (act === 'load') {
        clearTimeout(autoLoadTimer);
        start();
      } else if (act === 'wire') { btn.classList.toggle('on', V.toggleWireframe()); }
      else if (act === 'grid') { btn.classList.toggle('on', !V.toggleHelpers()); }
      else if (act === 'reset') { V.reset(); }
      else { V.setMode(act);
             tools.querySelectorAll('[data-vw]').forEach(b => {
               if (['textured', 'clay', 'normals'].includes(b.dataset.vw)) b.classList.remove('on');
             });
             btn.classList.add('on'); }
    });
  });
}

/* A score sitting on this computer for one written question. Not Canvas's. */
function localWrittenNote(q, e) {
  if (!q.manual || !isScored(e)) return '';
  const key = 'q' + String(q.id || '');
  const scores = e.scores || {};
  if (!Object.prototype.hasOwnProperty.call(scores, key)) return '';
  const where = e.source === 'human' ? 'Scored on this computer'
    : 'Proposed on this computer';
  const why = (e.rationales || {})[key];
  return `<div class="prose"><b>${esc(where)}, not in Canvas</b><br>${
    num(scores[key])} / ${num(q.points_possible)}`
    + (why ? `<br>${esc(why)}` : '') + '</div>';
}

/* The extracted text is one flat string, which is what grading reads. It drops
   headings, tables, text boxes, pictures and charts. A .docx is drawn from
   the file itself; the flat string stays behind "Text only". */
function isDocxPart(p) {
  return !!(p && p.path && /\.docx$/i.test(p.path) && p.kind !== 'error');
}

function docxStem(p) {
  const base = (p.path || '').split(/[\\/]/).pop() || '';
  return base.replace(/\.docx$/i, '');
}

function docxUrl(p) {
  return fileUrl((p.path || '').split(/[\\/]/).pop() || '');
}

function docxMediaStem(p, stems) {
  if (!p || p.kind !== 'image' || !p.path) return '';
  const path = String(p.path).replace(/\\/g, '/');
  for (const stem of stems) {
    if (stem && path.includes('/' + stem + '-media/')) return stem;
  }
  return '';
}

let docxWire = 0;

function disposeDocx(keep) {
  docxWire += 1;
  if (window.DocxView) window.DocxView.dispose(keep);
}

function docxCard(p, reused) {
  const url = docxUrl(p);
  const stem = docxStem(p);
  const again = reused && reused.has(url);
  const plain = (p.text && String(p.text).trim())
    ? `<details class="docxPlain"><summary>Text only</summary>${renderProse(p.text)}</details>`
    : '';
  const mount = again
    ? `<div class="docxSlot" data-src="${esc(url)}"></div>`
    : `<div class="docxHost" data-src="${esc(url)}" data-stem="${esc(stem)}"
         role="region" aria-label="Word document" aria-busy="true"></div>
       <p class="docxStatus">Opening the document…</p>`;
  return `<div class="workSec docxCard"><h4>${esc(p.label)}
      <span class="wc">Word document</span></h4>
    ${mount}
    <div class="docxBar"><a class="btn sm" href="${esc(url)}&amp;dl=1" download>Download</a></div>
    ${plain}</div>`;
}

function docxFailed(el) {
  if (!el.isConnected) return;
  const status = el.parentElement && el.parentElement.querySelector('.docxStatus');
  if (status) {
    status.className = 'callout';
    status.textContent = 'This Word file could not be drawn. The text below is only the words, not the layout or the figures. Download it to open it in Word.';
  }
  const plain = el.parentElement && el.parentElement.querySelector('.docxPlain');
  if (plain) plain.open = true;
  el.remove();
}

function pdfNameOf(p) {
  if (!p || !p.path || p.kind === 'error') return '';
  const norm = String(p.path).replace(/\\/g, '/');
  const base = norm.split('/').pop() || '';
  if (/\.pdf$/i.test(base)) return base;
  const folder = norm.match(/\/([^/]+)-pages\/page-\d+\.png$/i);
  return folder ? folder[1] + '.pdf' : '';
}

let pdfWire = 0;

function disposePdf(keep) {
  pdfWire += 1;
  if (window.PdfView) window.PdfView.dispose(keep);
}

function pdfCard(p, url, reused) {
  const again = reused && reused.has(url);
  const fromFile = /\.pdf$/i.test(String(p.path || ''));
  let label = p.label;
  if (!fromFile) {
    try {
      label = decodeURIComponent((url.split('name=').pop() || '').split('&')[0]);
    } catch (_) { label = 'PDF'; }
  }
  const plain = (fromFile && p.text && String(p.text).trim())
    ? `<details class="docxPlain"><summary>Text only</summary>${renderProse(p.text)}</details>`
    : '';
  const mount = again
    ? `<div class="pdfSlot" data-src="${esc(url)}"></div>`
    : `<div class="pdfHost" data-src="${esc(url)}" role="region"
         aria-label="PDF" aria-busy="true"></div>
       <p class="pdfStatus">Opening the document…</p>`;
  return `<div class="workSec pdfCard"><h4>${esc(label)}
      <span class="wc">PDF</span></h4>
    ${mount}
    <div class="docxBar"><a class="btn sm" href="${esc(url)}&amp;dl=1" download>Download</a></div>
    ${plain}</div>`;
}

function pdfFailed(el) {
  if (!el.isConnected) return;
  const status = el.parentElement && el.parentElement.querySelector('.pdfStatus');
  if (status) {
    status.className = 'callout';
    status.textContent = 'This PDF could not be drawn. The text below is only the words, not the layout or the figures. Download it to open it.';
  }
  const plain = el.parentElement && el.parentElement.querySelector('.docxPlain');
  if (plain) plain.open = true;
  const url = el.dataset.src || '';
  const pane = el.closest('#workPane');
  if (pane) {
    pane.querySelectorAll('.pdfPageImg').forEach(node => {
      if (node.dataset.pdf === url) node.hidden = false;
    });
  }
  el.remove();
}

function wirePdf(host) {
  const token = ++pdfWire;
  host.querySelectorAll('.pdfHost').forEach(el => {
    if (el.dataset.ready === '1' && el.querySelector('canvas')) return;
    const url = el.dataset.src;
    let tries = 0;
    const run = () => {
      if (token !== pdfWire || !el.isConnected) return;
      const V = window.PdfView;
      if (!V) {
        if (++tries > 40) { pdfFailed(el); return; }
        setTimeout(run, 150);
        return;
      }
      V.mount(el, url).then(viewer => {
        if (token !== pdfWire || !el.isConnected || !viewer) return;
        const status = el.parentElement && el.parentElement.querySelector('.pdfStatus');
        if (status) status.remove();
        host.querySelectorAll('.pdfPageImg').forEach(node => {
          if (node.dataset.pdf === url) node.remove();
        });
      }).catch(() => {
        if (token !== pdfWire || !el.isConnected) return;
        pdfFailed(el);
      });
    };
    run();
  });
}

function wireDocx(host) {
  const token = ++docxWire;
  host.querySelectorAll('.docxHost').forEach(el => {
    if (el.dataset.ready === '1' && el.querySelector('canvas')) return;
    const url = el.dataset.src;
    const stem = el.dataset.stem || '';
    let tries = 0;
    const run = () => {
      if (token !== docxWire || !el.isConnected) return;
      const V = window.DocxView;
      if (!V) {
        if (++tries > 40) { docxFailed(el); return; }
        setTimeout(run, 150);
        return;
      }
      V.mount(el, url).then(viewer => {
        if (token !== docxWire || !el.isConnected || !viewer) return;
        const status = el.parentElement && el.parentElement.querySelector('.docxStatus');
        if (status) status.remove();
        host.querySelectorAll('.docxMedia').forEach(node => {
          if (node.dataset.stem === stem) node.remove();
        });
        if (V.relayout) V.relayout(url);
      }).catch(() => {
        if (token !== docxWire || !el.isConnected) return;
        docxFailed(el);
      });
    };
    run();
  });
}

function renderWork(s, e) {
  // Free the previous WebGL context before innerHTML orphans its canvas, and
  // drop any auto-load that was queued for the student we just left.
  clearTimeout(autoLoadTimer);
  if (window.BlendViewer) window.BlendViewer.dispose();
  const host = $('#workPane');
  // innerHTML alone leaves an orphaned <video> downloading in the background,
  // and j/k through the roster would stack up a fetch per student.
  host.querySelectorAll('video').forEach(v => { v.pause(); v.removeAttribute('src'); v.load(); });
  // Keep a Word document that is already drawn. Rebuilding the pane on every
  // keystroke would otherwise parse the file again and the pages would flash.
  const drawDocs = S.showWork
    && !(s.external_tool && !(s.quiz_review && s.quiz_review.length))
    && !(s.status === 'unsubmitted' && !(s.quiz_review && s.quiz_review.length));
  const stemUrl = new Map();
  const pdfUrls = [];
  const reused = new Map();
  if (drawDocs) {
    (s.parts || []).forEach(p => {
      if (isDocxPart(p)) stemUrl.set(docxStem(p), docxUrl(p));
      const pdfName = pdfNameOf(p);
      if (pdfName && !pdfUrls.includes(fileUrl(pdfName))) pdfUrls.push(fileUrl(pdfName));
    });
    if (window.DocxView) {
      stemUrl.forEach(url => {
        const el = window.DocxView.take(url);
        if (el) reused.set(url, el);
      });
    }
    if (window.PdfView) {
      pdfUrls.forEach(url => {
        const el = window.PdfView.take(url);
        if (el) reused.set(url, el);
      });
    }
  }
  disposeDocx([...stemUrl.values()].filter(url => reused.has(url)));
  disposePdf(pdfUrls.filter(url => reused.has(url)));
  const meta = [];
  if (s.submitted_at) meta.push('submitted ' + fmtDate(s.submitted_at));
  if (s.late) meta.push(lateText(s).toUpperCase());
  meta.push(`${s.words || 0} words`);

  let body = '';
  if (s.external_tool && !(s.quiz_review && s.quiz_review.length)) {
    const max = (S.ws.draft && S.ws.draft.points_possible) || 0;
    body = `<div class="callout">Canvas graded this in the publisher tool${
      s.canvas_score == null ? '' : ` and recorded <b>${num(s.canvas_score)}</b>${max ? ' of ' + num(max) : ''}`
    }. The questions are not stored on the Canvas submission, so they cannot be shown here.
      <br><br><a href="${sgUrl(s.user_id)}" target="_blank" rel="noopener">Open in SpeedGrader ↗</a></div>`;
  } else if (s.status === 'unsubmitted' && !(s.quiz_review && s.quiz_review.length)) {
    body = `<div class="callout bad">Canvas shows this student as <b>unsubmitted</b> — there is nothing to read.
      <br><br><a href="${sgUrl(s.user_id)}" target="_blank" rel="noopener">Confirm in SpeedGrader ↗</a></div>`;
  } else {
    if (s.quiz_review && s.quiz_review.length) {
      if (writtenOpen(s)) {
        body += `<div class="callout">Written answers are not graded in Canvas.
          A zero on a short answer is not a score.
          ${isScored(e) ? 'The note under each one is a proposal on this computer. It has not been posted.' : ''}
          </div>`;
      }
      body += s.quiz_review.map(q => {
        const ungraded = q.manual && writtenOpen(s)
          && (q.points == null || q.points === '' || +q.points === 0);
        const ours = (q.manual && !writtenOpen(s) && e && e.scores
          && Object.prototype.hasOwnProperty.call(e.scores, 'q' + String(q.id || '')))
          ? e.scores['q' + String(q.id || '')] : null;
        const mark = ungraded ? `Not graded · worth ${num(q.points_possible)}`
          : q.manual ? 'Written'
            : (q.correct === true ? 'Correct' : q.correct === false ? 'Incorrect' : '');
        const pill = ungraded ? 'warn' : (q.manual ? 'muted' : (q.correct === false ? 'warn' : 'good'));
        const pts = ungraded ? ''
          : (ours != null
            ? ` <span class="wc">${num(ours)} / ${num(q.points_possible)}</span>`
            : ((q.points != null && q.points !== '')
              ? ` <span class="wc">${num(q.points)} / ${num(q.points_possible)}</span>` : ''));
        const note = (q.manual && writtenOpen(s)) ? localWrittenNote(q, e) : '';
        return `<div class="workSec"><h4>${esc(q.name || ('Question ' + (q.position || '')))}${pts}`
          + (mark ? ` <span class="pill ${pill}">${mark}</span>` : '')
          + `</h4><div class="prose"><b>Question</b><br>${esc(q.prompt || '')}</div>`
          + `<div class="prose"><b>${q.manual ? 'Response' : 'Their answer'}</b><br>${
            esc(q.response || '')}</div>${note}</div>`;
      }).join('');
    }
    if (s.filenames && s.filenames.length) {
      body += `<div class="workSec"><h4>Attachments</h4>
        <div style="font:11.5px var(--mono);color:var(--muted);word-break:break-all">
        ${s.filenames.map(f => esc(decodeURIComponent(f))).join('<br>')}</div></div>`;
    }
    const seenPdf = new Set();
    (s.parts || []).forEach(p => {
      if (s.quiz_review && s.quiz_review.length) {
        const stub = (p.text || '').trim();
        if (stub.indexOf('- user:') === 0 && stub.indexOf('quiz:') >= 0) return;
      }
      if (isDocxPart(p)) {
        if (drawDocs) body += docxCard(p, reused);
        return;
      }
      const pdfName = pdfNameOf(p);
      if (pdfName) {
        const url = fileUrl(pdfName);
        const norm = String(p.path || '').replace(/\\/g, '/');
        const isPdf = /\.pdf$/i.test(norm);
        if (drawDocs && !seenPdf.has(url)) {
          seenPdf.add(url);
          body += pdfCard(p, url, reused);
        }
        if (isPdf) return;
        if (reused.has(url) || !drawDocs) return;
        const rel = fileRel(p.path);
        body += `<div class="workSec pdfPageImg" data-pdf="${esc(url)}" hidden><h4>${esc(p.label)}</h4>
          <img class="workImg" loading="lazy" alt="${esc(p.label)}"
            src="${esc(fileUrl(rel))}"></div>`;
        return;
      }
      const mediaStem = docxMediaStem(p, stemUrl.keys());
      if (mediaStem) {
        if (reused.has(stemUrl.get(mediaStem))) return;
        body += `<div class="workSec docxMedia" data-stem="${esc(mediaStem)}"><h4>${esc(p.label)}</h4>
          <img class="workImg" loading="lazy" alt="${esc(p.label)}"
            src="${esc(fileUrl(fileRel(p.path)))}"></div>`;
        return;
      }
      if (p.kind === 'text' && p.label === 'Canvas text entry' && p.html
          && /<(?:p|div|h[1-6]|table|ul|ol|li|blockquote|pre)\b/i.test(p.html)) {
        body += `<div class="workSec"><h4>${esc(p.label)} <span class="wc">${p.words} words</span></h4>
          <div class="canvasHtml workEntry">${p.html}</div></div>`;
      } else if (p.kind === 'text' && p.text && p.text.trim()) {
        body += `<div class="workSec"><h4>${esc(p.label)} <span class="wc">${p.words} words</span></h4>
          ${renderProse(p.text)}</div>`;
      } else if (p.kind === 'image' && inlinedInEntry(s, p.path)) {
        return;
      } else if (p.kind === 'image') {
        body += `<div class="workSec"><h4>${esc(p.label)}</h4>
          <img class="workImg" loading="lazy" alt="${esc(p.label)}"
            src="${esc(fileUrl(fileRel(p.path)))}"></div>`;
      } else if (isVideoPart(p)) {
        body += videoCard(p);
      } else if (p.kind === 'blend') {
        body += blendCard(p);
      } else if (p.note) {
        body += `<div class="callout">${esc(p.label)} — ${esc(p.note)}
          <br><br><a href="${sgUrl(s.user_id)}" target="_blank" rel="noopener">Open in SpeedGrader ↗</a></div>`;
      }
    });
    if (s.discussion) {
      const d = s.discussion;
      if (d.post) {
        body += `<div class="workSec"><h4>Original post <span class="wc">${d.post.words} words · ${esc(fmtDate(d.post.created_at))}</span></h4>
          <div class="prose">${esc(d.post.text)}</div></div>`;
      } else body += '<div class="callout bad">No original post on this topic.</div>';
      (d.replies || []).forEach(r => {
        body += `<div class="workSec"><h4>Reply to ${esc(r.to)} <span class="wc">${r.words} words</span></h4>
          <div class="quoted">${esc(r.to_excerpt)}</div><div class="prose small">${esc(r.text)}</div></div>`;
      });
      if (!(d.replies || []).length) body += '<div class="callout bad">No reply to a classmate on this topic.</div>';
    }
    if (!body) body = `<div class="callout bad">Nothing readable was extracted.
      <a href="${sgUrl(s.user_id)}" target="_blank" rel="noopener">Open in SpeedGrader ↗</a></div>`;
  }

  host.innerHTML = `<div class="workHead"><h3>Submitted work</h3>
      <span class="meta">${esc(meta.join(' · '))}</span><span class="spacer"></span>
      <a class="btn sm" target="_blank" rel="noopener" href="${sgUrl(s.user_id)}">SpeedGrader ↗</a>
      <button class="btn sm" type="button" id="btnWorkClose">Close</button></div>
    <div class="workBody">${body}</div>`;
  const closeWork = host.querySelector('#btnWorkClose');
  if (closeWork) closeWork.onclick = toggleWork;
  host.querySelectorAll('.docxSlot, .pdfSlot').forEach(slot => {
    const url = slot.dataset.src;
    const el = reused.get(url);
    if (!el) return;
    slot.replaceWith(el);
    if (slot.classList.contains('docxSlot') && window.DocxView) window.DocxView.relayout(url);
  });
  wireDocx(host);
  wirePdf(host);
  wireViewers(host);
  wireVideos(host);
  host.scrollTop = 0;
}

/* ----------------------------------------------------------------- events */
$('#search').addEventListener('input', ev => { S.query = ev.target.value.trim().toLowerCase(); S.sel = 0; render(); });
$('#filters').querySelectorAll('.chip').forEach(c => c.onclick = () => {
  setFilter(c.dataset.f);
  render();
});

/* The order is a habit, like the filter chip: highest-first should still be
   highest-first on the next assignment. */
function setSort(name) {
  if (!ROSTER_SORTS.includes(name)) name = 'submitted';
  const current = S.ws ? (students()[S.sel] || {}).user_id : null;
  S.sort = name;
  if (S.ws) {
    const idx = students().findIndex(s => String(s.user_id) === String(current));
    S.sel = idx >= 0 ? idx : 0;
  }
  const box = $('#rosterSort');
  if (box && box.value !== name) box.value = name;
  try { localStorage.setItem('cg.sort', name); } catch (_) { /* private mode */ }
}

const rosterSortBox = $('#rosterSort');
if (rosterSortBox) {
  rosterSortBox.onchange = () => { setSort(rosterSortBox.value); render(); };
  let savedSort = null;
  try { savedSort = localStorage.getItem('cg.sort'); } catch (_) { /* ignore */ }
  if (ROSTER_SORTS.includes(savedSort)) setSort(savedSort);
}

/* The chip is a working preference, not a property of the assignment, so it
   outlives both. Someone who works from "Turned in" wants it still chosen the
   next morning, not reset to All by a browser restart. */
function setFilter(name) {
  S.filter = name;
  S.sel = 0;
  $('#filters').querySelectorAll('.chip').forEach(
    x => x.setAttribute('aria-pressed', String(x.dataset.f === name)));
  try { localStorage.setItem('cg.filter', name); } catch (_) { /* private mode */ }
}

(function restoreFilter() {
  let saved = null;
  try { saved = localStorage.getItem('cg.filter'); } catch (_) { /* ignore */ }
  // Matched against the chips that exist rather than fed to a selector: a
  // stored name from an older build should be ignored, not thrown over.
  const known = [...$('#filters').querySelectorAll('.chip')].some(c => c.dataset.f === saved);
  if (known) setFilter(saved);
})();

document.addEventListener('contextmenu', onRosterContext, true);

document.addEventListener('keydown', ev => {
  if (/^(INPUT|TEXTAREA)$/.test(ev.target.tagName)) return;
  if ($('#viewWork').classList.contains('hidden')) return;
  const n = students().length;
  if (ev.key === 'j' || ev.key === 'ArrowDown') { ev.preventDefault(); S.sel = Math.min(n - 1, S.sel + 1); render(); }
  if (ev.key === 'k' || ev.key === 'ArrowUp') { ev.preventDefault(); S.sel = Math.max(0, S.sel - 1); render(); }
  if (ev.key === 'w') { ev.preventDefault(); toggleWork(); }
});

/* The grader is the Grade area of the Studio: the first card on the course hub
   and the first tab of the area bar. */
registerArea({
  id: 'grade', label: 'Grade', zone: 'grade',
  open: (cid) => openCourse(cid),
  badge: hub => hub?.grade?.badge ?? null,
});
