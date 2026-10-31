"""Terminal and Assistant verbs for the HTML remediation.

    python -m courseforge a11y dump            --course ID
    python -m courseforge a11y scan            --course ID
    python -m courseforge a11y restyle         --course ID [--look clean|hybrid|rich]
    python -m courseforge a11y verify          --course ID
    python -m courseforge a11y push            --course ID [--apply] [--kinds page quiz ...]
    python -m courseforge a11y restore         --course ID [--apply]
    python -m courseforge a11y bold-structure  --course ID [--promote-labels]
                                               [--unbold-sentences] [--convert-code-runs]
                                               [--labels RX ...] [--apply]
    python -m courseforge a11y bordered-boxes  --course ID [--color #d7dce3] [--apply]
    python -m courseforge a11y batch           --course ID --course ID ... [--look L] [--apply]

Every writing verb is a dry run without --apply and prints the plan either
way. With --apply it asks for a typed yes (cli.confirm_apply), unless it runs
inside a gated Assistant session where the Allow click was the confirmation.
Exit codes: 0 ok, 1 error, 2 usage or verify failure, 3 refused.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import ledger
from . import batch, bold_structure, bordered_boxes, dump, push, restyle
from .workdir import LOOKS, course_label, load_fixes, load_manifest, workdir


def register(sub) -> None:
    p = sub.add_parser("a11y", help="Accessibility: HTML remediation (fetch, restyle, verify, push)")
    p.set_defaults(area="a11y")
    v = p.add_subparsers(dest="verb", required=True)

    def course(parser):
        parser.add_argument("--course", required=True, help="Canvas course id")

    d = v.add_parser("dump", help="fetch every HTML body into data/<course>/a11y/bodies")
    course(d)
    s = v.add_parser("scan", help="list the hard accessibility issues in the fetched bodies")
    course(s)
    r = v.add_parser("restyle", help="restyle the fetched bodies and verify them")
    course(r)
    r.add_argument("--look", choices=list(LOOKS), default=None,
                   help="clean (default; no fills), hybrid or rich")
    ve = v.add_parser("verify", help="prove the styled bodies against the originals")
    course(ve)
    pu = v.add_parser("push", help="write the verified bodies back in place (dry run without --apply)")
    course(pu)
    pu.add_argument("--apply", action="store_true")
    pu.add_argument("--kinds", nargs="*", help="limit to kinds: page assignment discussion quiz syllabus")
    rs = v.add_parser("restore", help="put back the bodies the last push replaced")
    course(rs)
    rs.add_argument("--apply", action="store_true")
    b = v.add_parser("bold-structure", help="triage wholly-bold paragraphs; remedies are opt-in")
    course(b)
    b.add_argument("--promote-labels", action="store_true", help="allowlisted labels become real h3 headings")
    b.add_argument("--unbold-sentences", action="store_true", help="drop the blanket bold on sentences")
    b.add_argument("--convert-code-runs", action="store_true", help="collapse bolded code into a code block")
    b.add_argument("--labels", action="append", help="label regex to allow (repeatable; replaces the default list)")
    b.add_argument("--sentence-min", type=int, default=bold_structure.DEFAULT_SENTENCE_MIN)
    b.add_argument("--title-filter", default="", help="regex; only items whose title matches")
    b.add_argument("--apply", action="store_true")
    bb = v.add_parser("bordered-boxes", help="remove inline bordered-box spans used as emphasis")
    course(bb)
    bb.add_argument("--color", default=None, help="the stray border colour (default: brand hairline)")
    bb.add_argument("--apply", action="store_true")
    ba = v.add_parser("batch", help="fetch, restyle, verify and push several courses")
    ba.add_argument("--course", action="append", required=True, help="repeat for each course id")
    ba.add_argument("--look", choices=list(LOOKS), default=None)
    ba.add_argument("--apply", action="store_true")
    ba.add_argument("--stop-on-error", action="store_true")


class _Ctx:
    """What the library needs from an App, built from a Config for the terminal."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.data_root = Path(cfg.data)
        self._client = None
        try:
            from ..store import Store
            self.store = Store(cfg.data)
        except Exception:  # noqa: BLE001
            self.store = None

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


def _print_plan_rows(rows, skipped=None):
    for r in rows:
        print("  would write  %-52s %s -> %s" % (r["label"][:52], r.get("from", ""), r.get("to", "")))
    for r in skipped or []:
        print("  skip         %-52s %s" % (r.get("label", "")[:52], r.get("reason", "")))


def _writes_allowed(cfg) -> bool:
    if getattr(cfg, "allow_canvas_writes", True):
        return True
    print('Canvas writes are locked off in config.json ("allow_canvas_writes": false). '
          "Nothing was sent.")
    return False


