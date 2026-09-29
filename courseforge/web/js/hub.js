/* CourseForge Studio - the course hub. One screen per course: a navy band with
   the name and a few figures, five cards saying where each area stands (from
   local state only, so it paints at once), the install cards for anything
   missing, and the last eight things the Studio wrote to Canvas here. Also the
   picker's "Across your courses" strip. */
(function () {
  'use strict';

  S.hub = S.hub || {};

  /* The five cards, in the order the area bar shows them. `blurb` is the empty
     state: what the first action does, and that nothing is pushed. */
  const CARDS = [
    { id: 'grade', label: 'Grade', zone: 'grade', href: cid => `#/c/${cid}/grade`,
      blurb: 'Sync an assignment, grade it with Claude against the rubric, and push the scores when you confirm. Students can see a grade once it is posted.' },
    { id: 'a11y', label: 'Accessibility', zone: 'a11y', href: cid => `#/c/${cid}/a11y`,
      blurb: 'Fix pages, PowerPoints, Word files and PDFs to ADA and Ally standards. Every change is verified against the original and pushed only when you confirm.' },
    { id: 'build', label: 'Build', zone: 'build', href: cid => `#/c/${cid}/build`,
      blurb: 'Draft a page, assignment, quiz or study guide with Claude, then place it in a module. Drafts stay here until you place them.' },
    { id: 'tools', label: 'Tools', zone: 'tools', href: cid => `#/c/${cid}/tools`,
      blurb: 'Shift due dates, export or clone the course, trim the navigation, back up a quiz. Each one is a dry run first.' },
    { id: 'assistant', label: 'Assistant', zone: 'ai', href: cid => `#/c/${cid}/assistant`,
      blurb: 'Ask for work in plain words. Every Canvas write stops for your Allow, with the exact command shown.' },
    { id: 'record', label: 'Record', zone: 'record', href: cid => `#/c/${cid}/record`,
      blurb: 'Every change the Studio makes in this course, written down as it happens and accommodations named by student. Kept in your own Canvas files, so it outlives this PC.' },
    { id: 'attendance', label: 'Attendance', zone: 'record', href: cid => `#/c/${cid}/attendance`,
      blurb: 'Mark who was here, tardy, or absent, and see it on the term calendar. Nothing is written to the gradebook.' },
  ];

  const stillHere = cid => S.view === 'hub' && S.route && String(S.route.courseId) === String(cid);
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  /* ---------------------------------------------------------------- open */
  async function openHub(courseId) {
    showView('hub');
    $('#hubTitle').textContent = (S.course && String(S.course.id) === String(courseId)
      && courseTitle(S.course)) || 'Course';
    $('#hubSub').textContent = 'Loading…';
    $('#hubStats').innerHTML = '';
    $('#hubBody').innerHTML = '';
    $('#headerActions').innerHTML = `<button class="btn" id="hubRefresh"
      title="Re-read this course's assignment list from Canvas. Reads only.">Refresh from Canvas</button>`;
    $('#hubRefresh').onclick = () => refreshHub(courseId);

    const course = await ensureCourse(courseId);
    if (!stillHere(courseId)) return;
    crumbs([{ label: 'Courses', href: '#/' }, { label: courseTitle(course) }]);
    $('#hubTitle').textContent = courseTitle(course);
    $('#hubTitle').title = course.name || '';
    document.title = `${courseTitle(course)} · CourseForge Studio`;

    let hub;
    try { hub = await api(`/courses/${courseId}/hub`); }
    catch (err) {
      if (!stillHere(courseId)) return;
      $('#hubSub').textContent = '';
      $('#hubBody').replaceChildren(emptyState('Could not load this course: ' + firstLine(err.message)
        + '. Nothing has been changed.', 'Try again', () => openHub(courseId)));
      return;
    }
    if (!stillHere(courseId)) return;
    adopt(courseId, hub);
    paint(courseId, hub);
    if (hub.stale) quietRefresh(courseId);
  }

  /* Keep what the server said where the area bar and the areas can see it. */
  function adopt(courseId, hub) {
    S.hub = { ...S.hub, courseId, areas: hub.areas || {}, data: hub, at: Date.now() };
    const c = hub.course || {};
    if (c.name && (!S.course || String(S.course.id) !== String(courseId) || /^Course \d+$/.test(S.course.name || ''))) {
      S.course = { ...(S.course && String(S.course.id) === String(courseId) ? S.course : {}), ...c };
    }
    renderAreaBar(courseId, null);
  }

  /* --------------------------------------------------------------- paint */
  function paint(courseId, hub) {
    const course = { ...(S.course || {}), ...(hub.course || {}) };
    $('#hubTitle').textContent = courseTitle(course) || 'Course ' + courseId;
    const bits = [];
    if (course.term_label || course.term) bits.push(course.term_label || course.term);
    if (course.code) bits.push(course.code);
    if (course.students != null) bits.push(`${course.students} student${course.students === 1 ? '' : 's'}`);
    if (hub.stale) bits.push('assignment list not read from Canvas recently');
    $('#hubSub').textContent = bits.join(' · ');
    statStrip($('#hubStats'), hub.stats || []);

    const areas = hub.areas || {};
    $('#hubBody').innerHTML = `
      <div class="hubGrid">${CARDS.map(c => cardHtml(c, courseId, areas[c.id], hub)).join('')}</div>
      <div id="hubNeeds" class="hubSection"></div>
      <section class="hubSection" aria-labelledby="hubLedgerH">
        <div class="sectionHead"><h2 id="hubLedgerH">What changed in this course</h2>
          <span class="hint">The last eight Canvas writes from the Studio, every area.</span></div>
        <div id="hubLedger"></div>
      </section>`;

    const needs = hub.needs || [];
    if (needs.length) {
      const host = $('#hubNeeds');
      host.innerHTML = `<div class="sectionHead"><h2>Install to enable</h2>
        <span class="hint">These tools are missing on this machine. Everything else works without them.</span></div>
        <div id="hubNeedCards"></div>`;
      needCards(needs, $('#hubNeedCards'));
    }
    renderLedger($('#hubLedger'), (hub.ledger || []).slice(0, 8), {
      empty: 'Nothing has been written to Canvas from here yet.',
      onChange: () => openHub(courseId),
    });
  }

  function cardHtml(c, courseId, a, hub) {
    a = a || {};
    const installed = a.installed !== false;
    const lines = (a.lines && a.lines.length) ? a.lines : [installed ? c.blurb : 'Not installed in this build.'];
    let acts = (a.actions && a.actions.length) ? a.actions.slice() : (installed ? [{ label: 'Open ' + c.label, href: c.href(courseId) }] : []);
    if (c.id === 'grade') {
      const last = continueTarget(courseId, a, hub);
      if (last) acts.unshift(last);
    }
    const badge = a.badge;
    const hasBadge = !(badge == null || badge === 0 || badge === '');
    return `<article class="hubCard ${installed ? '' : 'off'}" data-zone="${c.zone}" aria-labelledby="hubCard-${c.id}">
      <h2 id="hubCard-${c.id}">${installed ? `<a href="${c.href(courseId)}">${esc(c.label)}</a>` : esc(c.label)}
        ${hasBadge ? `<span class="n" title="${esc(a.badge_title || 'waiting for you')}">${esc(badge)}</span>` : ''}</h2>
      <ul class="hubLines">${lines.map(l => `<li>${esc(l)}</li>`).join('')}</ul>
      ${(a.needs && a.needs.length) ? `<p class="zoneNote">Needs ${esc(a.needs.join(', '))}: see the install cards below.</p>` : ''}
      ${a.error ? `<p class="zoneNote bad">${esc(a.error)}</p>` : ''}
      <div class="hubActs">${acts.map((x, i) => actHtml(x, i === 0)).join('')}</div>
    </article>`;
  }

  function actHtml(x, primary) {
    const cls = `btn ${primary ? 'primary' : ''} ${x.cls || ''}`.trim();
    if (x.href) return `<a class="${cls}" href="${esc(x.href)}"${x.title ? ` title="${esc(x.title)}"` : ''}>${esc(x.label)}</a>`;
    return `<button class="${cls}" type="button" disabled title="${esc(x.title || 'Not available here')}">${esc(x.label)}</button>`;
  }

  /* "Continue grading X": the assignment this browser last had open in this
     course, named from the cached list when the name is known. */
  function continueTarget(courseId, grade, hub) {
    const aid = lastAssignment(courseId);
    if (!aid) return null;
    const list = (grade && grade.assignments) || S.assignments || [];
    const found = list.find(a => String(a.id) === String(aid));
    const name = found ? found.name : null;
    return {
      label: 'Continue grading ' + (name ? shortName(name) : 'where you left off'),
      href: `#/c/${courseId}/a/${aid}`,
      title: name ? `Open ${name}` : 'Open the assignment you last had open here',
    };
  }
  const shortName = s => (s.length > 38 ? s.slice(0, 37) + '…' : s);

  /* ------------------------------------------------------------- refresh */
  /* The one Canvas read the hub makes, and only when asked or when the cached
     assignment list is old. Reads only; the job says so in its log. */
  function refreshHub(courseId) {
    runJob('Refreshing from Canvas',
      () => api(`/courses/${courseId}/hub/refresh`, { body: {} }),
      result => { if (stillHere(courseId) && result) { adopt(courseId, result); paint(courseId, result); } },
      { autoClose: true });
  }

  async function quietRefresh(courseId) {
    if (S.hub.refreshing) return;
    if (S.health && S.health.canvas && S.health.canvas.ok === false) return;   // the banner says why
    S.hub.refreshing = true;
    setStatus('refreshing the assignment list from Canvas…');
    try {
      const { job } = await api(`/courses/${courseId}/hub/refresh`, { body: {} });
      let info = null;
      for (let i = 0; i < 180; i++) {
        info = await api('/jobs/' + job);
        if (info.state !== 'running' || !stillHere(courseId)) break;
        await sleep(700);
      }
      if (!stillHere(courseId)) return;
      if (info && info.state === 'done' && info.result) {
        adopt(courseId, info.result);
        paint(courseId, info.result);
        setStatus('assignment list refreshed from Canvas', 'ok');
      } else if (info && info.state === 'error') {
        setStatus('could not refresh: ' + firstLine(info.error), 'err');
      }
    } catch (err) {
      if (stillHere(courseId)) setStatus('could not refresh: ' + firstLine(err.message), 'err');
    } finally {
      S.hub.refreshing = false;
    }
  }

  /* --------------------------------------------- across your courses strip */
  /* Under the course cards on the picker: the three things that are not about
     one course. Painted on the shell's studio:picker event, which fires each
     time the course list is shown and never for the assignment list. */
  /* A stack beside the course list rather than a fourth row of cards under it.
     Built as cards, these four sat in the same grid as the courses in the same
     shape, and the page read as nine equal boxes of which four were not
     courses at all. */
  function renderAcross(host) {
    if (!host) return;
    const batchOn = typeof openBatch === 'function' || (window.Studio && window.Studio.areas && window.Studio.areas.has('a11y'));
    /* The sentence stays on the link (hover and screen readers). Drawing it
       under the name made this column taller than a 1080p window. */
    const item = (href, ic, title, blurb, on) => {
      const note = on ? blurb : 'Not installed in this build.';
      const body = `<span class="ic${on ? ' ' + ic : ''}"></span>
        <span><span class="t">${esc(title)}</span>
        <span class="m">${esc(note)}</span></span>`;
      if (!on) {
        return `<span class="acrossItem off" aria-disabled="true" title="${esc(note)}">${body}</span>`;
      }
      return `<a class="acrossItem" href="${href}" title="${esc(note)}">${body}</a>`;
    };
    host.innerHTML = `<div class="sectionHead"><h2 id="acrossH">Across your courses</h2>
      <span class="hint">several at once</span></div>
    <div class="acrossCol">
    <section class="acrossStack" aria-labelledby="acrossH">
      ${item('#/schedule', 'grade', 'Term schedule',
        'Every dated assignment in the term, week by week, with reminders and announcements.', true)}
      ${item('#/students', 'grade', 'Students',
        'Search one person across every course: accommodations, extensions, grades, mail and notes.', true)}
      ${item('#/roster', 'grade', 'Accommodations roster',
        'Standing extra time and attempts, applied to any quiz in any course.', true)}
      ${item('#/batch', 'a11y', 'Batch Course Restyle',
        'Restyle and verify pages across several courses at once. Dry run first.', batchOn)}
      ${item('#/files', 'a11y', 'ADA file compliance',
        'PDFs, slide decks and Word documents across several courses. Survey first; nothing is uploaded until you say so.', batchOn)}
      ${item('#/extensions', 'record', 'Deadline extensions',
        "A student was ill or bereaved: give them longer on the work that fell during the absence, in every course they are in. Nobody else's dates move.", true)}
      ${item('#/reports', 'record', 'Reports',
        'An accessibility score with a before and an after, and every syllabus checked against the statements your college requires.', true)}
    </section>
    <div id="behindMount"></div>
    </div>`;
    if (typeof S !== 'undefined' && Array.isArray(S.behind)) renderBehind(host, S.behind);
  }

  /* To Do, same name as the Canvas list. Five assignments past their due
     date, furthest first. The number is calendar days, not submissions. */
  function renderBehind(host, rows) {
    if (!host) return;
    let mount = host.querySelector('#behindMount');
    if (!mount) {
      mount = document.createElement('div');
      mount.id = 'behindMount';
      const col = host.querySelector('.acrossCol');
      (col || host).appendChild(mount);
    }
    const list = (Array.isArray(rows) ? rows : []).filter(row => (+row.days_behind || 0) >= 1).slice(0, 5);
    const body = list.length
      ? `<div class="behindList">${list.map(row => {
          const course = row.course_title || row.course_code || '';
          const code = row.course_code && row.course_code !== course ? row.course_code : '';
          const klass = [code, course].filter(Boolean).join(' ') || 'Course';
          const due = row.due_at && typeof fmtDate === 'function' ? fmtDate(row.due_at) : '';
          const assignment = row.name || 'Assignment';
          const meta = [klass, due].filter(Boolean).join(' · ');
          const days = +row.days_behind || 0;
          const label = days === 1 ? '1 day behind' : days + ' days behind';
          return `<a class="behindItem" href="#/c/${esc(row.course_id)}/a/${esc(row.assignment_id)}">
            <span class="behindText">
              <span class="nm">${esc(assignment)}</span>
              <span class="m">${esc(meta)}</span></span>
            <span class="n">${esc(label)}</span></a>`;
        }).join('')}</div>`
      : '<p class="behindEmpty">Nothing is past its due date.</p>';
    mount.innerHTML = `<section class="behindGrade" aria-labelledby="behindH">
      <h2 id="behindH">To Do</h2>
      ${body}</section>`;
  }

  document.addEventListener('studio:picker', ev => renderAcross(ev.detail && ev.detail.host));

  window.openHub = openHub;
  window.renderBehind = renderBehind;
  Object.assign(window.Studio || (window.Studio = {}), { openHub, refreshHub });
})();
