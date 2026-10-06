/* inbox.js: the Canvas Inbox.

   The chip in the header is painted once at boot and refreshed on a slow
   timer, because mail waiting is true wherever you are standing and every
   view clears #headerActions.

   The screen itself is a list of threads and one open thread beside it. Read
   with Claude fills in what the student is asking and drafts a reply into a
   box. The box is the only thing that can be sent, one thread at a time, and
   sending goes through the server's confirm gate like every other Canvas
   write: refused once with a sentence, sent on the second pass. There is no
   send-all and no rule that sends by itself.

   Selecting several threads and marking, archiving or deleting them is a
   different thing from replying to them, which is why it is offered here and
   sending in bulk still is not. None of it reaches a student: it changes your
   own copy of a thread, and the person who wrote it is never told. Deleting
   is the one that cannot be undone, so it says so and keeps its own red
   button. Shift-click takes a range and ctrl-click toggles one, the same as
   the grading roster, because a second way to mean the same thing is one more
   thing to remember.

   A reply can carry files: picked, dropped on the box, or pasted as a
   picture. Each waits on this computer until that reply is sent and reaches
   Canvas only after the confirmation, which names it. */
(function () {
  'use strict';

  S.inbox = S.inbox || { scope: '', threads: [], open: null, read: {}, drafts: {},
    told: {}, picked: new Set(), anchor: null, files: {} };
  const mem = () => S.inbox;
  const CHIP_MS = 120000;
  // The server's own limits (inbox.MAX_ATTACH_BYTES, MAX_ATTACH), checked here
  // first so a file that is too big is said at once, before it is read.
  const MAX_FILE = 10 * 1024 * 1024;
  const MAX_FILES = 10;
  // Pictures a chip can show from the file itself. Not SVG: a picture that can
  // carry script is a file here, not a thumbnail.
  const RASTER = /^image\/(png|jpe?g|gif|webp|bmp)$/i;
  let FILE_SEQ = 0;

  const SCOPES = [
    { id: '', label: 'Inbox' },
    { id: 'unread', label: 'Unread' },
    { id: 'archived', label: 'Archived' },
    { id: 'sent', label: 'Sent' },
  ];

  /* ------------------------------------------------------------- the chip */
  /* A number in the corner is never worth a banner, so a failed count says
     nothing at all and tries again on the next tick. */
  async function inboxChip() {
    const host = $('#inboxChip');
    if (!host) return;
    let info = null;
    try { info = await api('/inbox/unread'); } catch (_) { info = null; }
    if (!info || info.unread == null) { host.innerHTML = ''; return; }
    const n = +info.unread || 0;
    host.innerHTML = `<a class="btn sm inboxBtn${n ? ' lit' : ''}" href="#/inbox"
      title="${n ? n + ' unread in your Canvas Inbox' : 'Your Canvas Inbox'}">Inbox${
      n ? `<span class="n">${esc(n)}</span>` : ''}</a>`;
  }

  function startChip() {
    inboxChip();
    if (mem().chipTimer) clearInterval(mem().chipTimer);
    mem().chipTimer = setInterval(inboxChip, CHIP_MS);
  }

  /* ------------------------------------------------------------ the screen */
  async function openInbox(threadId) {
    const nicks = (typeof loadNicks === 'function') ? loadNicks() : null;
    showView('inbox');
    crumbs([{ label: 'Courses', href: '#/' }, { label: 'Inbox' }]);
    $('#headerActions').innerHTML =
      '<button class="btn" id="ibRefresh" title="Read the inbox from Canvas again">Refresh</button>';
    $('#ibRefresh').onclick = () => openInbox(mem().open);

    const host = $('#viewInbox');
    host.innerHTML = `<div class="sectionHead">
        <h2>Canvas Inbox</h2>
        <span class="hint" id="ibHint">Reading…</span>
        <span class="spacer"></span>
        <div class="chips" id="ibScopes"></div>
      </div>
      <p class="hint ibNote">Messages from students across every course. Claude reads a
        thread with the names taken out and drafts a reply; nothing is sent until you
        press Send on that reply.</p>
      <div id="ibTools"></div>
      <div class="ibCols">
        <div id="ibList">Loading…</div>
        <div id="ibPane"></div>
      </div>`;

    $('#ibScopes').innerHTML = SCOPES.map(s =>
      `<button class="chip" type="button" data-scope="${esc(s.id)}"
        aria-pressed="${s.id === mem().scope}">${esc(s.label)}</button>`).join('');
    $('#ibScopes').querySelectorAll('[data-scope]').forEach(b => {
      b.onclick = () => {
        // A different list: ids picked in one scope mean nothing in the next.
        mem().picked.clear();
        mem().anchor = null;
        mem().scope = b.dataset.scope;
        openInbox(null);
      };
    });

    let data;
    try {
      data = await api('/inbox?scope=' + encodeURIComponent(mem().scope));
    } catch (err) {
      $('#ibList').innerHTML = '';
      $('#ibList').appendChild(emptyState('Could not read the inbox: '
        + firstLine(err.message) + '. Nothing has been changed.'));
      $('#ibHint').textContent = '';
      return;
    }
    /* The route, not the view: Reports paints in this same section, so a slow
       inbox read finishing after someone has moved on to Reports would find
       S.view still saying 'inbox' and write into a screen that is no longer
       there. */
    if (!(S.route && S.route.parts && S.route.parts[0] === 'inbox')) return;
    if (nicks) await nicks;
    mem().threads = data.threads || [];
    $('#ibHint').textContent = `${mem().threads.length} thread${
      mem().threads.length === 1 ? '' : 's'}`
      + (data.unread ? ` · ${data.unread} unread` : '');
    // A thread that has gone (archived, deleted, or simply off the end of
    // this scope) must not stay selected and turn up in the next action.
    const here = new Set(mem().threads.map(t => String(t.id)));
    [...mem().picked].forEach(id => { if (!here.has(id)) mem().picked.delete(id); });
    drawList();
    drawTools();
    watchKeys();
    guardDrops();
    watchPaste();
    inboxChip();
    if (threadId) openThread(threadId);
    else $('#ibPane').appendChild(emptyState('Pick a thread on the left. Opening one '
      + 'marks it read.'));
  }

  function drawList() {
    const host = $('#ibList');
    if (!host) return;
    const rows = mem().threads;
    if (!rows.length) {
      host.innerHTML = '';
      host.appendChild(emptyState('Nothing in this view.'));
      return;
    }
    host.innerHTML = '<div class="ibList">' + rows.map((t, i) => {
      const who = (t.with || []).map(p => studentLabel(p) || p.tag).join(', ') || 'someone';
      const sel = mem().picked.has(String(t.id));
      return `<div class="ibRow${t.unread ? ' unread' : ''}${sel ? ' sel' : ''}${
        String(mem().open) === String(t.id) ? ' open' : ''}">
        <input type="checkbox" class="ibPick" data-i="${i}" ${sel ? 'checked' : ''}
          aria-label="Select the thread from ${esc(who)} about ${esc(t.subject)}">
        <button class="ibRowMain" type="button" data-i="${i}" data-id="${esc(t.id)}">
          <span class="ibTop"><span class="ibWho">${esc(who)}</span>
            ${t.course_name ? `<span class="ibCourse">${esc(t.course_name)}</span>` : ''}
            <span class="ibAgo">${esc(t.ago || '')}</span></span>
          <span class="ibSubj">${esc(t.subject)}</span>
          <span class="ibPrev">${esc(t.preview || '')}</span>
        </button>
      </div>`;
    }).join('') + '</div>';
    host.querySelectorAll('.ibRowMain').forEach(b => {
      b.onclick = ev => {
        const i = +b.dataset.i;
        // Held keys mean "pick", not "open". A plain click is still the way in
        // to a thread, and it drops whatever was selected, the same as the
        // grading roster.
        if (ev.shiftKey || ev.ctrlKey || ev.metaKey) {
          ev.preventDefault();
          pick(i, ev.shiftKey);
          return;
        }
        mem().picked.clear();
        mem().anchor = i;
        drawTools();
        openThread(b.dataset.id);
      };
    });
    host.querySelectorAll('.ibPick').forEach(box => {
      box.onclick = ev => { ev.stopPropagation(); pick(+box.dataset.i, ev.shiftKey); };
    });
  }

  /* ------------------------------------------------- selecting several */
  /* Shift extends from the last row touched; anything else toggles one. The
     range runs over the rows as they are on screen, which is what you see and
     therefore what you mean. */
  function pick(index, extend) {
    const rows = mem().threads;
    if (!rows[index]) return;
    if (extend && mem().anchor != null && rows[mem().anchor]) {
      const from = Math.min(mem().anchor, index), to = Math.max(mem().anchor, index);
      for (let i = from; i <= to; i++) mem().picked.add(String(rows[i].id));
    } else {
      const key = String(rows[index].id);
      if (mem().picked.has(key)) mem().picked.delete(key); else mem().picked.add(key);
      mem().anchor = index;
    }
    drawList();
    drawTools();
  }

  function pickAll(on) {
    mem().picked.clear();
    if (on) mem().threads.forEach(t => mem().picked.add(String(t.id)));
    mem().anchor = null;
    drawList();
    drawTools();
  }

  /* What can be done to a selection, and what each one is called when it is
     put as a question. Archiving reverses in the archived scope, where the
     inbox verb would make no sense. */
  const BULK = {
    read: { doing: 'Marking read', ask: 'Mark these read?', verb: 'Yes, mark them read' },
    unread: { doing: 'Marking unread', ask: 'Mark these unread?', verb: 'Yes, mark them unread' },
    archive: { doing: 'Archiving', ask: 'Archive these?', verb: 'Yes, archive them' },
    unarchive: { doing: 'Moving to the inbox', ask: 'Move these back to the inbox?',
      verb: 'Yes, move them back' },
    delete: { doing: 'Deleting', ask: 'Delete these threads?', verb: 'Yes, delete them' },
  };

  function drawTools() {
    const host = $('#ibTools');
    if (!host) return;
    const rows = mem().threads;
    const n = mem().picked.size;
    const all = rows.length > 0 && n === rows.length;
    const archived = mem().scope === 'archived';
    if (!rows.length) { host.innerHTML = ''; return; }
    host.innerHTML = `<div class="ibTools${n ? ' on' : ''}">
      <label class="ibAll"><input type="checkbox" id="ibAll" ${all ? 'checked' : ''}>
        <span>${n ? esc(n) + ' selected' : 'Select all'}</span></label>
      ${n ? `<div class="ibActs">
        <button class="btn sm" type="button" data-act="read">Mark read</button>
        <button class="btn sm" type="button" data-act="unread">Mark unread</button>
        <button class="btn sm" type="button"
          data-act="${archived ? 'unarchive' : 'archive'}">${
          archived ? 'Move to inbox' : 'Archive'}</button>
        <button class="btn sm danger" type="button" data-act="delete">Delete…</button>
      </div>
      <span class="spacer"></span>
      <span class="hint">Changes your copy. Nothing is sent to anybody.</span>
      <button class="btn sm" type="button" id="ibNone">Clear</button>` : ''}
    </div>`;
    const box = $('#ibAll');
    box.indeterminate = n > 0 && !all;
    box.onchange = () => pickAll(box.checked);
    const none = $('#ibNone');
    if (none) none.onclick = () => pickAll(false);
    host.querySelectorAll('[data-act]').forEach(b => {
      b.onclick = () => runBulk(b.dataset.act);
    });
  }

  /* Escape drops a selection, so a mis-click is one key away from undone.
     Re-registered on every draw, so it is removed first rather than stacked. */
  function onKey(ev) {
    if (ev.key !== 'Escape' || !mem().picked.size) return;
    if ($('#modalHost').innerHTML) return;          // the dialog owns Escape
    pickAll(false);
    setStatus('selection cleared', 'ok');
  }
  function watchKeys() {
    document.removeEventListener('keydown', onKey);
    document.addEventListener('keydown', onKey);
    onLeave(() => document.removeEventListener('keydown', onKey));
  }

  /* Inviting a drop on the reply box makes a near miss likely, and a file
     dropped anywhere else makes the browser open it in place of this tab,
     taking the unsent draft with it. While the inbox is up, a file dropped
     outside the box does nothing. */
  function onStrayDrag(ev) {
    if (!hasFiles(ev.dataTransfer)) return;
    if (ev.target && ev.target.closest && ev.target.closest('#ibDraftBox')) return;
    ev.preventDefault();
    if (ev.type === 'dragover') ev.dataTransfer.dropEffect = 'none';
  }
  function guardDrops() {
    const kinds = ['dragover', 'drop'];
    kinds.forEach(k => {
      document.removeEventListener(k, onStrayDrag);
      document.addEventListener(k, onStrayDrag);
    });
    onLeave(() => kinds.forEach(k => document.removeEventListener(k, onStrayDrag)));
  }

  /* The obvious thing to do with a screenshot is paste it, and the reply box
     may not be open yet. A picture pasted while a thread is open, with no
     typing box in use, opens the reply with the picture already on it. A
     typing box keeps its own paste: the reply box attaches, and the others
     are not for pictures. */
  function onPagePaste(ev) {
    const t = mem().shown;
    if (!t || String(t.id) !== String(mem().open) || !$('#ibPane')) return;
    const el = ev.target;
    if (el && el.closest && el.closest('input, textarea, select, [contenteditable]')) return;
    const got = clipFiles(ev.clipboardData);
    if (!got.length || hasText(ev.clipboardData)) return;
    ev.preventDefault();
    openReply(t);
    addFiles(t, got, true);
    const box = $('#ibReply');
    if (box) box.focus();
  }
  function watchPaste() {
    document.removeEventListener('paste', onPagePaste);
    document.addEventListener('paste', onPagePaste);
    onLeave(() => document.removeEventListener('paste', onPagePaste));
  }

  function runBulk(act) {
    const ids = [...mem().picked];
    const spec = BULK[act];
    if (!ids.length || !spec) return;
    runJobConfirmed(`${spec.doing}: ${ids.length} thread${ids.length === 1 ? '' : 's'}`,
      token => api('/inbox/bulk', { body: {
        ids, action: act, scope: mem().scope, confirm: token } }),
      out => {
        if (!out) return;
        // A deleted thread cannot stay open in the pane beside the list.
        if (act === 'delete' && (out.done || []).some(id => String(id) === String(mem().open))) {
          mem().open = null;
        }
        mem().picked.clear();
        mem().anchor = null;
        setStatus(out.sentence_done || 'done', (out.failed || []).length ? 'err' : 'ok');
        openInbox(mem().open);
      },
      { title: spec.ask, verb: spec.verb,
        note: 'Nothing has been sent to anybody, and nothing will be.' });
  }

  async function openThread(id) {
    mem().open = id;
    drawList();
    const pane = $('#ibPane');
    pane.innerHTML = '<p class="hint">Opening…</p>';
    let t;
    try { t = await api('/inbox/' + encodeURIComponent(id)); }
    catch (err) {
      pane.innerHTML = '';
      pane.appendChild(emptyState('Could not open that thread: ' + firstLine(err.message)));
      return;
    }
    if (String(mem().open) !== String(id)) return;
    markLocalRead(id);
    drawThread(t);
  }

  function markLocalRead(id) {
    const row = mem().threads.find(t => String(t.id) === String(id));
    if (row) row.unread = false;
    const n = mem().threads.filter(t => t.unread).length;
    const hint = $('#ibHint');
    if (hint) hint.textContent = `${mem().threads.length} thread${
      mem().threads.length === 1 ? '' : 's'}` + (n ? ` · ${n} unread` : '');
    drawList();
    if (typeof inboxChip === 'function') inboxChip();
  }

  /* The name in the thread heading opens that student's page. The course is
     known, so the page opens on that course rather than a search across all. */
  function whoHtml(t) {
    const people = t.with || [];
    if (!people.length) return 'someone';
    return people.map(p => {
      const label = (typeof studentLabel === 'function' && studentLabel(p)) || p.name || p.tag || 'student';
      if (!p.user_id) return esc(label);
      const href = t.course_id
        ? `#/c/${esc(t.course_id)}/student/${esc(p.user_id)}`
        : `#/student/${esc(p.user_id)}`;
      return `<a class="ibWhoLink" href="${href}">${esc(label)}</a>`;
    }).join(', ');
  }

  function drawThread(t) {
    const pane = $('#ibPane');
    const who = whoHtml(t);
    const read = mem().read[t.id];
    mem().shown = t;
    pane.innerHTML = `<div class="ibThread">
      <div class="ibHead">
        <h3>${esc(t.subject)}</h3>
        <p class="hint">${who}${t.course_name ? ' · ' + esc(t.course_name) : ''}
          · ${esc(t.ago || '')}</p>
      </div>
      <div id="ibInsight"></div>
      <div class="ibMsgs">${(t.transcript || []).map(m => `
        <div class="ibMsg${m.from === 'you' ? ' mine' : ''}">
          <div class="ibFrom">${esc(m.from === 'you' ? 'You' : nameFor(t, m.from))}</div>
          ${m.body ? `<div class="ibBody">${esc(m.body)}</div>` : ''}
          ${fileHtml(t, m)}
        </div>`).join('')}</div>
      <div class="ibVerbs">
        <button class="btn ai" type="button" id="ibRead">Draft a reply with Claude</button>
        <button class="btn" type="button" id="ibManual">Write it myself</button>
        <a class="btn" href="${esc(canvasLink(t))}" target="_blank" rel="noopener">Open in Canvas</a>
        <span class="hint">or paste a screenshot to start a reply with it attached</span>
      </div>
      <div id="ibAsk"></div>
      <div id="ibDraft"></div>`;
    pane.querySelectorAll('img.ibImg, video.ibVid, audio.ibAudio').forEach(el => {
      el.onerror = () => {
        const note = document.createElement('p');
        note.className = 'hint';
        note.textContent = 'Could not show this file. Open the thread in Canvas.';
        el.replaceWith(note);
      };
    });
    $('#ibRead').onclick = () => askFirst(t);
    $('#ibManual').onclick = () => {
      /* No model call at all. An empty box and the cursor in it. */
      $('#ibAsk').innerHTML = '';
      if (mem().drafts[t.id] == null) mem().drafts[t.id] = '';
      drawDraft(t, mem().drafts[t.id]);
      const box = $('#ibReply');
      if (box) box.focus();
    };
    if (read) drawInsight(t, read);
    if (mem().drafts[t.id] != null) drawDraft(t, mem().drafts[t.id]);
  }

  /* The transcript says Student-14; the header says who that is. Both are
     true and the pairing is the point: you read a name, Anthropic read a tag. */
  function nameFor(t, tag) {
    const hit = (t.with || []).find(p => p.tag === tag);
    return hit ? (studentLabel(hit) || tag) : tag;
  }

  /* Pictures and files ride through this app. A Canvas address in the message
     would not carry the token, so the browser asks us and we ask Canvas. */
  function fileHtml(t, m) {
    const files = m.files || [];
    if (!files.length) return '';
    return `<div class="ibFiles">${files.map(f => {
      const href = '/api/inbox/' + encodeURIComponent(t.id) + '/file/' + encodeURIComponent(f.key);
      const name = f.name || 'file';
      if (f.kind === 'image') {
        return `<figure class="ibFile"><img class="ibImg" alt="${esc(name)}" src="${esc(href)}">
          <figcaption>${esc(name)}</figcaption></figure>`;
      }
      if (f.kind === 'video') {
        return `<video class="ibVid" controls preload="metadata" src="${esc(href)}"></video>
          <div class="hint">${esc(name)}</div>`;
      }
      if (f.kind === 'audio') {
        return `<audio class="ibAudio" controls preload="metadata" src="${esc(href)}"></audio>
          <div class="hint">${esc(name)}</div>`;
      }
      return `<a class="ibFileLink" href="${esc(href)}" target="_blank" rel="noopener">${esc(name)}</a>`;
    }).join('')}</div>`;
  }

  function canvasLink(t) {
    const base = (S.health && S.health.base_url) || '';
    return base ? base + '/conversations/' + encodeURIComponent(t.id) : '#';
  }

  /* Say how to answer, or say nothing. The box is the point of this step: most
     of the time the message decides the reply, but the times it does not are
     the times a draft is useless without being told "no extensions this week"
     or "we have been through this twice". Empty is a real answer and the
     button says so, rather than making somebody delete a placeholder. */
  function askFirst(t) {
    const host = $('#ibAsk');
    if (!host) return;
    if (host.dataset.open === '1') { host.innerHTML = ''; host.dataset.open = '0'; return; }
    host.dataset.open = '1';
    host.innerHTML = `<div class="ibAskBox">
      <label for="ibTell">How should this be answered? <span class="muted">Optional.
        This is a direction for the draft, not the words that get sent.
        Leave it empty and the draft comes from the student's message alone.</span></label>
      <textarea id="ibTell" rows="2"
        placeholder="No extensions this week. Point them at the rubric on the module page."
        >${esc(mem().told[t.id] || '')}</textarea>
      <div class="row">
        <button class="btn ai" type="button" id="ibGo">Draft it</button>
        <button class="btn" type="button" id="ibAskNo">Cancel</button>
        <span class="spacer"></span>
        <span class="hint">A student's name typed here is swapped before it is sent,
          the same as anywhere else.</span>
      </div>
    </div>`;
    const tell = $('#ibTell');
    tell.focus();
    tell.oninput = () => { mem().told[t.id] = tell.value; };
    tell.onkeydown = ev => {
      if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); go(); }
    };
    const go = () => {
      host.innerHTML = '';
      host.dataset.open = '0';
      readThread(t, (tell.value || '').trim());
    };
    $('#ibGo').onclick = go;
    $('#ibAskNo').onclick = () => { host.innerHTML = ''; host.dataset.open = '0'; };
  }

  function readThread(t, instructions) {
    mem().told[t.id] = instructions || '';
    runJob(instructions ? 'Drafting to your instruction' : 'Drafting a reply',
      () => api('/inbox/' + encodeURIComponent(t.id) + '/read',
        { body: { instructions: instructions || '' } }), out => {
      if (!out) return;
      mem().read[t.id] = out;
      mem().drafts[t.id] = out.draft || '';
      drawInsight(t, out);
      drawDraft(t, out.draft || '');
      setStatus('drafted; nothing sent', 'ok');
    });
  }

  function drawInsight(t, out) {
    const host = $('#ibInsight');
    if (!host) return;
    const urgency = String(out.urgency || 'routine');
    host.innerHTML = `<div class="ibInsight${out.needs_you ? ' needsYou' : ''}">
      <div class="ibAsk"><b>Asking:</b> ${esc(out.asking || 'it did not say')}</div>
      <div class="ibTags">
        ${pill(out.kind || 'other', 'ai')}
        ${pill(urgency === 'routine' ? 'routine' : urgency, urgency === 'routine' ? '' : 'warn')}
        ${out.needs_you ? pill('needs your judgement', 'warn') : ''}
      </div>
      ${out.needs_you && out.why ? `<p class="ibWhy">${esc(out.why)}</p>` : ''}
    </div>`;
  }

  function drawDraft(t, text) {
    const host = $('#ibDraft');
    if (!host) return;
    host.innerHTML = `<div class="ibDraftBox" id="ibDraftBox">
      <label class="srOnly" for="ibReply">Your reply</label>
      <textarea id="ibReply" rows="6"
        placeholder="Nothing is sent until you press Send.">${esc(text || '')}</textarea>
      <div class="ibAttach" id="ibAttach" data-thread="${esc(t.id)}" hidden></div>
      <div class="ibOffer" id="ibOffer" data-thread="${esc(t.id)}" role="status" hidden></div>
      <div class="row">
        <button class="btn primary" type="button" id="ibSend">Send this reply</button>
        <button class="btn" type="button" id="ibAddFile">Attach files…</button>
        <input type="file" id="ibPickFile" multiple hidden>
        <button class="btn" type="button" id="ibRedo">Draft it again…</button>
        <button class="btn" type="button" id="ibClear">Discard</button>
        <span class="spacer"></span>
        <span class="hint">Goes to ${esc((t.with || []).map(p => studentLabel(p) || p.tag).join(', '))}
          as you, from your Canvas account.</span>
      </div>
      <p class="hint ibAttachHint">Paste a picture into the reply, or drop files on this
        box, to attach them. They stay on this computer until you send.</p>
    </div>`;
    const box = $('#ibReply');
    box.oninput = () => { mem().drafts[t.id] = box.value; };
    drawFiles(t);
    wireAttach(t, box);
    $('#ibRedo').onclick = () => askFirst(t);
    $('#ibClear').onclick = () => {
      delete mem().drafts[t.id];
      dropFiles(t.id);
      host.innerHTML = '';
      setStatus('draft discarded; nothing was sent', 'ok');
    };
    $('#ibSend').onclick = () => {
      const body = (box.value || '').trim();
      if (!body) { setStatus('there is nothing in the box to send', 'err'); box.focus(); return; }
      const files = filesOf(t.id);
      if (files.some(f => !f.id)) {
        setStatus('an attachment is still being added; send again in a moment', 'err');
        return;
      }
      const attachments = files.map(f => f.id);
      runJobConfirmed(attachments.length ? 'Sending the reply and its attachments'
        : 'Sending the reply',
        (token) => api('/inbox/' + encodeURIComponent(t.id) + '/reply',
          { body: { body, attachments, confirm: token } }),
        out => {
          if (!out) return;
          delete mem().drafts[t.id];
          delete mem().read[t.id];
          dropFiles(t.id);
          setStatus(out.warning || ('sent to ' + (out.sent_to || []).join(', ')),
            out.warning ? 'err' : 'ok');
          openInbox(t.id);
        },
        { title: 'Send this to a student?',
          verb: 'Yes, send it',
          note: 'This goes to the student from your Canvas account, under your name. '
              + 'A sent message cannot be taken back.' });
    };
  }

  /* ---------------------------------------------------------- attachments */
  /* Attaching is not a Canvas write. The server keeps the file and hands back
     an id; only the reply, after its confirmation, puts the file in Canvas.
     So taking a chip off again needs no question. */
  function filesOf(id, create) {
    const all = mem().files || (mem().files = {});
    if (!all[id] && create) all[id] = [];
    return all[id] || [];
  }

  function dropFiles(id) {
    const list = filesOf(id);
    list.dropped = true;          // an add still on its way must not bring it back
    list.forEach(f => { if (f.preview) URL.revokeObjectURL(f.preview); });
    if (mem().files) delete mem().files[id];
  }

  function wireAttach(t, box) {
    const pick = $('#ibPickFile');
    $('#ibAddFile').onclick = () => pick.click();
    pick.onchange = () => {
      const got = [...pick.files];
      pick.value = '';
      addFiles(t, got, false);
    };
    box.onpaste = ev => {
      const got = clipFiles(ev.clipboardData);
      if (!got.length) return;                  // plain text
      if (hasText(ev.clipboardData)) {          // the text pastes; the picture is asked about
        offerPictures(t, got.filter(f => /^image\//i.test(f.type || '')));
        return;
      }
      ev.preventDefault();
      addFiles(t, got, true);
    };
    const zone = $('#ibDraftBox');
    zone.ondragover = ev => {
      if (!hasFiles(ev.dataTransfer)) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = 'copy';
      zone.classList.add('dropping');
    };
    zone.ondragleave = ev => {
      if (!zone.contains(ev.relatedTarget)) zone.classList.remove('dropping');
    };
    zone.ondrop = ev => {
      zone.classList.remove('dropping');
      if (!hasFiles(ev.dataTransfer)) return;
      ev.preventDefault();
      addFiles(t, [...ev.dataTransfer.files], false);
    };
  }

  const hasFiles = dt => !!dt && [...(dt.types || [])].includes('Files');

  /* Every file on the clipboard, read during the paste itself: the clipboard
     is gone once the event is over, though the files it handed over are not. */
  function clipFiles(dt) {
    if (!dt) return [];
    const files = [...(dt.files || [])];
    if (files.length) return files;
    return [...(dt.items || [])].filter(it => it.kind === 'file')
      .map(it => it.getAsFile()).filter(Boolean);
  }

  /* A screenshot arrives as a file with no text beside it, and attaches.
     Copying cells from Excel, or a slide from PowerPoint, puts a picture of
     them on the clipboard as well, next to their text. Which one was meant
     differs -- the cells' text, the slide's picture -- so the text pastes as
     usual and the picture is offered rather than guessed at. */
  const hasText = dt => !!dt && !!(dt.getData('text/plain') || '').trim();

  function offerPictures(t, pics) {
    const host = $('#ibOffer');
    if (!host || host.dataset.thread !== String(t.id) || !pics.length) return;
    const one = pics.length === 1;
    host.hidden = false;
    host.innerHTML = `<span>That paste also carried ${one ? 'a picture' : pics.length + ' pictures'}
        of what you copied.</span>
      <button class="btn sm" type="button" id="ibOfferYes">Attach ${one ? 'it' : 'them'}</button>
      <button class="btn sm" type="button" id="ibOfferNo">No thanks</button>`;
    const done = () => {
      host.hidden = true;
      host.innerHTML = '';
      const box = $('#ibReply');
      if (box) box.focus();
    };
    $('#ibOfferYes').onclick = () => { done(); addFiles(t, pics, true); };
    $('#ibOfferNo').onclick = done;
  }

  /* The reply box, opened the way "Write it myself" opens it, keeping any
     draft already written. */
  function openReply(t) {
    if ($('#ibReply')) return;
    const ask = $('#ibAsk');
    if (ask) { ask.innerHTML = ''; ask.dataset.open = '0'; }
    if (mem().drafts[t.id] == null) mem().drafts[t.id] = '';
    drawDraft(t, mem().drafts[t.id]);
  }

  /* Every pasted screenshot is called image.png. Three on one reply would be
     three chips with one name, so a pasted picture is named for the time. */
  function pastedName(file, taken) {
    const given = String(file.name || '');
    if (given && !/^image\.[a-z0-9]+$/i.test(given)) return given;
    const ext = ({ 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp',
      'image/bmp': 'bmp' })[file.type] || 'png';
    const d = new Date();
    const two = n => String(n).padStart(2, '0');
    const stem = `pasted-image-${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}-`
      + `${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`;
    let name = `${stem}.${ext}`;
    for (let n = 2; taken.some(f => f.name === name); n++) name = `${stem}-${n}.${ext}`;
    return name;
  }

  async function addFiles(t, list, pasted) {
    const files = filesOf(t.id, true);
    for (const file of list) {
      if (files.dropped) return;
      if (files.length >= MAX_FILES) {
        setStatus(`one reply can carry ${MAX_FILES} attachments here`, 'err');
        break;
      }
      const name = pasted ? pastedName(file, files) : (file.name || 'attachment');
      if (!file.size) { setStatus(`${name} is empty, so it was not attached`, 'err'); continue; }
      if (file.size > MAX_FILE) {
        setStatus(`${name} is ${sizeText(file.size)}; one attachment can be up to `
          + `${sizeText(MAX_FILE)} here`, 'err');
        continue;
      }
      const entry = { key: 'f' + (++FILE_SEQ), id: '', name, size: file.size,
        preview: RASTER.test(file.type || '') ? URL.createObjectURL(file) : '' };
      files.push(entry);
      drawFiles(t);
      try {
        const data = await asBase64(file);
        const out = await api('/inbox/' + encodeURIComponent(t.id) + '/attach',
          { body: { name, type: file.type || '', data } });
        Object.assign(entry, { id: out.id, name: out.name || name, size: out.size || file.size });
        if (!files.dropped) setStatus(`attached ${entry.name}; nothing has been sent`, 'ok');
      } catch (err) {
        const at = files.indexOf(entry);
        if (at >= 0) files.splice(at, 1);
        if (entry.preview) URL.revokeObjectURL(entry.preview);
        setStatus(`could not attach ${name}: ${firstLine(err.message)}`, 'err');
      }
      drawFiles(t);
    }
  }

  /* Only into this thread's own box: by the time a file finishes adding, the
     pane may be showing a different thread. */
  function drawFiles(t) {
    const host = $('#ibAttach');
    if (!host || host.dataset.thread !== String(t.id)) return;
    const files = filesOf(t.id);
    host.hidden = !files.length;
    host.innerHTML = files.map(f => `<div class="ibChip${f.id ? '' : ' busy'}">
        ${f.preview ? `<img class="ibThumb" src="${esc(f.preview)}" alt="">`
          : `<span class="ibThumb ibThumbFile" aria-hidden="true">${esc(extOf(f.name))}</span>`}
        <span class="ibChipText"><span class="ibChipName" title="${esc(f.name)}">${esc(f.name)}</span>
          <span class="ibChipSize">${f.id ? esc(sizeText(f.size)) : 'adding…'}</span></span>
        <button class="ibChipX" type="button" data-key="${esc(f.key)}" ${f.id ? '' : 'disabled'}
          aria-label="Remove ${esc(f.name)}" title="Remove">×</button>
      </div>`).join('');
    host.querySelectorAll('[data-key]').forEach(b => {
      b.onclick = () => {
        const list = filesOf(t.id);
        const at = list.findIndex(f => f.key === b.dataset.key);
        if (at < 0) return;
        const [gone] = list.splice(at, 1);
        if (gone.preview) URL.revokeObjectURL(gone.preview);
        drawFiles(t);
        setStatus(`removed ${gone.name}; nothing was sent`, 'ok');
        const box = $('#ibReply');
        if (box) box.focus();
      };
    });
  }

  const extOf = name => (String(name).match(/\.([a-z0-9]{1,5})$/i) || [])[1] || 'file';

  function sizeText(n) {
    n = +n || 0;
    if (n < 1024) return `${n} bytes`;
    if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
    return `${(n / 1024 / 1024).toFixed(1).replace(/\.0$/, '')} MB`;
  }

  function asBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || '').replace(/^data:[^,]*,/, ''));
      reader.onerror = () => reject(reader.error || new Error('the file could not be read'));
      reader.readAsDataURL(file);
    });
  }

  window.openInbox = openInbox;
  window.inboxChip = inboxChip;
  Object.assign(window.Studio || (window.Studio = {}), { openInbox, startChip });
  document.addEventListener('studio:booted', startChip);
})();
