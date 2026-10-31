/* assistant.js: the Assistant panel. One conversation per course, in plain
   words, with every change to the live course stopping at an Allow / Deny card
   that shows the exact command.

   The transcript is built from GET .../events?since=<seq>, polled at 700 ms
   while this view is open and stopped in onLeave. Nothing here decides what is
   safe: the gate in the Claude session does that, and this page shows the
   gate's own sentence rather than the model's description of it. */
(function () {
  'use strict';

  S.assistant = S.assistant || {};
  const asstBase = cid => '/assistant/' + encodeURIComponent(cid);
  const asstMem = () => S.assistant;
  const asstClamp = (text, n) => {
    const s = String(text == null ? '' : text);
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  };
  const asstClock = secs => {
    const s = Math.max(0, Math.round(secs || 0));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  };

  /* ------------------------------------------------------------- the area */
  async function openAssistant(courseId, rest) {
    if (typeof loadNicks === 'function') loadNicks();
    const mem = asstMem();
    mem.courseId = String(courseId);
    mem.seq = 0;
    mem.pending = [];
    mem.answering = null;
    mem.draftSeen = null;
    mem.draftPlaced = {};
    showView('area');
    await courseCrumbs(courseId, [{ label: 'Assistant' }]);
    if (String(mem.courseId) !== String(courseId)) return;
    $('#headerActions').innerHTML = '';
    areaHead('Assistant', 'Ask for work in plain words. Every change to the live course '
      + 'stops here for your Allow, with the exact command shown.');
    areaTabs([], null, null);

    const body = $('#areaBody');
    body.innerHTML = `<div class="asst">
      <div class="asstMain">
        <div id="asstSync"></div>
        <div class="asstChips" id="asstChips"></div>
        <div class="asstLog" id="asstLog" role="log" aria-live="polite" aria-label="The conversation"></div>
        <div id="asstNameFix"></div>
        <div class="asstCompose">
          <label class="srOnly" for="asstText">What do you want done?</label>
          <div class="asstBox">
            <textarea id="asstText" autocomplete="off" role="combobox" aria-expanded="false"
              aria-autocomplete="list" aria-controls="asstMention"
              placeholder="Tell it what to do. Type @ for a student. Enter sends; Shift and Enter make a new line."></textarea>
            <ul class="asstMention" id="asstMention" role="listbox" hidden
              aria-label="Students on this roster"></ul>
          </div>
          <div class="col">
            <div class="asstModes" id="asstModes" role="radiogroup" aria-label="Assistant mode"></div>
            <label class="asstModelPick"><span class="srOnly">Model</span>
              <select id="asstModel" title="Claude model for this conversation"></select>
            </label>
            <button class="btn ai" type="button" id="asstSend">Send</button>
            <button class="btn" type="button" id="asstStop" disabled title="Nothing is running">Stop</button>
            <button class="btn" type="button" id="asstNew">New conversation</button>
          </div>
        </div>
        <div class="asstFoot">
          <span id="asstState">Not started.</span>
          <span class="spacer"></span>
          <span class="hint" id="asstNames"></span>
          <span class="hint" id="asstModeHint">Nothing reaches Canvas without an Allow.</span>
        </div>
      </div>
      <aside class="asstRail" aria-label="This conversation">
        <div class="card"><div class="t">Student names</div>
          <p class="muted" id="asstNamesNote">Checking the roster…</p>
          <p class="muted" id="asstNamesHint"></p>
          <button class="btn sm" type="button" id="asstNamesRefresh"
            title="Reads the class list from Canvas. A read; nothing is written.">Re-read the roster</button>
        </div>
        <div class="card"><div class="t">This conversation</div><dl class="asstFacts" id="asstFacts"></dl></div>
        <div class="card"><div class="t">Drafts</div><div id="asstDrafts"><p class="muted">Checking…</p></div></div>
        <div class="card"><div class="t">What it changed</div><div id="asstLedger"></div></div>
      </aside>
    </div>`;

    $('#asstSend').onclick = () => asstSend(courseId);
    $('#asstNamesRefresh').onclick = () => asstNamesRefresh(courseId);
    $('#asstStop').onclick = () => asstStop(courseId);
    $('#asstNew').onclick = () => asstNew(courseId);
    const text = $('#asstText');
    text.oninput = () => mentionSync();
    text.onclick = () => mentionSync();
    text.onblur = () => setTimeout(mentionClose, 120);   // let a click land first
    text.onkeydown = ev => {
      if (mentionKey(ev)) return;
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.altKey && !ev.ctrlKey && !ev.metaKey) {
        ev.preventDefault();
        asstSend(courseId);
      }
    };

    let state = null;
    try { state = await api(asstBase(courseId) + '/state'); }
    catch (err) {
      $('#asstLog').innerHTML = `<div class="asstNotice err">The Assistant is not available on this
        server: ${esc(firstLine(err.message))}</div>`;
      return;
    }
    if (String(mem.courseId) !== String(courseId)) return;
    mem.headlines = state.headlines || {};
    asstSyncBanner(courseId, state);
    asstChips($('#asstChips'), state);
    asstNames(state);
    asstModes(state);
    asstFillModel(state);
    asstRoster(courseId);
    asstFacts(state);
    asstBoot(state);
    asstLedger(courseId);
    asstWatchDrafts(courseId);

    /* One poller for this view, stopped on the way out. A question still
       waiting when the person leaves is said out loud, because the only other
       place it shows is a card they have just walked away from. */
    const stop = poll(() => asstTick(courseId), 700);
    onLeave(() => {
      stop();
      if ((asstMem().pending || []).length) {
        setStatus('a change is waiting for your answer', 'err');
      }
    });
  }

  /* Quick jobs fill the box; they never send. The person reads what they are
     about to ask for, and edits it, before anything starts. */
  function asstChips(host, state) {
    const jobs = state.quick_jobs || [];
    if (!host || !jobs.length) { if (host) host.innerHTML = ''; return; }
    host.innerHTML = '<span class="lbl">Start with:</span>' + jobs.map((j, i) =>
      `<button class="chip" type="button" data-job="${i}" title="Puts this in the box. It does not send it.">${esc(j.label)}</button>`).join('');
    host.querySelectorAll('[data-job]').forEach(btn => {
      btn.onclick = () => {
        const job = jobs[+btn.dataset.job];
        const box = $('#asstText');
        if (!job || !box) return;
        box.value = job.prompt;
        box.focus();
        box.setSelectionRange(box.value.length, box.value.length);
        setStatus('put in the box, not sent', 'ok');
      };
    });
  }

  /* ----------------------------------------------------------- @ a student */
  /* The swap only fires on a spelling the roster knows, so the surest way to
     get it right is not to type the name at all. `@` offers the roster and
     inserts the name exactly as Canvas spells it; from there the ordinary
     exact match does the work, and there is no second protocol to keep honest.

     The list lives above the box rather than at the caret: a textarea has no
     caret coordinates without measuring the text in a mirror element, and a
     fixed position that always works beats a clever one that sometimes does. */
  const MENTION = /@([\p{L}][\p{L}'\-. ]{0,28}|)$/u;

  function mentionState() {
    const mem = asstMem();
    mem.mention = mem.mention || { open: false, hits: [], pick: 0, from: 0, to: 0 };
    return mem.mention;
  }

  function mentionClose() {
    const list = $('#asstMention');
    const box = $('#asstText');
    mentionState().open = false;
    if (list) { list.hidden = true; list.innerHTML = ''; }
    if (box) box.setAttribute('aria-expanded', 'false');
  }

  /* Scores the roster against what has been typed after the @. A name that
     starts with it comes first, then one where a word starts with it, then
     anything that merely contains it. */
  function mentionRank(people, query) {
    const q = query.trim().toLowerCase();
    if (!q) return people.slice(0, 8);
    const scored = [];
    for (const p of people) {
      const name = [p.name, p.sortable, studentLabel(p)].join(' ').toLowerCase();
      const words = name.split(/\s+/);
      let rank = null;
      if (name.startsWith(q)) rank = 0;
      else if (words.some(w => w.startsWith(q))) rank = 1;
      else if (name.includes(q)) rank = 2;
      if (rank != null) scored.push([rank, name, p]);
    }
    scored.sort((a, b) => (a[0] - b[0]) || a[1].localeCompare(b[1]));
    return scored.slice(0, 8).map(s => s[2]);
  }

  function mentionSync() {
    const box = $('#asstText');
    const list = $('#asstMention');
    const mem = asstMem();
    const st = mentionState();
    if (!box || !list) return;
    const people = mem.roster || [];
    const caret = box.selectionStart;
    const before = box.value.slice(0, caret);
    const m = people.length ? MENTION.exec(before) : null;
    if (!m) { mentionClose(); return; }

    const hits = mentionRank(people, m[1] || '');
    st.from = caret - m[0].length;
    st.to = caret;
    st.hits = hits;
    st.pick = 0;
    if (!hits.length) {
      list.hidden = false;
      list.innerHTML = `<li class="asstMentionNone" role="option" aria-disabled="true">
        Nobody on this roster matches "${esc(m[1] || '')}".</li>`;
      st.open = true;
      box.setAttribute('aria-expanded', 'true');
      return;
    }
    list.hidden = false;
    list.innerHTML = hits.map((p, i) => `<li role="option" data-i="${i}"
      id="asstMention-${i}" aria-selected="${i === 0}">${esc(studentLabel(p))}</li>`).join('');
    list.querySelectorAll('[data-i]').forEach(li => {
      li.onmousedown = ev => { ev.preventDefault(); mentionTake(+li.dataset.i); };
    });
    st.open = true;
    box.setAttribute('aria-expanded', 'true');
    box.setAttribute('aria-activedescendant', 'asstMention-0');
  }

  function mentionMove(step) {
    const st = mentionState();
    const list = $('#asstMention');
    if (!st.hits.length) return;
    st.pick = (st.pick + step + st.hits.length) % st.hits.length;
    list.querySelectorAll('[data-i]').forEach(li => {
      li.setAttribute('aria-selected', String(+li.dataset.i === st.pick));
    });
    const box = $('#asstText');
    if (box) box.setAttribute('aria-activedescendant', 'asstMention-' + st.pick);
  }

  /* Puts the roster's own spelling into the box, in place of the @ and
     whatever was typed after it. */
  function mentionTake(index) {
    const st = mentionState();
    const box = $('#asstText');
    const person = st.hits[index == null ? st.pick : index];
    if (!person || !box) { mentionClose(); return; }
    const head = box.value.slice(0, st.from);
    const tail = box.value.slice(st.to);
    const shown = studentLabel(person);
    const insert = shown + (tail.startsWith(' ') ? '' : ' ');
    box.value = head + insert + tail;
    const caret = head.length + insert.length;
    box.setSelectionRange(caret, caret);
    mentionClose();
    box.focus();
    setStatus(shown + ' goes out as ' + (person.tag || 'a tag'), 'ok');
  }

  /* True when the key belonged to the menu, so the composer leaves it alone.
     Enter has to be claimed here or picking a name would send the message. */
  function mentionKey(ev) {
    const st = mentionState();
    if (!st.open) return false;
    if (ev.key === 'ArrowDown') { ev.preventDefault(); mentionMove(1); return true; }
    if (ev.key === 'ArrowUp') { ev.preventDefault(); mentionMove(-1); return true; }
    if (ev.key === 'Escape') { ev.preventDefault(); mentionClose(); return true; }
    if ((ev.key === 'Enter' || ev.key === 'Tab') && st.hits.length) {
      ev.preventDefault();
      mentionTake();
      return true;
    }
    return false;
  }

  async function asstRoster(courseId) {
    try {
      if (typeof loadNicks === 'function') await loadNicks();
      const out = await api(asstBase(courseId) + '/roster');
      const mem = asstMem();
      if (String(mem.courseId) === String(courseId)) mem.roster = out.students || [];
    } catch (_) { /* the picker is a convenience; typing the name still works */ }
  }

  /* What the name swap is doing, said where it is being relied on. A person
     will only type a student's name here if they can see that it is handled,
     so this is not decoration. */
  function asstNames(state) {
    const info = state.names || {};
    const note = $('#asstNamesNote');
    const foot = $('#asstNames');
    if (note) note.textContent = info.note || '';
    if (foot) {
      foot.textContent = info.enabled
        ? `${info.students} names swapped for tags`
        : 'Names are not being swapped';
      foot.className = 'hint' + (info.enabled ? '' : ' warn');
    }
  }

  function asstNamesRefresh(courseId) {
    const btn = $('#asstNamesRefresh');
    if (btn) { btn.disabled = true; btn.textContent = 'Reading…'; }
    api(asstBase(courseId) + '/names/refresh', { body: {} })
      .then(info => {
        asstNames({ names: info });
        setStatus(info.students
          ? info.students + ' students on the roster' : 'no roster came back', 'ok');
      })
      .catch(err => setStatus('could not read the roster: ' + firstLine(err.message), 'err'))
      .finally(() => {
        if (btn) { btn.disabled = false; btn.textContent = 'Re-read the roster'; }
      });
  }

  function asstFacts(state) {
    const host = $('#asstFacts');
    if (!host) return;
    const rows = [
      ['Course', state.course_name || state.course_id],
      ['Model', state.model || 'Claude Code default'],
      ['Session', state.alive ? (state.busy ? 'working' : 'open, waiting for you') : 'not running'],
      ['Started', state.started ? fmtDate(state.started) : 'not yet'],
      ['Last used', state.last_used ? fmtDate(state.last_used) : '—'],
      ['Working folder', state.workspace || '', true],
    ];
    host.innerHTML = rows.map(([k, v, mono]) => `<dt>${esc(k)}</dt>
      <dd${mono ? ' class="mono"' : ''}>${esc(v || '—')}</dd>`).join('');
    if (state.claude === false) {
      banner('err', '<b>Claude Code is not on this PC.</b> The Assistant needs the '
        + '<code>claude</code> command; install it and reopen this page.');
    }
  }

  /* Content the Assistant just drafted lives in build/drafts, not in the
     transcript. Watch that folder while this view is open: new drafts get a
     preview in the log, and the rail always lists them with a Canvas link
     once they have been placed. */
  const ASST_KIND = {
    page: 'Page', syllabus: 'Syllabus', assignment: 'Assignment',
    discussion: 'Discussion', quiz: 'Quiz', 'study-guide': 'Study guide',
  };

  function asstDraftUrl(d) {
    return (d && d.placed && d.placed.html_url) || '';
  }

  async function asstWatchDrafts(courseId) {
    const mem = asstMem();
    let rows;
    try {
      // First look at this view hydrates unpublished drafts from Canvas user
      // files. Later ticks only re-read the local list so we do not pull every
      // 700 ms while the conversation is open.
      if (!mem.draftSeen) {
        const st = await api('/build/' + encodeURIComponent(courseId) + '/state');
        rows = st.drafts || [];
      } else {
        rows = (await api('/build/' + encodeURIComponent(courseId) + '/drafts')).drafts || [];
      }
    }
    catch (_) { return; }
    if (String(mem.courseId) !== String(courseId) || !$('#asstDrafts')) return;
    asstDraftsRail(courseId, rows);
    if (!mem.draftSeen) {
      mem.draftSeen = new Set(rows.map(d => String(d.id)));
      mem.draftPlaced = {};
      rows.forEach(d => { mem.draftPlaced[d.id] = !!asstDraftUrl(d); });
      return;
    }
    rows.forEach(d => {
      const id = String(d.id);
      const nowPlaced = !!asstDraftUrl(d);
      if (!mem.draftSeen.has(id)) {
        mem.draftSeen.add(id);
        mem.draftPlaced[id] = nowPlaced;
        asstDraftIntoLog(courseId, d, nowPlaced
          ? 'Placed in the course. Preview it here, or open it in Canvas.'
          : 'Draft saved. Preview it here before it is placed in the course.');
      } else if (nowPlaced && !mem.draftPlaced[id]) {
        mem.draftPlaced[id] = true;
        asstDraftIntoLog(courseId, d, 'Now in Canvas. Preview it here, or open the live page.');
      }
    });
  }

  function asstDraftsRail(courseId, rows) {
    const host = $('#asstDrafts');
    if (!host) return;
    if (!rows.length) {
      host.innerHTML = '<p class="muted">No drafts yet. Ask it to write a page or assignment and the preview lands here.</p>';
      return;
    }
    host.innerHTML = `<ul class="asstDraftList">${rows.slice(0, 8).map(d => {
      const canvas = asstDraftUrl(d);
      const build = '#/c/' + courseId + '/build/draft/' + encodeURIComponent(d.id);
      return `<li>
        <button type="button" class="asstDraftOpen" data-id="${esc(d.id)}"
          title="Preview this draft here">${esc(d.title || '(untitled)')}</button>
        <span class="hint">${esc(ASST_KIND[d.kind] || d.kind || '')}${
          canvas ? ' · in Canvas' : ' · draft'}</span>
        <span class="asstDraftLinks">
          <a href="${esc(build)}">Build</a>
          ${canvas ? `<a href="${esc(canvas)}" target="_blank" rel="noopener">Canvas</a>` : ''}
        </span>
      </li>`;
    }).join('')}</ul>`;
    const byId = Object.fromEntries(rows.map(d => [String(d.id), d]));
    host.querySelectorAll('.asstDraftOpen').forEach(btn => {
      btn.onclick = () => asstPreviewDraft(courseId, byId[btn.dataset.id]);
    });
  }

  function asstDraftIntoLog(courseId, d, note) {
    const log = $('#asstLog');
    if (!log) return;
    const empty = log.querySelector('.asstEmpty');
    if (empty) empty.remove();
    const canvas = asstDraftUrl(d);
    const build = '#/c/' + courseId + '/build/draft/' + encodeURIComponent(d.id);
    const el = asstAdd(log, `<article class="asstDraft" data-draft="${esc(d.id)}">
      <header>
        <b>${esc(d.title || '(untitled)')}</b>
        <span class="hint">${esc(ASST_KIND[d.kind] || d.kind || '')}</span>
      </header>
      ${note ? `<p class="asstDraftNote">${esc(note)}</p>` : ''}
      <div class="paperFrame canvasPage asstDraftPaper"><p class="muted">Reading the preview…</p></div>
      <div class="asstDraftActs">
        <a class="btn sm" href="${esc(build)}">Open in Build</a>
        ${canvas ? `<a class="btn sm" href="${esc(canvas)}" target="_blank" rel="noopener">Open in Canvas</a>`
                 : '<span class="hint">Not in Canvas yet</span>'}
      </div>
    </article>`);
    asstLoadPreview(courseId, d.id, el && el.querySelector('.asstDraftPaper'));
    log.scrollTop = log.scrollHeight;
  }

  function asstPreviewDraft(courseId, d) {
    if (!d) return;
    const host = $('#modalHost');
    if (!host) return;
    const canvas = asstDraftUrl(d);
    const build = '#/c/' + courseId + '/build/draft/' + encodeURIComponent(d.id);
    host.innerHTML = `<div class="modalBack"><div class="modal wide">
      <h3>${esc(d.title || 'Draft')}</h3>
      <div class="sub">${esc(ASST_KIND[d.kind] || d.kind || '')}${
        canvas ? '' : ' · not in Canvas yet'}</div>
      <div class="paperFrame canvasPage asstDraftPaper"><p class="muted">Reading the preview…</p></div>
      <div class="foot">
        <a class="btn" href="${esc(build)}">Open in Build</a>
        ${canvas ? `<a class="btn" href="${esc(canvas)}" target="_blank" rel="noopener">Open in Canvas</a>` : ''}
        <span class="spacer"></span>
        <button class="btn" type="button" id="asstPrevClose">Close</button>
      </div>
    </div></div>`;
    const close = () => { host.innerHTML = ''; };
    $('#asstPrevClose').onclick = close;
    const back = host.querySelector('.modalBack');
    if (back) back.addEventListener('click', ev => { if (ev.target === back) close(); });
    host.querySelectorAll('a[href^="#/"]').forEach(a => { a.addEventListener('click', close); });
    asstLoadPreview(courseId, d.id, host.querySelector('.asstDraftPaper'));
  }

  function asstLoadPreview(courseId, id, paper) {
    if (!paper) return;
    api('/build/' + encodeURIComponent(courseId) + '/drafts/' +
        encodeURIComponent(id) + '/preview')
      .then(p => {
        if (!paper.isConnected) return;
        paper.innerHTML = '<div class="canvasHtml asIs">' +
          ((p && p.html) || '<p class="muted">This draft has no body yet.</p>') + '</div>';
      })
      .catch(err => {
        if (paper.isConnected) paper.innerHTML = '<p class="muted">Could not load the preview: ' +
          esc(firstLine(err.message)) + '</p>';
      });
  }

  async function asstLedger(courseId) {
    const host = $('#asstLedger');
    if (!host) return;
    let rows = [];
    try { rows = (await api('/courses/' + encodeURIComponent(courseId) + '/ledger?limit=8')).rows || []; }
    catch (_) { /* the rail is a courtesy; the conversation is the point */ }
    if (!host.isConnected) return;
    if (!rows.length) {
      host.innerHTML = '<p class="ledgerEmpty">Nothing has been written to Canvas from here yet.</p>';
      return;
    }
    host.innerHTML = `<ul class="ledger">${rows.map(r => `<li>
      <span class="when">${esc(fmtDate(r.at) || '')}</span>
      ${esc(r.sentence || '')}
      ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">open</a>` : ''}</li>`).join('')}</ul>`;
  }

  function asstSyncBanner(courseId, state) {
    const host = $('#asstSync');
    if (!host) return;
    const sync = (state && state.sync) || {};
    const did = sync.did || sync.action || '';
    if (did === 'diverged') {
      const who = sync.remote_machine || 'another computer';
      host.innerHTML = `<div class="callout warn">This conversation also changed on
        ${esc(String(who))}. Nothing was overwritten.
        <div class="foot" style="margin-top:.6rem">
          <button class="btn" id="asstTakeRemote" type="button">Use the Canvas copy</button>
          <button class="btn" id="asstTakeLocal" type="button">Keep this computer's chat</button>
        </div></div>`;
      const remote = $('#asstTakeRemote'), local = $('#asstTakeLocal');
      if (remote) remote.onclick = () => asstResolveSync(courseId, 'remote');
      if (local) local.onclick = () => asstResolveSync(courseId, 'local');
      return;
    }
    if (did === 'error') {
      host.innerHTML = `<div class="callout warn">Could not copy this chat to your Canvas
        files: ${esc(sync.reason || sync.detail || 'unknown error')}. It is still saved here.</div>`;
      return;
    }
    if (did === 'picked_up' || did === 'seeded' || did === 'sent' || did === 'in_sync') {
      host.innerHTML = '<p class="hint">Saved here and in your Canvas files, so another PC signed in as you can pick it up.</p>';
      return;
    }
    host.innerHTML = '';
  }

  function asstResolveSync(courseId, take) {
    api(asstBase(courseId) + '/sync', { body: { take } })
      .then(() => openAssistant(courseId, []))
      .catch(err => setStatus('could not settle the two copies: ' + firstLine(err.message), 'err'));
  }

  /* What the ring has forgotten, written to disk at the time: shown once, at
     the top, so a restarted server does not look like a lost conversation. */
  function asstBoot(state) {
    const log = $('#asstLog');
    if (!log) return;
    log.innerHTML = '';
    if (state.last_error) {
      log.insertAdjacentHTML('beforeend', `<div class="asstNotice err">${esc(state.last_error)}</div>`);
    }
    if (state.history) {
      log.insertAdjacentHTML('beforeend', `<div class="asstHistory"><div class="hdr">Earlier</div>${
        esc(state.history.trim())}</div>`);
    }
    if (!state.history && !state.seq) {
      log.insertAdjacentHTML('beforeend', `<div class="asstEmpty">Nothing has been asked for in this
        course yet. Say what you want done — "make this course ADA compliant", "add a study
        guide for the midterm to week 8" — and it works in a folder of its own. Every change to
        the live course stops for your Allow first, and the exact command is shown before you
        answer.</div>`);
    }
    asstBusy(state);
  }

  function asstBusy(state) {
    const send = $('#asstSend'), stop = $('#asstStop'), line = $('#asstState');
    if (!send) return;
    const busy = !!state.busy;
    send.disabled = busy;
    send.title = busy ? 'Claude is still working on the last message' : '';
    stop.disabled = !busy;
    stop.title = busy ? 'Stop this turn. The conversation is kept.' : 'Nothing is running';
    if (line) {
      line.className = busy ? 'working' : '';
      line.textContent = busy ? 'Working…'
        : state.alive ? 'Waiting for you.' : 'Not started.';
    }
  }

  /* ----------------------------------------------------------- the poller */
  async function asstTick(courseId) {
    const mem = asstMem();
    if (String(mem.courseId) !== String(courseId)) return;
    let data;
    try { data = await api(asstBase(courseId) + '/events?since=' + (mem.seq || 0)); }
    catch (_) { return; }                       // the next tick tries again
    if (String(mem.courseId) !== String(courseId) || !$('#asstLog')) return;
    if (data.gap) {                             // the ring forgot the middle
      mem.seq = 0;
      const log = $('#asstLog');
      if (log) log.innerHTML = '';
      mem.live = null;
      return;
    }
    // Only what is genuinely new. The server filters by `since` already; this
    // is here so a repeat can never print the same line of the conversation
    // twice, which is the one thing a transcript must not do.
    (data.events || []).forEach(ev => {
      if (ev.seq != null && ev.seq <= (mem.seq || 0)) return;
      if (ev.seq != null) mem.seq = ev.seq;
      asstAppend(ev);
    });
    if (data.seq != null && data.seq > (mem.seq || 0)) mem.seq = data.seq;
    mem.pending = data.pending || [];
    asstBusy(data);
    asstPermission(courseId);
    asstWatchDrafts(courseId);
  }

  /* ------------------------------------------------------- the transcript */
  function asstAppend(ev) {
    const log = $('#asstLog');
    if (!log) return;
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
    const mem = asstMem();
    const empty = log.querySelector('.asstEmpty');
    if (empty && ev.kind !== 'init') empty.remove();

    if (ev.kind === 'user') {
      mem.live = null;
      asstAdd(log, `<div class="asstMsg you">${esc(ev.text || '')}</div>`);
    } else if (ev.kind === 'text') {
      if (!mem.live || !mem.live.isConnected) {
        mem.live = asstAdd(log, '<div class="asstMsg claude live"></div>');
      }
      mem.live.textContent += ev.text || '';
    } else if (ev.kind === 'text_end') {
      if (mem.live) mem.live.classList.remove('live');
      mem.live = null;
    } else if (ev.kind === 'tool') {
      mem.live = null;
      asstTool(log, ev);
    } else if (ev.kind === 'tool_result') {
      asstToolResult(log, ev);
    } else if (ev.kind === 'permission_answered') {
      mem.live = null;
      let word;
      if (ev.via === 'auto') word = 'Allowed in Auto mode';
      else if (ev.via === 'plan') word = ev.decision === 'allow'
        ? 'Read in Plan mode' : 'Skipped in Plan mode';
      else if (ev.via === 'timeout' || (ev.auto && ev.decision !== 'allow'))
        word = 'No answer, so it was denied';
      else word = ev.decision === 'allow' ? 'You allowed it' : 'You denied it';
      asstAdd(log, `<div class="asstDecision ${esc(ev.decision === 'allow' ? 'allow' : 'deny')}">
        <b>${esc(word)}</b> ${esc(asstClamp(ev.what || '', 120))}</div>`);
      if (ev.decision === 'allow') asstLedger(asstMem().courseId);
    } else if (ev.kind === 'notice') {
      asstAdd(log, `<div class="asstNotice">${esc(ev.text || '')}</div>`);
    } else if (ev.kind === 'result' && ev.error) {
      asstAdd(log, `<div class="asstNotice err">Claude stopped with an error.
        ${esc(asstClamp(ev.text || '', 200))}</div>`);
    } else if (ev.kind === 'exit' && ev.text) {
      asstAdd(log, `<div class="asstNotice err">${esc(ev.text)}</div>`);
    }
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  function asstAdd(log, html) {
    log.insertAdjacentHTML('beforeend', html);
    return log.lastElementChild;
  }

  /* Tool rows are collapsed, and a run of them is one row that says how many.
     The work is not the point; what it changed is, and that has its own card. */
  function asstTool(log, ev) {
    let group = log.lastElementChild;
    if (!group || !group.classList || !group.classList.contains('asstTools')) {
      group = asstAdd(log, `<details class="asstTools"><summary>
        <span class="count">1</span> <span class="what">step</span></summary></details>`);
    }
    const asking = ev.decision !== 'allow';
    group.insertAdjacentHTML('beforeend', `<div class="asstTool${asking ? ' asking' : ''}"
        data-tool="${esc(ev.id || '')}">
        <span class="name">${esc(ev.name || 'tool')}</span>
        <span class="sum">${esc(ev.summary || ev.what || '')}</span>
        ${ev.detail ? `<span class="det">${esc(asstClamp(ev.detail, 300))}</span>` : ''}
      </div>`);
    const n = group.querySelectorAll('.asstTool').length;
    group.querySelector('.count').textContent = String(n);
    group.querySelector('.what').textContent = n === 1 ? 'step' : 'steps';
  }

  function asstToolResult(log, ev) {
    if (!ev.error) return;                      // a step that worked says nothing
    const row = log.querySelector(`.asstTool[data-tool="${CSS.escape(String(ev.id || ''))}"]`);
    if (!row) return;
    row.insertAdjacentHTML('beforeend', `<span class="err">${esc(asstClamp(ev.text || 'it failed', 200))}</span>`);
    const group = row.closest('.asstTools');
    if (group) group.open = true;               // an error is not something to fold away
  }

  /* ------------------------------------------------------ Allow  /  Deny */
  /* One card at a time, in the transcript where the question was asked. Deny
     has the focus, Allow is the dangerous one, and the countdown says what
     happens if nobody answers: it is denied. */
  function asstPermission(courseId) {
    const mem = asstMem();
    const log = $('#asstLog');
    if (!log) return;
    const pending = mem.pending || [];
    const card = log.querySelector('.asstPerm');
    const want = pending[0] || null;

    if (!want) {
      if (card) card.remove();
      return;
    }
    if (card && card.dataset.req === want.request_id) {
      const left = want.expires_at - (Date.now() / 1000);
      const clock = card.querySelector('.countdown b');
      if (clock) {
        clock.textContent = left > 0 ? asstClock(left) : 'over';
        clock.parentElement.classList.toggle('soon', left < 60);
      }
      return;
    }
    if (card) card.remove();

    const headline = (mem.headlines || {})[want.kind]
      || 'Claude wants to use a tool that may change something';
    log.insertAdjacentHTML('beforeend', `<div class="asstPerm" data-req="${esc(want.request_id)}"
        role="group" aria-label="A change is waiting for your answer">
      <h3>${esc(headline)}</h3>
      <p class="what"><b>What:</b> ${esc(want.what || want.summary || 'it did not say')}</p>
      ${want.description ? `<p class="says">Claude describes it as: ${esc(want.description)}</p>` : ''}
      ${want.why ? `<p class="why">Why it asks: ${esc(want.why)}</p>` : ''}
      ${want.leaks_names ? `<p class="why warn">Real names: the swap covers what you type
        and what comes back, not what Claude reads. Anything in this file goes to
        Anthropic exactly as it is written.</p>` : ''}
      <pre class="cmd">${esc(want.command || '')}</pre>
      <div class="row">
        <button class="btn" type="button" data-deny="1">Deny</button>
        <button class="btn danger" type="button" data-allow="1">Allow it once</button>
        <span class="countdown">No answer in <b>${esc(asstClock(want.expires_at - Date.now() / 1000))}</b>
          means Deny</span>
      </div>
    </div>`);
    const fresh = log.querySelector('.asstPerm');
    fresh.querySelector('[data-deny]').onclick = () => asstAnswer(courseId, want.request_id, 'deny');
    fresh.querySelector('[data-allow]').onclick = () => asstAnswer(courseId, want.request_id, 'allow');
    fresh.querySelector('[data-deny]').focus();
    announce(headline);
    log.scrollTop = log.scrollHeight;
  }

  function asstAnswer(courseId, requestId, decision) {
    const mem = asstMem();
    if (mem.answering === requestId) return;
    mem.answering = requestId;
    const card = $('#asstLog') && $('#asstLog').querySelector('.asstPerm');
    if (card) card.querySelectorAll('button').forEach(b => { b.disabled = true; });
    api(asstBase(courseId) + '/answer', { body: { request_id: requestId, decision } })
      .then(() => {
        mem.pending = (mem.pending || []).filter(p => p.request_id !== requestId);
        setStatus(decision === 'allow' ? 'allowed, once' : 'denied', decision === 'allow' ? 'ok' : 'err');
        asstPermission(courseId);
      })
      .catch(err => {
        setStatus('could not answer: ' + firstLine(err.message), 'err');
        if (card) card.querySelectorAll('button').forEach(b => { b.disabled = false; });
      })
      .finally(() => { mem.answering = null; });
  }

  /* ------------------------------------------------- a name spelled wrong */
  /* The server stops a message whose name is a keystroke away from somebody on
     the roster, because a name spelled almost right matches nothing and would
     go out as typed while the person believed it was swapped. Telling them off
     is not enough: the fix belongs one click away, and so does the way past it
     when the checker is the one that is wrong. */
  /* Put `suggestion` in, over whichever words around `wrote` the suggestion
     already accounts for.

     Only one word of a name is usually wrong, and the check reports that word
     on its own. Swapping just it for the whole name duplicates the half that
     was right: "Jordan Vancc" became "Jordan Jordan Vance". So the neighbours
     are offered up too, longest run first, and the widest span that is really
     there is the one replaced. When no neighbour fits, the single word is
     replaced and nothing is duplicated because there was nothing to duplicate. */
  function repairName(text, wrote, suggestion) {
    const rx = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
    const parts = String(suggestion).trim().split(/\s+/);
    const tries = [];
    for (let i = 0; i < parts.length; i++) {
      tries.push({
        words: parts.length,
        body: parts.map((p, j) => (j === i ? rx(wrote) : rx(p))).join('\\s+'),
      });
    }
    tries.push({ words: 1, body: rx(wrote) });
    tries.sort((a, b) => b.words - a.words);
    for (const t of tries) {
      const pattern = new RegExp('(^|[^\\p{L}\\p{N}])' + t.body + '(?![\\p{L}\\p{N}])', 'giu');
      if (pattern.test(text)) {
        pattern.lastIndex = 0;
        return text.replace(pattern, (m, lead) => lead + suggestion);
      }
    }
    return text;
  }

  function asstNameFix(courseId, body) {
    const host = $('#asstNameFix');
    const box = $('#asstText');
    if (!host) return;
    /* Both refusals end the same way -- one word in the box is not the name of
       exactly one student -- so they offer the same repair. A misspelling has
       one candidate; a shared surname has two, and picking either is what the
       server asked for. */
    const near = []
      .concat((body && body.near) || [])
      .concat(((body && body.ambiguous) || []).flatMap(a =>
        (a.candidates || []).map(c => ({ wrote: a.wrote, suggestion: c }))));
    if (!near.length) { host.innerHTML = ''; return; }
    host.innerHTML = `<div class="asstFix" role="group" aria-label="A name needs fixing">
      <p>${esc(body.error || '')}</p>
      <div class="row">
        ${near.map((n, i) => `<button class="btn sm primary" type="button" data-fix="${i}"
          >Use ${esc(n.suggestion)}</button>`).join('')}
        ${body.can_send_anyway ? '<button class="btn sm" type="button" data-anyway="1" '
          + 'title="Send the message exactly as you typed it. No name in it will be swapped.">'
          + 'Send as typed</button>' : ''}
        <button class="btn sm" type="button" data-drop="1">Let me edit it</button>
      </div>
    </div>`;
    host.querySelectorAll('[data-fix]').forEach(b => {
      b.onclick = () => {
        const n = near[+b.dataset.fix];
        if (box && n) box.value = repairName(box.value, n.wrote, n.suggestion);
        host.innerHTML = '';
        asstSend(courseId);
      };
    });
    const anyway = host.querySelector('[data-anyway]');
    if (anyway) {
      anyway.onclick = () => { host.innerHTML = ''; asstSend(courseId, true); };
    }
    host.querySelector('[data-drop]').onclick = () => {
      host.innerHTML = '';
      if (box) box.focus();
    };
  }

  /* ------------------------------------------------------------- mode */
  const ASST_MODE = {
    plan: { label: 'Plan', title: 'Read and think. Anything that would change Canvas or this PC is refused.' },
    ask: { label: 'Ask', title: 'Content drafts it just wrote are read without asking. Live Canvas writes still need Allow.' },
    auto: { label: 'Auto', title: 'Local file reads run without asking. Live Canvas writes still need Allow.' },
  };

  function asstModes(state) {
    const host = $('#asstModes');
    const hint = $('#asstModeHint');
    if (!host) return;
    const mode = (state && state.mode) || 'ask';
    const mem = asstMem();
    mem.mode = mode;
    host.innerHTML = ['plan', 'ask', 'auto'].map(id => {
      const m = ASST_MODE[id];
      return `<button type="button" class="chip" role="radio" data-mode="${id}"
        aria-checked="${id === mode}" title="${esc(m.title)}">${esc(m.label)}</button>`;
    }).join('');
    host.querySelectorAll('[data-mode]').forEach(btn => {
      btn.onclick = () => asstSetMode(mem.courseId, btn.dataset.mode);
    });
    if (hint) hint.textContent = (state && state.mode_hint)
      || ASST_MODE[mode].title;
  }

  function asstFillModel(state) {
    const sel = $('#asstModel');
    if (!sel) return;
    const mem = asstMem();
    const models = (state && state.models && state.models.length)
      ? state.models
      : ((S.health && S.health.models) || ['opus', 'sonnet', 'haiku']);
    const current = (state && state.model) || mem.model || '';
    mem.models = models;
    mem.model = current;
    const opts = ['<option value="">Claude default</option>'].concat(
      models.map(m => `<option value="${esc(m)}"${m === current ? ' selected' : ''}>${esc(m)}</option>`));
    sel.innerHTML = opts.join('');
    sel.value = models.indexOf(current) >= 0 ? current : '';
    sel.onchange = () => asstSetModel(mem.courseId, sel.value);
  }

  function asstRestartHint(err, prefix) {
    if (err && err.status === 404) {
      return prefix + ': close the CourseForge Studio window and open it again so this control can talk to the server.';
    }
    return prefix + ': ' + firstLine(err && err.message);
  }

  function asstSetMode(courseId, mode) {
    const mem = asstMem();
    const prev = mem.mode || 'ask';
    asstModes({ mode, mode_hint: (ASST_MODE[mode] || ASST_MODE.ask).title });
    api(asstBase(courseId) + '/mode', { body: { mode } })
      .then(out => {
        asstModes(out);
        if (out.models) asstFillModel(out);
        setStatus(out.hint || ('mode: ' + out.mode), 'ok');
      })
      .catch(err => {
        asstModes({ mode: prev, mode_hint: (ASST_MODE[prev] || ASST_MODE.ask).title });
        setStatus(asstRestartHint(err, 'could not change mode'), 'err');
      });
  }

  function asstSetModel(courseId, model) {
    const mem = asstMem();
    const prev = mem.model || '';
    mem.model = model;
    api(asstBase(courseId) + '/mode', { body: { model } })
      .then(out => {
        asstFillModel(out);
        setStatus('model: ' + (out.model || 'Claude default'), 'ok');
      })
      .catch(err => {
        mem.model = prev;
        const sel = $('#asstModel');
        if (sel) sel.value = prev;
        setStatus(asstRestartHint(err, 'could not change model'), 'err');
      });
  }

  /* ------------------------------------------------------------- the verbs */
  function asstSend(courseId, allowNear) {
    const box = $('#asstText');
    if (!box) return;
    const text = (box.value || '').trim();
    if (!text) { setStatus('type something to send first', 'err'); box.focus(); return; }
    mentionClose();
    const send = $('#asstSend');
    send.disabled = true;
    const mem = asstMem();
    const payload = { text };
    if (allowNear) payload.allow_near = true;
    if (mem.model) payload.model = mem.model;
    api(asstBase(courseId) + '/send', { body: payload })
      .then(res => {
        box.value = '';
        box.focus();
        const fix = $('#asstNameFix');
        if (fix) fix.innerHTML = '';
        const mem = asstMem();
        if (res && res.seq != null && mem.seq > res.seq) mem.seq = 0;
        /* Say when a name was swapped, and which. Silence would leave the
           person guessing whether it happened, and guessing is exactly why
           someone stops typing names at all. */
        const swaps = (res && res.swapped) || [];
        const byTag = [...new Map(swaps.map(s => [s.tag, s])).values()];
        setStatus(byTag.length
          ? `sent, ${byTag.length === 1 ? '1 name' : byTag.length + ' names'} swapped for tags`
          : 'sent', 'ok');
        const hint = $('#asstNamesHint');
        if (hint) {
          hint.textContent = byTag.length
            ? 'Last message: ' + byTag.map(s => `${s.name} went as ${s.tag}`).join(', ') + '.'
            : '';
        }
      })
      .catch(err => {
        send.disabled = false;
        const body = (err && err.body) || {};
        /* A name the server would not guess at is not a failure, it is a
           question, so it gets the inline card rather than the red banner
           that means something broke. */
        if ((body.near || []).length || (body.ambiguous || []).length) {
          setStatus(firstLine(err.message), 'err');
          asstNameFix(courseId, body);
          if (box) box.focus();
          return;
        }
        setStatus(firstLine(err.message), 'err');
        banner('err', '<b>That message did not go.</b> ' + esc(err.message));
      });
  }

  function asstStop(courseId) {
    api(asstBase(courseId) + '/stop', { body: {} })
      .then(() => setStatus('stopped; the conversation is kept', 'ok'))
      .catch(err => setStatus('could not stop: ' + firstLine(err.message), 'err'));
  }

  function asstNew(courseId) {
    askConfirm({
      summary: 'Start a new conversation. Claude forgets this one; the files it made in the '
        + 'working folder stay, and nothing in Canvas changes.',
    }, () => {
      api(asstBase(courseId) + '/new', { body: {} }).then(() => {
        const mem = asstMem();
        mem.seq = 0;
        mem.live = null;
        mem.pending = [];
        const log = $('#asstLog');
        if (log) log.innerHTML = '';
        setStatus('new conversation', 'ok');
      }).catch(err => setStatus('could not start a new one: ' + firstLine(err.message), 'err'));
    }, { title: 'Forget this conversation?', verb: 'Yes, start fresh',
      note: 'Nothing in Canvas changes either way.' });
  }

  /* ------------------------------------------------------------ register */
  window.openAssistant = openAssistant;
  registerArea({
    id: 'assistant', label: 'Assistant', zone: 'ai', open: openAssistant,
    badge: hub => (hub && hub.assistant && hub.assistant.badge != null) ? hub.assistant.badge : null,
  });
  Object.assign(window.Studio || (window.Studio = {}), { openAssistant });
})();
