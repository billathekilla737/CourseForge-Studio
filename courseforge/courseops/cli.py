"""Terminal and Assistant verbs for the Course tools.

    python -m courseforge course export      --course ID [--type cartridge|zip] [--out DIR]
    python -m courseforge course import      --course ID (--from ID | --imscc FILE)
                                             [--into ID | --new-shell NAME [--account ID]]
                                             [--force] [--apply]
    python -m courseforge course clone       --course ID --name NAME [--account ID]
                                             [--code CODE] [--apply]
    python -m courseforge course nav         --course ID [--keep ID ...] [--apply]
    python -m courseforge course due-dates   --course ID [--start YYYY-MM-DD] [--weeks N]
                                             [--finals-end YYYY-MM-DD] [--breaks LIST]
                                             [--weekday Monday] [--time 23:59] [--apply]
    python -m courseforge course quiz-backup --course ID [--quiz ID] [--apply]
    python -m courseforge course slo         --course ID [--step resolve|fetch|align|report|all]
                                             [--program SLUG] [--use-prior] [--apply]

Every writing verb is a dry run without --apply and prints the plan either
way. With --apply it prints the same sentence the web UI shows and asks for a
typed yes (cli.confirm_apply), unless it runs inside a gated Assistant session
where the Allow click was the confirmation.

Exit codes: 0 ok, 1 error, 2 usage or verify failure, 3 refused.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import calendar as cal
from . import due_dates, export_import, nav, quiz_backup, slo
from .common import Refused, plural


def register(sub) -> None:
    p = sub.add_parser("course", help="Course tools: export, import, clone, navigation, "
                                      "due dates, quiz backup, outcome alignment")
    p.set_defaults(area="courseops")
    v = p.add_subparsers(dest="verb", required=True)

    def course(parser):
        parser.add_argument("--course", required=True, help="Canvas course id")

    e = v.add_parser("export", help="download the whole course as a file on this machine")
    course(e)
    e.add_argument("--type", choices=["cartridge", "imscc", "zip"], default="cartridge",
                   help="cartridge is the whole course; zip is the files only")
    e.add_argument("--out", help="where to put the file (default: data/<course>/exports)")

    i = v.add_parser("import", help="copy another course, or an .imscc file, into a course")
    course(i)
    i.add_argument("--from", dest="source", help="the source course id")
    i.add_argument("--imscc", help="an .imscc cartridge file to import")
    i.add_argument("--into", help="the destination course id (default: --course)")
    i.add_argument("--new-shell", dest="new_shell", help="create an unpublished shell with this name")
    i.add_argument("--account", help="the account to create the shell in")
    i.add_argument("--code", default="", help="course code for the new shell")
    i.add_argument("--force", action="store_true",
                   help="import even though the destination already has content")
    i.add_argument("--apply", action="store_true")

    c = v.add_parser("clone", help="this course into a new unpublished shell")
    course(c)
    c.add_argument("--name", required=True, help="name for the new shell")
    c.add_argument("--account", help="the account to create it in")
    c.add_argument("--code", default="")
    c.add_argument("--apply", action="store_true")

    n = v.add_parser("nav", help="trim the left-hand navigation to a keep-list")
    course(n)
    n.add_argument("--keep", nargs="*", help="tab ids to keep, in order (default: config or the "
                                             "school list)")
    n.add_argument("--apply", action="store_true")

    d = v.add_parser("due-dates", help="lay the Week N modules onto the term calendar")
    course(d)
    d.add_argument("--start", help="term start, yyyy-mm-dd")
    d.add_argument("--weeks", type=int, help="instructional weeks")
    d.add_argument("--term", help="term name, e.g. Fall 2026")
    d.add_argument("--finals-end", dest="finals_end", help="last day of finals, yyyy-mm-dd")
    d.add_argument("--breaks", help="days off: '2026-09-07,2026-11-23..2026-11-27'")
    d.add_argument("--weekday", choices=list(due_dates.WEEKDAYS), help="due weekday (default Monday)")
    d.add_argument("--time", help="due time, hh:mm (default 23:59)")
    d.add_argument("--apply", action="store_true")

    q = v.add_parser("quiz-backup", help="an unpublished copy of one Classic Quiz")
    course(q)
    q.add_argument("--quiz", help="the quiz id (omit to list the quizzes)")
    q.add_argument("--apply", action="store_true")

    s = v.add_parser("slo", help="align the course against its Student Learning Outcomes")
    course(s)
    s.add_argument("--step", choices=["resolve", "fetch", "align", "report", "all"],
                   default="all", help="one step, or the whole run")
    s.add_argument("--program", help="program slug, when the course code is ambiguous")
    s.add_argument("--course-code", dest="course_code", help="override the Canvas course code")
    s.add_argument("--use-prior", dest="use_prior", action="store_true",
                   help="read the previous year's framework")
    s.add_argument("--model", help="model for the judgment step")
    s.add_argument("--apply", action="store_true",
                   help="push the report into the course as an unpublished page")


class _Ctx:
    """What the modules need from an App, built from a Config for the terminal."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._client = None
        try:
            from ..store import Store
            self.store = Store(cfg.data)
            self.data_root = self.store.root
        except Exception:  # noqa: BLE001
            self.store = None
            self.data_root = Path(cfg.data)

    @property
    def content(self):
        if self._client is None:
            from ..canvas import CanvasClient
            self._client = CanvasClient(self.cfg.base_url, self.cfg.token(), scope="content",
                                        allowed_hosts=getattr(self.cfg, "canvas_hosts", None))
        return self._client

    def course_dir(self, course_id) -> Path:
        from ..store import Store
        return Store(Path(self.data_root)).course_dir(course_id)


