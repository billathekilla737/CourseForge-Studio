"""Terminal verbs for the Build area.

    python -m courseforge content draft        --course ID --kind page --title "..." [--module M] [--points N] [--brief TEXT | --brief-file F] [--source-file F] [--look L] [--group G] [--model M]
    python -m courseforge content drafts       --course ID
    python -m courseforge content place        --course ID --draft DRAFT_ID [--module-id N] [--position N] [--publish] [--group G] [--apply]
    python -m courseforge content push-pages   --course ID [--manifest F] [--root DIR] [--publish] [--apply]
    python -m courseforge content push-project --course ID [--manifest F] [--root DIR] [--publish] [--skip-modules] [--rebuild-modules] [--apply]
    python -m courseforge content rubrics      --course ID [--file F] [--apply]
    python -m courseforge content verify-slots --course ID [--manifest F] [--root DIR]
    python -m courseforge content check-style  FILE [--palette F]
    python -m courseforge content check-quiz   FILE [--expect-count N]

Every writing verb is a dry run without --apply; with --apply it prints the
plan and asks for a typed yes (cli.confirm_apply). Exit codes: 0 ok, 1 error,
2 usage or verify failure, 3 refused.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import check_quiz, check_style, generate, manifest as mf, push_pages, push_project
from . import place as placemod, rubrics as rubricsmod, state as statemod, verify_slots
from .paths import BuildDir


def register(sub) -> None:
    p = sub.add_parser("content", help="Build content: draft with Claude, push manifests, rubrics")
    p.set_defaults(area="content")
    v = p.add_subparsers(dest="verb", required=True)

    d = v.add_parser("draft", help="ask Claude for a draft and save it")
    d.add_argument("--course", required=True)
    d.add_argument("--kind", required=True, choices=generate.KINDS)
    d.add_argument("--title", required=True)
    d.add_argument("--module", default="")
    d.add_argument("--points", default=None)
    d.add_argument("--brief", default="")
    d.add_argument("--brief-file", default=None)
    d.add_argument("--source-file", default=None)
    d.add_argument("--look", default=None, choices=generate.LOOKS)
    d.add_argument("--group", default="")
    d.add_argument("--model", default=None)

    ls = v.add_parser("drafts", help="list the saved drafts")
    ls.add_argument("--course", required=True)

    pl = v.add_parser("place", help="create a draft in the course and its module item")
    pl.add_argument("--course", required=True)
    pl.add_argument("--draft", required=True)
    pl.add_argument("--module-id", default=None)
    pl.add_argument("--position", default=None)
    pl.add_argument("--publish", action="store_true")
    pl.add_argument("--group", default=None)
    pl.add_argument("--apply", action="store_true")

    for name, help_ in (("push-pages", "push a pages-only manifest"),
                        ("push-project", "push a project manifest")):
        pp = v.add_parser(name, help=help_)
        pp.add_argument("--course", required=True)
        pp.add_argument("--manifest", default=None, help="default: the saved manifest for this course")
        pp.add_argument("--root", default=None)
        pp.add_argument("--publish", action="store_true")
        pp.add_argument("--apply", action="store_true")
        if name == "push-project":
            pp.add_argument("--skip-modules", action="store_true")
            pp.add_argument("--rebuild-modules", action="store_true")

    r = v.add_parser("rubrics", help="create rubrics and attach them to assignments")
    r.add_argument("--course", required=True)
    r.add_argument("--file", default=None, help="default: the saved rubrics for this course")
    r.add_argument("--apply", action="store_true")

    vs = v.add_parser("verify-slots", help="check every manifest slot before pushing")
    vs.add_argument("--course", required=True)
    vs.add_argument("--manifest", default=None)
    vs.add_argument("--root", default=None)

    cs = v.add_parser("check-style", help="check one HTML body")
    cs.add_argument("file")
    cs.add_argument("--palette", default=None)

    cq = v.add_parser("check-quiz", help="check a quiz questions file")
    cq.add_argument("file")
    cq.add_argument("--expect-count", type=int, default=None)


# ----------------------------------------------------------------- helpers
def _client(cfg):
    from ..canvas import CanvasClient
    return CanvasClient(cfg.base_url, cfg.token(), scope="content",
                        allowed_hosts=getattr(cfg, "canvas_hosts", None))


def _build(cfg, course_id) -> BuildDir:
    return BuildDir(_course_dir(cfg, course_id), course_id)


def _course_dir(cfg, course_id) -> Path:
    from ..store import Store
    return Store(Path(cfg.data)).course_dir(course_id)


def _manifest(args, build: BuildDir) -> tuple[dict, Path]:
    if args.manifest:
        manifest = mf.load(args.manifest)
        root = mf.resolve_root(manifest, manifest_path=Path(args.manifest),
                               explicit=Path(args.root) if args.root else None)
    else:
        manifest = build.manifest()
        if manifest is None:
            raise SystemExit(f"No manifest saved for course {build.course_id} and none given with --manifest.")
        root = mf.resolve_root(manifest, build.root, explicit=Path(args.root) if args.root else None)
    return manifest, root


def _course_name(client, course_id) -> str:
    try:
        return client.course_detail(course_id).get("name") or f"course {course_id}"
    except Exception:  # noqa: BLE001
        return f"course {course_id}"


def _print_rows(rows, cols):
    for r in rows:
        print("  " + "  ".join(str(r.get(c, "") or "")[:48].ljust(w) for c, w in cols))


# --------------------------------------------------------------------- run
def run(args, cfg) -> int:
    from .. import cli
    verb = args.verb

    if verb == "check-style":
        return check_style.main([args.file] + (["--palette", args.palette] if args.palette else []))
    if verb == "check-quiz":
        return check_quiz.main([args.file] + (["--expect-count", str(args.expect_count)]
                                              if args.expect_count is not None else []))

    build = _build(cfg, args.course)

    if verb == "drafts":
        rows = build.drafts()
        if not rows:
            print("No drafts yet.")
            return 0
        for d in rows:
            f = d.get("findings") or {}
            print(f"  {d['id']}  {d.get('kind', ''):<11} {'ok  ' if f.get('ok') else 'FAIL'}  "
                  f"{'placed' if d.get('placed') else 'draft '}  {d.get('title', '')[:60]}")
        return 0

    if verb == "draft":
        brief = args.brief
        if args.brief_file:
            brief = Path(args.brief_file).read_text(encoding="utf-8")
        source = Path(args.source_file).read_text(encoding="utf-8") if args.source_file else ""
        form = {"kind": args.kind, "title": args.title, "module": args.module, "points": args.points,
                "brief": brief, "source": source, "look": args.look, "group": args.group,
                "model": args.model}
        record = generate.draft(cfg, build, form, log=lambda m, *a: print("  " + m))
        f = record["findings"]
        print(f"\nDraft {record['id']}: '{record['title']}' ({len(record['html'])} characters)")
        for c in f["style"]["chips"]:
            print(f"  {c['state'].upper():<4} {c['label']}" + (f": {c['detail']}" if c.get("detail") else ""))
        for msg in f["style"]["failed"]:
            print("  FAIL " + msg)
        if f.get("quiz"):
            for msg in f["quiz"]["failed"]:
                print("  FAIL quiz: " + msg)
        print(f"Saved to {build.draft_path(record['id'])}. "
              + ("Ready to place." if f["ok"] else "Fix the failures before placing."))
        return 0 if f["ok"] else 2

    if verb == "verify-slots":
        manifest, root = _manifest(args, build)
        result = verify_slots.verify(manifest, root, palette=check_style.brand_palette())
        verify_slots.print_report(result)
        return 0 if result["ok"] else 2

    client = _client(cfg)
    course_id = args.course

    if verb == "place":
        record = build.draft(args.draft)
        if not record:
            print(f"No draft {args.draft} for course {course_id}.")
            return 2
        if record.get("placed"):
            print("That draft was already placed. Draft it again to place a second copy.")
            return 3
        failures = generate.hard_failures(record)
        if failures:
            print("The draft fails the style check and cannot be placed:")
            for f in failures:
                print("  FAIL " + f)
            return 2
        module_name = ""
        if args.module_id:
            module_name = next((m.get("name") for m in client.modules(course_id)
                                if str(m.get("id")) == str(args.module_id)), "")
        sentence = placemod.sentence(record, module_name if args.module_id else None, args.position,
                                     args.publish, _course_name(client, course_id))
        print(sentence)
        if not args.apply:
            print("Dry run. Add --apply to create it.")
            return 0
        if not cli.confirm_apply(sentence):
            print("Not applied.")
            return 3
        result = placemod.place(client, cfg.base_url, course_id, record, args.module_id, args.position,
                                args.publish, args.group, log=lambda m, *a: print("  " + m),
                                backup_dir=build.backups_dir)
        record["placed"] = result
        build.save_draft(record)
        from .. import ledger
        ledger.record(_course_dir(cfg, course_id), "build",
                      sentence.replace("Create the", "Created the", 1).replace("Replace the", "Replaced the", 1),
                      url=result.get("html_url"), count=1, kind="place")
        print(f"Done: {result.get('html_url') or result.get('id')}")
        for m in result.get("mismatches") or []:
            print("  read back: " + m)
        return 0

    if verb in ("push-pages", "push-project"):
        manifest, root = _manifest(args, build)
        problems = mf.validate(manifest, root)
        for p in problems:
            print("  MANIFEST " + p)
        verify = verify_slots.verify(manifest, root, palette=check_style.brand_palette())
        st = statemod.load(build)
        publish = bool(args.publish)
        if verb == "push-pages":
            plan = push_pages.plan(client, course_id, manifest, root, st, publish)
            print(f"Pages ({plan['counts']['create']} create, {plan['counts']['update']} update, "
                  f"{plan['counts']['skip']} skip), {'published' if publish else 'unpublished'}:")
            _print_rows(plan["pages"], [("action", 7), ("title", 48), ("module", 30)])
            print("Modules:")
            _print_rows(plan["modules"], [("action", 7), ("name", 48)])
        else:
            plan = push_project.plan(client, course_id, manifest, root, st, publish,
                                     args.skip_modules, args.rebuild_modules)
            print(f"Items ({plan['counts']['create']} create, {plan['counts']['update']} update, "
                  f"{plan['counts']['skip']} skip), {'published' if publish else 'unpublished'}:")
            _print_rows(plan["rows"], [("action", 7), ("kind", 11), ("title", 48), ("reason", 30)])
            print(f"Modules: {len(plan['modules'])} in the manifest. {plan['gate']['sentence']}")
        print()
        print(verify["summary"])
        if not verify["ok"] or problems:
            verify_slots.print_report(verify)
            print("Fix these before pushing.")
            return 2
        name = _course_name(client, course_id)
        sentence = plan["sentence"].rstrip(".") + f" in {name}."
        if not args.apply:
            print(sentence)
            print("Dry run. Add --apply to push.")
            return 0
        if verb == "push-project" and plan["gate"].get("refused"):
            print(plan["gate"]["sentence"])
            return 3
        if not cli.confirm_apply(sentence):
            print("Not applied.")
            return 3
        log = lambda m, *a: print("  " + m)  # noqa: E731
        save = lambda s: statemod.save(build, s)  # noqa: E731
        if verb == "push-pages":
            result = push_pages.apply(client, course_id, manifest, root, st, publish, log, on_state=save)
        else:
            result = push_project.apply(client, course_id, manifest, root, st, publish, log,
                                        args.skip_modules, args.rebuild_modules, on_state=save,
                                        backup_dir=build.backups_dir)
        statemod.save(build, st)
        from .. import ledger
        ledger.record(_course_dir(cfg, course_id), "build",
                      f"Pushed the {'pages' if verb == 'push-pages' else 'project'} manifest "
                      f"({result.get('count')} item(s), {'published' if publish else 'unpublished'}) in {name}.",
                      url=f"{cfg.base_url}/courses/{course_id}/modules", count=result.get("count"), kind="push")
        build.save_last_push({"at": statemod.load(build).get("updated_at"), "mode": verb.split("-")[1],
                              "publish": publish, "count": result.get("count"),
                              "mismatches": result.get("mismatches") or []})
        for m in result.get("mismatches") or []:
            print("  read back: " + m)
        print(f"Done. Review: {cfg.base_url}/courses/{course_id}/modules")
        return 0

    if verb == "rubrics":
        entries = json.loads(Path(args.file).read_text(encoding="utf-8-sig")) if args.file else build.rubrics()
        if isinstance(entries, dict):
            entries = entries.get("rubrics") or [entries]
        if not isinstance(entries, list) or not entries:
            print("No rubric definitions saved and none given with --file.")
            return 2
        plan = rubricsmod.plan(client, course_id, entries)
        _print_rows(plan["rows"], [("action", 7), ("title", 40), ("assignment", 36), ("points", 6)])
        for r in plan["rows"]:
            for w in r["warnings"]:
                print(f"      WARN {w}")
        sentence = plan["sentence"].rstrip(".") + f" in {_course_name(client, course_id)}."
        if not args.apply:
            print(sentence)
            print("Dry run. Add --apply to write.")
            return 0
        if not plan["keys"]:
            print("Nothing to push: every rubric was skipped.")
            return 2
        if not cli.confirm_apply(sentence):
            print("Not applied.")
            return 3
        result = rubricsmod.apply(client, course_id, entries, log=lambda m, *a: print("  " + m))
        from .. import ledger
        ledger.record(_course_dir(cfg, course_id), "build",
                      f"Pushed {result['count']} rubric definition(s) in {_course_name(client, course_id)}.",
                      url=f"{cfg.base_url}/courses/{course_id}/rubrics", count=result["count"], kind="rubrics")
        for m in result.get("mismatches") or []:
            print("  read back: " + m)
        return 0

    print(f"unknown verb {verb!r}", file=sys.stderr)
    return 2
