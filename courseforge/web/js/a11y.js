/* a11y.js: the Accessibility area.

   Renders the kind tabs (HTML, PowerPoint, Word, PDFs, PDF text, Office text),
   owns the HTML gateway (fetch, restyle, verify, push, restore, and the two
   markup sweeps), and delegates the other kinds to docs.js and pdf.js through
   Studio.a11yKinds. Also #/batch, the cross-course run.

   Everything that writes goes through runJobConfirmed: the server refuses the
   first attempt with a sentence, the page shows it as-is, and the token only
   spends on that exact change. */
(function () {
  'use strict';

  window.Studio = window.Studio || {};
  Studio.a11yKinds = Studio.a11yKinds || {};
  S.a11y = S.a11y || {};

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
    /* Triage is one of docs.js's kinds and the hub links straight to it. Left
       off this list, opening it made docs.js rebuild the strip from its own
       list: a seventh tab appeared and the group headers vanished until you
       left. The strip is the same seven tabs everywhere. pdf.js keeps a copy
       of this list for the direct #/c/<cid>/pdf route; keep the two the same. */
    { id: 'triage', label: 'PDF triage', group: FIX,
      title: 'Which PDFs are worst, before fixing any of them. Reads only.' },
    { id: 'pdf-text', label: 'in PDFs', group: FIND,
      title: 'Change the words inside PDFs -- a retired name, an old course code. '
        + 'Nothing to do with accessibility.' },
    { id: 'office-text', label: 'in Office files', group: FIND,
      title: 'The same find and replace inside .docx, .pptx and .xlsx. '
        + 'Nothing to do with accessibility.' },
  ];
  /* Canvas's nouns, as an instructor says them. */
  const KIND_WORD = {
    page: 'Page', assignment: 'Assignment', discussion: 'Discussion',
    quiz: 'Quiz description', syllabus: 'Syllabus', announcement: 'Announcement',
  };

  const LOOKS = [
    { id: 'clean', label: 'Clean', hint: 'No background fills. Navy headings and borders only. Scores 0 use-of-colour advisories in Ally.' },
    { id: 'hybrid', label: 'Hybrid', hint: 'Filled navy hero and footer. Adds advisory colour flags, about 2 per page.' },
    { id: 'rich', label: 'Rich', hint: 'Every component filled. Adds advisory colour flags, up to 7 per page.' },
  ];

  const has = name => typeof window[name] === 'function';
  const base = cid => '/a11y/' + encodeURIComponent(cid) + '/html';
  const fmtSize = n => (n == null ? '' : (n < 1024 ? n + ' B' : (n / 1024).toFixed(1) + ' KB'));

  function pillHtml(text, cls) {
    if (has('pill')) return pill(text, cls);
    return '<span class="pill ' + esc(cls || '') + '">' + esc(text) + '</span>';
  }

  function mountEmpty(host, text, buttonLabel, onClick) {
    if (has('emptyState')) {
      const out = emptyState(text, buttonLabel, onClick);
      if (typeof out === 'string') {
        host.innerHTML = out;
        const b = host.querySelector('button');
        if (b && onClick) b.onclick = onClick;
      } else if (out instanceof Node) {
        host.innerHTML = '';
        host.appendChild(out);
      }
      return;
    }
    host.innerHTML = '<div class="emptyState"><p>' + esc(text) + '</p>'
      + (buttonLabel ? '<button class="btn primary">' + esc(buttonLabel) + '</button>' : '') + '</div>';
    const b = host.querySelector('button');
    if (b && onClick) b.onclick = onClick;
  }

  function modal(html) {
    if (has('openModal')) { openModal(html); return $('#modalHost'); }
    const host = $('#modalHost');
    host.innerHTML = '<div class="modalBack"><div class="modal">' + html + '</div></div>';
    return host;
  }
  function closeTheModal() {
    if (has('closeModal')) closeModal(); else $('#modalHost').innerHTML = '';
  }

  /* ------------------------------------------------------------- the area */
  async function openA11y(cid, rest) {
    rest = Array.isArray(rest) ? rest : [];
    const kind = rest[0] || 'html';
    S.a11y.courseId = cid;
    showView('area');
    await courseCrumbs(cid, [{ label: 'Accessibility' }]);
    if (String(S.a11y.courseId) !== String(cid)) return;
    $('#headerActions').innerHTML = '';
    areaHead('Accessibility', 'Two different jobs share this screen. Fix accessibility makes a file usable by a screen reader; Find and replace changes the words inside one. Nothing is pushed until you confirm.');
    areaTabs(KINDS.map(k => ({ id: k.id, label: k.label, group: k.group, title: k.title })), kind, id => {
      if (id !== kind) location.hash = '#/c/' + cid + '/a11y/' + id;
    });
    const body = $('#areaBody');
    if (kind === 'html') return openHtml(cid, body);
    const opener = Studio.a11yKinds[kind];
    if (typeof opener === 'function') return opener(cid, rest.slice(1));
    const label = (KINDS.find(k => k.id === kind) || {}).label || kind;
    body.innerHTML = '';
    mountEmpty(body, 'The ' + label + ' fixer is not installed in this build, so there is nothing to '
      + 'do here yet. Nothing has been pushed.', null, null);
  }

  /* --------------------------------------------------------- HTML gateway */
  function openHtml(cid, body) {
    body.innerHTML = '<p class="docsBlurb">Restyles the course text Canvas stores as HTML: '
      + 'pages, assignments, discussions, quiz descriptions and the syllabus. A gate proves '
      + 'the visible words did not change, so this never edits your writing.</p>'
      + '<div class="a11ySweeps" id="a11ySweeps"></div>'
      + '<div class="a11yVerbs" id="a11yVerbs"></div><div id="a11yGateway"></div>';
    renderSweeps(cid);
    renderVerbs(cid);
    const host = $('#a11yGateway');
    if (!has('renderGateway')) {
      host.innerHTML = '<div class="callout">The shared gateway component (components.js) is not '
        + 'loaded, so the steps cannot be shown. The command line still works: '
        + '<code>python -m courseforge a11y dump --course ' + esc(cid) + '</code></div>';
      return;
    }
    renderGateway(host, {
      kind: 'html',
      courseId: cid,
      label: 'Pages and other course text',
      endpoints: {
        state: base(cid) + '/state',
        list: base(cid) + '/list',
        fetch: base(cid) + '/fetch',
        describe: null,
        fixes: base(cid) + '/fixes',
        push: base(cid) + '/push',
      },
      // {key, render(item)}, the shape the gateway reads. Written as
      // {id, format(value)} these silently produced a table of empty cells.
      listColumns: [
        { label: 'Name', key: 'title',
          render: it => `<b>${esc(it.title || it.key || '')}</b>`
            + (it.published === false ? '<span class="muted"> · unpublished</span>' : '') },
        { label: 'Type', key: 'kind', render: it => esc(KIND_WORD[it.kind] || it.kind || '') },
        // The server counts characters of body, not bytes on a disk, which for
        // HTML is close enough to read as a size and is what it is here for:
        // telling a one-line page from a long one at a glance.
        { label: 'Length', key: 'size',
          // Canvas does not say how long a body is until it has been read, so
          // before Fetch this is unknown rather than zero, and says which.
          render: it => (it.size == null
            ? '<span class="muted">not read yet</span>'
            : `<span class="num">${esc(fmtSize(it.size))}</span>`) },
        { label: 'Modified', key: 'modified',
          render: it => `<span class="muted">${esc(
            (has('fmtDate') && fmtDate(it.modified)) || it.modified || '')}</span>` },
        { label: 'State', key: 'state',
          render: it => (has('statePill') ? statePill(it.state) : esc(it.state || '')) },
      ],
      reviewRenderer: renderHtmlReview,
      confirmTitle: 'Replace these bodies in Canvas?',
      describeLabel: null,
      fetchLabel: 'Fetch & scan',
      applyLabel: 'Apply',
      emptyText: 'Fetch reads every page, assignment, discussion and quiz body plus the syllabus '
        + 'into a local folder and scans them. Nothing is pushed.',
    });
  }

  /* Two things Ally flags that a restyle does not touch, because both are
     judgement calls about what the author meant rather than a change of look.
     Each one reads the course and reports; the fixes are behind the report. */
  const SWEEPS = [
    { id: 'a11yBold', label: 'Bold used as structure',
      what: 'Paragraphs that are entirely bold or italic, standing in for a heading.',
      why: 'A screen reader announces nothing for bold, so the structure you can '
        + 'see on the page is not there for everyone. The report sorts them into '
        + 'short labels, instruction sentences and code, because the right fix '
        + 'differs for each, and every fix is a separate tick box.' },
    { id: 'a11yBoxes', label: 'Bordered boxes',
      what: 'Sentences wrapped in a bordered span, usually pasted in from Word or '
        + 'an older course.',
      why: 'The border splits into ragged halves when the line wraps, and it marks '
        + 'something as important using decoration alone, which a screen reader '
        + 'cannot pass on. Cards and headings keep their borders.' },
  ];

  function renderSweeps(cid) {
    const host = $('#a11ySweeps');
    if (!host) return;
    host.innerHTML = '<p class="a11ySweepHead">Two more things Ally flags. Each one '
      + 'reads every page, assignment, discussion and quiz description in this course '
      + 'from Canvas and tells you what it found. Neither changes anything until you '
      + 'pick a fix in the report and confirm it.</p>'
      + SWEEPS.map(s => '<div class="a11ySweep">'
        + '<button class="btn" id="' + s.id + '">' + esc(s.label) + '</button>'
        + '<p><b>' + esc(s.what) + '</b> ' + esc(s.why) + '</p>'
        + '</div>').join('');
    $('#a11yBold').onclick = () => runBold(cid, {}, false);
    $('#a11yBoxes').onclick = () => runBoxes(cid, false);
  }

  function renderVerbs(cid) {
    const host = $('#a11yVerbs');
    if (!host) return;
    host.innerHTML = ''
      + '<span class="spacer"></span>'
      + '<button class="btn danger" id="a11yRestore" title="Put back the bodies the last push replaced">Restore previous bodies</button>';
    $('#a11yRestore').onclick = () => runRestore(cid);
  }

  function refreshHtml(cid) {
    // Never location.reload(): re-run the opener.
    if (location.hash.indexOf('#/c/' + cid + '/a11y') === 0) openA11y(cid, ['html']);
  }

  /* ---------------------------------------------------------- the review */
  function verifyLine(state) {
    if (!state.verified_at) return state.transformed_at ? 'Restyled, not verified yet.' : 'Not restyled yet.';
    const c = state.counts || {};
    return 'Verified ' + (has('fmtDate') ? fmtDate(state.verified_at) : state.verified_at)
      + ' (' + state.look + ' look): ' + (c.verified || 0) + ' pass'
      + (c.failed ? ', ' + c.failed + ' fail and will not be pushed' : '') + '.';
  }

  /* The card is the radio; the Example button sits beside it, not inside it.
     A button within a button is invalid markup and the inner one stops being
     reachable by keyboard, which would put the preview out of reach of exactly
     the people this area is for. */
  function lookCard(l, current) {
    const on = l.id === current;
    return '<div class="a11yLookWrap">'
      + '<button type="button" class="a11yLook" role="radio" aria-checked="' + on + '" data-look="' + l.id + '">'
      + '<b>' + esc(l.label) + '</b>' + (l.id === 'clean' ? ' <span class="pill">default</span>' : '')
      + '<div class="hint">' + esc(l.hint) + '</div></button>'
      + '<button type="button" class="a11yLookEg" data-eg="' + l.id + '"'
      + ' title="See a page in the ' + esc(l.label) + ' look"'
      + ' aria-label="Example of the ' + esc(l.label) + ' look">Example</button>'
      + '</div>';
  }

  /* A course reads like a look: a card you click, wearing the same selected
     colour. The tick box stays visible and real, because unlike the looks this
     is a multiple choice and a card that hides its checkbox does not say so. */
  function courseCard(c, on, showTerm) {
    const id = esc(String(c.id));
    const title = esc(c.title || c.name || c.id);
    const bits = [];
    if (c.code) bits.push(esc(c.code));
    if (showTerm && c.term_label) bits.push(esc(c.term_label));
    if (c.students != null) bits.push(c.students + (c.students === 1 ? ' student' : ' students'));
    return '<label class="a11yCourse' + (on ? ' on' : '') + '" data-course="' + id + '">'
      + '<input type="checkbox" class="a11yPick" value="' + id + '"' + (on ? ' checked' : '') + '>'
      + '<span class="a11yCourseBody"><b>' + title + '</b>'
      + (bits.length ? '<span class="hint">' + bits.join(' &middot; ') + '</span>' : '')
      + '</span></label>';
  }

  /* The three renders come from the server once and are kept for the session:
     they are the restyler's own output on a sample page, so they are the same
     for every course and there is nothing to refresh. */
  async function lookPreviews() {
    if (!S.a11y.looks) S.a11y.looks = await api('/a11y/looks').then(r => r.looks || []);
    return S.a11y.looks;
  }

  async function showLookExample(id) {
    let looks;
    try { looks = await lookPreviews(); }
    catch (err) { setStatus('could not load the example: ' + firstLine(err.message), 'err'); return; }
    const l = looks.find(x => x.id === id);
    if (!l) { setStatus('no example for ' + id, 'err'); return; }
    const tab = x => '<button type="button" class="chip" role="tab" aria-selected="' + (x.id === id)
      + '" data-egtab="' + x.id + '">' + esc(x.label) + '</button>';
    openModal('<h3 id="egTitle">' + esc(l.label) + ' look</h3>'
      + '<p class="sub" id="egHint">' + esc(l.hint) + '</p>'
      + '<div class="chips" role="tablist" aria-label="Look">' + looks.map(tab).join('') + '</div>'
      + '<div class="paperFrame canvasPage"><div class="canvasHtml asIs" id="egBody">' + l.html + '</div></div>'
      + '<p class="hint">A sample page, restyled by the same code the real run uses. '
      + 'Your own pages keep their own words; only the styling changes.</p>'
      + '<div class="foot"><button class="btn" id="egClose">Close</button></div>',
      { cls: 'wide', label: 'Example of each look' });
    const paint = x => {
      $('#egBody').innerHTML = x.html;
      document.querySelectorAll('[data-egtab]').forEach(b =>
        b.setAttribute('aria-selected', String(b.dataset.egtab === x.id)));
      const h = $('#egTitle'); if (h) h.textContent = x.label + ' look';
      const sub = $('#egHint'); if (sub) sub.textContent = x.hint;
    };
    document.querySelectorAll('[data-egtab]').forEach(b => {
      b.onclick = () => { const x = looks.find(y => y.id === b.dataset.egtab); if (x) paint(x); };
    });
    $('#egClose').onclick = closeModal;
  }

  function itemRow(it, selected) {
    const v = it.verify;
    let pills;
    if (it.excluded) pills = pillHtml('left alone', 'warn');
    else if (!v) pills = pillHtml('not verified', 'muted');
    else if (v.ok) pills = pillHtml('text identical', 'ok') + pillHtml('links unchanged', 'ok') + pillHtml('images unchanged', 'ok');
    else pills = v.issues.map(x => pillHtml(x, 'err')).join('') + pillHtml('excluded from push', 'warn');
    return '<div class="a11yItem" role="option" aria-selected="' + (selected ? 'true' : 'false') + '" data-key="' + esc(it.key) + '">'
      + '<button type="button" class="a11yItemBtn" data-key="' + esc(it.key) + '">' + esc(it.title || it.key) + '</button>'
      + ' <span class="pill">' + esc(it.kind) + '</span>'
      + (it.transform_note === 'wrapped' ? ' <span class="pill">heading added</span>' : '')
      + (it.pushed ? ' <span class="pill ok">pushed</span>' : '')
      + '<div class="a11yPills">' + pills + '</div>'
      + '<label class="a11yExcludeLbl"><input type="checkbox" class="a11yExclude" data-key="' + esc(it.key) + '"'
      + (it.excluded ? ' checked' : '') + '> Leave this one alone</label>'
      + '</div>';
  }

  function renderHtmlReview(host, state) {
    state = state || {};
    const cid = state.course_id || S.a11y.courseId;
    S.a11y.reviewHost = host;
    S.a11y.courseLabel = state.course_label || S.a11y.courseLabel;
    const look = S.a11y.look || state.look || state.default_look || 'clean';
    S.a11y.look = look;
    const items = (state.items || []).filter(it => it.fetched);
    const styled = items.filter(it => it.styled);
    const selectedKey = (S.a11y.selected && styled.some(i => i.key === S.a11y.selected))
      ? S.a11y.selected : (styled[0] && styled[0].key);

    host.innerHTML = ''
      + '<div class="a11yLooks" role="radiogroup" aria-label="Look">' + LOOKS.map(l => lookCard(l, look)).join('') + '</div>'
      + (look !== 'clean'
        ? '<div class="callout a11yWarn">The ' + esc(look) + ' look adds background fills, and Ally raises an '
          + 'advisory "use of colour" flag on every fill (about ' + (look === 'hybrid' ? '2' : '7')
          + ' per page). It lowers the score until each one is reviewed in Ally. Pick Clean for a compliance-driven pass.</div>'
        : '')
      + '<div class="a11yVerbs">'
      + '<button class="btn primary" id="a11yRestyle"' + (items.length ? '' : ' disabled title="Fetch first"') + '>Restyle &amp; verify (' + esc(look) + ')</button>'
      + '<span class="hint" id="a11yVerifyLine">' + esc(verifyLine(state)) + '</span>'
      + '</div>'
      + '<div class="a11yReview">'
      + '<div class="a11yItems" role="listbox" aria-label="Restyled items" id="a11yItems">'
      + (styled.length ? styled.map(it => itemRow(it, it.key === selectedKey)).join('')
        : '<div class="a11yNone">Nothing restyled yet. Pick a look and click Restyle &amp; verify. Nothing is pushed.</div>')
      + '</div>'
      + '<div class="a11yPanesWrap"><div id="a11yPanes" class="a11yPanes"><div class="a11yNone">Select an item to compare before and after.</div></div></div>'
      + '</div>';

    host.querySelectorAll('.a11yLookEg').forEach(el => {
      el.onclick = () => showLookExample(el.dataset.eg);
    });
    host.querySelectorAll('.a11yLook').forEach(el => {
      el.onclick = () => { S.a11y.look = el.dataset.look; renderHtmlReview(host, state); };
    });
    const restyleBtn = host.querySelector('#a11yRestyle');
    if (restyleBtn) restyleBtn.onclick = () => runJob('Restyle & verify (' + S.a11y.look + ' look)',
      () => api(base(cid) + '/transform', { body: { look: S.a11y.look } }),
      res => {
        const v = (res && res.verify) || {};
        setStatus('verified ' + (v.count || 0) + ' items, ' + (v.fails || 0) + ' failing; nothing pushed', v.fails ? 'err' : 'ok');
        renderHtmlReview(host, (res && res.state) || state);
      });
    host.querySelectorAll('.a11yItemBtn').forEach(btn => {
      btn.onclick = () => showItem(cid, btn.dataset.key, host);
    });
    host.querySelectorAll('.a11yExclude').forEach(cb => {
      cb.onchange = async () => {
        try {
          const st = await api(base(cid) + '/fixes', { body: { key: cb.dataset.key, excluded: cb.checked } });
          renderHtmlReview(host, st);
        } catch (err) { setStatus(err.message, 'err'); }
      };
    });
    if (selectedKey) showItem(cid, selectedKey, host);
  }

  async function showItem(cid, key, host) {
    S.a11y.selected = key;
    host.querySelectorAll('.a11yItem').forEach(el => el.setAttribute('aria-selected', String(el.dataset.key === key)));
    const panes = host.querySelector('#a11yPanes');
    if (!panes) return;
    panes.innerHTML = '<div class="a11yNone">Loading the before and after...</div>';
    const q = which => api(base(cid) + '/item/' + encodeURIComponent(key) + '?which=' + which).catch(err => ({ error: err.message }));
    const [before, after] = await Promise.all([q('before'), q('after')]);
    if (S.a11y.selected !== key) return;
    const pane = (title, r) => '<section><h4>' + esc(title)
      + (r && r.chars != null ? ' <span class="hint">' + esc(fmtSize(r.chars)) + '</span>' : '') + '</h4>'
      + '<div class="paperFrame"><div class="canvasHtml asIs">'
      + (r && !r.error ? (r.html || '<p class="hint">(empty)</p>') : '<p class="hint">' + esc((r && r.error) || 'not available') + '</p>')
      + '</div></div>'
      + (r && r.issues && r.issues.length ? '<div class="a11yPills">' + r.issues.map(x => pillHtml(x, 'warn')).join('') + '</div>' : '')
      + '</section>';
    panes.innerHTML = pane('Before', before) + pane('After', after);
  }

  /* ------------------------------------------------------------- restore */
  function runRestore(cid) {
    runJobConfirmed('Restore previous bodies',
      token => api(base(cid) + '/restore', { body: { apply: true, confirm: token } }),
      res => {
        setStatus('restored ' + (res.written_count || 0) + ' bodies'
          + (res.live_fails ? ', ' + res.live_fails + ' live check(s) failed' : ''), res.live_fails ? 'err' : 'ok');
        refreshHtml(cid);
      },
      { title: 'Put the previous bodies back?', verb: 'Yes, restore them' });
  }

  /* ---------------------------------------------------- bold as structure */
  function runBold(cid, options, apply) {
    const start = token => api(base(cid) + '/bold-structure', { body: { apply: !!apply, options: options || {}, confirm: token } });
    const done = res => showBoldReport(cid, res, options || {});
    if (apply) runJobConfirmed('Bold used as structure: apply', start, done, { title: 'Change this markup in Canvas?' });
    else runJob(hasRemedy(options)
      ? 'Bold used as structure: working out the changes'
      : 'Reading every body in the course to find bold used as structure',
      () => start(null), done);
  }
  const hasRemedy = o => !!(o && (o.promote_labels || o.unbold_sentences || o.convert_code_runs));

  function showBoldReport(cid, res, options) {
    res = res || {};
    const c = res.counts || {};
    const items = res.items || [];
    const changes = res.changes || [];
    const applied = res.dry_run === false;
    const rows = items.map(it => '<tr><th scope="row">' + esc(it.label) + '</th><td>'
      + (it.hits || []).map(h => '<div><span class="pill ' + esc(h.cls) + '">' + esc(h.cls) + '</span> ' + esc(h.text.length > 70 ? h.text.slice(0, 70) + '...' : h.text) + '</div>').join('')
      + (it.made && it.made.length ? '<div class="hint">' + it.made.map(esc).join('<br>') + '</div>' : '')
      + (it.skipped_reason ? '<div class="hint">' + esc(it.skipped_reason) + '</div>' : '')
      + '</td></tr>').join('');
    const labels = (options.labels && options.labels.length) ? options.labels : (res.options && res.options.labels) || [];
    const host = modal(''
      + '<h3>Bold used as structure</h3>'
      + '<p class="hint">' + esc((res.hit_count || 0) + ' wholly-bold paragraphs: ' + (c.label || 0) + ' labels, '
        + (c.sentence || 0) + ' sentences, ' + (c.code || 0) + ' code, ' + (c.unclassified || 0) + ' unclassified.')
      + (applied ? ' Changed ' + (res.written_count || 0) + ' item(s).' : ' Nothing has been written.') + '</p>'
      + '<div class="a11yOptions">'
      + '<label><input type="checkbox" id="boPromote"' + (options.promote_labels ? ' checked' : '') + '> Promote allowlisted labels to real h3 headings</label>'
      + '<label><input type="checkbox" id="boUnbold"' + (options.unbold_sentences ? ' checked' : '') + '> Drop the blanket bold on instruction sentences</label>'
      + '<label><input type="checkbox" id="boCode"' + (options.convert_code_runs ? ' checked' : '') + '> Collapse bolded code into one code block</label>'
      + '<label>Label allowlist (one regex per line)<textarea id="boLabels" rows="3">' + esc(labels.join('\n')) + '</textarea></label>'
      + '</div>'
      + (changes.length && !applied ? '<div class="applyList">' + changes.map(ch => '<div class="planned"><b>' + esc(ch.label) + '</b> ' + esc(ch.from) + ' -&gt; ' + esc(ch.to) + '</div>').join('') + '</div>' : '')
      + '<div class="a11yTableWrap"><table class="a11yTable"><caption>Hits by item</caption><thead><tr><th scope="col">Item</th><th scope="col">Paragraphs</th></tr></thead><tbody>'
      + (rows || '<tr><td colspan="2">No wholly-bold paragraphs found.</td></tr>') + '</tbody></table></div>'
      + '<div class="foot"><span class="spacer"></span>'
      + '<button class="btn" id="boClose">Close</button>'
      + '<button class="btn primary" id="boDry">Dry run with these options</button>'
      + (changes.length && !applied ? '<button class="btn danger" id="boApply">Apply ' + changes.length + ' change(s)</button>' : '')
      + '</div>');
    const read = () => ({
      promote_labels: host.querySelector('#boPromote').checked,
      unbold_sentences: host.querySelector('#boUnbold').checked,
      convert_code_runs: host.querySelector('#boCode').checked,
      labels: host.querySelector('#boLabels').value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
    });
    host.querySelector('#boClose').onclick = closeTheModal;
    host.querySelector('#boDry').onclick = () => { const o = read(); closeTheModal(); runBold(cid, o, false); };
    const ap = host.querySelector('#boApply');
    if (ap) ap.onclick = () => { const o = read(); closeTheModal(); runBold(cid, o, true); };
  }

  /* -------------------------------------------------------- bordered boxes */
  function runBoxes(cid, apply) {
    const start = token => api(base(cid) + '/bordered-boxes', { body: { apply: !!apply, confirm: token } });
    const done = res => showBoxesPlan(cid, res);
    if (apply) runJobConfirmed('Bordered boxes: apply', start, done, { title: 'Remove these bordered boxes in Canvas?' });
    else runJob('Reading every body in the course to find bordered boxes',
      () => start(null), done);
  }

  function showBoxesPlan(cid, res) {
    res = res || {};
    const items = res.items || [];
    const skipped = res.skipped || [];
    const applied = res.dry_run === false;
    const host = modal(''
      + '<h3>Inline bordered boxes</h3>'
      + '<p class="hint">' + esc((res.box_count || 0) + ' bordered-box span(s) (' + (res.color || '') + ') in ' + items.length + ' item(s).')
      + (applied ? ' Removed ' + (res.boxes_removed || 0) + ' from ' + (res.written_count || 0) + ' item(s).' : ' Nothing has been written.') + '</p>'
      + '<div class="applyList">'
      + (items.length ? items.map(it => '<div class="planned"><b>' + esc(it.label) + '</b> ' + it.boxes + ' box(es) -&gt; ' + it.boxes_after + '</div>').join('')
        : '<div class="hint">No inline bordered boxes found.</div>')
      + (skipped.length ? '<h4>Left alone</h4>' + skipped.map(it => '<div class="planned"><b>' + esc(it.label) + '</b> ' + esc(it.skipped_reason) + '</div>').join('') : '')
      + '</div>'
      + '<div class="foot"><span class="spacer"></span><button class="btn" id="bbClose">Close</button>'
      + (items.length && !applied ? '<button class="btn danger" id="bbApply">Remove ' + res.box_count + ' box(es)</button>' : '')
      + '</div>');
    host.querySelector('#bbClose').onclick = closeTheModal;
    const ap = host.querySelector('#bbApply');
    if (ap) ap.onclick = () => { closeTheModal(); runBoxes(cid, true); };
  }

  /* --------------------------------------------------------------- batch */
  async function openBatch() {
    showView('area');
    // This screen is the accessibility area working across courses rather than
    // inside one, so it wears that zone. showView reads the zone off the route,
    // and #/batch names no course or area for it to read.
    document.body.dataset.area = 'a11y';
    crumbs([{ label: 'Courses', href: '#/' }, { label: 'Batch Course Restyle' }]);
    $('#headerActions').innerHTML = '';
    areaHead('Batch Course Restyle', 'Fetch, restyle, verify and push several courses in one run. Dry run first; nothing is pushed until you confirm.');
    if (has('areaTabs')) areaTabs([], null, null);
    const body = $('#areaBody');
    body.innerHTML = '<div class="hint">Loading your courses...</div>';
    let courses = [];
    try {
      const got = (S.courses && S.courses.length) ? S.courses : await api('/courses');
      courses = Array.isArray(got) ? got : (got && got.courses) || [];
    } catch (err) {
      body.innerHTML = '<div class="callout">Could not load the course list: ' + esc(err.message) + '</div>';
      return;
    }
    const last = await api('/batch/a11y').catch(() => null);
    if (!S.termInfo) S.termInfo = await api('/terms').catch(() => ({ terms: [] }));
    const look = S.a11y.batchLook || 'clean';
    const picked = new Set(S.a11y.batchPicked || []);
    const live = courses.filter(c => !c.excluded);

    // A term, because a batch run is normally "the courses I am teaching now",
    // and nobody wants to hunt for five of those among four years of shells.
    // Default to the term the picker defaults to, so the two screens agree.
    if (S.a11y.batchTerm == null) S.a11y.batchTerm = (S.termInfo && S.termInfo.default) || '__all';
    const term = S.a11y.batchTerm;
    const shown = term === '__all' ? live : live.filter(c => (c.term_label || '') === term);

    // Picks survive a change of term, so a run can span terms on purpose. That
    // also means a picked course can be out of sight, and a batch that quietly
    // includes courses you cannot see is exactly the kind of surprise this tool
    // is built to avoid -- so say so, and offer a way out.
    const shownIds = new Set(shown.map(c => String(c.id)));
    const hidden = live.filter(c => picked.has(String(c.id)) && !shownIds.has(String(c.id)));

    const termOpts = ((S.termInfo && S.termInfo.terms) || []).map(t =>
      '<option value="' + esc(t.label) + '"' + (t.label === term ? ' selected' : '') + '>'
      + esc(t.label) + ' (' + t.count + ')</option>').join('');

    body.innerHTML = ''
      + '<div class="a11yBatch">'
      + '<section><div class="a11yCourseHead"><h3 id="batchCoursesH">Courses</h3>'
      + '<label class="srOnly" for="batchTerm">Term</label>'
      + '<select class="termSel" id="batchTerm">' + termOpts
      + '<option value="__all"' + (term === '__all' ? ' selected' : '') + '>All terms (' + live.length + ')</option>'
      + '</select>'
      + '<span class="hint" id="batchCount">' + shown.length + ' course' + (shown.length === 1 ? '' : 's')
      + ' &middot; <span id="batchPicked">' + picked.size + '</span> picked</span>'
      + '<span class="spacer"></span>'
      + '<button class="btn sm" id="batchAll" type="button">Select all shown</button>'
      + '<button class="btn sm" id="batchNone" type="button">Clear</button>'
      + '</div>'
      + '<div class="a11yCourseList" role="group" aria-labelledby="batchCoursesH">'
      + (shown.length
        ? shown.map(c => courseCard(c, picked.has(String(c.id)), term === '__all')).join('')
        : '<div class="a11yNone">No courses in ' + esc(term) + '.</div>')
      + '</div>'
      + (hidden.length
        ? '<div class="callout a11yWarn" id="batchHidden">' + hidden.length + ' picked course'
          + (hidden.length === 1 ? ' is' : 's are') + ' in another term and not listed here. '
          + 'They are still part of the run. <button class="btn sm" id="batchDropHidden" type="button">Drop them</button></div>'
        : '')
      + '<div class="a11yCourseFoot">'
      + (term === '__all'
        ? '<span class="hint">Showing every term.</span>'
        : '<button class="btn" id="batchShowAll" type="button">Show all terms (' + live.length + ' courses)</button>')
      + '</div></section>'
      + '<section><h3>Look</h3><div class="a11yLooks" role="radiogroup" aria-label="Look">' + LOOKS.map(l => lookCard(l, look)).join('') + '</div>'
      + (look !== 'clean' ? '<div class="callout a11yWarn">The ' + esc(look) + ' look adds advisory colour flags in Ally on every filled element.</div>' : '')
      + '<div class="a11yVerbs">'
      + '<button class="btn primary" id="batchDry">Dry run</button>'
      + '<button class="btn danger" id="batchApply" title="Runs the dry run again, then asks before pushing">Apply to every course that passes verify</button>'
      + '</div></section>'
      + '<section id="batchResult"></section>'
      + '</div>';
    body.querySelectorAll('.a11yLook').forEach(el => { el.onclick = () => { S.a11y.batchLook = el.dataset.look; openBatch(); }; });
    body.querySelectorAll('.a11yLookEg').forEach(el => { el.onclick = () => showLookExample(el.dataset.eg); });

    // The run is what is picked, not what is on screen: a course hidden by the
    // term filter still counts, and the banner above says so.
    const pickedIds = () => Array.from(new Set(
      Array.from(body.querySelectorAll('.a11yPick:checked')).map(el => el.value)
        .concat(hidden.map(c => String(c.id)))));
    const remember = () => {
      S.a11y.batchPicked = pickedIds();
      const n = $('#batchPicked');
      if (n) n.textContent = String(S.a11y.batchPicked.length);
      body.querySelectorAll('.a11yCourse').forEach(lb => {
        const box = lb.querySelector('.a11yPick');
        lb.classList.toggle('on', !!(box && box.checked));
      });
    };
    body.querySelectorAll('.a11yPick').forEach(el => { el.onchange = remember; });
    $('#batchTerm').onchange = ev => { S.a11y.batchTerm = ev.target.value; openBatch(); };
    const showAll = $('#batchShowAll');
    if (showAll) showAll.onclick = () => { S.a11y.batchTerm = '__all'; openBatch(); };
    const dropHidden = $('#batchDropHidden');
    if (dropHidden) dropHidden.onclick = () => {
      const drop = new Set(hidden.map(c => String(c.id)));
      S.a11y.batchPicked = (S.a11y.batchPicked || []).filter(id => !drop.has(String(id)));
      openBatch();
    };
    $('#batchAll').onclick = () => {
      body.querySelectorAll('.a11yPick').forEach(el => { el.checked = true; });
      remember();
    };
    $('#batchNone').onclick = () => {
      body.querySelectorAll('.a11yPick').forEach(el => { el.checked = false; });
      S.a11y.batchPicked = hidden.map(c => String(c.id));
      const n = $('#batchPicked');
      if (n) n.textContent = String(S.a11y.batchPicked.length);
    };
    const result = $('#batchResult');
    if (last && last.rows && last.rows.length) renderBatchSummary(result, last, true);
    $('#batchDry').onclick = () => {
      const ids = pickedIds();
      if (!ids.length) { setStatus('pick at least one course', 'err'); return; }
      runJob('Batch Course Restyle: dry run', () => api('/batch/a11y', { body: { course_ids: ids, look: S.a11y.batchLook || 'clean', apply: false } }),
        res => renderBatchSummary(result, res, false));
    };
    $('#batchApply').onclick = () => {
      const ids = pickedIds();
      if (!ids.length) { setStatus('pick at least one course', 'err'); return; }
      runJobConfirmed('Batch Course Restyle: apply',
        token => api('/batch/a11y', { body: { course_ids: ids, look: S.a11y.batchLook || 'clean', apply: true, confirm: token } }),
        res => renderBatchSummary(result, res, false),
        { title: 'Push the restyled bodies to these courses?' });
    };
  }

  function renderBatchSummary(host, s, previous) {
    if (!host) return;
    const rows = (s && s.rows) || [];
    host.innerHTML = '<h3>' + (previous ? 'Last run' : 'Result') + ' <span class="hint">' + esc((s.ran_at ? (has('fmtDate') ? fmtDate(s.ran_at) : s.ran_at) : '') + ' (' + (s.look || '') + ' look, ' + (s.applied ? 'applied' : 'dry run, nothing written') + ')') + '</span></h3>'
      + '<div class="a11yTableWrap"><table class="a11yTable"><caption>One row per course</caption><thead><tr>'
      + ['Course', 'Items', 'Styled', 'Wrapped', 'Skipped', 'Fills', 'Verify', 'Push', 'Error'].map(h => '<th scope="col">' + h + '</th>').join('')
      + '</tr></thead><tbody>'
      + rows.map(r => '<tr><th scope="row">' + esc(r.name || r.course) + ' <span class="hint">' + esc(String(r.course)) + '</span></th><td>' + r.items + '</td><td>' + r.styled + '</td><td>' + r.wrapped + '</td><td>' + r.skipped + '</td><td>' + r.fills + '</td>'
        + '<td>' + pillHtml(r.verify, r.verify === 'PASS' ? 'ok' : (r.verify === '-' ? 'muted' : 'err')) + '</td><td>' + esc(r.push) + '</td><td>' + esc(r.error || '') + '</td></tr>').join('')
      + '</tbody></table></div>';
  }

  /* ------------------------------------------------------------ register */
  window.openBatch = openBatch;
  window.openA11y = openA11y;
  // The picker shares this card with the ADA file compliance screen.
  Object.assign(window.Studio || (window.Studio = {}), { courseCard, openBatch });
  if (has('registerArea')) {
    registerArea({
      id: 'a11y', label: 'Accessibility', zone: 'a11y', open: openA11y,
      badge: hub => (hub && hub.a11y && hub.a11y.badge != null) ? hub.a11y.badge : null,
    });
  }
})();
