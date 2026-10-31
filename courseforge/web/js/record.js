/* CourseForge Studio - the record of actions.

   One screen per course, and the same screen for the account-level things that
   belong to no single course. It answers one question: what did this tool do,
   when, on whose account, and to which student. That question gets asked
   properly exactly once, long after the fact, by somebody who is not in a good
   mood, so the screen leads with whether the chain still adds up and where a
   copy of the file is, and puts the pretty formatting second. */
(function () {
  'use strict';

  S.record = S.record || {};

  const AREA_WORDS = {
    grade: 'Grade', a11y: 'Accessibility', docs: 'Accessibility', pdf: 'Accessibility',
    build: 'Build', content: 'Build', courseops: 'Tools', tools: 'Tools',
    assistant: 'Assistant', accommodations: 'Accommodations',
    extend: 'Extensions',
  };

  function base(cid) { return '/record/' + encodeURIComponent(cid); }

  async function openRecord(courseId, rest) {
    if (typeof loadNicks === 'function') loadNicks();
    const cid = String(courseId);
    S.record.cid = cid;
    S.record.filter = { student: '', area: '', month: '' };
    showView('area');
    await courseCrumbs(cid, [{ label: 'Record' }]);
    if (String(S.record.cid) !== cid) return;
    $('#headerActions').innerHTML = '';
    areaHead('Record of actions',
      'What the Studio did in this course, written down as it happened. '
      + 'Accommodations and grades name the student; everything else names the page or file.');
    areaTabs([], null, null);

    $('#areaBody').innerHTML = `
      <div id="recChain" class="recChain">Checking…</div>
      <div id="recWhere" class="recWhere"></div>
      <div class="recFilters" id="recFilters"></div>
      <div id="recRows">Loading…</div>`;

    await load(cid);
    onLeave(() => { S.record.cid = null; });
  }

  async function load(cid) {
    let data;
    try {
      data = await api(base(cid) + '?' + params());
    } catch (err) {
      $('#recRows').innerHTML = `<div class="callout err">The record could not be read:
        ${esc(firstLine(err.message))}</div>`;
      return;
    }
    if (String(S.record.cid) !== String(cid)) return;
    if (typeof loadNicks === 'function') await loadNicks();
    S.record.data = data;
    drawWhere(cid, data);
    drawFilters(cid, data);
    drawRows(data);
    verify(cid, false);
  }

  function params() {
    const f = S.record.filter || {};
    const q = new URLSearchParams();
    if (f.student) q.set('student', f.student);
    if (f.area) q.set('area', f.area);
    if (f.month) q.set('month', f.month);
    q.set('limit', '500');
    return q.toString();
  }

  /* --------------------------------------------------------- the chain */
  /* Said first and in words, because "is this still the file you wrote?" is
     the only question that matters if it is ever produced as evidence. */
  function verify(cid, loud) {
    const host = $('#recChain');
    if (host) host.textContent = 'Checking the record…';
    api(base(cid) + '/verify', { body: {} })
      .then(out => {
        if (String(S.record.cid) !== String(cid) || !$('#recChain')) return;
        const ok = out.ok;
        $('#recChain').className = 'recChain ' + (ok ? 'ok' : 'bad');
        $('#recChain').innerHTML = ok
          ? `<b>${out.entries} entr${out.entries === 1 ? 'y' : 'ies'}, unbroken.</b>
             <span class="muted">${esc(out.why)} Each entry carries a fingerprint of the
             one before it, so a line that was changed, removed or added later would
             show up here.</span>`
          : `<b>This record has been changed since it was written.</b>
             <span>${esc(out.why)}</span>
             <span class="muted">It breaks at entry ${esc(String((out.broke_at || {}).seq || '?'))}
             in ${esc((out.broke_at || {}).file || '?')}
             (${esc((out.broke_at || {}).sentence || '')}). Entries before that point
             still check out.</span>`;
        if (loud) setStatus(ok ? 'the record checks out' : 'the record does not check out', ok ? 'ok' : 'err');
      })
      .catch(err => {
        if ($('#recChain')) {
          $('#recChain').className = 'recChain';
          $('#recChain').textContent = 'Could not check the record: ' + firstLine(err.message);
        }
      });
  }

  /* ------------------------------------------------------- where it lives */
  function drawWhere(cid, d) {
    const host = $('#recWhere');
    if (!host) return;
    const saved = Object.keys(d.uploaded || {}).filter(k => k !== 'readme');
    host.innerHTML = `
      <div class="card">
        <div class="t">Where this is kept</div>
        <p>On this PC at <code class="mono">${esc(d.folder)}</code>, one file a month.</p>
        <p>${d.to_canvas
          ? `In Canvas under <b>Files, ${esc(d.canvas_folder)}</b> in your own user files, which a
             course copy, a term rollover and a sandbox cleanup all leave alone.
             ${saved.length ? esc(saved.length + ' file(s) saved so far.') : 'Nothing saved there yet.'}`
          : 'The Canvas copy is turned off, so this record exists on this PC only. '
            + 'If the PC is replaced, it goes with it.'}</p>
        <div class="row">
          <button class="btn" type="button" id="recSync"${d.to_canvas ? '' : ' disabled'}
            title="Uploads any month that has changed">Save to Canvas now</button>
          <button class="btn" type="button" id="recCheck">Check the record</button>
          <a class="btn" href="/api${base(cid)}/file" download>Download this month</a>
          <button class="btn sm" type="button" id="recToggle">${d.to_canvas
            ? 'Stop keeping it in Canvas' : 'Keep it in Canvas'}</button>
        </div>
      </div>`;
    $('#recCheck').onclick = () => verify(cid, true);
    const sync = $('#recSync');
    if (sync) {
      sync.onclick = () => runJob('Saving the record to Canvas',
        () => api(base(cid) + '/sync', { body: { force: true } }),
        out => { setStatus((out && out.sentence_done) || 'saved', 'ok'); load(cid); });
    }
    $('#recToggle').onclick = () => {
      const want = !d.to_canvas;
      const change = () => api(base(cid) + '/setting', { body: { to_canvas: want } })
        .then(() => { setStatus(want ? 'kept in Canvas from now on' : 'the Canvas copy is off', 'ok'); load(cid); })
        .catch(err => setStatus(firstLine(err.message), 'err'));
      if (want) { change(); return; }
      /* The shell's own dialog, not the browser's: it is the one the rest of
         the Studio asks with, and it says what does and does not stop. */
      askConfirm({
        summary: 'Stop keeping this record in Canvas. It carries on being written on '
          + 'this PC. What stops is the copy that survives this PC being replaced.',
      }, change, { title: 'Stop the Canvas copy?', verb: 'Yes, keep it on this PC only',
        note: 'Nothing already saved in Canvas is removed.' });
    };
  }

  /* ------------------------------------------------------------ filters */
  function drawFilters(cid, d) {
    const host = $('#recFilters');
    if (!host) return;
    const f = S.record.filter;
    const people = d.students || [];
    const areas = [...new Set((d.rows || []).map(r => r.area).filter(Boolean))];
    host.innerHTML = `
      <label>Student
        <select class="termSel" id="recStudent">
          <option value="">everyone</option>
          ${people.map(p => `<option value="${esc(p.id || p.name)}"${
            String(f.student) === String(p.id || p.name) ? ' selected' : ''
          }>${esc(studentLabel(p))}</option>`).join('')}
        </select>
      </label>
      <label>Part of the Studio
        <select class="termSel" id="recArea">
          <option value="">all of it</option>
          ${areas.map(a => `<option value="${esc(a)}"${f.area === a ? ' selected' : ''}>${
            esc(AREA_WORDS[a] || a)}</option>`).join('')}
        </select>
      </label>
      <label>Month
        <select class="termSel" id="recMonth">
          <option value="">every month</option>
          ${(d.months || []).map(m => `<option value="${esc(m)}"${
            f.month === m ? ' selected' : ''}>${esc(m)}</option>`).join('')}
        </select>
      </label>
      <span class="muted" id="recCount"></span>`;
    $('#recStudent').onchange = ev => { f.student = ev.target.value; load(cid); };
    $('#recArea').onchange = ev => { f.area = ev.target.value; load(cid); };
    $('#recMonth').onchange = ev => { f.month = ev.target.value; load(cid); };
  }

  /* --------------------------------------------------------------- rows */
  function drawRows(d) {
    const host = $('#recRows');
    if (!host) return;
    const rows = d.rows || [];
    const count = $('#recCount');
    if (count) {
      count.textContent = rows.length
        ? `${rows.length} shown of ${d.entries} recorded`
        : `nothing matches, of ${d.entries} recorded`;
    }
    if (!rows.length) {
      /* emptyState hands back an element, not a string. Assigned to innerHTML
         it stringifies to "[object HTMLDivElement]", which is what the page
         was showing where the empty state should have been. */
      host.replaceChildren(emptyState(d.entries
        ? 'Nothing in the record matches those filters.'
        : 'Nothing has been recorded in this course yet. Every change the Studio '
          + 'pushes to Canvas is written here as it happens.'));
      return;
    }
    host.innerHTML = `<div class="gwTableWrap"><table class="gwTable recTbl">
      <caption class="srOnly">Actions this tool took, newest first</caption>
      <thead><tr>
        <th scope="col">When</th><th scope="col">Part</th><th scope="col">What happened</th>
        <th scope="col">Students</th><th scope="col">By</th>
      </tr></thead>
      <tbody>${rows.map(rowHtml).join('')}</tbody></table></div>`;
  }

  function rowHtml(r) {
    const people = r.students || [];
    const who = people.length
      ? people.map(p => `<span class="pill">${esc(studentLabel(p))}${
          p.extra_time ? esc(' +' + p.extra_time + ' min') : ''}${
          p.score != null && p.score !== '' ? esc(' ' + p.score) : ''}</span>`).join(' ')
      : '<span class="muted">—</span>';
    const bad = r.result && r.result !== 'ok';
    return `<tr${bad ? ' class="warnRow"' : ''}>
      <td class="nowrap">${esc(fmtDate(r.at))}</td>
      <td>${esc(AREA_WORDS[r.area] || r.area || '')}</td>
      <td>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(nickSentence(r.sentence, people))}</a>`
        : esc(nickSentence(r.sentence, people))}${bad ? ' <span class="pill warn">Canvas refused it</span>' : ''}</td>
      <td>${who}</td>
      <td class="muted nowrap">${esc((r.actor || {}).name || '')}</td>
    </tr>`;
  }

  registerArea({
    id: 'record', label: 'Record', zone: 'record',
    open: (courseId, rest) => openRecord(courseId, rest),
    badge: () => null,
  });
  window.openRecord = openRecord;
})();