def _log(message, done=None, total=None) -> None:  # noqa: ARG001
    print("  " + str(message))


def _writes_allowed(cfg) -> bool:
    if getattr(cfg, "allow_canvas_writes", True):
        return True
    print('Canvas writes are locked off in config.json ("allow_canvas_writes": false). '
          "Nothing was sent.")
    return False


def _gate(cfg):
    """The second click on a terminal: the module's own sentence, then a typed
    yes. A no raises, which the verb turns into exit 3."""
    from .. import cli

    def gate(kind, payload, sentence, detail=None):  # noqa: ARG001
        for row in (detail or [])[:40]:
            if isinstance(row, dict):
                print("  %-46s %s -> %s" % (str(row.get("label", ""))[:46],
                                            row.get("from") or "", row.get("to") or ""))
        print()
        if not cli.confirm_apply(sentence):
            raise Refused("Not applied.")
    return gate


def run(args, cfg) -> int:
    ctx = _Ctx(cfg)
    cid = str(args.course)
    verb = args.verb
    try:
        if verb == "export":
            return _export(ctx, cfg, cid, args)
        if verb == "import":
            return _import(ctx, cfg, cid, args)
        if verb == "clone":
            return _clone(ctx, cfg, cid, args)
        if verb == "nav":
            return _nav(ctx, cfg, cid, args)
        if verb == "due-dates":
            return _dates(ctx, cfg, cid, args)
        if verb == "quiz-backup":
            return _quiz(ctx, cfg, cid, args)
        if verb == "slo":
            return _slo(ctx, cfg, cid, args)
    except Refused as exc:
        print(f"Refused: {exc}")
        return 3
    print(f"unknown verb {verb!r}", file=sys.stderr)
    return 2


# ------------------------------------------------------------------ export
def _export(ctx, cfg, cid, args) -> int:
    kind = "zip" if args.type == "zip" else "common_cartridge"
    print(f"Exporting course {cid} as {'a zip of files' if kind == 'zip' else 'a common cartridge'}.")
    entry = export_import.export_course(ctx, cid, args.out, _log, export_type=kind)
    print(f"\nEXPORTED -> {entry['path']} ({entry['kb']} KB)")
    print("Keep this file on this machine. A taught course's cartridge can carry "
          "student-written discussion text.")
    return 0


# ------------------------------------------------------------------ import
def _import(ctx, cfg, cid, args) -> int:
    new_course = None
    if args.new_shell:
        new_course = {"name": args.new_shell, "account_id": args.account, "course_code": args.code}
    dest = None if new_course else (args.into or cid)
    try:
        plan = export_import.import_plan(ctx, dest, args.source, args.imscc, new_course,
                                         args.force, _log)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Refused: {exc}")
        return 2
    print()
    print(export_import.plan_text(plan))
    if plan["refusal"]:
        print("\nNothing was sent. Add --force to import anyway.")
        return 3 if args.apply else 0
    if not args.apply:
        print("\nDry run. Nothing was sent. Add --apply to import.")
        return 0
    if not _writes_allowed(cfg):
        return 3
    out = export_import.import_course(ctx, dest, args.source, args.imscc, new_course,
                                      args.force, apply=True, gate=_gate(cfg), log=_log,
                                      ledger_course_id=cid)
    result = out.get("result") or {}
    print(f"\nIMPORT COMPLETE -> course {result.get('dest')}: {result.get('url')}")
    for issue in result.get("issues") or []:
        print(f"  issue: {issue.get('kind')}: {issue.get('text')}")
    return 0


