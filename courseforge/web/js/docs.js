/* docs.js: the Documents part of Accessibility -- PowerPoint, Word, PDF text,
   Office text and the read-only PDF triage.

   a11y.js owns the area chrome (the kind tabs) and calls in here through
   Studio.a11yKinds[kind]; the same openers are registered as their own area so
   #/c/<cid>/docs/<kind> works on its own. Every kind is the shared five-step
   gateway: List, Scan, Review, Dry run, Apply. Nothing reaches Canvas before
   step 5, and step 5 shows the server's own sentence before it sends.

   The backend (courseforge/docs/routes.py) predates the gateway component and
   speaks a slightly different dialect; docsFitGateway() below is the one place
   that translates, and it says exactly what differs. */
(function () {
  'use strict';

  window.Studio = window.Studio || {};
  const DocsStudio = window.Studio;
  DocsStudio.a11yKinds = DocsStudio.a11yKinds || {};
  S.docs = S.docs || {};

  /* Two jobs share this screen and they are not variations on each other.
     Some tabs make a file usable by a screen reader; two change the words
     inside a file and have nothing to do with accessibility. Left ungrouped,
     "Word" and "Office text" sat next to each other looking like the same
     idea twice -- which is how it read to the person who built it. So each
     kind names its job, and the first line of every screen says which job it
     is doing and which it is not. */
  const FIX = 'Fix accessibility';
  const FIND = 'Find and replace';

  const DOCS_KINDS = [
    {
      id: 'pptx', label: 'PowerPoint', one: 'deck', many: 'PowerPoint files', alt: true,
      group: FIX,
      title: 'Alt text, slide titles and table header rows inside .pptx files',
      blurb: 'Makes the PowerPoint files in this course readable by a screen reader: '
        + 'alt text, slide titles, table header rows. It never changes the wording. '
        + 'To do that, use Find and replace.',
      empty: 'Refresh the list reads the file names from Canvas. Fetch & scan downloads a '
        + 'copy of each deck and reads it for missing alt text, untitled slides and tables '
        + 'without a header row. Nothing is uploaded.',
    },
    {
      id: 'docx', label: 'Word', one: 'document', many: 'Word documents', alt: true,
      group: FIX,
      title: 'Alt text, table header rows and heading structure inside .docx files',
      blurb: 'Makes the Word files in this course readable by a screen reader: alt text, '
        + 'table header rows, real headings. It never changes the wording. To do that, '
        + 'use Find and replace.',
      empty: 'Refresh the list reads the file names from Canvas. Fetch & scan downloads a '
        + 'copy of each document and reads it for missing alt text, tables without a header '
        + 'row and paragraphs that look like headings. Nothing is uploaded.',
    },
    {
      id: 'pdf-text', label: 'in PDFs', one: 'PDF', many: 'PDF files', map: true,
      group: FIND,
      title: 'Change the words inside PDFs. Nothing to do with accessibility.',
      blurb: 'Changes the words inside PDFs: a retired name, an old course code, a dead '
        + 'link. This is not an accessibility fix. For tagging, OCR and alt text, use '
        + 'PDFs under Fix accessibility.',
      empty: 'Type what to look for, then Fetch & scan reads every page of every PDF for it. '
        + 'Nothing is uploaded, and nothing is replaced until you write the replacement yourself.',
    },
    {
      id: 'office-text', label: 'in Office files', one: 'file', many: 'Office files', map: true,
      group: FIND,
      title: 'The same find and replace inside .docx, .pptx and .xlsx. '
        + 'Nothing to do with accessibility.',
      blurb: 'The same find and replace, inside .docx, .pptx and .xlsx. This is not an '
        + 'accessibility fix. For alt text and headings, use Word or PowerPoint under '
        + 'Fix accessibility.',
      empty: 'Type what to look for, then Fetch & scan reads the text of every Office file '
        + 'for it. Nothing is uploaded, and nothing is replaced until you write the '
        + 'replacement yourself.',
    },
    {
      id: 'triage', label: 'PDF triage', one: 'PDF', many: 'PDF files', report: true,
      group: FIX,
      title: 'Which PDFs are worst, before fixing any of them. Reads only.',
      blurb: 'Which PDFs are hurting the Ally score, worst first. Detection only: it never '
        + 'changes a file.',
      empty: 'Fetch & scan downloads a copy of each PDF and classifies it: scanned image, '
        + 'untagged text, or tagged. It never changes a file and never uploads one.',
    },
  ];
  const docsKindOf = id => DOCS_KINDS.find(k => k.id === id) || null;
  const docsBase = (cid, kindId) => '/a11y/' + encodeURIComponent(cid) + '/' + kindId;
  const docsMem = kindId => (S.docs[kindId] = S.docs[kindId] || {});
  const docsClamp = (text, n) => {
    const s = String(text == null ? '' : text);
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  };

  /* ------------------------------------------------------------- the area */
  /* a11y.js already paints the head and the kind tabs, so arriving through it
     only needs the body. Arriving straight on #/c/<cid>/docs/<kind> borrows
     a11y.js's chrome when it is loaded, and paints a plain head when it is
     not, so the area is never a blank screen. */
  async function openDocs(courseId, rest) {
    rest = Array.isArray(rest) ? rest : [];
    const kindId = docsKindOf(rest[0]) ? rest[0] : 'pptx';
    if (typeof openA11y === 'function') {
      return openA11y(courseId, [kindId].concat(rest.slice(1)));
    }
    showView('area');
    await courseCrumbs(courseId, [{ label: 'Documents' }]);
    $('#headerActions').innerHTML = '';
    areaHead('Documents', 'Fetch, scan, fix, verify, upload. The originals are kept here.');
    areaTabs(DOCS_KINDS.map(k => ({ id: k.id, label: k.label, group: k.group, title: k.title })), kindId,
      id => { location.hash = '#/c/' + courseId + '/docs/' + id; });
    return docsOpenKind(courseId, kindId, rest.slice(1));
  }

  /* ------------------------------------------------------------ one kind */
  function docsOpenKind(courseId, kindId, rest) {
    const kind = docsKindOf(kindId);
    const host = $('#areaBody');
    if (!host) return;
    if (!kind) { host.innerHTML = ''; host.appendChild(emptyState('No such document kind.')); return; }
    const mem = docsMem(kindId);
    mem.courseId = String(courseId);
    mem.rest = rest || [];
    mem.stepped = false;
    mem.plan = null;             // a dry run belongs to the visit that ran it

    host.innerHTML = `<p class="docsBlurb">${esc(kind.blurb)}</p>
      <div class="docsVerbs" id="docsVerbs"></div>
      <div id="docsGw"></div>`;
    docsEnsureTab(courseId, kindId);
    docsRenderVerbs(courseId, kind);

    if (typeof renderGateway !== 'function') {
      $('#docsGw').innerHTML = '<div class="callout bad">The shared gateway component '
        + '(components.js) did not load, so the steps cannot be shown. Nothing has been '
        + 'changed.</div>';
      return;
    }
    const gw = renderGateway($('#docsGw'), {
      kind: kindId,
      courseId: String(courseId),
      label: kind.many,
      pollMs: 4000,
      endpoints: {
        state: docsBase(courseId, kindId) + '/state',
        list: docsBase(courseId, kindId) + '/list',
        fetch: docsBase(courseId, kindId) + '/fetch',
        describe: kind.alt ? docsBase(courseId, kindId) + '/describe' : null,
        // '/fixes' on this backend saves one person's edits for one file; it is
        // not the component's "compute the fixes" call. docsFitGateway rewires
        // the button instead of pointing at it.
        fixes: null,
        push: docsBase(courseId, kindId) + '/push',
      },
      listColumns: docsColumns(kind),
      reviewRenderer: (rhost, state) => docsReview(rhost, state, courseId, kind),
      confirmTitle: kind.report ? 'Nothing is ever uploaded from here'
        : 'Upload the fixed ' + kind.many + ' over the originals?',
      describeLabel: 'Describe images with Claude',
    });
    mem.gw = gw;
    docsFitGateway($('#docsGw'), courseId, kind, gw);
  }

  /* a11y.js paints six kind tabs and PDF triage is not one of them, so a
     person who arrives there from the hub would see no tab held down at all.
     Take the tabs that are already up, add the one that is missing, and keep
     every other tab pointing where it pointed before. */
  function docsEnsureTab(courseId, kindId) {
    const host = $('#areaTabs');
    if (!host) return;
    const tabs = [...host.querySelectorAll('.subTab')].map(b => ({
      id: b.dataset.tab, label: b.textContent.trim(),
    })).filter(t => t.id);
    if (!tabs.length) return;                    // nobody painted tabs: nothing to line up with
    if (host.querySelector('.subTab[aria-selected="true"][data-tab="' + CSS.escape(kindId) + '"]')) return;
    if (!tabs.some(t => t.id === kindId)) {
      tabs.push({ id: kindId, label: (docsKindOf(kindId) || {}).label || kindId });
    }
    areaTabs(tabs, kindId, id => {
      const owner = (id === 'triage' || typeof openA11y !== 'function') ? 'docs' : 'a11y';
      location.hash = '#/c/' + courseId + '/' + owner + '/' + id;
    });
  }

  /* The list table. The component's default columns read `kind`/`type`, which
     these rows do not carry; these read what the rows actually have. */
  function docsColumns(kind) {
    const cols = [
      { label: 'Name', key: 'name', render: it => `<b>${esc(it.name || it.id)}</b>`
        + (it.folder ? `<span class="muted"> · ${esc(it.folder)}</span>` : '') },
      { label: 'Type', key: 'ext', render: it => esc((it.ext || '').replace(/^\./, '') || '—') },
      { label: 'Size', key: 'size', render: it => `<span class="num">${esc(fmtBytes(it.size))}</span>` },
      { label: 'Modified', key: 'modified', render: it => `<span class="muted">${esc(fmtDate(it.modified) || '')}</span>` },
    ];
    if (kind.alt) {
      cols.push({ label: 'Alt text', key: 'alt_todo', render: it => (it.alt_todo
        ? pill(it.alt_todo + ' still to describe', 'warn')
        : (it.alt_done ? pill(it.alt_done + ' described', 'ok')
          : (it.images ? pill('all have alt text', 'ok') : '<span class="muted">no pictures</span>'))) });
    } else if (kind.map) {
      cols.push({ label: 'Matches', key: 'hits', render: it => (it.hits
        ? esc(it.hits + ' on ' + (it.pages || '?') + ' pages')
        : '<span class="muted">none</span>') });
    } else {
      cols.push({ label: 'Class', key: 'cls', render: it => (it.cls
        ? pill(it.cls, 'tri-' + esc(it.cls).replace(/[^a-z]+/g, '-'))
        : '<span class="muted">not scanned</span>') });
    }
    cols.push({ label: 'State', key: 'state', render: it => statePill(it.state) });
    return cols;
  }

  /* ------------------------------------------------------------- the verbs */
  function docsRenderVerbs(courseId, kind) {
    const host = $('#docsVerbs');
    if (!host) return;
    const mem = docsMem(kind.id);
    let html = '';
    if (kind.map) {
      html += `<label class="docsPat"><span>Look for</span>
        <input type="search" id="docsPattern" value="${esc(mem.pattern || '')}"
          placeholder="a course code, a name, a regular expression"
          aria-describedby="docsPatHint"></label>
        <button class="btn" id="docsPatGo" type="button">Scan with this</button>
        <span class="hint" id="docsPatHint">Read-only: it searches the copies on this computer.</span>`;
    }
    if (!kind.report) {
      html += `<span class="spacer"></span>
        <button class="btn danger" id="docsRestore" type="button"
        title="Upload the kept originals back over the fixed files. You are asked first.">Put the originals back</button>`;
    }
    host.innerHTML = html;                       // report-only kinds have no verbs at all

    const go = $('#docsPatGo');
    if (go) {
      const input = $('#docsPattern');
      const run = () => {
        const pattern = (input.value || '').trim();
        if (!pattern) { setStatus('type what to look for first', 'err'); input.focus(); return; }
        mem.pattern = pattern;
        runJob('Scanning for “' + docsClamp(pattern, 40) + '”',
          () => api(docsBase(courseId, kind.id) + '/pattern', { body: { pattern } }),
          () => { setStatus('scanned; nothing was changed', 'ok'); docsRefresh(courseId, kind.id); },
          { autoClose: true });
      };
      go.onclick = run;
      input.onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); run(); } };
    }
    const restore = $('#docsRestore');
    if (restore) {
      restore.onclick = () => runJobConfirmed('Putting the original ' + kind.many + ' back',
        token => api(docsBase(courseId, kind.id) + '/restore', { body: { confirm: token } }),
        res => {
          const n = ((res && res.restored) || []).length;
          setStatus(n ? n + ' original file(s) put back' : 'there was nothing to put back', n ? 'ok' : 'err');
          docsRefresh(courseId, kind.id);
        },
        { title: 'Put the originals back in Canvas?', verb: 'Yes, put them back' });
    }
  }

  /* Never location.reload(), and never a second gateway on the same screen:
     the one that is up re-reads the state. */
  function docsRefresh(courseId, kindId) {
    const mem = docsMem(kindId);
    if (mem.gw && typeof mem.gw.refresh === 'function') mem.gw.refresh();
  }

  /* ----------------------------------------------------- the review panes */
  function docsReview(host, state, courseId, kind) {
    if (kind.report) return docsTriagePane(host, state, courseId, kind);
    const rows = (state && state.items) || [];
    const ready = rows.filter(r => r.scanned);
    if (!ready.length) {
      host.replaceChildren(emptyState('No ' + kind.many + ' have been scanned yet. Fetch & scan '
        + 'downloads a copy of each one and reads it; nothing is uploaded.'));
      return;
    }
    const mem = docsMem(kind.id);
    if (!ready.some(r => String(r.id) === String(mem.file))) mem.file = String(ready[0].id);
    host.innerHTML = `<div class="docsPick">
        <label for="docsFile">File</label>
        <select id="docsFile">${ready.map(r => `<option value="${esc(r.id)}"
          ${String(r.id) === String(mem.file) ? 'selected' : ''}>${esc(r.name)}${
          r.folder ? ' — ' + esc(r.folder) : ''}</option>`).join('')}</select>
        <span class="hint">${esc(ready.length)} scanned of ${esc(rows.length)}. Edits save as you
          make them, on this computer only.</span>
      </div>
      <div id="docsOne" class="docsOne"><p class="muted">Reading the scan…</p></div>`;
    $('#docsFile').onchange = ev => {
      mem.file = ev.target.value;
      docsLoadFile(courseId, kind);
    };
    docsLoadFile(courseId, kind);
  }

  async function docsLoadFile(courseId, kind) {
    const mem = docsMem(kind.id);
    const fid = mem.file;
    const host = $('#docsOne');
    if (!host || !fid) return;
    let report;
    try {
      report = await api(docsBase(courseId, kind.id) + '/file/' + encodeURIComponent(fid) + '/report');
    } catch (err) {
      host.innerHTML = `<div class="callout bad">Could not read the scan for that file: ${esc(firstLine(err.message))}</div>`;
      return;
    }
    if (String(mem.file) !== String(fid) || !host.isConnected) return;   // moved on while it loaded
    mem.report = report;
    if (kind.alt) docsAltPane(host, courseId, kind, fid, report);
    else docsMapPane(host, courseId, kind, fid, report);
  }

  /* --------------------------------------------- pptx and docx: the fixes */
  const DOCS_SOURCE = { human: 'you', model: 'claude', claude: 'claude' };

  function docsAltPane(host, courseId, kind, fid, report) {
    const fixes = report.fixes || {};
    const sources = fixes.sources || {};
    /* One object for what this file's fixes now are. Every control writes into
       it and the save sends it whole, because the server replaces each map
       with what it is given rather than merging key by key. */
    const edit = {
      alts: { ...(fixes.alts || {}) },
      titles: { ...(fixes.titles || {}) },
      headings: { ...(fixes.headings || {}) },
      table_headers: fixes.table_headers !== false,
    };
    const alts = edit.alts;

    const save = docsDebounce(() => {
      api(docsBase(courseId, kind.id) + '/fixes', { body: { file_id: fid, fixes: edit } })
        .then(() => setStatus('saved here; nothing is uploaded', 'ok'))
        .catch(err => setStatus('could not save: ' + firstLine(err.message), 'err'));
    }, 450);

    const images = (report.images || []).map(im => ({
      key: im.key,
      picture: im.has_picture
        ? '/api' + docsBase(courseId, kind.id) + '/picture?file=' + encodeURIComponent(fid)
          + '&hash=' + encodeURIComponent(im.hash || '') + '&key=' + encodeURIComponent(im.key)
        : '',
      noPicture: !im.has_picture,
      kind: im.slide ? 'slide image' : 'image',
      page: im.slide || null,
      file: im.name || '',
      context: im.slide_context || '',
      alt: alts[im.key] != null ? alts[im.key] : (im.current_alt || ''),
      decorative: alts[im.key] === '' ? true : !!im.decorative,
      source: alts[im.key] != null ? (DOCS_SOURCE[sources[im.key]] || 'you')
        : (im.current_alt ? 'file' : 'none'),
    }));

    host.innerHTML = `<div id="docsAlt"></div><div id="docsOther"></div>`;
    AltGrid($('#docsAlt'), images, {
      title: report.meta && report.meta.display_name,
      onChange: (item, patch) => {
        if ('decorative' in patch) alts[item.key] = patch.decorative ? '' : (item.alt || '');
        else alts[item.key] = item.alt || '';
        save();
      },
    });
    docsOtherFixes($('#docsOther'), kind, report, edit, save);
  }

  /* The fixes that are not alt text: slide titles, header rows, and the
     appearance changes that are opt-in because they move things on the page. */
  function docsOtherFixes(host, kind, report, edit, save) {
    const untitled = report.report && report.report.untitled || [];
    const tables = ((report.report && report.report.tables) || []).filter(t => !t.header_row);
    const faux = (report.report && report.report.faux_heading_candidates) || [];
    if (!untitled.length && !tables.length && !faux.length) {
      host.innerHTML = '<p class="muted docsNone">Nothing else to fix in this file: every '
        + 'slide or section has a title and every table has a header row.</p>';
      return;
    }
    const rows = [];
    untitled.forEach(u => rows.push(`<tr>
      <th scope="row">Slide title</th>
      <td><span class="muted">slide ${esc(u.slide)}</span>
        ${u.existing_text ? `<div class="docsCtx">${esc(docsClamp(u.existing_text, 90))}</div>` : ''}</td>
      <td><label class="srOnly" for="docsT${esc(u.slide)}">Title for slide ${esc(u.slide)}</label>
        <input id="docsT${esc(u.slide)}" type="text" data-title="${esc(u.slide)}"
          value="${esc(edit.titles[String(u.slide)] || '')}"
          placeholder="What this slide is about, in a few words"></td></tr>`));
    faux.forEach(f => rows.push(`<tr>
      <th scope="row">Heading</th>
      <td><div class="docsCtx">${esc(docsClamp(f.text, 90))}</div>
        <span class="muted">paragraph ${esc(f.index)} — looks like a heading, is styled as body text</span></td>
      <td><label class="tick"><input type="checkbox" data-head="${esc(f.index)}"
        ${edit.headings[String(f.index)] ? 'checked' : ''}> Make it a real heading (H3)</label>
        <div class="muted">This changes how the page looks.</div></td></tr>`));
    if (tables.length) {
      rows.push(`<tr><th scope="row">Table header rows</th>
        <td>${tables.map(t => `<div class="docsCtx">${esc(t.slide ? 'slide ' + t.slide : 'table ' + ((t.index || 0) + 1))}
          — ${esc(t.rows)} rows × ${esc(t.cols)} columns</div>`).join('')}</td>
        <td><label class="tick"><input type="checkbox" id="docsTh" ${edit.table_headers ? 'checked' : ''}>
          Mark the first row as the header row</label>
          <div class="muted">Screen readers read it back with every cell.</div></td></tr>`);
    }
    host.innerHTML = `<h3 class="gwH">Other fixes in this file</h3>
      <div class="gwTableWrap"><table class="gwTable">
        <caption class="srOnly">Fixes other than alt text, for this file</caption>
        <thead><tr><th scope="col">What</th><th scope="col">Where</th><th scope="col">Fix</th></tr></thead>
        <tbody>${rows.join('')}</tbody></table></div>
      <p class="muted gwNote">Saved on this computer as you type. Nothing is uploaded until
        the last step, and that step asks first.</p>`;

    host.querySelectorAll('[data-title]').forEach(input => {
      input.oninput = () => { edit.titles[input.dataset.title] = input.value; save(); };
    });
    host.querySelectorAll('[data-head]').forEach(cb => {
      cb.onchange = () => {
        if (cb.checked) edit.headings[cb.dataset.head] = 3; else delete edit.headings[cb.dataset.head];
        save();
      };
    });
    const th = host.querySelector('#docsTh');
    if (th) th.onchange = () => { edit.table_headers = th.checked; save(); };
  }

  /* ------------------------------------------- pdf-text and office-text */
  function docsMapPane(host, courseId, kind, fid, report) {
    const scan = report.report || {};
    const fixes = report.fixes || {};
    const map = Array.isArray(fixes.map) ? fixes.map.slice() : [];
    const hazards = (report.row && report.row.hazards) || [];
    const hits = scan.hits || [];
    const byTerm = new Map();
    hits.forEach(h => {
      const term = h.term || '';
      const seen = byTerm.get(term) || { term, count: 0, pages: [], context: h.context || '' };
      seen.count += 1;
      if (seen.pages.length < 6 && seen.pages.indexOf(h.page) < 0) seen.pages.push(h.page);
      byTerm.set(term, seen);
    });
    const terms = [...byTerm.values()];
    const replacementFor = term => {
      const found = map.find(m => m.find === term);
      return found ? (found.replace || '') : '';
    };

    const save = docsDebounce(() => {
      api(docsBase(courseId, kind.id) + '/fixes', { body: { file_id: fid, fixes: { map } } })
        .then(() => setStatus('saved here; nothing is uploaded', 'ok'))
        .catch(err => setStatus('could not save: ' + firstLine(err.message), 'err'));
    }, 450);

    const legacy = scan.unsupported || (report.row && report.row.state === 'unsupported');
    host.innerHTML = `
      ${hazards.length || legacy ? `<div class="callout bad">
        <b>Read this before you replace anything in this file.</b>
        <ul class="docsHaz">${hazards.map(h => `<li>${esc(h)}</li>`).join('')}
        ${legacy ? '<li>This is a legacy .doc/.ppt/.xls file. It cannot be edited here: save it '
          + 'as .docx, .pptx or .xlsx in Office first, then scan again.</li>' : ''}</ul></div>` : ''}
      ${terms.length ? `<div class="gwTableWrap"><table class="gwTable">
        <caption class="srOnly">Every match in this file, and what to put in its place</caption>
        <thead><tr><th scope="col">Found</th><th scope="col">Where</th>
          <th scope="col">In context</th><th scope="col">Replace with</th></tr></thead>
        <tbody>${terms.map((t, i) => `<tr>
          <th scope="row"><code>${esc(t.term)}</code></th>
          <td class="muted">${esc(t.count)} time${t.count === 1 ? '' : 's'}${
            t.pages.length ? ' · p' + t.pages.map(p => esc(p)).join(', ') : ''}</td>
          <td class="docsCtx">${esc(docsClamp(t.context, 110))}</td>
          <td><label class="srOnly" for="docsR${i}">Replacement for ${esc(t.term)}</label>
            <input id="docsR${i}" type="text" data-find="${esc(t.term)}"
              value="${esc(replacementFor(t.term))}"
              placeholder="leave blank to leave it alone"></td></tr>`).join('')}
        </tbody></table></div>`
        : '<div class="empty"><p>No matches in this file for the current pattern. Change the '
          + 'pattern above and scan again; nothing is uploaded either way.</p></div>'}
      <p class="muted gwNote">A replacement is saved on this computer as you type. The file in
        Canvas is untouched until the last step, and that step asks first.</p>`;

    host.querySelectorAll('[data-find]').forEach(input => {
      input.oninput = () => {
        const find = input.dataset.find;
        const at = map.findIndex(m => m.find === find);
        const value = input.value;
        if (!value) { if (at >= 0) map.splice(at, 1); }
        else if (at >= 0) map[at] = { find, replace: value };
        else map.push({ find, replace: value });
        save();
      };
    });
  }

  /* --------------------------------------------------------- PDF triage */
  function docsTriagePane(host, state, courseId, kind) {
    const rows = (state && state.triage) || [];
    const mem = docsMem(kind.id);
    if (!rows.length) {
      host.replaceChildren(emptyState('No PDFs have been triaged yet. Fetch & scan downloads a '
        + 'copy of each PDF and classifies it. It never changes a file and never uploads one.'));
      return;
    }
    const help = mem.classes || {};
    const words = { 3: 'worst', 2: 'bad', 1: 'odd', 0: 'ok' };
    host.innerHTML = `<div class="callout">PDF triage only reports. Real PDF tagging is a job
      for Acrobat and a person; this says which files are worth that time, worst first.</div>
      <div class="gwTableWrap"><table class="gwTable">
        <caption class="srOnly">Every PDF in this course, ranked worst first</caption>
        <thead><tr><th scope="col">File</th><th scope="col">Pages</th>
          <th scope="col">Class</th><th scope="col">What it means</th></tr></thead>
        <tbody>${rows.map(r => `<tr>
          <th scope="row">${esc(r.name || r.id)}${r.folder ? `<span class="muted"> · ${esc(r.folder)}</span>` : ''}</th>
          <td class="num">${esc(r.pages == null ? '—' : r.pages)}</td>
          <td>${pill(r.cls || 'not scanned', 'tri-' + String(r.cls || 'none').replace(/[^a-z]+/g, '-'))}
            <span class="srOnly">severity ${esc(words[r.severity] || 'unknown')}</span></td>
          <td class="muted">${esc(help[r.cls] || r.note || '')}</td></tr>`).join('')}
        </tbody></table></div>`;
    if (!mem.classes) {
      api(docsBase(courseId, kind.id) + '/report').then(res => {
        mem.classes = (res && res.classes) || {};
        if (host.isConnected) docsTriagePane(host, state, courseId, kind);
      }).catch(() => { mem.classes = {}; });
    }
  }

  /* ------------------------------------------------------- the translator */
  /* Three things the Documents backend says differently from what the gateway
     component expects. Rather than paraphrasing the server anywhere else, the
     difference is handled here, once, in the open:

       1. `state.summary` is a counts object, not a sentence. The component
          prints it straight into the foot, which reads "[object Object]".
       2. `POST .../fixes` saves one file's edits and needs a file_id; it is not
          the component's "compute the fixes" step. Scanning already computed
          them, so the button only has to move to Review.
       3. The dry run answers {rows, blocked, uploads, count}, not the
          {planned:[...]} the component reads, so step 4 would show an empty
          plan and a dead Apply. The dry run is re-run here and its own rows
          are drawn, and Apply sends the push the server asked about.

     Triage has neither a dry run nor a push at all: both verbs are disabled
     with a sentence saying why. */
  function docsFitGateway(host, courseId, kind, gw) {
    const root = host && host.querySelector('.gw');
    if (!root || !gw) return;
    const mem = docsMem(kind.id);
    const summaryNode = root.querySelector('.gwSummary');
    const pane = root.querySelector('.gwPane');

    const sentence = () => {
      const c = (gw.state && gw.state.summary) || null;
      if (!c || typeof c !== 'object') return '';
      const bits = [c.files + ' ' + (c.files === 1 ? kind.one : kind.many)];
      if (c.fetched != null) bits.push(c.fetched + ' fetched');
      if (kind.alt && c.alt_todo) bits.push(c.alt_todo + ' still need a description');
      if (kind.map && c.hits) bits.push(c.hits + ' matches');
      if (c.verified) bits.push(c.verified + ' verified, not yet uploaded');
      if (c.failed) bits.push(c.failed + ' failed verify and will not be uploaded');
      if (c.pushed) bits.push(c.pushed + ' uploaded');
      if (c.unsupported) bits.push(c.unsupported + ' in a legacy format');
      return bits.join(' · ');
    };

    const drawPlan = () => {
      if (!pane) return;
      const plan = mem.plan;
      const stamp = plan ? String(plan.count) + ':' + (plan.rows || []).length + ':' + (plan.at || '') : 'none';
      const holder = pane.querySelector('[data-docs-plan]');
      if (holder && holder.dataset.docsPlan === stamp) return;
      const uploads = (plan && plan.uploads) || [];
      const blocked = (plan && plan.blocked) || [];
      const already = ((plan && plan.rows) || []).filter(r => r.already_pushed);
      pane.innerHTML = `<div data-docs-plan="${esc(stamp)}">
        <h3 class="gwH">Will be uploaded (${uploads.length})</h3>
        ${uploads.length ? `<ul class="applyList">${uploads.map(r => `<li class="planned">
            <b>${esc(r.name)}</b>${r.folder ? `<span class="muted"> · ${esc(r.folder)}</span>` : ''}
            <span class="muted"> ${esc((r.changes || []).join(', ') || 'no change listed')}</span></li>`).join('')}</ul>`
          : `<div class="empty"><p>${esc(plan ? 'Nothing is ready to upload. A file is uploaded only '
            + 'once it has a fix saved and that fix passes the check against the original.'
            : 'Run the dry run to see what would be uploaded. It changes nothing in Canvas.')}</p></div>`}
        ${blocked.length ? `<h3 class="gwH">Will not be uploaded (${blocked.length})</h3>
          <ul class="applyList">${blocked.map(r => `<li class="bad"><b>${esc(r.name)}</b>
            <span class="muted"> ${esc((r.problems || []).join('; ') || 'did not pass the check against the original')}</span></li>`).join('')}</ul>` : ''}
        ${already.length ? `<h3 class="gwH">Already on Canvas (${already.length})</h3>
          <ul class="applyList">${already.map(r => `<li class="done"><b>${esc(r.name)}</b>
            <span class="muted"> uploaded already, unchanged since</span></li>`).join('')}</ul>` : ''}
        <p class="muted gwNote">A dry run. Nothing has been sent to Canvas.</p></div>`;
    };

    const dryRun = () => runJob('Dry run: applying and checking the fixes',
      () => api(docsBase(courseId, kind.id) + '/push', { body: { apply: false } }),
      res => {
        mem.plan = { ...(res || {}), at: new Date().toISOString() };
        setStatus((mem.plan.count || 0) + ' ready to upload; nothing was sent', 'ok');
        gw.goto(4);
        drawPlan();
      }, { autoClose: true });

    const apply = () => runJobConfirmed('Uploading the fixed ' + kind.many,
      token => api(docsBase(courseId, kind.id) + '/push', { body: { apply: true, confirm: token } }),
      res => {
        const n = ((res && res.uploaded) || []).length;
        setStatus(n ? n + ' file(s) uploaded over the originals' : 'nothing was uploaded', n ? 'ok' : 'err');
        announce(n + ' file(s) uploaded to Canvas');
        mem.plan = null;
        gw.goto(5);
        gw.refresh();
      },
      { title: 'Upload the fixed ' + kind.many + ' over the originals?', verb: 'Yes, upload them' });

    const polish = () => {
      /* The state here carries no step, so the gateway would open at List
         every time even when the scanning is long done. Once, on the first
         state that arrives, pick up where the work actually is. */
      if (!mem.stepped && gw.state && (gw.state.items || []).length) {
        mem.stepped = true;
        const rows = gw.state.items;
        const ready = rows.some(r => /fixes ready|verified|failed|pushed/.test(r.state || ''));
        const scanned = rows.some(r => r.scanned);
        if (ready) gw.goto(3);
        else if (scanned) gw.goto(2);
      }
      if (summaryNode) {
        const want = sentence();
        if (want && summaryNode.textContent !== want) summaryNode.textContent = want;
      }
      const step3 = root.querySelector('[data-act="fixes"]');
      if (step3 && !step3.dataset.docsWired) {
        step3.dataset.docsWired = '1';
        step3.disabled = false;
        step3.textContent = 'Review the fixes';
        step3.title = 'Show what the scan found, file by file. Nothing is sent.';
        step3.onclick = () => gw.goto(3);
      }
      const dry = root.querySelector('[data-act="dry"]');
      if (dry && !dry.dataset.docsWired) {
        dry.dataset.docsWired = '1';
        if (kind.report) {
          dry.disabled = true;
          dry.title = 'PDF triage only reports; there is nothing to upload and nothing to plan.';
        } else dry.onclick = dryRun;
      }
      const act = root.querySelector('[data-act="apply"]');
      if (act && !act.dataset.docsWired) {
        act.dataset.docsWired = '1';
        if (kind.report) {
          act.disabled = true;
          act.title = 'PDF triage never uploads a file.';
        } else {
          const n = ((mem.plan && mem.plan.uploads) || []).length;
          act.disabled = !n;
          act.textContent = 'Upload ' + n;
          act.title = n ? 'Upload to Canvas. You are asked first.'
            : mem.plan ? 'Nothing passed the check against the original, so there is nothing to upload'
              : 'Run the dry run first';
          act.onclick = apply;
        }
      }
      if (!kind.report && pane && root.querySelector('.step[data-step="4"][aria-current="step"]')) drawPlan();
    };

    polish();
    const watch = new MutationObserver(polish);
    watch.observe(root, { childList: true, subtree: true, characterData: true });
    onLeave(() => watch.disconnect());
  }

  /* --------------------------------------------------------------- small */
  /* Saves while the person types, and once more on the way out so the last
     keystroke is never the one that is lost. A pane with nothing waiting sends
     nothing, so an old pane cannot overwrite a newer one's edits. */
  function docsDebounce(fn, ms) {
    let timer = null, waiting = false;
    const fire = () => { waiting = false; fn(); };
    const run = () => { waiting = true; clearTimeout(timer); timer = setTimeout(fire, ms); };
    onLeave(() => { clearTimeout(timer); if (waiting) fire(); });
    return run;
  }

  /* ------------------------------------------------------------ register */
  DOCS_KINDS.forEach(kind => {
    DocsStudio.a11yKinds[kind.id] = (courseId, rest) => docsOpenKind(courseId, kind.id, rest || []);
  });
  window.openDocs = openDocs;
  registerArea({
    id: 'docs', label: 'Documents', zone: 'a11y', open: openDocs,
    badge: hub => (hub && hub.docs && hub.docs.badge != null) ? hub.docs.badge : null,
  });
  Object.assign(DocsStudio, { openDocs });
})();