def run(args, cfg) -> int:
    from .. import cli
    restyle.configure(getattr(cfg, "brand_path", "") or None)
    ctx = _Ctx(cfg)
    verb = args.verb

    if verb == "batch":
        look = args.look or getattr(cfg, "a11y_look", "clean") or "clean"
        if args.apply and not _writes_allowed(cfg):
            return 3

        def gate(payload, sentence, detail):
            if not cli.confirm_apply(sentence):
                raise PermissionError("Not applied.")
        try:
            summary = batch.run(ctx, args.course, look=look, apply=args.apply,
                                log=lambda m, d=None, t=None: print("  " + m),
                                gate=gate if args.apply else None,
                                stop_on_error=args.stop_on_error)
        except PermissionError as exc:
            print(str(exc))
            return 3
        print()
        print(batch.table(summary))
        print("\nSummary: %s" % summary.get("path"))
        if not args.apply:
            print("Dry run. Nothing written. Add --apply to push every course whose verify passed.")
        return 1 if any(r.get("error") for r in summary["rows"]) else 0

    cid = str(args.course)
    wd = workdir(ctx.course_dir(cid))
    label = (load_manifest(wd) or {}).get("course_label") or course_label(ctx, cid)

    if verb == "dump":
        m = dump.dump(ctx.content, cid, wd, log=lambda msg, d=None, t=None: print("  " + msg),
                      course_label=label, base_url=cfg.base_url)
        print("\nFetched %d items into %s. Nothing is pushed." % (len(m["items"]), wd))
        return 0

    if verb == "scan":
        if not load_manifest(wd):
            print("Nothing fetched yet. Run: python -m courseforge a11y dump --course %s" % cid)
            return 2
        return restyle.cmd_scan(str(wd))

    if verb == "restyle":
        if not load_manifest(wd):
            print("Nothing fetched yet. Run: python -m courseforge a11y dump --course %s" % cid)
            return 2
        look = args.look or getattr(cfg, "a11y_look", "clean") or "clean"
        restyle.cmd_transform(str(wd), look)
        print()
        fails = restyle.cmd_verify(str(wd))
        return 2 if fails else 0

    if verb == "verify":
        if not load_manifest(wd):
            print("Nothing fetched yet.")
            return 2
        fails = restyle.cmd_verify(str(wd))
        return 2 if fails else 0

    if verb == "push":
        exclude = load_fixes(wd).get("excluded") or []
        try:
            p = push.plan(wd, kinds=args.kinds or None, exclude=exclude)
        except push.PushRefused as exc:
            print("Refused: %s" % exc)
            return 2
        print("PUSH PLAN: %s in %s (%s look)" % (p["phrase"], label, p.get("look")))
        _print_plan_rows(p["rows"], p["skipped"])
        if not args.apply:
            print("\nDry run. Nothing written. Add --apply to push.")
            return 0
        if not p["rows"]:
            print("\nNothing to push.")
            return 0
        if not _writes_allowed(cfg):
            return 3
        if not cli.confirm_apply(push.confirm_sentence(p, label)):
            print("Not applied.")
            return 3
        try:
            result = push.push(ctx.content, cid, wd, log=lambda m, d=None, t=None: print("  " + m),
                               kinds=args.kinds or None, exclude=exclude)
        except push.PushRefused as exc:
            print("Refused: %s" % exc)
            return 3
        if result["written_count"]:
            ledger.record(ctx.course_dir(cid), "a11y",
                          "Replaced the bodies of %s with restyled versions (%s look)"
                          % (result["phrase"], result["look"]),
                          url="%s/courses/%s/pages" % (cfg.base_url, cid),
                          count=result["written_count"], kind="a11y.push",
                          undo={"route": "/a11y/%s/html/restore" % cid, "body": {}})
        print("\nPUSH COMPLETE: %d written, %d error(s), %d live check(s) failed."
              % (result["written_count"], len(result["errors"]), result["live_fails"]))
        for rec in result["live"]:
            if not rec["ok"]:
                print("  CHECK %s: %s" % (rec["label"], "; ".join(rec["issues"])))
        return 1 if (result["errors"] or result["live_fails"]) else 0

    if verb == "restore":
        try:
            p = push.restore_plan(wd)
        except push.PushRefused as exc:
            print("Refused: %s" % exc)
            return 2
        print("RESTORE PLAN: %s in %s (undoing the push of %s)" % (p["phrase"], label, p.get("pushed_at")))
        _print_plan_rows(p["rows"], p["skipped"])
        if not args.apply:
            print("\nDry run. Nothing written. Add --apply to restore.")
            return 0
        if not p["rows"]:
            print("\nNothing to restore.")
            return 0
        if not _writes_allowed(cfg):
            return 3
        if not cli.confirm_apply(push.restore_sentence(p, label)):
            print("Not applied.")
            return 3
        try:
            result = push.restore(ctx.content, cid, wd, log=lambda m, d=None, t=None: print("  " + m))
        except push.PushRefused as exc:
            print("Refused: %s" % exc)
            return 3
        if result["written_count"]:
            ledger.record(ctx.course_dir(cid), "a11y",
                          "Put back the previous bodies of %s" % result["phrase"],
                          url="%s/courses/%s/pages" % (cfg.base_url, cid),
                          count=result["written_count"], kind="a11y.restore")
        print("\nRESTORE COMPLETE: %d written, %d error(s), %d live check(s) failed."
              % (result["written_count"], len(result["errors"]), result["live_fails"]))
        return 1 if (result["errors"] or result["live_fails"]) else 0

    if verb == "bold-structure":
        options = bold_structure.normalise_options({
            "promote_labels": args.promote_labels, "unbold_sentences": args.unbold_sentences,
            "convert_code_runs": args.convert_code_runs, "labels": args.labels,
            "sentence_min": args.sentence_min, "title_filter": args.title_filter})
        plan = bold_structure.scan_course(ctx.content, cid, options)
        print("Course %s: bold-as-structure triage (%s)" % (cid, "REPORT ONLY" if plan["mode"] == "report"
                                                             else ("APPLY" if args.apply else "DRY RUN")))
        for it in plan["items"]:
            print("%s" % it["label"])
            for h in it["hits"]:
                text = h["text"][:56] + "..." if len(h["text"]) > 56 else h["text"]
                print("   [%-12s] len=%-4d '%s'" % (h["cls"], h["length"], text))
            for made in it["made"]:
                print("   -> %s" % made)
            if it["skipped_reason"]:
                print("   ! %s" % it["skipped_reason"])
        c = plan["counts"]
        print("\nhits by class: label=%d  sentence=%d  code=%d  unclassified=%d"
              % (c["label"], c["sentence"], c["code"], c["unclassified"]))
        if plan["mode"] == "report":
            print("\nREPORT ONLY. Re-run with --promote-labels, --unbold-sentences and/or "
                  "--convert-code-runs, plus --apply to write.")
            return 0
        print("\n%d item(s) would change." % plan["change_count"])
        if not args.apply:
            print("Dry run. Nothing written. Add --apply.")
            return 0
        if not plan["change_count"]:
            return 0
        if not _writes_allowed(cfg):
            return 3
        if not cli.confirm_apply(bold_structure.sentence(plan, label)):
            print("Not applied.")
            return 3
        try:
            result = bold_structure.apply_plan(ctx.content, cid, plan,
                                               log=lambda m, d=None, t=None: print("  " + m))
        except PermissionError as exc:
            print("Refused: %s" % exc)
            return 3
        if result["written_count"]:
            ledger.record(ctx.course_dir(cid), "a11y",
                          "Changed the markup of %d items so bold is no longer used as structure"
                          % result["written_count"],
                          url="%s/courses/%s/pages" % (cfg.base_url, cid),
                          count=result["written_count"], kind="a11y.bold-structure")
        print("items changed: %d, errors: %d, skipped: %d"
              % (result["written_count"], len(result["errors"]), len(result["skipped"])))
        return 1 if result["errors"] else 0

    if verb == "bordered-boxes":
        plan = bordered_boxes.scan_course(ctx.content, cid, args.color)
        print("Course %s: remove inline bordered-box emphasis (%s), %s"
              % (cid, plan["color"], "APPLY" if args.apply else "DRY RUN"))
        for it in plan["items"]:
            print("   %-58s %d -> %d" % (it["label"][:58], it["boxes"], it["boxes_after"]))
        for it in plan["skipped"]:
            print("   ! %s: %s" % (it["label"], it["skipped_reason"]))
        print("\nitems: %d   boxes: %d   skipped: %d"
              % (plan["change_count"], plan["box_count"], len(plan["skipped"])))
        if not args.apply:
            print("Dry run. Nothing written. Add --apply.")
            return 0
        if not plan["change_count"]:
            return 0
        if not _writes_allowed(cfg):
            return 3
        if not cli.confirm_apply(bordered_boxes.sentence(plan, label)):
            print("Not applied.")
            return 3
        try:
            result = bordered_boxes.apply_plan(ctx.content, cid, plan,
                                               log=lambda m, d=None, t=None: print("  " + m))
        except PermissionError as exc:
            print("Refused: %s" % exc)
            return 3
        if result["written_count"]:
            ledger.record(ctx.course_dir(cid), "a11y",
                          "Removed %d inline bordered-box spans from %d items"
                          % (result["boxes_removed"], result["written_count"]),
                          url="%s/courses/%s/pages" % (cfg.base_url, cid),
                          count=result["written_count"], kind="a11y.bordered-boxes")
        print("items changed: %d   boxes removed: %d   errors: %d"
              % (result["written_count"], result["boxes_removed"], len(result["errors"])))
        return 1 if result["errors"] else 0

    print("unknown verb %r" % verb)
    return 2


def _dump_json(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False)
