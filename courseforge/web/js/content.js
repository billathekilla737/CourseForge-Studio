/* content.js: the Build area. Draft a page, syllabus, assignment, discussion,
   quiz or study guide with Claude, read it, edit it, and place it in a module.
   Also the two bulk paths: the manifest and the rubric definitions.

   A draft is saved on this computer and copied to your Canvas files so another
   PC can pick it up. Place is refused once by the server so the sentence it
   answers with is the one shown here, word for word. Routes:

     #/c/<cid>/build                home: the New cards and the drafts table
     #/c/<cid>/build/new/<kind>     the draft form
     #/c/<cid>/build/draft/<id>     the review screen
     #/c/<cid>/build/manifest       the manifest tab
     #/c/<cid>/build/rubrics        the rubrics tab                            */
(function () {
  'use strict';

  S.buildArea = S.buildArea || {};

  const BUILD_KINDS = [
    { id: 'page', label: 'Page', blurb: 'A content page: a topic, an overview, a how-to.' },
    { id: 'syllabus', label: 'Syllabus', blurb: 'The course syllabus body. It replaces the syllabus, and nothing else.' },
    { id: 'assignment', label: 'Assignment', blurb: 'Instructions, what to submit, and how it is marked.' },
    { id: 'discussion', label: 'Discussion', blurb: 'A graded discussion: the prompt, and what a good reply does.' },
    { id: 'quiz', label: 'Quiz', blurb: 'Questions with answers and points, plus the description.' },
    { id: 'study-guide', label: 'Study guide', blurb: 'A revision page for an exam or a unit.' },
  ];
  const BUILD_LOOKS = [
    { id: 'clean', label: 'Clean', hint: 'No background fills. The safest for the Ally score.' },
    { id: 'hybrid', label: 'Hybrid', hint: 'A filled hero and footer, plain in between.' },
    { id: 'rich', label: 'Rich', hint: 'Every component filled. The most colour, and the most Ally advisories.' },
  ];
  const buildKindOf = id => BUILD_KINDS.find(k => k.id === id) || null;
  const buildBase = cid => '/build/' + encodeURIComponent(cid);
  const buildClamp = (text, n) => {
    const s = String(text == null ? '' : text);
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  };
  const buildPublishWord = on => (on ? 'published, students see it' : 'unpublished, only you see it');

  /* ------------------------------------------------------------- the area */
  async function openBuild(courseId, rest) {
    rest = Array.isArray(rest) ? rest : [];
    const tab = (rest[0] === 'manifest' || rest[0] === 'rubrics') ? rest[0] : 'home';
    S.buildArea.courseId = String(courseId);
    showView('area');
    // S.course is whatever course was looked at last. A tab opened straight on
    // a Build route, or one that came here from another course's hub, would
    // otherwise name that other course in the crumb above a screen that
    // writes to this one.
    if (typeof ensureCourse === 'function') await ensureCourse(courseId);
    crumbs([
      { label: 'Courses', href: '#/' },
      { label: courseTitle(S.course), href: '#/c/' + courseId },
      { label: 'Build' },
    ]);
    $('#headerActions').innerHTML = '';
    areaHead('Build', 'Draft it here, read it here, place it when you are ready. '
      + 'Nothing reaches Canvas until you place it.');
    areaTabs([
      { id: 'home', label: 'Drafts' },
      { id: 'manifest', label: 'Manifest' },
      { id: 'rubrics', label: 'Rubrics' },
    ], tab, id => {
      location.hash = '#/c/' + courseId + '/build' + (id === 'home' ? '' : '/' + id);
    });

    const body = $('#areaBody');
    if (rest[0] === 'new') return buildForm(courseId, rest[1] || 'page', body);
    if (rest[0] === 'draft' && rest[1]) return buildReview(courseId, rest[1], body);
    if (rest[0] === 'manifest') return buildManifest(courseId, body);
    if (rest[0] === 'rubrics') return buildRubrics(courseId, body);
    return buildHome(courseId, body);
  }

  function buildSyncBanner(courseId, state) {
    const host = $('#bdSync');
    if (!host) return;
    const sync = (state && state.sync) || {};
    const did = sync.did || sync.action || '';
    if (did === 'diverged') {
      const who = sync.remote_machine || 'another computer';
      host.innerHTML = `<div class="callout warn">Unpublished drafts also changed on
        ${esc(String(who))}. Nothing was overwritten.
        <div class="foot" style="margin-top:.6rem">
          <button class="btn" id="bdTakeRemote" type="button">Use the Canvas copy</button>
          <button class="btn" id="bdTakeLocal" type="button">Keep this computer's drafts</button>
        </div></div>`;
      const remote = $('#bdTakeRemote'), local = $('#bdTakeLocal');
      if (remote) remote.onclick = () => buildResolveSync(courseId, 'remote');
      if (local) local.onclick = () => buildResolveSync(courseId, 'local');
      return;
    }
    if (did === 'error') {
      host.innerHTML = `<div class="callout warn">Could not copy drafts to your Canvas
        files: ${esc(sync.reason || sync.detail || 'unknown error')}. They are still saved here.</div>`;
      return;
    }
    if (did === 'picked_up' || did === 'seeded' || did === 'sent' || did === 'in_sync') {
      host.innerHTML = '<p class="hint">Drafts are saved here and in your Canvas files, so another PC signed in as you can pick them up.</p>';
      return;
    }
    host.innerHTML = '';
  }

  function buildResolveSync(courseId, take) {
    api(buildBase(courseId) + '/sync', { body: { take } })
      .then(() => openBuild(courseId, []))
      .catch(err => setStatus('could not settle the two copies: ' + firstLine(err.message), 'err'));
  }

  /* ------------------------------------------------------------- the home */
  async function buildHome(courseId, body) {
    body.innerHTML = '<p class="muted">Reading drafts…</p>';
    let state;
    try { state = await api(buildBase(courseId) + '/state'); }
    catch (err) {
      body.replaceChildren(emptyState('Could not read the Build area: ' + firstLine(err.message)
        + '. Nothing has been changed.', 'Try again', () => buildHome(courseId, body)));
      return;
    }
    S.buildArea.state = state;
    const drafts = state.drafts || [];
    body.innerHTML = `
      <div id="bdSync"></div>
      <section aria-labelledby="bdNewH">
        <div class="sectionHead"><h2 id="bdNewH">Something new</h2>
          <span class="hint">Claude writes the first version in the school's house style.
            It is saved here and copied to your Canvas files. Nothing in the course
            changes until you place it.</span></div>
        <div class="cards bdNew">${BUILD_KINDS.map(k => `
          <a class="card bdCard" href="#/c/${esc(courseId)}/build/new/${esc(k.id)}">
            <div class="t">${esc(k.label)}</div><div class="m">${esc(k.blurb)}</div></a>`).join('')}</div>
      </section>
      <section class="hubSection" aria-labelledby="bdDraftsH">
        <div class="sectionHead"><h2 id="bdDraftsH">Drafts</h2>
          <span class="hint">${drafts.length ? esc(drafts.length + ' here, '
            + drafts.filter(d => d.placed).length + ' already placed') : ''}</span></div>
        <div id="bdDrafts"></div>
      </section>`;
    buildSyncBanner(courseId, state);
    const host = $('#bdDrafts');
    if (!drafts.length) {
      host.replaceChildren(emptyState('No drafts yet. Pick one of the cards above and Claude '
        + 'writes a first version you can read and edit here. Nothing is pushed to Canvas until '
        + 'you place it, and placing asks first.'));
      return;
    }
    host.innerHTML = `<div class="gwTableWrap"><table class="gwTable">
      <caption class="srOnly">Every draft in this course, newest first</caption>
      <thead><tr><th scope="col">Title</th><th scope="col">Kind</th><th scope="col">Where it goes</th>
        <th scope="col">Checks</th><th scope="col">State</th><th scope="col">Updated</th>
        <th scope="col"><span class="srOnly">Actions</span></th></tr></thead>
      <tbody>${drafts.map(d => `<tr>
        <th scope="row"><a href="#/c/${esc(courseId)}/build/draft/${esc(d.id)}">${esc(d.title || '(untitled)')}</a>
          ${d.summary ? `<div class="muted">${esc(buildClamp(d.summary, 90))}</div>` : ''}</th>
        <td>${esc((buildKindOf(d.kind) || {}).label || d.kind || '')}
          ${d.questions ? `<span class="muted">· ${esc(d.questions)} questions</span>` : ''}</td>
        <td>${d.module ? esc(d.module) : '<span class="muted">no module yet</span>'}
          ${d.points != null && d.points !== '' ? `<span class="muted"> · ${esc(d.points)} points</span>` : ''}</td>
        <td>${d.scan_ok ? pill('checks pass', 'v-pass')
          : pill((d.scan_failed || 0) + ' to fix', 'v-fail')}</td>
        <td>${d.placed ? pill('placed in Canvas', 'st-pushed') : pill('unpublished draft', 'st-scanned')}</td>
        <td class="muted">${esc(fmtDate(d.updated_at || d.created_at) || '')}</td>
        <td><a class="btn sm" href="#/c/${esc(courseId)}/build/draft/${esc(d.id)}">Open</a></td>
      </tr>`).join('')}</tbody></table></div>`;
  }

  /* ------------------------------------------------------------- the form */
  async function buildForm(courseId, kindId, body) {
    const kind = buildKindOf(kindId) || BUILD_KINDS[0];
    const state = S.buildArea.state || await api(buildBase(courseId) + '/state').catch(() => ({}));
    S.buildArea.state = state;
    const look = S.buildArea.look || state.default_look || 'clean';
    body.innerHTML = `
      <div class="bdBack"><a class="btn sm" href="#/c/${esc(courseId)}/build">← All drafts</a></div>
      <form class="bdForm" id="bdForm" novalidate>
        <h2 class="bdFormH">New ${esc(kind.label.toLowerCase())}</h2>
        <p class="muted">${esc(kind.blurb)} Claude writes it; you read it before anything is placed.</p>
        <label class="bdField"><span>Title</span>
          <input id="bdTitle" type="text" required autocomplete="off"
            placeholder="What it is called in the module list"></label>
        <div class="bdRow">
          <label class="bdField"><span>Module</span>
            <span id="bdModuleWrap"><input id="bdModule" type="text" autocomplete="off"
              placeholder="Reading the module list…"></span>
            <span class="bdHint">Where it is placed. Leave it empty to leave it out of the modules.</span></label>
          <label class="bdField"><span>Position</span>
            <input id="bdPosition" type="number" min="1" step="1" placeholder="end">
            <span class="bdHint">Which place in that module. Empty means last.</span></label>
        </div>
        <div class="bdRow">
          <label class="bdField"><span>Points</span>
            <input id="bdPoints" type="number" min="0" step="0.5" placeholder="0"></label>
          <label class="bdField"><span>Assignment group</span>
            <span id="bdGroupWrap"><input id="bdGroup" type="text" autocomplete="off" placeholder="(optional)"></span></label>
        </div>
        <fieldset class="bdField bdLooks">
          <legend>Look</legend>
          <div class="bdLookRow" role="radiogroup" aria-label="Look">${BUILD_LOOKS.map(l => `
            <button type="button" class="bdLook" role="radio" data-look="${esc(l.id)}"
              aria-checked="${l.id === look}"><b>${esc(l.label)}</b>
              <span class="bdHint">${esc(l.hint)}</span></button>`).join('')}</div>
        </fieldset>
        <label class="bdField"><span>Brief</span>
          <textarea id="bdBrief" rows="5" placeholder="What it should cover, who it is for, anything it must say. The more you put here, the less you rewrite."></textarea></label>
        <label class="tick bdPublish"><input type="checkbox" id="bdPublish">
          Publish it when it is placed</label>
        <p class="muted bdNote">Left unticked, it is placed unpublished: it is in the course, and
          no student can see it until you publish it yourself.</p>
        <div class="bdActs">
          <button class="btn ai" type="submit" id="bdGo">Draft with Claude</button>
          <span class="hint">Nothing is sent to Canvas by this button.</span>
        </div>
      </form>`;

    body.querySelectorAll('.bdLook').forEach(btn => {
      btn.onclick = () => {
        S.buildArea.look = btn.dataset.look;
        body.querySelectorAll('.bdLook').forEach(b => b.setAttribute('aria-checked', String(b === btn)));
      };
    });
    S.buildArea.look = look;

    // The module and group pickers are Canvas reads; a course that cannot be
    // read keeps the plain text boxes rather than an empty dropdown.
    api(buildBase(courseId) + '/modules').then(res => {
      const rows = (res && res.modules) || [];
      const wrap = $('#bdModuleWrap');
      if (!wrap || !rows.length) { const i = $('#bdModule'); if (i) i.placeholder = 'Type a module name'; return; }
      wrap.innerHTML = `<select id="bdModule"><option value="">(no module)</option>${rows.map(m =>
        `<option value="${esc(m.id)}" data-name="${esc(m.name)}">${esc(m.name)}${
          m.published ? '' : ' (unpublished)'}</option>`).join('')}</select>`;
    }).catch(() => { const i = $('#bdModule'); if (i) i.placeholder = 'Type a module name'; });
    api(buildBase(courseId) + '/groups').then(res => {
      const rows = (res && res.groups) || [];
      const wrap = $('#bdGroupWrap');
      if (!wrap || !rows.length) return;
      wrap.innerHTML = `<select id="bdGroup"><option value="">(the default group)</option>${rows.map(g =>
        `<option value="${esc(g.name)}">${esc(g.name)}</option>`).join('')}</select>`;
    }).catch(() => {});

    $('#bdForm').onsubmit = ev => {
      ev.preventDefault();
      const title = ($('#bdTitle').value || '').trim();
      if (!title) { setStatus('give it a title first', 'err'); $('#bdTitle').focus(); return; }
      const moduleEl = $('#bdModule');
      const picked = moduleEl && moduleEl.tagName === 'SELECT' ? moduleEl.selectedOptions[0] : null;
      const form = {
        kind: kind.id, title, look: S.buildArea.look,
        module: picked ? (picked.dataset.name || '') : (moduleEl ? moduleEl.value.trim() : ''),
        module_id: picked && picked.value ? picked.value : null,
        position: $('#bdPosition').value ? +$('#bdPosition').value : null,
        points: $('#bdPoints').value ? +$('#bdPoints').value : null,
        group: ($('#bdGroup') && $('#bdGroup').value) || '',
        publish: $('#bdPublish').checked,
        brief: $('#bdBrief').value || '',
      };
      runJob('Drafting the ' + kind.label.toLowerCase() + ' “' + buildClamp(title, 40) + '”',
        () => api(buildBase(courseId) + '/draft', { body: form }),
        res => {
          const id = res && (res.id || (res.draft && res.draft.id));
          S.buildArea.state = null;
          if (id) location.hash = '#/c/' + courseId + '/build/draft/' + id;
          else setStatus('the draft came back without an id', 'err');
        }, { autoClose: true });
    };
    const first = $('#bdTitle');
    if (first) first.focus();
  }

  /* ----------------------------------------------------------- the review */
  async function buildReview(courseId, draftId, body) {
    body.innerHTML = '<p class="muted">Reading the draft…</p>';
    let record, preview;
    try {
      record = (await api(buildBase(courseId) + '/drafts/' + encodeURIComponent(draftId))).draft;
      preview = await api(buildBase(courseId) + '/drafts/' + encodeURIComponent(draftId) + '/preview')
        .catch(() => ({ html: record.html || '' }));
    } catch (err) {
      body.replaceChildren(emptyState('That draft could not be read: ' + firstLine(err.message),
        'All drafts', () => { location.hash = '#/c/' + courseId + '/build'; }));
      return;
    }
    const kind = buildKindOf(record.kind) || {};
    const findings = record.findings || {};
    const style = findings.style || {};
    const quiz = findings.quiz;
    const placed = record.placed;

    body.innerHTML = `
      <div class="bdBack"><a class="btn sm" href="#/c/${esc(courseId)}/build">← All drafts</a></div>
      <div class="bdHead">
        <h2 id="bdTitleH">${esc(record.title || '(untitled)')}</h2>
        <span class="hint">${esc(kind.label || record.kind || '')}
          ${record.module ? '· ' + esc(record.module) : ''}
          ${record.look ? '· ' + esc(record.look) + ' look' : ''}</span>
        <span class="spacer"></span>
        ${placed ? pill('placed in Canvas', 'st-pushed') : pill('draft only, nothing pushed', 'st-scanned')}
      </div>
      ${placed ? `<div class="callout">This draft was placed on
        ${esc((placed.placed_at || '').slice(0, 10))}.
        ${placed.html_url ? `<a href="${esc(placed.html_url)}" target="_blank" rel="noopener">Open it in Canvas</a>.` : ''}
        Editing it here does not change what is in Canvas; draft it again to place a second copy.</div>` : ''}
      <div class="bdChips" id="bdChips"></div>
      <div class="bdPanes">
        <section aria-labelledby="bdPrevH">
          <h3 id="bdPrevH" class="gwH">How it will read</h3>
          <div class="paperFrame canvasPage"><div class="canvasHtml asIs">${(preview && preview.html) || ''}</div></div>
        </section>
        <section aria-labelledby="bdEditH">
          <h3 id="bdEditH" class="gwH">The body</h3>
          <label class="srOnly" for="bdHtml">The draft's HTML</label>
          <textarea id="bdHtml" class="bdHtml" rows="18" spellcheck="false">${esc(record.html || '')}</textarea>
          <div class="bdEditFoot">
            <span class="hint" id="bdEditHint">Edit it and the border turns blue: from then on it is
              yours, and drafting again will not overwrite it.</span>
            <span class="spacer"></span>
            <button class="btn" type="button" id="bdSave" disabled>Save and re-check</button>
          </div>
          <details class="bdRaw"><summary>The HTML as it will be sent</summary>
            <pre class="bdRawPre">${esc(record.html || '')}</pre></details>
        </section>
      </div>
      ${quiz ? `<section class="bdQuiz" aria-labelledby="bdQuizH">
        <h3 id="bdQuizH" class="gwH">Questions</h3>
        <p class="muted">${esc(quiz.count)} question(s), ${esc(quiz.points)} points in total.</p>
        ${(quiz.failed || []).length ? `<ul class="applyList">${quiz.failed.map(f =>
          `<li class="bad">${esc(f)}</li>`).join('')}</ul>` : ''}
        ${(quiz.warned || []).length ? `<ul class="applyList">${quiz.warned.map(f =>
          `<li class="planned">${esc(f)}</li>`).join('')}</ul>` : ''}</section>` : ''}
      <div class="bdActs bdPlaceRow">
        <button class="btn danger" type="button" id="bdPlace">Place in course…</button>
        <span class="hint">${esc(buildPublishWord(!!record.publish))}. You are asked first, and the
          question names exactly what will be created.</span>
        <span class="spacer"></span>
        <button class="btn sm" type="button" id="bdDelete">Delete this draft</button>
      </div>`;

    buildChips($('#bdChips'), style, quiz);

    const area = $('#bdHtml');
    const save = $('#bdSave');
    area.oninput = () => {
      area.classList.add('edited');
      save.disabled = false;
      $('#bdEditHint').textContent = 'Edited by you. Save and re-check runs the same checks again; '
        + 'nothing is sent to Canvas.';
    };
    save.onclick = () => {
      save.disabled = true;
      api(buildBase(courseId) + '/drafts', { body: { id: draftId, html: area.value } })
        .then(() => { setStatus('saved here and re-checked', 'ok'); buildReview(courseId, draftId, body); })
        .catch(err => { save.disabled = false; setStatus('could not save: ' + firstLine(err.message), 'err'); });
    };

    $('#bdPlace').onclick = () => {
      const failed = (style.failed || []).concat((quiz && quiz.failed) || []);
      if (failed.length) {
        banner('warn', '<b>This draft does not pass the checks yet.</b> ' + esc(failed[0])
          + ' Fix it in the body and save, or the server will refuse to place it.');
        setStatus('the checks have to pass before it can be placed', 'err');
        return;
      }
      runJobConfirmed('Placing “' + buildClamp(record.title, 40) + '” in the course',
        token => api(buildBase(courseId) + '/place', {
          body: {
            id: draftId, module_id: record.module_id || null, position: record.position,
            publish: !!record.publish, group: record.group || null,
            module_name: record.module || '', confirm: token,
          },
        }),
        res => {
          const url = res && res.placed && res.placed.html_url;
          setStatus('placed in the course', 'ok');
          announce('The draft was placed in the course');
          if (url) banner('warn', '<b>Placed.</b> <a href="' + esc(url)
            + '" target="_blank" rel="noopener">Open it in Canvas</a>');
          buildReview(courseId, draftId, body);
        },
        { title: 'Create this in the live course?', verb: 'Yes, create it' });
    };

    $('#bdDelete').onclick = () => {
      askConfirm({ summary: 'Delete the draft “' + (record.title || '') + '” from this '
        + 'computer and from your Canvas files replica. The live course is not touched.' },
      () => api(buildBase(courseId) + '/drafts/' + encodeURIComponent(draftId) + '/delete', { body: {} })
        .then(() => { S.buildArea.state = null; setStatus('draft deleted', 'ok'); location.hash = '#/c/' + courseId + '/build'; })
        .catch(err => setStatus('could not delete: ' + firstLine(err.message), 'err')),
      { title: 'Delete this draft?', verb: 'Yes, delete it',
        note: 'Removes the draft here and the copy in your Canvas files. Nothing in the course changes.' });
    };
  }

  /* The scan chips: one h2, headings in order, alt text present, plain ASCII,
     contrast. Each carries its own word, and its detail as a tooltip. */
  function buildChips(host, style, quiz) {
    if (!host) return;
    const chips = (style.chips || []).slice();
    const words = { pass: 'passes', fail: 'fix this', warn: 'have a look' };
    if (quiz) chips.push({ id: 'quiz', label: 'Questions', state: quiz.ok ? 'pass' : 'fail',
      detail: (quiz.failed || []).join('; ') });
    if (!chips.length) { host.innerHTML = ''; return; }
    host.innerHTML = chips.map(c => `<span class="pill v-${esc(c.state === 'fail' ? 'fail'
      : c.state === 'warn' ? 'warn' : 'pass')}"${c.detail ? ` title="${esc(c.detail)}"` : ''}>${
      esc(c.label)} · ${esc(words[c.state] || c.state)}</span>`).join('')
      + ((style.failed || []).length ? `<span class="bdWhy">${esc(style.failed[0])}</span>` : '');
  }

  /* ---------------------------------------------------------- the manifest */
  async function buildManifest(courseId, body) {
    body.innerHTML = '<p class="muted">Reading the manifest on this computer…</p>';
    let got;
    try { got = await api(buildBase(courseId) + '/manifest'); }
    catch (err) {
      body.replaceChildren(emptyState('Could not read the manifest: ' + firstLine(err.message)));
      return;
    }
    const summary = got.summary;
    const problems = got.problems || [];
    if (!summary) {
      body.innerHTML = '<div id="bdMfEmpty"></div>';
      $('#bdMfEmpty').appendChild(emptyState('There is no manifest in this course yet. A manifest '
        + 'is one JSON file listing every page, assignment, quiz and module of a course, so a whole '
        + 'course can be built or rebuilt in one pass. Put one at ' + (got.path || 'the course folder')
        + ' and it shows up here. Nothing is pushed to Canvas until you ask, and the dry run comes '
        + 'first.'));
      return;
    }
    body.innerHTML = `
      <div id="bdMfStats"></div>
      ${problems.length ? `<div class="callout bad"><b>The manifest has problems, so it cannot be
        pushed until they are fixed:</b><ul class="bdProblems">${problems.map(p =>
        `<li>${esc(p)}</li>`).join('')}</ul></div>` : ''}
      <p class="muted bdPath">Read from <code>${esc(got.path || '')}</code></p>
      <div class="bdActs">
        <button class="btn primary" type="button" id="bdMfDry">Dry run</button>
        <button class="btn danger" type="button" id="bdMfPush">Push manifest…</button>
        <label class="tick"><input type="checkbox" id="bdMfPublish"> Publish what it creates</label>
        ${summary.mode === 'project' ? `
        <label class="tick"><input type="checkbox" id="bdMfSkip"> Leave the modules alone</label>
        <label class="tick"><input type="checkbox" id="bdMfRebuild"> Rebuild the modules</label>` : ''}
        <span class="hint">The dry run reads the course and changes nothing.</span>
      </div>
      <div id="bdMfPlan"></div>`;
    // The server's module-wipe gate reads these two from the request, not from
    // the manifest, so without them a course that already has modules could
    // never be pushed from this screen at all.
    const mfBody = apply => ({
      apply, publish: $('#bdMfPublish').checked,
      skip_modules: !!($('#bdMfSkip') && $('#bdMfSkip').checked),
      rebuild_modules: !!($('#bdMfRebuild') && $('#bdMfRebuild').checked),
    });
    statStrip($('#bdMfStats'), [
      { label: 'Mode', value: summary.mode },
      { label: 'Pages', value: summary.pages },
      { label: 'Assignments', value: summary.assignments },
      { label: 'Discussions', value: summary.discussions },
      { label: 'Quizzes', value: summary.quizzes },
      { label: 'Modules', value: summary.modules },
    ]);

    const plan = $('#bdMfPlan');
    // A remembered plan belongs to one course; another course's table must not
    // paint here as if it were this one's.
    if (S.buildArea.mfPlan && S.buildArea.mfPlanFor === String(courseId)) {
      buildManifestPlan(plan, S.buildArea.mfPlan);
    }
    $('#bdMfDry').onclick = () => runJob('Manifest dry run',
      () => api(buildBase(courseId) + '/manifest/push', { body: mfBody(false) }),
      res => {
        S.buildArea.mfPlan = (res && res.plan) || null;
        S.buildArea.mfPlanFor = String(courseId);
        buildManifestPlan(plan, S.buildArea.mfPlan);
        setStatus('dry run done; nothing was sent', 'ok');
      }, { autoClose: true });
    $('#bdMfPush').onclick = () => {
      if (problems.length) { setStatus('fix the manifest problems first', 'err'); return; }
      runJobConfirmed('Pushing the manifest',
        token => api(buildBase(courseId) + '/manifest/push', {
          body: Object.assign(mfBody(true), { confirm: token }),
        }),
        res => {
          S.buildArea.mfPlan = (res && res.plan) || S.buildArea.mfPlan;
          S.buildArea.mfPlanFor = String(courseId);
          buildManifestPlan(plan, S.buildArea.mfPlan, res && res.result);
          setStatus('the manifest was pushed', 'ok');
          announce('The manifest was pushed to Canvas');
        },
        { title: 'Build this course from the manifest?', verb: 'Yes, push it' });
    };
  }

  function buildManifestPlan(host, plan, result) {
    if (!host) return;
    if (!plan) { host.innerHTML = ''; return; }
    const rows = plan.rows || plan.pages || [];
    const gate = plan.gate || {};
    const verify = plan.verify || {};
    host.innerHTML = `
      ${gate.refused ? `<div class="callout bad"><b>The modules are not rebuilt.</b>
        ${esc(gate.sentence || '')} Tick Leave the modules alone to push the content and keep the
        module list exactly as it is, or Rebuild the modules to replace it, then run the dry run
        again. Until one is ticked, nothing is pushed.</div>`
        : (gate.sentence ? `<div class="callout">${esc(gate.sentence)}</div>` : '')}
      ${verify.summary ? `<p class="muted">${esc(verify.summary)}</p>` : ''}
      ${(verify.failing || []).length ? `<ul class="applyList">${verify.failing.slice(0, 12).map(f =>
        `<li class="bad"><b>${esc(f.title || f.key || '')}</b>
          <span class="muted"> ${esc(f.state || '')} ${esc((f.problems || []).join('; '))}</span></li>`).join('')}</ul>` : ''}
      <h3 class="gwH">${esc(result ? 'Pushed' : 'Would be pushed')} (${rows.length})</h3>
      ${rows.length ? `<div class="gwTableWrap"><table class="gwTable">
        <caption class="srOnly">Every item the manifest would create or update</caption>
        <thead><tr><th scope="col">Title</th><th scope="col">What happens</th>
          <th scope="col">Module</th><th scope="col">Why</th></tr></thead>
        <tbody>${rows.map(r => `<tr>
          <th scope="row">${esc(r.title || r.key || '')}</th>
          <td>${pill(r.action || 'skip', r.action === 'skip' ? 'muted'
            : r.action === 'create' ? 'v-pass' : 'st-fetched')}</td>
          <td>${esc(r.module || '')}</td>
          <td class="muted">${esc(r.reason || '')}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="empty"><p>The manifest matches the course already; there is nothing to push.</p></div>'}
      <p class="muted gwNote">${esc(result ? 'Done. ' + (result.count || 0) + ' item(s) written.'
        : 'A dry run. Nothing has been sent to Canvas.')}</p>`;
  }

  /* ---------------------------------------------------------- the rubrics */
  async function buildRubrics(courseId, body) {
    body.innerHTML = '<p class="muted">Reading the rubric definitions…</p>';
    let got;
    try { got = await api(buildBase(courseId) + '/rubrics'); }
    catch (err) {
      body.replaceChildren(emptyState('Could not read the rubrics: ' + firstLine(err.message)));
      return;
    }
    const rubrics = got.rubrics || [];
    if (!rubrics.length) {
      body.innerHTML = '<div id="bdRbEmpty"></div>';
      $('#bdRbEmpty').appendChild(emptyState('No rubric definitions in this course yet. A rubric '
        + 'file is a JSON list of rubrics, each with its criteria and ratings and the assignment it '
        + 'attaches to; put one at ' + (got.path || 'the course folder') + ' and it shows up here. '
        + 'Nothing is pushed to Canvas until you ask, and the dry run comes first.'));
      return;
    }
    body.innerHTML = `
      <p class="muted">${esc(rubrics.length)} rubric definition(s) read from
        <code>${esc(got.path || '')}</code>.</p>
      <div class="gwTableWrap"><table class="gwTable">
        <caption class="srOnly">The rubric definitions on this computer</caption>
        <thead><tr><th scope="col">Rubric</th><th scope="col">Attaches to</th>
          <th scope="col">Criteria</th><th scope="col">Points</th></tr></thead>
        <tbody>${rubrics.map(r => `<tr>
          <th scope="row">${esc(r.title || '(untitled)')}</th>
          <td>${esc(r.assignment || '')}</td>
          <td class="num">${esc((r.criteria || []).length)}</td>
          <td class="num">${esc((r.criteria || []).reduce((n, c) => n + (+c.points || 0), 0))}</td>
        </tr>`).join('')}</tbody></table></div>
      <div class="bdActs">
        <button class="btn primary" type="button" id="bdRbDry">Dry run</button>
        <button class="btn danger" type="button" id="bdRbPush">Push rubrics…</button>
        <span class="hint">The dry run reads the course's assignments and changes nothing.</span>
      </div>
      <div id="bdRbPlan"></div>`;
    const plan = $('#bdRbPlan');
    $('#bdRbDry').onclick = () => runJob('Rubrics dry run',
      () => api(buildBase(courseId) + '/rubrics/push', { body: { apply: false } }),
      res => { buildRubricPlan(plan, res && res.plan); setStatus('dry run done; nothing was sent', 'ok'); },
      { autoClose: true });
    $('#bdRbPush').onclick = () => runJobConfirmed('Pushing the rubrics',
      token => api(buildBase(courseId) + '/rubrics/push', { body: { apply: true, confirm: token } }),
      res => {
        buildRubricPlan(plan, res && res.plan, res && res.result);
        setStatus('the rubrics were pushed', 'ok');
        announce('The rubrics were pushed to Canvas');
      },
      { title: 'Attach these rubrics in the live course?', verb: 'Yes, push them' });
  }

  function buildRubricPlan(host, plan, result) {
    if (!host || !plan) return;
    const rows = plan.rows || [];
    host.innerHTML = `<h3 class="gwH">${esc(result ? 'Pushed' : 'Would be pushed')} (${
      rows.filter(r => r.action !== 'skip').length})</h3>
      <ul class="applyList">${rows.map(r => `<li class="${r.action === 'skip' ? 'bad'
        : result ? 'done' : 'planned'}"><b>${esc(r.title || '')}</b>
        <span class="muted"> ${esc(r.action)} · ${esc(r.assignment || 'no assignment')}
        ${esc((r.warnings || []).join('; '))}</span></li>`).join('')}</ul>
      <p class="muted gwNote">${esc(result ? 'Done.' : 'A dry run. Nothing has been sent to Canvas.')}</p>`;
  }

  /* ------------------------------------------------------------ register */
  window.openBuild = openBuild;
  registerArea({
    id: 'build', label: 'Build', zone: 'build', open: openBuild,
    badge: hub => (hub && hub.build && hub.build.badge != null) ? hub.build.badge : null,
  });
  Object.assign(window.Studio || (window.Studio = {}), { openBuild });
})();
