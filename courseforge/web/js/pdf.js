/* pdf.js: the PDF fixer.

   Three panes behind one verb row: the file table with the queue panel, the
   alt-text grid, and the compliance census. The Accessibility area delegates
   here through Studio.a11yKinds.pdf, and the same opener is registered as an
   area so #/c/<cid>/pdf works on its own.

   Nothing here reaches Canvas except Upload and Roll back, and both go through
   runJobConfirmed: the server refuses the first attempt with a sentence, this
   page shows that sentence as it was written, and the token only spends on
   that exact change. */
(function () {
  'use strict';

  window.Studio = window.Studio || {};
  Studio.a11yKinds = Studio.a11yKinds || {};
  window.S = window.S || {};
  S.pdf = S.pdf || { filter: 'all', dirty: {}, sub: '' };

  const has = name => typeof window[name] === 'function';
  const base = cid => '/pdf/' + encodeURIComponent(cid);
  const FIX = 'Fix accessibility';
  const FIND = 'Find and replace';
  const KINDS = [
    { id: 'html', label: 'Pages', group: FIX,
      title: 'Pages, assignments, discussions, quiz descriptions and the syllabus: '
        + 'the course text Canvas stores as HTML' },
    { id: 'pptx', label: 'PowerPoint', group: FIX,
      title: 'Alt text, slide titles and table header rows inside .pptx files' },
    { id: 'docx', label: 'Word', group: FIX,
      title: 'Alt text, table header rows and heading structure inside .docx files' },
    { id: 'pdf', label: 'PDFs', group: FIX,
      title: 'Tag tree, reading order, OCR, alt text and PDF/UA-1 compliance for .pdf files' },
    { id: 'triage', label: 'PDF triage', group: FIX,
      title: 'Which PDFs are worst, before fixing any of them. Reads only.' },
    { id: 'pdf-text', label: 'in PDFs', group: FIND,
      title: 'Change the words inside PDFs -- a retired name, an old course code. '
        + 'Nothing to do with accessibility.' },
    { id: 'office-text', label: 'in Office files', group: FIND,
      title: 'The same find and replace inside .docx, .pptx and .xlsx. '
        + 'Nothing to do with accessibility.' },
  ];
  const SUBS = [
    { id: '', label: 'Files' },
    { id: 'alt', label: 'Alt text' },
    { id: 'prove', label: 'Prove compliance' },
  ];
  const FILTERS = [
    { id: 'all', label: 'All' },
    { id: 'todo', label: 'Not backed up' },
    { id: 'fixed', label: 'Fixed' },
    { id: 'person', label: 'Needs a person' },
    { id: 'unpushed', label: 'Not uploaded' },
    { id: 'placeholder', label: 'Placeholders left' },
  ];
  const PASSES = {
    all: () => true,
    todo: r => r.state === 'not fetched',
    fixed: r => r.fixed_size != null,
    person: r => ['queued', 'refused', 'review'].indexOf(r.lane) >= 0,
    unpushed: r => r.state === 'fixed, not uploaded',
    placeholder: r => !!(r.alt && (r.alt.waiting || r.alt.no_picture)),
  };

  const pillOf = (text, cls) => (has('pill') ? pill(text, cls)
    : '<span class="pill ' + esc(cls || '') + '">' + esc(text) + '</span>');
  const laneOf = lane => (has('lanePill') ? lanePill(lane) : pillOf(lane || 'no lane', ''));
  const stateOf = st => (has('statePill') ? statePill(st) : pillOf(st, ''));
  const bytes = n => (has('fmtBytes') ? fmtBytes(n) : (n == null ? '' : n + ' B'));
  const fmtSize = n => bytes(n);
  const n1 = (n, one, many) => n + ' ' + (n === 1 ? one : (many || one + 's'));

  function hashFor(cid, sub) {
    const root = S.pdf.viaA11y ? '#/c/' + cid + '/a11y/pdf' : '#/c/' + cid + '/pdf';
    return sub ? root + '/' + sub : root;
  }

  function modal(html) {
    if (has('openModal')) { openModal(html); return $('#modalHost'); }
    $('#modalHost').innerHTML = '<div class="modalBack"><div class="modal">' + html + '</div></div>';
    return $('#modalHost');
  }
  const shut = () => (has('closeModal') ? closeModal() : ($('#modalHost').innerHTML = ''));

  /* ------------------------------------------------------------- the area */
  async function openPdf(cid, rest) {
    rest = Array.isArray(rest) ? rest : [];
    const sub = SUBS.some(s => s.id === rest[0]) ? rest[0] : '';
    S.pdf.courseId = String(cid);
    S.pdf.sub = sub;
    S.pdf.viaA11y = (location.hash || '').indexOf('/a11y/') >= 0;

    showView('area');
    await courseCrumbs(cid, [
      { label: 'Accessibility', href: '#/c/' + cid + '/a11y/html' },
      { label: 'PDFs' },
    ]);
    if (String(S.pdf.courseId) !== String(cid)) return;
    if (!S.pdf.viaA11y && has('areaHead')) {
      areaHead('Accessibility', 'Back up, repair, describe, prove, upload. The originals stay on this computer.');
      if (has('areaTabs')) {
        areaTabs(KINDS.map(k => ({ id: k.id, label: k.label, group: k.group, title: k.title })), 'pdf', id => {
          if (id !== 'pdf') location.hash = '#/c/' + cid + '/a11y/' + id;
        });
      }
    }
    $('#headerActions').innerHTML = '<button class="btn" id="pdfRefresh" '
      + 'title="Read this page again from the copy on this computer">Refresh</button>';
    $('#pdfRefresh').onclick = () => openPdf(cid, rest);

    const body = $('#areaBody');
    body.innerHTML = '<div class="pdfLoading hint">Reading what is on this computer...</div>';
    let st;
    try {
      st = await api(base(cid) + '/state');
    } catch (err) {
      body.innerHTML = '<div class="callout bad">Could not read the PDF state: '
        + esc(firstLine(err.message)) + '</div>';
      return;
    }
    S.pdf.state = st;
    S.pdf.courseLabel = st.course_label || S.pdf.courseLabel;
    paint(cid, st, sub);

    if (has('poll')) {
      onLeave(poll(async () => {
        if (S.pdf.sub !== sub || String(S.pdf.courseId) !== String(cid)) return;
        const fresh = await api(base(cid) + '/state');
        if (signature(fresh) === signature(S.pdf.state)) { S.pdf.state = fresh; return; }
        S.pdf.state = fresh;
        paint(cid, fresh, sub);
      }, 4000));
    }
  }

  /* A cheap fingerprint so the poller repaints only when something moved. */
  function signature(st) {
    const c = st.counts || {};
    return [st.listed_at, st.busy, c.files, c.fetched, c.fixed, c.verified, c.compliant,
      c.queued, c.not_uploaded, c.uploaded, c.alt_waiting,
      (st.alt_items || []).length, (st.census || {}).compliant].join('|');
  }

  function paint(cid, st, sub) {
    const body = $('#areaBody');
    body.innerHTML = ''
      + '<div id="pdfNeeds"></div>'
      + '<div class="pdfVerbs" id="pdfVerbs"></div>'
      + '<p class="hint pdfSummary">' + esc(st.summary || '')
      /* PDF triage has a screen and no way in: it is not one of the kind tabs,
         because a seventh tab reading "PDF triage" next to "PDFs" is the very
         confusion this row was just untangled from. It belongs here instead,
         where somebody is looking at a long file list wondering where to
         start. */
      + ' <a class="pdfTriageLink" href="#/c/' + esc(cid) + '/a11y/triage"'
      + ' title="Ranks the PDFs worst first and changes nothing">'
      + 'Not sure where to start? Rank them worst first.</a></p>'
      + '<div id="pdfStats"></div>'
      + '<nav class="pdfSub" aria-label="PDF views">' + SUBS.map(s =>
        '<a class="subTab" href="' + hashFor(cid, s.id) + '"'
        + (s.id === sub ? ' aria-current="page" aria-selected="true"' : '') + '>'
        + esc(s.label) + '</a>').join('') + '</nav>'
      + '<div id="pdfPane"></div>';
    renderVerbs(cid, st);
    if (has('statStrip')) statStrip($('#pdfStats'), stats(st));
    if (has('needCards') && (st.needs || []).length) needCards(st.needs, $('#pdfNeeds'));
    const pane = $('#pdfPane');
    if (sub === 'alt') return renderAlt(cid, st, pane);
    if (sub === 'prove') return renderProve(cid, st, pane);
    return renderFiles(cid, st, pane);
  }

  function stats(st) {
    const c = st.counts || {};
    return [
      { label: 'Files', value: c.files || 0 },
      { label: 'Fixed & verified', value: c.verified || 0 },
      { label: 'Pass PDF/UA-1', value: (st.census && st.census.files) ? (c.compliant || 0) : '—',
        title: (st.census && st.census.files) ? '' : 'Run Prove compliance to fill this in.' },
      { label: 'Queued for a person', value: c.queued || 0, kind: c.queued ? 'warn' : '' },
      { label: 'Not yet uploaded', value: c.not_uploaded || 0 },
    ];
  }

  /* Why Describe images is greyed out, in the terms of what was actually
     found. "Nothing is waiting for a description" was true in three quite
     different situations -- nothing has been looked at, nothing has a picture
     in it, everything is already described -- and reading the same sentence
     after a run that reported two files fixed looks exactly like a bug. */
  function whyNoDescribing(st, noEngine) {
    if (st.engine_ok === false) return noEngine;
    const c = st.counts || {};
    const sum = st.alt_summary || {};
    if (!(c.fixed || 0)) {
      return 'Nothing has been looked at yet. Run Back up and fix first; it '
        + 'finds the pictures on the way through.';
    }
    const pictures = sum.unique_images;
    if (pictures === 0) {
      return n1(c.fixed, 'PDF') + ' checked and not one picture in '
        + (c.fixed === 1 ? 'it' : 'them') + ', so there is nothing to describe. '
        + 'Text-only documents are the usual reason.';
    }
    const stuck = sum.no_picture_available || 0;
    return 'Every picture here already has a description'
      + (stuck ? ', apart from ' + n1(stuck, 'figure') + ' no picture could be '
                 + 'made of, which a person has to write' : '') + '.';
  }

  /* ----------------------------------------------------------- the verbs */
  function renderVerbs(cid, st) {
    const c = st.counts || {};
    const engine = st.engine_ok !== false;
    const noEngine = 'Install PyMuPDF, pikepdf and pypdf to read a PDF here.';
    const btn = (id, label, cls, on, why) =>
      '<button class="btn ' + cls + '" id="' + id + '"' + (on ? '' : ' disabled')
      + (why ? ' title="' + esc(why) + '"' : '') + '>' + label + '</button>';
    const canDescribe = engine && (st.alt_items || []).length > 0;
    const canUpload = engine && (c.not_uploaded || 0) > 0;
    const canRoll = (c.fetched || 0) > 0;
    $('#pdfVerbs').innerHTML = ''
      + btn('pdfList', 'Refresh file list', '', true, 'Read the course file list from Canvas again')
      + btn('pdfFix', 'Back up &amp; fix', 'primary', engine,
        engine ? 'Download every original, then repair the copies here. Nothing is pushed.' : noEngine)
      + btn('pdfDescribe', 'Describe images', 'ai', canDescribe,
        canDescribe ? 'Write real descriptions for the figures still on a placeholder'
          : whyNoDescribing(st, noEngine))
      + btn('pdfUpload', 'Upload', 'danger', canUpload,
        canUpload ? 'Put the fixed PDFs over their originals in Canvas'
          : 'Nothing is fixed and waiting to be uploaded.')
      + btn('pdfProve', 'Prove compliance', '', st.can_prove !== false,
        st.can_prove !== false ? 'Check every fixed PDF against PDF/UA-1 with veraPDF'
          : 'Install veraPDF and Java to check against PDF/UA-1.')
      + '<span class="vsep"></span>'
      + btn('pdfBackup', 'Back up only', '', true, 'Download the originals and change nothing')
      + btn('pdfRoll', 'Roll back', 'danger', canRoll,
        canRoll ? 'Put the original PDFs back over what is in Canvas now'
          : 'No originals are backed up on this computer yet.')
      + '<span class="spacer"></span>'
      + (st.busy ? '<button class="btn" id="pdfStop" title="Stop the job that is running">Stop</button>' : '');

    const go = (id, fn) => { const el = $('#' + id); if (el && !el.disabled) el.onclick = fn; };
    go('pdfList', () => job(cid, 'Refresh file list', '/list', {}));
    go('pdfFix', () => job(cid, 'Back up and fix', '/fix', {}));
    go('pdfDescribe', () => job(cid, 'Describe images with Claude', '/describe', {}));
    go('pdfBackup', () => job(cid, 'Back up only', '/backup', {}));
    go('pdfProve', () => job(cid, 'Prove compliance', '/prove',
      { profile: S.pdf.profile || 'ua1' }, () => { location.hash = hashFor(cid, 'prove'); }));
    go('pdfUpload', () => uploadFlow(cid));
    go('pdfRoll', () => rollbackFlow(cid));
    const stop = $('#pdfStop');
    if (stop) stop.onclick = async () => {
      try {
        const res = await api(base(cid) + '/cancel', { body: {} });
        setStatus(res.message || 'stopping', 'ok');
      } catch (err) { setStatus(firstLine(err.message), 'err'); }
    };
  }

  /* One shape for every job that does not write to Canvas. */
  function job(cid, title, path, body, after) {
    runJob(title, () => api(base(cid) + path, { body: body || {} }), res => {
      if (res && res.cancelled) setStatus('stopped; nothing was sent to Canvas', 'ok');
      else setStatus(title.toLowerCase() + ': done', 'ok');
      if (res && res.state) { S.pdf.state = res.state; paint(cid, res.state, S.pdf.sub); }
      if (has('announce')) announce(title + ' finished.');
      if (after) after(res);
    });
  }

  /* ------------------------------------------------------- the file table */
  function renderFiles(cid, st, host) {
    const rows = (st.files || []);
    const filter = PASSES[S.pdf.filter] ? S.pdf.filter : 'all';
    const shown = rows.filter(PASSES[filter]);
    if (!rows.length) {
      host.innerHTML = '<div id="pdfEmpty"></div><div id="pdfQueue"></div>';
      const empty = $('#pdfEmpty');
      const text = st.listed_at
        ? 'No PDFs in this course. Nothing has been pushed.'
        : 'Refresh file list reads this course’s files from Canvas. Back up and fix '
          + 'then downloads a copy of every PDF and repairs the copies on this computer. '
          + 'Nothing is pushed.';
      if (has('emptyState')) {
        const node = emptyState(text, st.listed_at ? null : 'Refresh file list',
          () => job(cid, 'Refresh file list', '/list', {}));
        empty.replaceChildren(node);
      } else empty.innerHTML = '<div class="empty"><p>' + esc(text) + '</p></div>';
      renderQueue(cid, st, $('#pdfQueue'));
      return;
    }
    host.innerHTML = ''
      + '<div class="pdfChips" role="group" aria-label="Show">' + FILTERS.map(f =>
        '<button type="button" class="chip" data-filter="' + f.id + '" aria-pressed="'
        + (f.id === filter) + '">' + esc(f.label) + ' <span class="muted">'
        + rows.filter(PASSES[f.id]).length + '</span></button>').join('') + '</div>'
      + '<div class="pdfTableWrap"><table class="pdfTable">'
      + '<caption>' + esc(n1(shown.length, 'file') + ' shown of ' + rows.length)
      + '. Nothing is uploaded until you press Upload.</caption>'
      + '<thead><tr>'
      + ['Name', 'Pages', 'Lane', 'State', 'Verify', 'Compliance', 'Alt']
        .map(h => '<th scope="col">' + h + '</th>').join('')
      + '</tr></thead><tbody>'
      + (shown.length ? shown.map(r => fileRow(st, r)).join('')
        : '<tr><td colspan="7" class="muted">Nothing matches that filter.</td></tr>')
      + '</tbody></table></div>'
      + '<div id="pdfQueue"></div>';
    host.querySelectorAll('[data-filter]').forEach(b => {
      b.onclick = () => { S.pdf.filter = b.dataset.filter; renderFiles(cid, st, host); };
    });
    renderQueue(cid, st, $('#pdfQueue'));
  }

  function fileRow(st, r) {
    const v = r.verify;
    const verify = !v ? pillOf('not checked', 'v-unknown')
      : pillOf('text', v.text ? 'v-pass' : 'v-fail')
        + pillOf('render', v.render ? 'v-pass' : 'v-fail')
        + pillOf('tree', v.tree ? 'v-pass' : 'v-fail');
    const comp = r.compliance || {};
    let compliance;
    if (comp.verdict === 'pass') compliance = pillOf('pass', 'v-pass');
    else if (comp.verdict === 'fail') compliance = pillOf(n1((comp.rules || []).length, 'rule'), 'v-fail');
    else compliance = pillOf(st.can_prove === false ? 'needs veraPDF' : 'not checked', 'v-unknown');
    const a = r.alt || {};
    const alt = [
      a.described ? pillOf(a.described + ' described', 'v-pass') : '',
      a.waiting ? pillOf(a.waiting + ' placeholder' + (a.waiting === 1 ? '' : 's'), 'v-warn') : '',
      a.no_picture ? pillOf(a.no_picture + ' no picture', 'v-fail') : '',
    ].join('') || '<span class="muted">no figures</span>';
    return '<tr>'
      + '<th scope="row"><span class="pdfName">' + esc(r.name) + '</span>'
      + (r.folder ? '<span class="muted pdfFolder">' + esc(r.folder) + '</span>' : '')
      + (r.reason ? '<span class="muted pdfWhy">' + esc(r.reason) + '</span>' : '')
      + (r.prealt ? '<span class="muted pdfWhy">mid-description: the re-check has not '
        + 'finished, so this one cannot be uploaded yet</span>' : '')
      + '</th>'
      + '<td class="num">' + (r.pages == null ? '<span class="muted">?</span>' : r.pages) + '</td>'
      + '<td>' + (r.lane ? laneOf(r.lane) : '<span class="muted">not fixed</span>') + '</td>'
      + '<td>' + stateOf(r.state) + (r.fixed_size ? ' <span class="muted">' + esc(bytes(r.fixed_size)) + '</span>' : '') + '</td>'
      + '<td>' + verify + '</td>'
      + '<td>' + compliance + '</td>'
      + '<td>' + alt + '</td>'
      + '</tr>';
  }

  /* -------------------------------------------------------- the queue panel */
  function renderQueue(cid, st, host) {
    if (!host) return;
    const groups = st.queue || [];
    if (!groups.length) { host.innerHTML = ''; return; }
    const total = groups.reduce((n, g) => n + g.count, 0);
    host.innerHTML = '<section class="pdfQueue"><h3 class="gwH">Needs a person '
      + '<span class="muted">(' + n1(total, 'file') + ')</span></h3>'
      + '<p class="muted">Nothing here was broken: these files were left exactly as they '
      + 'were, and they are not uploaded.</p>'
      + groups.map((g, i) => ''
        + '<div class="pdfQGroup">'
        + '<div class="pdfQHead">' + pillOf(g.severity === 'error' ? 'not fixed' : 'fixed, needs review',
          g.severity === 'error' ? 'v-fail' : 'v-warn')
        + '<b>' + esc(g.reason) + '</b><span class="muted">' + n1(g.count, 'file') + '</span></div>'
        + (g.hint ? '<p class="muted pdfQHint">' + esc(g.hint) + '</p>' : '')
        + '<ul class="applyList">' + g.items.map(it => '<li class="planned'
          + (it.handled ? ' done' : '') + '"><b>' + esc(it.file || it.dir) + '</b> '
          + (it.class_before ? '<span class="muted">' + esc(it.class_before) + '</span> ' : '')
          + '<span class="spacer"></span>'
          + '<button class="btn sm" type="button" data-open="' + esc(it.folder) + '">Open folder</button>'
          + '<button class="btn sm" type="button" data-handled="' + esc(it.dir) + '" '
          + 'data-on="' + (it.handled ? '0' : '1') + '">'
          + (it.handled ? 'Mark not handled' : 'Mark handled') + '</button></li>').join('')
        + '</ul></div>').join('')
      + '</section>';
    host.querySelectorAll('[data-handled]').forEach(b => {
      b.onclick = async () => {
        try {
          const fresh = await api(base(cid) + '/queue',
            { body: { dirs: [b.dataset.handled], handled: b.dataset.on === '1' } });
          S.pdf.state = fresh;
          paint(cid, fresh, S.pdf.sub);
        } catch (err) { setStatus(firstLine(err.message), 'err'); }
      };
    });
    host.querySelectorAll('[data-open]').forEach(b => {
      b.onclick = () => {
        modal('<h3>Where this file is kept</h3><p class="hint">A copy of the original, and '
          + 'everything the repair wrote, is in this folder on your computer.</p>'
          + '<code class="needHow">' + esc(b.dataset.open) + '</code>'
          + '<div class="foot"><span class="spacer"></span>'
          + '<button class="btn" id="pdfPathClose">Close</button></div>');
        const c = $('#pdfPathClose');
        if (c) c.onclick = shut;
      };
    });
  }

  /* ------------------------------------------------------------ alt text */
  function renderAlt(cid, st, host) {
    const items = (st.alt_items || []).map(it => ({
      key: it.key, picture: it.picture, kind: it.kind, context: it.context,
      alt: it.alt, source: it.source, decorative: !!it.decorative,
      page: it.page, file: it.file,
    }));
    const sum = st.alt_summary || {};
    const noPic = (sum.no_picture_files || []).map((line, i) => ({
      key: 'nopic-' + i, noPicture: true, kind: 'figure', context: String(line),
      alt: '', source: 'placeholder', file: String(line).split(' figure #')[0],
    }));
    const alone = (sum.useless_existing_alt_files || []).map((line, i) => ({
      key: 'alone-' + i, leftAlone: true, kind: 'figure', context: String(line),
      alt: '', source: 'filename', file: String(line).split(' figure #')[0],
    }));
    host.innerHTML = '<div class="pdfAltBar"><span class="hint" id="pdfAltHint"></span>'
      + '<span class="spacer"></span>'
      + '<button class="btn primary" id="pdfAltSave" disabled '
      + 'title="Nothing has been edited yet">Save descriptions</button></div>'
      + '<div id="pdfAltGrid"></div>';
    const hint = $('#pdfAltHint');
    hint.textContent = items.length
      ? n1(items.length, 'picture') + ' waiting. A description is at most 110 characters; '
        + 'leave it empty and tick Decorative for a picture that carries no meaning.'
      : whyNoDescribing(st, 'The PDF engine is not installed, so nothing here can be read.')
        + ' Nothing is uploaded from here.';
    S.pdf.dirty = {};
    const save = $('#pdfAltSave');
    const mark = () => {
      const n = Object.keys(S.pdf.dirty).length;
      save.disabled = !n;
      save.textContent = n ? 'Save ' + n1(n, 'description') : 'Save descriptions';
      save.title = n ? 'Write these into the fixed PDFs and re-check them'
        : 'Nothing has been edited yet';
    };
    if (!has('AltGrid')) {
      $('#pdfAltGrid').innerHTML = '<div class="callout">The shared alt-text grid is not '
        + 'loaded, so the pictures cannot be shown here.</div>';
      return;
    }
    AltGrid($('#pdfAltGrid'), items, {
      noPicture: noPic, leftAlone: alone,
      title: st.course_label,
      onChange: (it) => {
        if (it.noPicture || it.leftAlone) return;
        S.pdf.dirty[it.key] = it.decorative ? '' : String(it.alt || '');
        mark();
      },
      onDescribeAll: () => job(cid, 'Describe images with Claude', '/describe', {}),
    });
    mark();
    save.onclick = () => {
      const alt = S.pdf.dirty;
      if (!Object.keys(alt).length) return;
      runJob('Save descriptions', () => api(base(cid) + '/alt', { body: { alt } }), res => {
        const bad = (res && res.reverify_failed) || [];
        setStatus(bad.length
          ? bad.length + ' file(s) failed the re-check and were put back as they were'
          : 'descriptions written into the fixed PDFs', bad.length ? 'err' : 'ok');
        if (res && res.state) { S.pdf.state = res.state; paint(cid, res.state, 'alt'); }
      });
    };
  }

  /* --------------------------------------------------- prove compliance */
  function renderProve(cid, st, host) {
    const c = st.census || {};
    const sum = st.alt_summary || {};
    // A census already on this computer is worth showing even when the tool
    // that made it is no longer here: it is the last honest measurement.
    if (st.can_prove === false && !c.files) {
      host.innerHTML = '<div class="callout">Proving compliance runs veraPDF, which needs '
        + 'Java. Neither is installed here, so this pane cannot fill in. The files are '
        + 'still fixed and verified; only the PDF/UA-1 scoreboard is missing.</div>'
        + '<div id="pdfProveNeeds"></div>';
      if (has('needCards')) needCards(['verapdf', 'java'], $('#pdfProveNeeds'));
      return;
    }
    if (!c.files) {
      host.innerHTML = '';
      const text = 'Prove compliance runs veraPDF over every fixed PDF and reports, rule by '
        + 'rule, what still fails. It reads files only; nothing is uploaded.';
      if (has('emptyState')) {
        host.replaceChildren(emptyState(text, 'Prove compliance',
          () => job(cid, 'Prove compliance', '/prove', { profile: 'ua1' })));
      } else host.innerHTML = '<div class="empty"><p>' + esc(text) + '</p></div>';
      return;
    }
    const placeholders = (sum.needs_alt || 0) + (sum.no_picture_available || 0);
    host.innerHTML = ''
      + (st.can_prove === false ? '<div class="callout">This is the last census made on a '
        + 'computer that had veraPDF. It cannot be run again here until veraPDF and Java '
        + 'are installed.</div>' : '')
      + '<p class="pdfProveLine"><b>' + c.compliant + ' of ' + c.files + '</b> fixed PDF'
      + (c.files === 1 ? '' : 's') + ' pass ' + esc(c.profile || 'PDF/UA-1') + '. '
      + (c.noncompliant ? n1(c.noncompliant, 'file') + ' still fail' + (c.noncompliant === 1 ? 's' : '') + '.' : '')
      + '</p>'
      + (placeholders ? '<div class="callout pdfHonest"><b>A green report is not the same as '
        + 'an accessible file.</b> ' + n1(sum.needs_alt || 0, 'figure') + ' still carry a safe '
        + 'placeholder description'
        + ((sum.no_picture_available || 0) ? ', and ' + n1(sum.no_picture_available, 'figure')
          + ' cannot be described automatically because no picture of '
          + (sum.no_picture_available === 1 ? 'it' : 'them') + ' could be produced'
          : '')
        + ((sum.useless_existing_alt || 0) ? '. Another ' + n1(sum.useless_existing_alt, 'figure')
          + ' already ' + (sum.useless_existing_alt === 1 ? 'has' : 'have')
          + ' alt text that is only a file name, left exactly as the author wrote it'
          : '')
        + '. Those pass a compliance scan while telling a screen reader nothing. '
        + '<a href="' + hashFor(cid, 'alt') + '">Write the real descriptions</a>.</div>' : '')
      + '<div class="pdfTableWrap"><table class="pdfTable pdfCensus">'
      + '<caption>Files per rule, not occurrences: one file with a broken table can produce '
      + 'hundreds of hits of the same rule.</caption>'
      + '<thead><tr>' + ['Rule', 'Files', 'Lane', 'Kind', 'For example']
        .map(h => '<th scope="col">' + h + '</th>').join('') + '</tr></thead><tbody>'
      + ((c.rules || []).length ? c.rules.map(r => '<tr>'
        + '<th scope="row">' + esc(r.rule) + '</th>'
        + '<td class="num">' + r.files + '</td>'
        + '<td>' + Object.keys(r.lanes || {}).sort().map(k => laneOf(k)
          + '<span class="muted"> ' + r.lanes[k] + '</span>').join(' ') + '</td>'
        + '<td>' + pillOf(r.kind, r.kind === 'mechanical' ? 'v-warn' : 'v-unknown') + '</td>'
        + '<td class="muted">' + esc((r.examples || []).join(', ')) + '</td>'
        + '</tr>').join('')
        : '<tr><td colspan="5">Every fixed PDF passes. Nothing is left to fix here.</td></tr>')
      + '</tbody></table></div>'
      + '<p class="hint">Mechanical failures are the program’s to fix. Semantic ones need '
      + 'a person to decide what the content means. A source re-export means the original '
      + 'file has to be produced again, usually with its fonts embedded.</p>'
      + '<div class="pdfVerbs"><button class="btn" id="pdfProveAgain"'
      + (st.can_prove === false ? ' disabled title="Install veraPDF and Java to run this again."' : '')
      + '>Run it again</button></div>';
    const again = $('#pdfProveAgain');
    if (again && !again.disabled) {
      again.onclick = () => job(cid, 'Prove compliance', '/prove', { profile: 'ua1' });
    }
  }

  /* ---------------------------------------------------------- the upload */
  function uploadFlow(cid) {
    runJob('Upload: dry run', () => api(base(cid) + '/push', { body: { apply: false } }),
      plan => showPlan(cid, plan));
  }

  function showPlan(cid, p) {
    p = p || {};
    const rows = p.rows || [];
    const held = p.held || [];
    const blocked = (p.pending_alt || []).length;
    const host = modal(''
      + '<h3>Upload the fixed PDFs?</h3>'
      + '<p class="hint">' + esc(p.sentence || '') + ' Nothing has been sent yet.</p>'
      + (blocked ? '<div class="callout bad">' + n1(blocked, 'file') + ' are mid-description '
        + 'and have not been re-checked, so the upload is refused. Run Describe images again '
        + '(it finishes the check), then upload.</div>' : '')
      + (p.placeholders ? '<div class="callout">' + n1(p.placeholders, 'figure') + ' still '
        + (p.placeholders === 1 ? 'carries' : 'carry') + ' a placeholder description that no '
        + 'automated step can improve. Uploading now publishes '
        + (p.placeholders === 1 ? 'it' : 'them') + ' as ' + (p.placeholders === 1 ? 'it is' : 'they are')
        + '.</div>' : '')
      + '<ul class="applyList">' + rows.map(r => '<li class="planned"><b>' + esc(r.name) + '</b> '
        + '<span class="muted">' + esc(r.folder || '') + '</span><span class="spacer"></span>'
        + esc(fmtSize(r.from)) + ' &rarr; ' + esc(fmtSize(r.to))
        + (r.already_pushed ? ' <span class="pill">already uploaded</span>' : '')
        + '</li>').join('') + '</ul>'
      + (held.length ? '<h4>Will not be uploaded</h4><ul class="applyList">'
        + held.map(r => '<li class="bad"><b>' + esc(r.name) + '</b> <span class="muted">'
          + esc(r.why || r.reason || 'no verified fixed copy') + '</span></li>').join('')
        + '</ul>' : '')
      + '<div class="foot"><span class="spacer"></span>'
      + '<button class="btn" id="pdfPlanClose">Cancel</button>'
      + (rows.length && !blocked ? '<button class="btn danger" id="pdfPlanGo">Upload '
        + n1(rows.length, 'file') + '</button>' : '')
      + '</div>');
    const close = host.querySelector('#pdfPlanClose');
    if (close) close.onclick = shut;
    const go = host.querySelector('#pdfPlanGo');
    if (go) go.onclick = () => {
      shut();
      runJobConfirmed('Upload the fixed PDFs',
        token => api(base(cid) + '/push', { body: { apply: true, confirm: token } }),
        res => {
          const up = (res && res.uploaded) || [];
          const bad = (res && res.failed) || [];
          setStatus('uploaded ' + up.length + (bad.length ? ', ' + bad.length + ' failed' : ''),
            bad.length ? 'err' : 'ok');
          if (res && res.state) { S.pdf.state = res.state; paint(cid, res.state, S.pdf.sub); }
        },
        { title: 'Upload these PDFs over the originals in Canvas?', verb: 'Yes, upload them' });
    };
  }

  /* -------------------------------------------------------- the roll back */
  function rollbackFlow(cid) {
    runJob('Roll back: dry run', () => api(base(cid) + '/rollback', { body: { apply: false } }),
      p => showRollback(cid, p || {}));
  }

  function showRollback(cid, p) {
    const rows = p.rows || [];
    const suspect = p.suspect || [];
    const host = modal(''
      + '<h3>Put the original PDFs back?</h3>'
      + '<p class="hint">' + esc(p.sentence || '') + ' Nothing has been sent yet.</p>'
      + (suspect.length ? '<div class="callout bad">' + n1(suspect.length, 'backed-up original')
        + ' no longer match the size Canvas recorded and will not be restored: '
        + esc(suspect.map(s => s.name).join(', '))
        + '. Run Back up only to fetch clean copies of those first.</div>' : '')
      + '<ul class="applyList">' + rows.map(r => '<li class="planned"><b>' + esc(r.name)
        + '</b> <span class="muted">' + esc(r.folder || '') + '</span>'
        + '<span class="spacer"></span>' + esc(fmtSize(r.size)) + '</li>').join('') + '</ul>'
      + '<div class="foot"><span class="spacer"></span>'
      + '<button class="btn" id="pdfRollClose">Cancel</button>'
      + (rows.length ? '<button class="btn danger" id="pdfRollGo">Restore '
        + n1(rows.length, 'file') + '</button>' : '')
      + '</div>');
    const close = host.querySelector('#pdfRollClose');
    if (close) close.onclick = shut;
    const go = host.querySelector('#pdfRollGo');
    if (go) go.onclick = () => {
      shut();
      runJobConfirmed('Put the original PDFs back',
        token => api(base(cid) + '/rollback', { body: { apply: true, confirm: token } }),
        res => {
          const back = (res && res.restored) || [];
          setStatus('restored ' + back.length, 'ok');
          if (res && res.state) { S.pdf.state = res.state; paint(cid, res.state, S.pdf.sub); }
        },
        { title: 'Put the originals back over Canvas?', verb: 'Yes, restore them' });
    };
  }

  /* ------------------------------------------------------------ register */
  Studio.a11yKinds.pdf = openPdf;
  window.openPdf = openPdf;
  if (has('registerArea')) {
    registerArea({
      id: 'pdf', label: 'PDFs', zone: 'a11y', open: openPdf,
      badge: hub => (hub && hub.pdf && hub.pdf.badge != null) ? hub.pdf.badge : null,
    });
  }
})();