def _clone(ctx, cfg, cid, args) -> int:
    plan = export_import.clone_course(ctx, cid, args.name, args.account, args.code,
                                      apply=False, log=_log)
    print()
    print(export_import.plan_text(plan))
    if plan["refusal"]:
        print(f"\nRefused: {plan['refusal']}")
        return 3
    if not args.apply:
        print("\nDry run. Nothing was sent. Add --apply to create the shell and copy into it.")
        return 0
    if not _writes_allowed(cfg):
        return 3
    out = export_import.clone_course(ctx, cid, args.name, args.account, args.code,
                                     apply=True, gate=_gate(cfg), log=_log)
    result = out.get("result") or {}
    print(f"\nCLONE COMPLETE -> course {result.get('dest')}: {result.get('url')} (unpublished)")
    return 0


# --------------------------------------------------------------------- nav
def _nav(ctx, cfg, cid, args) -> int:
    keep = [str(k) for k in args.keep] if args.keep else None
    plan = nav.read_plan(ctx, cid, keep)
    label = _label(ctx, cid)
    print(f"NAVIGATION PLAN for {label}\n")
    print(nav.plan_text(plan))
    if not plan["changes"]:
        print("\nThe navigation already matches the keep-list. Nothing to write.")
        return 0
    if not args.apply:
        print(f"\nDry run. {plural(plan['changes'], 'tab')} would change. Nothing was sent. "
              "Add --apply to write.")
        return 0
    if not _writes_allowed(cfg):
        return 3
    out = nav.apply(ctx, cid, keep, _gate(cfg), _log)
    print(f"\nNAVIGATION WRITTEN: {out['written']} changed, {out['failed']} failed, "
          f"{out['mismatches']} answered 200 without changing.")
    print("visible now: " + "  >  ".join(out.get("visible_after_live") or []))
    return 1 if (out["failed"] or out["mismatches"]) else 0


# ------------------------------------------------------------------- dates
def _dates(ctx, cfg, cid, args) -> int:
    overrides = {}
    for key in ("start", "weeks", "term", "finals_end", "weekday", "time"):
        value = getattr(args, key, None)
        if value not in (None, ""):
            overrides[key] = value
    if args.breaks:
        try:
            overrides["breaks"] = cal.parse_breaks_text(args.breaks)
        except ValueError as exc:
            print(f"--breaks: {exc}", file=sys.stderr)
            return 2
    try:
        plan = due_dates.plan(ctx, cid, overrides)
    except ValueError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    facts = plan["facts"]
    print(f"DUE DATES for {_label(ctx, cid)}")
    for key in ("start", "weeks", "term", "finals_end"):
        print(f"  {key:<12} {facts.get(key) if facts.get(key) is not None else 'NOT KNOWN':<14} "
              f"{facts['sources'].get(key, '')}")
    print(f"  {'breaks':<12} {', '.join(facts.get('breaks') or []) or 'none'}")
    print(f"  {'due':<12} {facts.get('weekday')} at {facts.get('time')}")
    if facts["missing"]:
        print("\nTell me: " + ", ".join(facts["missing"])
              + ". Pass them as --start, --weeks, --finals-end.")
        return 2
    print()
    print(due_dates.table_text(plan["rows"]))
    print(f"\n{plural(plan['writes'], 'item')} would get a new due date.")
    if not args.apply:
        print("Dry run. Nothing was sent. Add --apply to write the dates.")
        return 0
    if not plan["writes"]:
        return 0
    if not _writes_allowed(cfg):
        return 3
    out = due_dates.apply(ctx, cid, plan["rows"], _gate(cfg), _log)
    print(f"\nDUE DATES WRITTEN: {out['written']} set, {out.get('failed', 0)} failed.")
    for r in out.get("results") or []:
        if not r["ok"]:
            print(f"  FAILED {r['name']}: {r['note']}")
    return 1 if out.get("failed") else 0


