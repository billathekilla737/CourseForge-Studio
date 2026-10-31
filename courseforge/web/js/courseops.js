/* courseops.js: the Course tools area (#/c/<cid>/tools/...).

   Seven screens behind one set of sub-tabs: due dates, export, import, clone,
   trim nav, back up a quiz, and outcome alignment. Each one shows the plan
   first and says what has not been sent yet.

   Everything that writes goes through runJobConfirmed: the server refuses the
   first attempt and hands back its own sentence, the page shows that sentence
   as it came, and the token only spends on that exact change. The due-date
   apply sends the browser's timezone with it, so a wall-clock 11:59 pm becomes
   the right instant in Canvas. */
(function () {
  'use strict';

  S.tools = S.tools || {};

  const SUBS = [
    { id: 'dates', label: 'Due dates' },
    { id: 'export', label: 'Export' },
    { id: 'import', label: 'Import' },
    { id: 'clone', label: 'Clone' },
    { id: 'nav', label: 'Trim nav' },
    { id: 'quiz-backup', label: 'Back up a quiz' },
    { id: 'slo', label: 'Outcomes' },
  ];

  const has = name => typeof window[name] === 'function';
  const base = cid => '/tools/' + encodeURIComponent(cid);
  const pillOf = (text, cls) => (has('pill') ? pill(text, cls)
    : '<span class="pill ' + esc(cls || '') + '">' + esc(text) + '</span>');
  const kb = n => (has('fmtBytes') ? fmtBytes(n) : (n == null ? '' : Math.round(n / 1024) + ' KB'));
  const when = v => (v ? (has('fmtDate') ? fmtDate(v) : v) : '');
  const val = id => { const el = $('#' + id); return el ? el.value.trim() : ''; };
  const on = id => { const el = $('#' + id); return !!(el && el.checked); };

  /* What the browser knows about the instructor's clock. The name is what
     survives the November change; the offset is the fallback. */
  function tz() {
    let name = null;
    try { name = Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch (_) { name = null; }
    return { tz: new Date().getTimezoneOffset(), tzname: name };
  }

  function note(text) {
    return '<div class="callout toolsNote">' + esc(text) + '</div>';
  }

  function table(caption, headers, rows) {
    return '<div class="toolsTableWrap"><table class="toolsTable"><caption>' + esc(caption) + '</caption>'
      + '<thead><tr>' + headers.map(h => '<th scope="col">' + esc(h) + '</th>').join('') + '</tr></thead>'
      + '<tbody>' + (rows.length ? rows.join('')
        : '<tr><td colspan="' + headers.length + '" class="toolsNone">Nothing here yet. '
          + 'Nothing has been sent to Canvas.</td></tr>') + '</tbody></table></div>';
  }

  /* ------------------------------------------------------------- the area */
  async function openTools(cid, rest) {
    rest = Array.isArray(rest) ? rest : [];
    const sub = SUBS.some(s => s.id === rest[0]) ? rest[0] : 'dates';
    S.tools.courseId = cid;
    S.tools.sub = sub;
    showView('area');
    // The crumb must name this course, not the one looked at before it.
    if (typeof ensureCourse === 'function') await ensureCourse(cid);
    crumbs([
      { label: 'Courses', href: '#/' },
      { label: courseTitle(S.course) || S.tools.label, href: '#/c/' + cid },
      { label: 'Course tools' },
    ]);
    $('#headerActions').innerHTML = '<button class="btn" id="toolsRefresh">Refresh</button>';
    $('#toolsRefresh').onclick = () => reopen(cid);
    areaHead('Course tools',
      'Due dates, backups, imports, clones, the left-hand navigation, a quiz copy and the '
      + 'outcome report. Every one shows the plan before anything is sent.');
    areaTabs(SUBS.map(s => ({ id: s.id, label: s.label })), sub, id => {
      if (id !== sub) location.hash = '#/c/' + cid + '/tools/' + id;
    });
    const body = $('#areaBody');
    body.innerHTML = '<div class="toolsLoading">Reading what is on this machine...</div>';
    let state;
    try {
      state = await api(base(cid) + '/state');
    } catch (err) {
      body.innerHTML = '<div class="callout bad">Could not read the local state: '
        + esc(firstLine(err.message)) + '</div>';
      return;
    }
    S.tools.state = state;
    S.tools.label = state.course_label || S.tools.label;
    body.innerHTML = '<div id="toolsPane"></div>';
    const host = $('#toolsPane');
    if (sub === 'dates') return renderDates(cid, host, state);
    if (sub === 'export') return renderExport(cid, host, state);
    if (sub === 'import') return renderImport(cid, host, state);
    if (sub === 'clone') return renderClone(cid, host, state);
    if (sub === 'nav') return renderNav(cid, host, state);
    if (sub === 'quiz-backup') return renderQuiz(cid, host, state);
    return renderSlo(cid, host, state.slo);
  }

  /* Never location.reload(): re-run the opener for whichever tab is showing. */
  function reopen(cid) {
    if (location.hash.indexOf('#/c/' + cid + '/tools') === 0) openTools(cid, [S.tools.sub]);
  }

  /* ==================================================================
     1. Due dates
     ================================================================== */
  const FACTS = [
    { id: 'start', label: 'Term start', type: 'date', hint: 'The first day of class.' },
    { id: 'weeks', label: 'Instructional weeks', type: 'number', hint: 'Counting the week the final is due.' },
    { id: 'term', label: 'Term', type: 'text', hint: 'Fall 2026, and so on.' },
    { id: 'finals_end', label: 'Finals end', type: 'date', hint: 'The last day of the finals window.' },
    { id: 'breaks', label: 'Days off', type: 'text', hint: '2026-09-07, 2026-11-23..2026-11-27' },
  ];

  function factField(f, facts) {
    const missing = (facts.missing || []).includes(f.id);
    let value = facts[f.id];
    if (f.id === 'breaks') value = (facts.breaks || []).join(', ');
    const source = (facts.sources || {})[f.id] || '';
    return '<label class="toolsField' + (missing ? ' toolsAsk' : '') + '">'
      + '<span class="toolsLbl">' + esc(f.label) + '</span>'
      + '<input id="dt_' + f.id + '" type="' + f.type + '" value="' + esc(value == null ? '' : value) + '">'
      + '<span class="toolsHint">' + esc(missing ? 'Tell me: ' + f.hint : (source || f.hint)) + '</span>'
      + '</label>';
  }

  /* The plan saved on disk paints at once; with none, it is computed. Either
     way the Compute button re-reads the course. */
  function renderDates(cid, host, state) {
    const plan = (state.dates && state.dates.plan) || null;
    if (!plan || !plan.facts) return loadDatesPlan(cid, host);
    paintDates(cid, host, plan, state);
  }

  function qs(obj) {
    const parts = Object.entries(obj).filter(([, v]) => v != null && v !== '')
      .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v));
    return parts.length ? '?' + parts.join('&') : '';
  }

  async function loadDatesPlan(cid, host, overrides) {
    host.innerHTML = '<div class="toolsLoading">Reading the modules and the term calendar...</div>';
    try {
      const plan = overrides
        ? await api(base(cid) + '/dates/plan', { body: Object.assign({}, overrides, tz()) })
        : await api(base(cid) + '/dates/plan' + qs(tz()));
      S.tools.datesPlan = plan;
      paintDates(cid, host, plan, S.tools.state || {});
    } catch (err) {
      host.innerHTML = '<div class="callout bad">' + esc(firstLine(err.message)) + '</div>';
    }
  }

  function paintDates(cid, host, plan, state) {
    const facts = plan.facts || {};
    const missing = facts.missing || [];
    const rows = plan.rows || [];
    const last = (state.dates && state.dates.last_apply) || null;
    const weekdays = (state.dates && state.dates.weekdays) || ['Monday'];

    const tableRows = rows.map((r, i) => {
      const live = (r.items || []).filter(it => !it.skip);
      const current = live.map(it => it.current).filter(Boolean);
      const same = current.length && current.every(c => c === current[0]);
      return '<tr data-row="' + i + '">'
        + '<th scope="row">' + esc(r.week == null ? '-' : 'Week ' + r.week) + '<div class="toolsSub">'
        + esc(r.module || '') + '</div></th>'
        + '<td>' + (live.length
          ? live.map(it => '<div class="toolsItem">' + esc(it.name || it.id)
            + ' ' + pillOf(it.kind, 'muted') + '</div>').join('')
          : '<span class="toolsNone">no assignments or quizzes</span>') + '</td>'
        + '<td>' + esc(same ? when(current[0]) : (current.length ? 'several dates' : 'no due date')) + '</td>'
        + '<td>' + (r.due_local
          ? '<input class="dtProposed" type="datetime-local" data-row="' + i + '" value="'
            + esc(String(r.due_local).slice(0, 16)) + '">'
          : '<span class="toolsNone">not dated</span>') + '</td>'
        + '<td class="toolsNoteCell">' + esc(r.note || '') + '</td>'
        + '</tr>';
    });

    host.innerHTML = ''
      + '<section class="toolsSection"><h3>What the term looks like</h3>'
      + (missing.length
        ? note('Canvas could not answer ' + missing.join(', ')
          + '. Type it in below and the table is computed from it. Nothing is sent while you do.')
        : '')
      + '<div class="toolsFacts">' + FACTS.map(f => factField(f, facts)).join('')
      + '<label class="toolsField"><span class="toolsLbl">Due weekday</span>'
      + '<select id="dt_weekday">' + weekdays.map(d => '<option value="' + esc(d) + '"'
        + (d === facts.weekday ? ' selected' : '') + '>' + esc(d) + '</option>').join('') + '</select>'
      + '<span class="toolsHint">Each week is due this day of the following week.</span></label>'
      + '<label class="toolsField"><span class="toolsLbl">Due time</span>'
      + '<input id="dt_time" type="time" value="' + esc(facts.time || '23:59') + '">'
      + '<span class="toolsHint">Your own clock, turned into the right instant for Canvas.</span></label>'
      + '</div>'
      + '<div class="toolsVerbs"><button class="btn primary" id="dtCompute">Compute the week table</button>'
      + '<span class="toolsHint">Reads the modules. Nothing is written.</span></div>'
      + '</section>'
      + '<section class="toolsSection"><h3>The weeks</h3>'
      + (rows.length
        ? table('One row per module, with the date each week\'s work is due',
          ['Week', 'Assignments and quizzes', 'Due now', 'Proposed due', 'Note'], tableRows)
        : '<div class="toolsNone">No Week N modules were found, so there is nothing to date. '
          + 'Modules named "Week 1", "Week 2" and so on are what this reads.</div>')
      + '<div class="toolsVerbs">'
      + '<button class="btn danger" id="dtApply"' + (plan.writes ? '' : ' disabled title="Every item already has its proposed date"')
      + '>Apply ' + (plan.writes || 0) + ' due date' + (plan.writes === 1 ? '' : 's') + '</button>'
      + '<span class="toolsHint">' + esc(plan.writes
        ? 'Asks first, and shows every week it would change.'
        : 'Every item already has the date below. Nothing to write.') + '</span>'
      + '</div>'
      + (last ? '<div class="toolsHint">Last written ' + esc(when(last.at)) + ': '
        + esc((last.written || 0) + ' set, ' + (last.failed || 0) + ' failed.') + '</div>' : '')
      + '</section>';

    $('#dtCompute').onclick = () => {
      const overrides = {
        start: val('dt_start'), weeks: val('dt_weeks'), term: val('dt_term'),
        finals_end: val('dt_finals_end'), breaks: val('dt_breaks'),
        weekday: val('dt_weekday'), time: val('dt_time'),
      };
      Object.keys(overrides).forEach(k => { if (overrides[k] === '') delete overrides[k]; });
      loadDatesPlan(cid, host, overrides);
    };
    const applyBtn = $('#dtApply');
    if (applyBtn) applyBtn.onclick = () => applyDates(cid, host, plan);
    // A proposed date typed over by hand is something to write even when the
    // computed table already matched the course, so the first edit wakes the
    // button up. The server still skips any item whose date is unchanged.
    host.querySelectorAll('.dtProposed').forEach(input => {
      input.oninput = () => {
        if (!applyBtn) return;
        applyBtn.disabled = false;
        applyBtn.title = '';
        applyBtn.textContent = 'Apply the dates as edited';
      };
    });
  }

  function applyDates(cid, host, plan) {
    const edited = (plan.rows || []).map((r, i) => {
      const input = host.querySelector('.dtProposed[data-row="' + i + '"]');
      if (!input || !input.value) return r;
      const value = input.value.length === 16 ? input.value + ':00' : input.value;
      return Object.assign({}, r, { due_local: value });
    });
    runJobConfirmed('Setting due dates',
      token => api(base(cid) + '/dates/apply',
        { body: Object.assign({ rows: edited, confirm: token }, tz()) }),
      res => {
        setStatus((res.written || 0) + ' due dates set'
          + (res.failed ? ', ' + res.failed + ' failed' : ''), res.failed ? 'err' : 'ok');
        announce((res.written || 0) + ' due dates written to Canvas.');
        reopen(cid);
      },
      { title: 'Set these due dates in Canvas?', verb: 'Yes, set them' });
  }

  /* ==================================================================
     2. Export
     ================================================================== */
  function renderExport(cid, host, state) {
    const ex = state.exports || {};
    const rows = (ex.rows || []).map(r => '<tr>'
      + '<th scope="row">' + esc(r.name || '') + '</th>'
      + '<td>' + esc(when(r.at)) + '</td>'
      + '<td>' + esc(r.type === 'zip' ? 'files only' : 'whole course') + '</td>'
      + '<td>' + esc(kb(r.bytes)) + '</td>'
      + '<td>' + (r.exists ? pillOf('on this machine', 'ok') : pillOf('file is gone', 'warn')) + '</td>'
      + '<td>' + (r.exists ? '<button class="btn sm" data-open="' + esc(r.path) + '">Open folder</button>' : '') + '</td>'
      + '</tr>');
    host.innerHTML = ''
      + '<section class="toolsSection"><h3>Back up the whole course</h3>'
      + '<p class="toolsHint">Canvas packs the course and this downloads the file. The course '
      + 'itself is not changed.</p>'
      + '<div class="toolsVerbs">'
      + '<label class="toolsInline"><span class="toolsLbl">What to pack</span>'
      + '<select id="exType">'
      + '<option value="common_cartridge">The whole course (.imscc)</option>'
      + '<option value="zip">The files only (.zip)</option></select></label>'
      + '<button class="btn primary" id="exRun">Export now</button>'
      + '</div>'
      + '<div class="callout toolsFerpa"><b>Keep the file on this machine.</b> A cartridge is '
      + 'course content, but on a course that has been taught Canvas can bundle discussion '
      + 'posts students wrote inside the exported topics. Nothing uploads it anywhere; it is '
      + 'only ever sent back into Canvas as an import.</div>'
      + '<p class="toolsHint">Folder: <code>' + esc(ex.dir || '') + '</code> '
      + '<button class="btn sm" id="exOpenDir">Open folder</button></p>'
      + '</section>'
      + '<section class="toolsSection"><h3>Earlier backups</h3>'
      + table('Every export taken from this course', ['File', 'When', 'What', 'Size', 'State', ''], rows)
      + '</section>';
    $('#exRun').onclick = () => runJob('Exporting the course',
      () => api(base(cid) + '/export', { body: { type: val('exType') } }),
      res => {
        const e = (res && res.export) || {};
        setStatus('exported ' + (e.name || '') + ' (' + (e.kb || 0) + ' KB)', 'ok');
        reopen(cid);
      });
    $('#exOpenDir').onclick = () => openFolder(cid, null);
    host.querySelectorAll('[data-open]').forEach(btn => {
      btn.onclick = () => openFolder(cid, btn.dataset.open);
    });
  }

  async function openFolder(cid, path) {
    try {
      const res = await api(base(cid) + '/exports/open', { body: path ? { path } : {} });
      setStatus(res.opened ? 'opened ' + res.dir : 'could not open that folder', res.opened ? 'ok' : 'err');
    } catch (err) { setStatus(firstLine(err.message), 'err'); }
  }

  /* ==================================================================
     3 and 4. Import and clone
     ================================================================== */
  function modeCard(id, title, hint, current) {
    return '<button type="button" class="toolsMode" role="radio" aria-checked="' + (id === current)
      + '" data-mode="' + esc(id) + '"><b>' + esc(title) + '</b>'
      + '<span class="toolsHint">' + esc(hint) + '</span></button>';
  }

  function renderImport(cid, host, state) {
    const mode = S.tools.importMode || 'course';
    const dest = S.tools.importDest || 'this';
    const plan = S.tools.importPlan || null;
    const imports = (state.imports && state.imports.rows) || [];

    host.innerHTML = ''
      + '<section class="toolsSection"><h3>Where the content comes from</h3>'
      + '<div class="toolsModes" role="radiogroup" aria-label="Source">'
      + modeCard('course', 'Another Canvas course', 'Copy everything a course holds into this one.', mode)
      + modeCard('imscc', 'A cartridge file', 'An .imscc already on this machine.', mode)
      + '</div>'
      + (mode === 'course'
        ? '<label class="toolsField"><span class="toolsLbl">Source course id</span>'
          + '<input id="imSource" type="text" inputmode="numeric" value="' + esc(S.tools.importSource || '') + '">'
          + '<span class="toolsHint">The number in the course URL in Canvas.</span></label>'
        : '<label class="toolsField"><span class="toolsLbl">Path to the .imscc file</span>'
          + '<input id="imPath" type="text" value="' + esc(S.tools.importPath || '') + '">'
          + '<span class="toolsHint">A file on this machine. Exports you took are in the Export tab.</span></label>')
      + '</section>'
      + '<section class="toolsSection"><h3>Where it goes</h3>'
      + '<div class="toolsModes" role="radiogroup" aria-label="Destination">'
      + '<button type="button" class="toolsMode" role="radio" aria-checked="' + (dest === 'this')
      + '" data-dest="this"><b>This course</b><span class="toolsHint">'
      + esc(state.course_label || 'this course') + '</span></button>'
      + '<button type="button" class="toolsMode" role="radio" aria-checked="' + (dest === 'new')
      + '" data-dest="new"><b>A new, unpublished shell</b><span class="toolsHint">'
      + 'Created first, then filled. Students cannot see it.</span></button>'
      + '</div>'
      + (dest === 'new'
        ? '<label class="toolsField"><span class="toolsLbl">Name for the new shell</span>'
          + '<input id="imName" type="text" value="' + esc(S.tools.importName || '') + '"></label>'
          + accountField('imAccount', plan)
        : '')
      + '</section>'
      + planPane(plan)
      + '<div class="toolsVerbs">'
      + '<button class="btn primary" id="imDry">Dry run</button>'
      + '<button class="btn danger" id="imGo"' + (plan && !plan.refusal ? '' : ' disabled title="Do the dry run first"')
      + '>Import</button>'
      + '<span class="toolsHint">The dry run counts both sides and sends nothing.</span>'
      + '</div>'
      + '<section class="toolsSection"><h3>Earlier imports</h3>'
      + table('What has been copied into this course', ['Source', 'Into', 'When', 'Landed'],
        imports.map(r => '<tr><th scope="row">' + esc(r.source_name || r.source || '') + '</th>'
          + '<td>' + esc(r.dest_name || r.dest || '') + (r.new_shell ? ' ' + pillOf('new shell', 'muted') : '') + '</td>'
          + '<td>' + esc(when(r.at)) + '</td>'
          + '<td>' + esc(countsText(r.actual)) + '</td></tr>'))
      + '</section>';

    host.querySelectorAll('[data-mode]').forEach(el => {
      el.onclick = () => { S.tools.importMode = el.dataset.mode; S.tools.importPlan = null; renderImport(cid, host, state); };
    });
    host.querySelectorAll('[data-dest]').forEach(el => {
      el.onclick = () => { S.tools.importDest = el.dataset.dest; S.tools.importPlan = null; renderImport(cid, host, state); };
    });
    $('#imDry').onclick = () => runImport(cid, host, state, false);
    const go = $('#imGo');
    if (go) go.onclick = () => runImport(cid, host, state, true);
  }

  function accountField(id, plan) {
    const accounts = ((plan && plan.destination && plan.destination.accounts) || []);
    if (!accounts.length) {
      return '<label class="toolsField"><span class="toolsLbl">Account id</span>'
        + '<input id="' + id + '" type="text" inputmode="numeric" value="' + esc(S.tools.importAccount || '') + '">'
        + '<span class="toolsHint">The account the shell is created in. The dry run lists the '
        + 'ones your role can see.</span></label>';
    }
    return '<label class="toolsField"><span class="toolsLbl">Account</span>'
      + '<select id="' + id + '">' + accounts.map(a => '<option value="' + esc(a.id) + '"'
        + (String(a.id) === String(S.tools.importAccount) ? ' selected' : '') + '>'
        + esc(a.name || a.id) + '</option>').join('') + '</select></label>';
  }

  function countsText(counts) {
    if (!counts) return '';
    return Object.entries(counts).filter(([, v]) => +v > 0)
      .map(([k, v]) => v + ' ' + k).join(', ') || 'nothing';
  }

  function importBody(cid, apply, token) {
    const mode = S.tools.importMode || 'course';
    const dest = S.tools.importDest || 'this';
    const body = { force: on('imForce'), apply: !!apply, confirm: token };
    if (mode === 'course') body.source_course = S.tools.importSource = val('imSource');
    else body.imscc_path = S.tools.importPath = val('imPath');
    if (dest === 'new') {
      S.tools.importName = val('imName');
      S.tools.importAccount = val('imAccount');
      body.new_course = { name: S.tools.importName, account_id: S.tools.importAccount || null };
    } else body.dest_course = cid;
    return body;
  }

  function runImport(cid, host, state, apply) {
    if (!apply) {
      return runJob('Counting both sides',
        () => api(base(cid) + '/import/plan', { body: importBody(cid, false, null) }),
        plan => { S.tools.importPlan = plan; renderImport(cid, host, state); });
    }
    runJobConfirmed('Importing into the course',
      token => api(base(cid) + '/import', { body: importBody(cid, true, token) }),
      res => {
        const r = (res && res.result) || {};
        setStatus('imported into ' + (r.dest_name || 'the course'), 'ok');
        S.tools.importPlan = null;
        reopen(cid);
      },
      { title: 'Copy this content into the course?', verb: 'Yes, import it' });
  }

  function planPane(plan) {
    if (!plan) {
      return '<section class="toolsSection"><div class="toolsNone">No dry run yet. The dry run '
        + 'counts what the source holds and what the destination already has, and sends '
        + 'nothing.</div></section>';
    }
    const s = plan.source || {}, d = plan.destination || {};
    const rows = (plan.detail || []).map(x => '<div class="planned"><b>' + esc(x.label) + '</b> '
      + esc(x.from || '') + ' &rarr; ' + esc(x.to || '') + '</div>');
    return '<section class="toolsSection"><h3>What the dry run found</h3>'
      + '<div class="toolsPair"><div><b>From</b><div>' + esc(s.name || '') + '</div>'
      + '<div class="toolsHint">' + esc(s.summary || '') + '</div></div>'
      + '<div><b>Into</b><div>' + esc(d.new ? d.name + ' (a new shell)' : d.name || '') + '</div>'
      + '<div class="toolsHint">' + esc(d.new ? 'created unpublished' : (d.summary || '')) + '</div></div></div>'
      + (rows.length ? '<div class="applyList">' + rows.join('') + '</div>' : '')
      + (plan.refusal
        ? '<div class="callout toolsWarn"><b>' + esc(plan.refusal) + '</b>'
          + '<label class="toolsTick"><input type="checkbox" id="imForce"' + (plan.force ? ' checked' : '')
          + '> Add anyway</label></div>'
        : '<div class="toolsSentence">' + esc(plan.sentence || '') + '</div>')
      + '</section>';
  }

  function renderClone(cid, host, state) {
    const plan = S.tools.clonePlan || null;
    host.innerHTML = ''
      + '<section class="toolsSection"><h3>Copy this course into a fresh shell</h3>'
      + '<p class="toolsHint">A new, unpublished course is created and everything this course '
      + 'holds is copied into it. This course is not changed.</p>'
      + '<label class="toolsField"><span class="toolsLbl">Name for the new shell</span>'
      + '<input id="clName" type="text" value="' + esc(S.tools.cloneName
        || ((state.course_label || 'Course') + ' sandbox')) + '"></label>'
      + '<label class="toolsField"><span class="toolsLbl">Course code</span>'
      + '<input id="clCode" type="text" value="' + esc(S.tools.cloneCode || '') + '">'
      + '<span class="toolsHint">Optional. The name is used when this is empty.</span></label>'
      + accountField('clAccount', plan)
      + '</section>'
      + planPane(plan)
      + '<div class="toolsVerbs">'
      + '<button class="btn primary" id="clDry">Dry run</button>'
      + '<button class="btn danger" id="clGo"' + (plan && !plan.refusal ? '' : ' disabled title="Do the dry run first"')
      + '>Create the shell and copy</button>'
      + '</div>';
    $('#clDry').onclick = () => runClone(cid, host, state, false);
    const go = $('#clGo');
    if (go) go.onclick = () => runClone(cid, host, state, true);
  }

  function cloneBody(apply, token) {
    S.tools.cloneName = val('clName');
    S.tools.cloneCode = val('clCode');
    S.tools.importAccount = val('clAccount');
    return {
      name: S.tools.cloneName, course_code: S.tools.cloneCode,
      account_id: S.tools.importAccount || null, apply: !!apply, confirm: token,
    };
  }

  function runClone(cid, host, state, apply) {
    if (!apply) {
      return runJob('Counting this course',
        () => api(base(cid) + '/clone', { body: cloneBody(false, null) }),
        plan => { S.tools.clonePlan = plan; renderClone(cid, host, state); });
    }
    runJobConfirmed('Cloning the course',
      token => api(base(cid) + '/clone', { body: cloneBody(true, token) }),
      res => {
        const r = (res && res.result) || {};
        setStatus('cloned into course ' + (r.dest || ''), 'ok');
        S.tools.clonePlan = null;
        reopen(cid);
      },
      { title: 'Create the shell and copy into it?', verb: 'Yes, clone it' });
  }

  /* ==================================================================
     5. Trim the navigation
     ================================================================== */
  async function renderNav(cid, host, state) {
    host.innerHTML = '<div class="toolsLoading">Reading the course navigation...</div>';
    let plan;
    try {
      plan = await api(base(cid) + '/nav');
    } catch (err) {
      host.innerHTML = '<div class="callout bad">' + esc(firstLine(err.message)) + '</div>';
      return;
    }
    const ACTION = {
      hide: 'hide it', show: 'show it', order: 'move it', keep: 'leave it shown',
      'keep hidden': 'leave it hidden', leave: 'never touched',
    };
    const rows = (plan.rows || []).map(r => '<tr>'
      + '<th scope="row">' + esc(r.label) + (r.lti ? ' ' + pillOf('external tool', 'muted') : '') + '</th>'
      + '<td>' + esc(r.hidden_now ? 'hidden' : 'shown, position ' + r.position_now) + '</td>'
      + '<td>' + esc(r.hidden_after ? 'hidden' : 'shown, position ' + r.position_after) + '</td>'
      + '<td>' + pillOf(ACTION[r.action] || r.action,
        r.action === 'hide' ? 'warn' : (r.action === 'show' || r.action === 'order' ? 'ok' : 'muted'))
      + '</td><td class="toolsNoteCell">' + esc(r.note || '') + '</td></tr>');
    host.innerHTML = ''
      + '<section class="toolsSection"><h3>What students would see</h3>'
      + '<p class="toolsSentence">' + esc((plan.visible_after || []).join('  >  ')) + '</p>'
      + (plan.dropped && plan.dropped.length
        ? note('Not in this course, so skipped: ' + plan.dropped.join(', ') + '.') : '')
      + table('Every tab in the course navigation',
        ['Tab', 'Now', 'After', 'Action', 'Note'], rows)
      + '<div class="toolsVerbs">'
      + '<button class="btn danger" id="navApply"' + (plan.changes ? '' : ' disabled title="The navigation already matches"')
      + '>Apply ' + (plan.changes || 0) + ' change' + (plan.changes === 1 ? '' : 's') + '</button>'
      + '<span class="toolsHint">No content is changed, and a hidden tab can be shown again '
      + 'from Settings.</span></div>'
      + '</section>';
    const btn = $('#navApply');
    if (btn) btn.onclick = () => runJobConfirmed('Trimming the navigation',
      token => api(base(cid) + '/nav', { body: { apply: true, confirm: token } }),
      res => {
        setStatus((res.written || 0) + ' tabs changed'
          + (res.mismatches ? ', ' + res.mismatches + ' did not take' : ''),
          res.mismatches ? 'err' : 'ok');
        reopen(cid);
      },
      { title: 'Change the course navigation?', verb: 'Yes, change it' });
  }

  /* ==================================================================
     6. Back up a quiz
     ================================================================== */
  async function renderQuiz(cid, host, state) {
    host.innerHTML = '<div class="toolsLoading">Reading the quizzes...</div>';
    let data;
    try {
      data = await api(base(cid) + '/quizzes');
    } catch (err) {
      host.innerHTML = '<div class="callout bad">' + esc(firstLine(err.message)) + '</div>';
      return;
    }
    const list = (data.quizzes || []).filter(q => !q.is_backup);
    const history = data.history || [];
    host.innerHTML = ''
      + '<section class="toolsSection"><h3>Copy a quiz before you change it</h3>'
      + '<p class="toolsHint">Canvas will not duplicate a quiz-backed assignment, so the copy is '
      + 'built question by question. It is always created unpublished, whatever the original is, '
      + 'and the original is not touched.</p>'
      + (list.length
        ? '<div class="toolsVerbs">'
          + '<label class="toolsInline"><span class="toolsLbl">Quiz</span><select id="qzPick">'
          + list.map(q => '<option value="' + esc(q.id) + '">' + esc(q.title || q.id)
            + ' (' + (q.question_count || 0) + ' questions, ' + (q.published ? 'published' : 'unpublished') + ')'
            + '</option>').join('') + '</select></label>'
          + '<button class="btn danger" id="qzGo">Back it up</button>'
          + '</div>'
        : '<div class="toolsNone">This course has no Classic Quizzes to copy.</div>')
      + '</section>'
      + '<section class="toolsSection"><h3>Copies already made</h3>'
      + table('Every backup quiz this tool created here',
        ['Backup', 'Of', 'When', 'Questions', 'State', ''],
        history.map(h => '<tr><th scope="row">' + esc(h.title || '') + '</th>'
          + '<td>' + esc(h.source_title || '') + '</td>'
          + '<td>' + esc(when(h.at)) + '</td>'
          + '<td>' + esc((h.questions_copy || 0) + ' of ' + (h.questions_source || 0)) + '</td>'
          + '<td>' + (h.ok ? pillOf('copied and checked', 'ok')
            : pillOf((h.warnings || ['check it'])[0], 'warn')) + '</td>'
          + '<td>' + (h.url ? '<a class="btn sm" href="' + esc(h.url) + '" target="_blank" rel="noopener">Open in Canvas</a>' : '')
          + '</td></tr>'))
      + '</section>';
    const go = $('#qzGo');
    if (go) go.onclick = () => runJobConfirmed('Backing up the quiz',
      token => api(base(cid) + '/quiz-backup', { body: { quiz_id: val('qzPick'), confirm: token } }),
      res => {
        setStatus('created ' + (res.title || 'the backup') + ' with '
          + (res.questions_copy || 0) + ' questions', res.ok ? 'ok' : 'err');
        reopen(cid);
      },
      { title: 'Create an unpublished copy of this quiz?', verb: 'Yes, copy it' });
  }

  /* ==================================================================
     7. Outcome alignment
     ================================================================== */
  const VERDICT_WORD = {
    assessed: 'assessed',
    'partially-assessed': 'partly assessed',
    'ungraded-only': 'ungraded coverage only',
    'not-assessed': 'not assessed',
    'not-applicable': 'not applicable',
  };
  const verdictClass = v => 'slo-' + String(v || 'unknown').replace(/[^a-z-]/g, '');

  function stepChip(n, label, done, current) {
    return '<li class="sloStep' + (done ? ' done' : '') + (current ? ' now' : '') + '">'
      + '<span class="sloStepN">' + n + '</span>' + esc(label)
      + (done ? ' ' + pillOf('done', 'ok') : '') + '</li>';
  }

  function renderSlo(cid, host, st) {
    st = st || {};
    const step = st.step || 'resolve';
    const counts = (st.validate && st.validate.counts) || {};
    const resolved = st.resolve || null;
    const fetched = st.fetch || null;
    const rows = st.alignment || [];
    const order = ['resolve', 'fetch', 'align', 'report', 'done'];
    const at = order.indexOf(step);

    host.innerHTML = ''
      + '<div id="sloNeeds"></div>'
      + '<ol class="sloSteps">'
      + stepChip(1, 'Find the program', at > 0, at === 0)
      + stepChip(2, 'Read the framework', at > 1, at === 1)
      + stepChip(3, 'Judge the alignment', at > 2, at === 2)
      + stepChip(4, 'Put the report in the course', at > 3, at === 3)
      + '</ol>'
      + sloResolvePane(resolved)
      + sloFetchPane(fetched, st)
      + sloAlignPane(st, counts, rows)
      + sloReportPane(st, counts);

    $('#sloResolve').onclick = () => runJob('Finding the program',
      () => api(base(cid) + '/slo/resolve', { body: { course_code: val('sloCode') } }),
      res => { setStatus(firstLine(res.conclusion), res.resolved ? 'ok' : 'err'); refreshSlo(cid, host); });
    const fetchBtn = $('#sloFetch');
    if (fetchBtn) fetchBtn.onclick = () => runJob('Reading the framework and the course',
      () => api(base(cid) + '/slo/fetch', { body: { slug: pickedSlug(host), use_prior: on('sloPrior') } }),
      res => { setStatus((res.outcomes || 0) + ' outcomes read; nothing pushed', 'ok'); refreshSlo(cid, host); });
    const alignBtn = $('#sloAlign');
    if (alignBtn) alignBtn.onclick = () => runJob('Judging the alignment with Claude',
      () => api(base(cid) + '/slo/align', { body: {} }),
      res => {
        const c = (res.validate && res.validate.counts) || {};
        setStatus(res.ok ? (c.assessed || 0) + ' of ' + (c.outcomes || 0) + ' outcomes assessed'
          : 'the alignment did not pass the check', res.ok ? 'ok' : 'err');
        renderSlo(cid, host, res.state);
      });
    const dryBtn = $('#sloDry');
    if (dryBtn) dryBtn.onclick = () => runJob('Writing the report',
      () => api(base(cid) + '/slo/report', { body: { apply: false } }),
      res => { S.tools.sloPlan = res; setStatus('report written on this machine; nothing pushed', 'ok');
        renderSlo(cid, host, res.state); });
    const pushBtn = $('#sloPush');
    if (pushBtn) pushBtn.onclick = () => runJobConfirmed('Adding the report to the course',
      token => api(base(cid) + '/slo/report', { body: { apply: true, confirm: token } }),
      res => {
        setStatus(res.ok ? 'report page written, unpublished' : 'written, but check the warnings',
          res.ok ? 'ok' : 'err');
        renderSlo(cid, host, res.state);
      },
      { title: 'Add the alignment report to the course?', verb: 'Yes, add the page' });
    host.querySelectorAll('.sloPick').forEach(el => {
      el.onchange = () => { S.tools.sloSlug = el.value; renderSlo(cid, host, st); };
    });
    /* The framework PDFs need PyMuPDF. Say what to install rather than failing
       halfway through the fetch. */
    if ((st.needs || []).length && has('needCards')) needCards(st.needs, $('#sloNeeds'));
    paintSloStats(counts);
  }

  async function refreshSlo(cid, host) {
    try { renderSlo(cid, host, await api(base(cid) + '/slo/state')); }
    catch (err) { setStatus(firstLine(err.message), 'err'); }
  }

  function pickedSlug(host) {
    const checked = host.querySelector('.sloPick:checked');
    return (checked && checked.value) || S.tools.sloSlug || '';
  }

  function sloResolvePane(resolved) {
    const choices = (resolved && resolved.choices) || [];
    return '<section class="toolsSection"><h3>1. Find the program</h3>'
      + '<p class="toolsHint">The course code names the program, and the program names the '
      + 'framework that lists the outcomes. Reads the Mississippi program index only.</p>'
      + '<div class="toolsVerbs">'
      + '<label class="toolsInline"><span class="toolsLbl">Course code</span>'
      + '<input id="sloCode" type="text" value="'
      + esc((resolved && resolved.query && resolved.query.course_code) || '') + '"'
      + ' placeholder="leave empty to read it from Canvas"></label>'
      + '<button class="btn primary" id="sloResolve">Find the program</button></div>'
      + (resolved
        ? '<p class="toolsSentence">' + esc(resolved.conclusion || '') + '</p>'
          + (choices.length > 1
            ? '<div class="sloChoices" role="radiogroup" aria-label="Program">'
              + choices.map(c => '<label class="sloChoice"><input type="radio" name="sloPick" '
                + 'class="sloPick" value="' + esc(c.slug) + '"'
                + (String(c.slug) === String(S.tools.sloSlug) ? ' checked' : '') + '> '
                + '<b>' + esc(c.name) + '</b> <span class="toolsHint">CIP ' + esc(c.cip || '')
                + ', prefixes ' + esc((c.prefixes || []).join(', ')) + '</span></label>').join('')
              + '</div>'
            : '')
        : '');
  }

  function sloFetchPane(fetched, st) {
    const course = (fetched && fetched.course) || null;
    const ready = !!(st.resolve && (!st.resolve.ambiguous || S.tools.sloSlug));
    return '<section class="toolsSection"><h3>2. Read the framework and the course</h3>'
      + '<p class="toolsHint">Downloads the framework, pulls this course\'s outcomes out of it, '
      + 'and lists what the Canvas course holds. Reads only.</p>'
      + '<div class="toolsVerbs">'
      + '<button class="btn primary" id="sloFetch"' + (ready ? '' : ' disabled title="Find the program first"')
      + '>Read them</button>'
      + '<label class="toolsTick"><input type="checkbox" id="sloPrior"> Use last year\'s framework</label>'
      + '</div>'
      + (fetched
        ? '<div class="toolsPair"><div><b>Framework</b><div>'
          + esc(fetched.framework.name || '') + '</div><div class="toolsHint">'
          + esc((fetched.framework.year || '') + (fetched.framework.current ? ', current' : ', prior year'))
          + '</div></div><div><b>Course in the framework</b><div>'
          + esc((((course && course.course) || '') + ' '
            + ((course && course.title) || '')).trim() || 'not matched') + '</div>'
          + '<div class="toolsHint">' + esc(fetched.outcomes + ' outcomes, matched on '
            + (fetched.match || 'the code')) + '</div></div></div>'
          + (fetched.note ? note(fetched.note) : '')
        : '');
  }

  function sloAlignPane(st, counts, rows) {
    const errors = (st.validate && st.validate.errors) || [];
    const table_ = rows.length ? table('Every outcome, its verdict, and what it is judged on',
      ['Outcome', 'Verdict', 'Evidence', 'Why'],
      rows.map(r => '<tr><th scope="row">' + esc(r.slo) + '<div class="toolsSub">'
        + esc(r.text || '') + '</div></th>'
        + '<td>' + pillOf(VERDICT_WORD[r.verdict] || r.verdict, verdictClass(r.verdict)) + '</td>'
        + '<td>' + ((r.evidence || []).length
          ? r.evidence.map(e => (e.url
            ? '<a href="' + esc(e.url) + '" target="_blank" rel="noopener">' + esc(e.name) + '</a>'
            : esc(e.name)) + (e.type ? ' ' + pillOf(e.type, 'muted') : '')).join('<br>')
          : '<span class="toolsNone">none</span>') + '</td>'
        + '<td class="toolsNoteCell">' + esc(r.rationale || '')
        + (r.suggestion ? '<div class="sloFix"><b>Fix:</b> ' + esc(r.suggestion) + '</div>' : '')
        + '</td></tr>')) : '';
    return '<section class="toolsSection"><h3>3. Judge the alignment</h3>'
      + '<p class="toolsHint">Claude maps each outcome to the graded work that meets it. The '
      + 'answer is then checked: an outcome left out, an invented id, an item that is not in '
      + 'this course, or coverage claimed with nothing cited, and it is rejected.</p>'
      + '<div class="toolsVerbs">'
      + '<button class="btn ai" id="sloAlign"' + (st.outcomes && st.outcomes.length ? '' : ' disabled title="Read the framework first"')
      + '>Judge it with Claude</button>'
      + (st.cost_usd ? '<span class="toolsHint">Last run cost about $'
        + esc((+st.cost_usd).toFixed(2)) + '.</span>' : '')
      + '</div>'
      + (errors.length
        ? '<div class="callout bad"><b>The alignment did not pass the check, so no report can be '
          + 'written from it.</b><ul>' + errors.map(e => '<li>' + esc(e) + '</li>').join('') + '</ul></div>'
        : '')
      + (counts.outcomes
        ? '<div id="sloStats"></div>'
        : '')
      + table_;
  }

  function sloReportPane(st, counts) {
    const pushed = st.report || null;
    const ready = st.validate && st.validate.ok;
    return '<section class="toolsSection"><h3>4. Put the report in the course</h3>'
      + '<p class="toolsHint">One page, always unpublished, always the same address, so running '
      + 'this again replaces it instead of leaving a second copy. Students cannot see it.</p>'
      + '<div class="toolsVerbs">'
      + '<button class="btn" id="sloDry"' + (ready ? '' : ' disabled title="The alignment has to pass the check first"')
      + '>Write it on this machine</button>'
      + '<button class="btn danger" id="sloPush"' + (ready && st.has_report_file ? '' : ' disabled title="Write it here first"')
      + '>Add it to the course</button>'
      + '</div>'
      + (pushed
        ? '<div class="toolsSentence">Last added ' + esc(when(pushed.at)) + ': '
          + '<a href="' + esc(pushed.url || '') + '" target="_blank" rel="noopener">'
          + esc(st.report_title) + '</a> ' + pillOf('unpublished', 'muted') + '</div>'
          + ((pushed.warnings || []).length
            ? '<div class="callout toolsWarn"><ul>'
              + pushed.warnings.map(w => '<li>' + esc(w) + '</li>').join('') + '</ul></div>'
            : '')
        : '<div class="toolsNone">The report is not in the course. Nothing has been pushed.</div>');
  }

  /* Stat strip for the alignment, painted after the HTML is in place. */
  function paintSloStats(counts) {
    const host = $('#sloStats');
    if (!host || !has('statStrip')) return;
    statStrip(host, [
      { label: 'Outcomes', value: counts.outcomes },
      { label: 'Assessed', value: counts.assessed },
      { label: 'Partly', value: counts.partial },
      { label: 'Gaps', value: counts.gaps, kind: counts.gaps ? 'warn' : '' },
      { label: 'Coverage', value: (counts.coverage || 0) + '%' },
    ]);
  }

  /* ------------------------------------------------------------ register */
  window.openTools = openTools;
  if (has('registerArea')) {
    registerArea({
      id: 'tools', label: 'Tools', zone: 'tools', open: openTools,
      badge: hub => (hub && hub.tools && hub.tools.badge != null) ? hub.tools.badge : null,
    });
  }
})();