# ------------------------------------------------------------- quiz backup
def _quiz(ctx, cfg, cid, args) -> int:
    if not args.quiz:
        print(f"QUIZZES in {_label(ctx, cid)}\n")
        print("%-12s %-52s %-9s %-6s %s" % ("ID", "TITLE", "PUBLISHED", "Qs", "POINTS"))
        for q in quiz_backup.quizzes(ctx, cid):
            print("%-12s %-52s %-9s %-6s %s" % (
                q["id"], (q["title"] or "")[:52], "yes" if q["published"] else "no",
                q["question_count"], q["points_possible"]))
        print("\nPick one with --quiz ID. Nothing was sent.")
        return 0
    plan = quiz_backup.plan(ctx, cid, args.quiz)
    src = plan["source"]
    print(f"BACK UP QUIZ {src['id']}: {src['title']}")
    print(f"  {plural(src['questions'], 'question')}, {src['points']:g} points, "
          f"{'published' if src['published'] else 'unpublished'}")
    print(f"  types: {', '.join(f'{k} x{v}' for k, v in (src.get('types') or {}).items()) or 'none'}")
    print(f"  would create: {plan['target']['title']} (unpublished)")
    if plan["refusal"]:
        print(f"\nRefused: {plan['refusal']}")
        return 3
    if not args.apply:
        print("\nDry run. Nothing was sent. Add --apply to create the copy.")
        return 0
    if not _writes_allowed(cfg):
        return 3
    entry = quiz_backup.backup(ctx, cid, args.quiz, _gate(cfg), _log)
    print(f"\nBACKUP CREATED: {entry['title']} ({entry['questions_copy']} of "
          f"{entry['questions_source']} questions) -> {entry['url']}")
    for w in entry.get("warnings") or []:
        print(f"  WARNING: {w}")
    return 1 if (entry.get("warnings") or entry.get("posted_failed")) else 0


# --------------------------------------------------------------------- slo
def _slo(ctx, cfg, cid, args) -> int:
    step = args.step
    label = _label(ctx, cid)

    if step in ("resolve", "all"):
        code = args.course_code
        if not code:
            code = (ctx.content.course_detail(cid) or {}).get("course_code")
        found = slo.resolve(code, app=ctx, course_id=cid)
        print(f"RESOLVE {code or label}")
        for p in found["candidates"]:
            print("  %-56s CIP %-9s %s" % (p["name"][:56], p.get("cip"), p.get("url")))
        print(f"\n{found['conclusion']}")
        if step == "resolve":
            return 0 if found["candidates"] and not found["ambiguous"] else 2
        if found["ambiguous"] and not args.program:
            print("\nPick one with --program SLUG and run again. Nothing was guessed.")
            return 2
        if not found["candidates"] and not args.program:
            return 2

    if step in ("fetch", "all"):
        out = slo.fetch(ctx, cid, slug=args.program, course_code=args.course_code,
                        use_prior=args.use_prior, log=_log)
        course = out.get("course") or {}
        print(f"\nFRAMEWORK {out['framework']['name']} ({out['framework'].get('year')})")
        print(f"  course  {course.get('course')} {course.get('title') or ''} "
              f"(matched {out.get('match')})")
        print(f"  {out['outcomes']} outcomes, {out['items']['graded']} graded items in the course")
        if out.get("note"):
            print(f"  note: {out['note']}")
        if step == "fetch":
            return 0

    if step in ("align", "all"):
        out = slo.align(ctx, cid, log=_log, model=args.model)
        checked = out["validate"]
        counts = checked.get("counts") or {}
        print(f"\nALIGNMENT: {counts.get('mapped', 0)} of {counts.get('outcomes', 0)} outcomes "
              f"mapped, {counts.get('assessed', 0)} assessed, {counts.get('partial', 0)} partly, "
              f"{counts.get('gaps', 0)} gaps. About ${out.get('cost_usd', 0):.2f}.")
        for w in checked.get("warnings") or []:
            print(f"  warn: {w}")
        for e in checked.get("errors") or []:
            print(f"  ERROR: {e}")
        if not checked["ok"]:
            print("\nThe alignment does not pass the check, so no report is written.")
            return 2
        if step == "align":
            return 0

    plan = slo.report(ctx, cid, apply=False, log=_log)
    counts = plan["counts"]
    print(f"\nREPORT: {counts.get('coverage', 0)}% of outcomes assessed, "
          f"{plural(counts.get('gaps', 0), 'gap')} listed.")
    print(f"  markdown -> {plan['md_path']}")
    print(f"  html     -> {plan['html_path']}")
    print(f"  {plan['sentence']}")
    if not args.apply:
        print("\nDry run. Nothing was sent. Add --apply to put the report in the course.")
        return 0
    if not _writes_allowed(cfg):
        return 3
    out = slo.report(ctx, cid, apply=True, gate=_gate(cfg), log=_log)
    print(f"\nREPORT PAGE -> {out['url']} (unpublished)")
    for w in out.get("warnings") or []:
        print(f"  WARNING: {w}")
    return 1 if out.get("warnings") else 0


def _label(ctx, cid) -> str:
    try:
        from .common import course_label
        return course_label(ctx.content.course_detail(cid) or {}, cid)
    except Exception:  # noqa: BLE001
        return f"course {cid}"
