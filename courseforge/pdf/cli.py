"""Terminal and Assistant verbs for the PDF fixer.

    python -m courseforge pdf list      --course ID
    python -m courseforge pdf fetch     --course ID
    python -m courseforge pdf fix       --course ID [--force] [--jobs N]
    python -m courseforge pdf figures   --course ID
    python -m courseforge pdf describe  --course ID
    python -m courseforge pdf apply-alt --course ID
    python -m courseforge pdf prove     --course ID [--profile ua1|wtpdf]
    python -m courseforge pdf push      --course ID [--apply]
    python -m courseforge pdf rollback  --course ID [--apply]

Every writing verb is a dry run without --apply and prints the plan either
way. With --apply it asks for a typed yes (cli.confirm_apply), unless it runs
inside a gated Assistant session where the Allow click was the confirmation.
Exit codes: 0 ok, 1 error, 2 usage or verify failure, 3 refused.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import course_pdfs as core


def register(sub) -> None:
    p = sub.add_parser("pdf", help="PDF accessibility: back up, repair, describe, prove, upload")
    p.set_defaults(area="pdf")
    v = p.add_subparsers(dest="verb", required=True)

    def course(parser):
        parser.add_argument("--course", required=True, help="Canvas course id")
        return parser

    course(v.add_parser("list", help="refresh the course's PDF list from Canvas"))
    course(v.add_parser("fetch", help="download every original (six at a time)"))
    f = course(v.add_parser("fix", help="back up, then repair every PDF in parallel"))
    f.add_argument("--force", action="store_true",
                   help="redo files that already have a result (alt text must be re-applied)")
    f.add_argument("--jobs", type=int, default=None, help="worker processes")
    course(v.add_parser("figures", help="list the figures still holding a placeholder"))
    course(v.add_parser("describe", help="real image descriptions from Claude"))
    course(v.add_parser("apply-alt", help="write alt.json into the fixed PDFs and re-verify"))
    pr = course(v.add_parser("prove", help="veraPDF census against PDF/UA-1"))
    pr.add_argument("--profile", choices=list(core.PROFILES), default="ua1")
    u = course(v.add_parser("push", help="upload the fixed PDFs over the originals"))
    u.add_argument("--apply", action="store_true", help="really upload (dry run without it)")
    u.add_argument("--file", action="append", help="limit to one Canvas file id (repeatable)")
    rb = course(v.add_parser("rollback", help="put the original PDFs back over Canvas"))
    rb.add_argument("--apply", action="store_true")
    rb.add_argument("--file", action="append", help="limit to one Canvas file id (repeatable)")


class _Ctx:
    """The slice of App the library needs, built from a Config for a terminal."""

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


def _writes_allowed(cfg) -> bool:
    if getattr(cfg, "allow_canvas_writes", True):
        return True
    print('Canvas writes are locked off in config.json ("allow_canvas_writes": false). '
          "Nothing was sent.")
    return False


def run(args, cfg) -> int:
    from .. import cli, ledger
    ctx = _Ctx(cfg)
    log = core.Printer()
    cid = str(args.course)
    verb = args.verb
    ids = getattr(args, "file", None) or None

    if verb == "list":
        core.list_files(ctx, cid, log)
        _print_files(core.state(ctx, cid))
        return 0

    if verb == "fetch":
        out = core.fetch(ctx, cid, log)
        return 1 if out.get("failures") else 0

    if verb == "fix":
        missing = [n for n in core.needs(ctx) if n in ("pymupdf", "pikepdf", "pypdf")]
        if missing:
            print("Install %s first; the PDF fixer cannot read a PDF without it."
                  % ", ".join(missing), file=sys.stderr)
            return 1
        out = core.fix(ctx, cid, log, jobs=args.jobs, force=args.force)
        st = core.state(ctx, cid)
        _print_files(st)
        _print_queue(st)
        return 2 if out.get("queued") else 0

    if verb == "figures":
        wd = core.workdir(ctx, cid)
        core.tools.wire_env(cfg)
        core.engine.collect_alt_todo(str(wd), quiet=False)
        return 0

    if verb == "describe":
        out = core.describe(ctx, cid, log)
        print("described %d of %d picture(s); about $%.2f"
              % (out["described"], out["requested"], out.get("cost_usd", 0.0)))
        for e in out.get("errors", [])[:10]:
            print("  problem:", e)
        for name in out.get("reverify_failed", [])[:10]:
            print("  re-check failed, put back as it was:", name)
        return 1 if out.get("errors") else 0

    if verb == "apply-alt":
        out = core.apply_and_verify(ctx, cid, log)
        if not out.get("applied"):
            print("No descriptions have been written for this course yet.")
            return 2
        print("applied; %d file(s) failed the re-check and were put back"
              % len(out.get("reverify_failed") or []))
        return 1 if out.get("reverify_failed") else 0

    if verb == "prove":
        out = core.prove(ctx, cid, log, profile=args.profile)
        if out.get("needs"):
            print("Prove compliance needs %s. The files are still fixed and verified."
                  % " and ".join(out["needs"]))
            return 1
        if not out.get("ran"):
            return 2
        _print_census(out["census"])
        return 0 if out["census"]["noncompliant"] == 0 else 3

    if verb == "push":
        p = core.plan(ctx, cid, ids)
        _print_plan(p)
        if not args.apply:
            print("\nDry run. Nothing was uploaded. Add --apply to upload.")
            return 0
        if not p["count"]:
            print("\nNothing to upload.")
            return 0
        if not _writes_allowed(cfg):
            return 3

        def gate(payload, sentence, detail):
            if not cli.confirm_apply(sentence):
                raise core.Refused(sentence)
        try:
            out = core.push(ctx, cid, ids, apply=True, gate=gate, log=log)
        except core.Refused as exc:
            print(str(exc) if "mid-description" in str(exc) else "Not applied.")
            return 3
        if out.get("uploaded"):
            ledger.record(ctx.course_dir(cid), "pdf", out["sentence_done"],
                          url="%s/courses/%s/files" % (cfg.base_url, cid),
                          count=len(out["uploaded"]), kind="pdf.push",
                          undo={"route": "/pdf/%s/rollback" % cid,
                                "body": {"file_ids": [u["file_id"] for u in out["uploaded"]],
                                         "apply": True}})
        for u in out.get("uploaded", []):
            print("uploaded %s%s" % (u["name"], "" if u.get("verified_back") else " (not read back)"))
        for f in out.get("failed", []):
            print("FAILED   %s: %s" % (f["name"], f["error"]))
        return 1 if out.get("failed") else 0

    if verb == "rollback":
        p = core.rollback_plan(ctx, cid, ids)
        print("ROLL BACK PLAN: %s" % p["sentence"])
        for r in p["rows"]:
            print("  would restore  %-52s %s" % (r["name"][:52], core.fmt_bytes(r["size"])))
        for s in p["suspect"]:
            print("  WILL NOT       %-52s local %d bytes, Canvas recorded %d"
                  % (s["name"][:52], s["local"], s["expected"]))
        if not args.apply:
            print("\nDry run. Nothing was uploaded. Add --apply to restore.")
            return 0
        if not p["count"]:
            print("\nNothing to restore.")
            return 0
        if not _writes_allowed(cfg):
            return 3

        def gate(payload, sentence, detail):
            if not cli.confirm_apply(sentence):
                raise core.Refused(sentence)
        try:
            out = core.rollback(ctx, cid, ids, apply=True, gate=gate, log=log)
        except core.Refused:
            print("Not applied.")
            return 3
        if out.get("restored"):
            n = len(out["restored"])
            ledger.record(ctx.course_dir(cid), "pdf",
                          "Put %s back over the fixed %s in %s"
                          % (core._plural(n, "original PDF"),
                             "copy" if n == 1 else "copies", core.course_name(ctx, cid)),
                          url="%s/courses/%s/files" % (cfg.base_url, cid),
                          count=n, kind="pdf.rollback")
        print("\nrestored %d, failed %d" % (len(out.get("restored") or []),
                                            len(out.get("failed") or [])))
        return 1 if out.get("failed") else 0

    print("unknown verb %r" % verb, file=sys.stderr)
    return 2


# ------------------------------------------------------------------ printing

def _print_files(st: dict) -> None:
    print("%s: %s" % (st["course_label"], st["summary"]))
    print("%-10s %-40s %-6s %-8s %-20s %s"
          % ("ID", "FILE", "PAGES", "LANE", "STATE", "VERIFY"))
    for r in st["files"]:
        v = r.get("verify") or {}
        ticks = "".join(("text " if v.get("text") else "",
                         "render " if v.get("render") else "",
                         "tree" if v.get("tree") else "")) if v else ""
        print("%-10s %-40s %-6s %-8s %-20s %s"
              % (r["id"], (r["name"] or "")[:40],
                 r.get("pages") if r.get("pages") is not None else "?",
                 r.get("lane") or "-", r["state"], ticks or "-"))
    if st.get("needs"):
        print("\nmissing tools: %s (the verbs that need them are disabled)"
              % ", ".join(st["needs"]))


def _print_queue(st: dict) -> None:
    groups = st.get("queue") or []
    if not groups:
        return
    print("\nNeeds a person (%d group(s)):" % len(groups))
    for g in groups:
        print("  [%s] %s  -  %d file(s)" % (g["severity"], g["reason"], g["count"]))
        for it in g["items"][:6]:
            print("      %s" % it["file"])
        if g["count"] > 6:
            print("      ... and %d more" % (g["count"] - 6))


def _print_plan(p: dict) -> None:
    print("PUSH PLAN: %s" % p["sentence"])
    for r in p["rows"]:
        tag = "already uploaded" if r["already_pushed"] else "would upload"
        print("  %-17s %-46s %s -> %s  (%s)"
              % (tag, r["name"][:46], core.fmt_bytes(r["from"]),
                 core.fmt_bytes(r["to"]), r["lane"] or "?"))
    for r in p["held"]:
        print("  WILL NOT UPLOAD   %-46s %s" % (r["name"][:46], (r.get("why") or "")[:70]))
    if p.get("pending_alt"):
        print("  %d file(s) are mid-description and block the upload until the "
              "description step finishes its re-check." % len(p["pending_alt"]))
    if p.get("placeholders"):
        print("  NOTE: %d figure(s) still carry a placeholder no automated step "
              "can improve; uploading publishes them as they are." % p["placeholders"])


def _print_census(c: dict) -> None:
    print("\n%d of %d file(s) pass %s" % (c["compliant"], c["files"], c["profile"]))
    print("%-46s %-6s %-16s %s" % ("RULE", "FILES", "KIND", "LANES"))
    for r in c["rules"]:
        lanes = ", ".join("%s %d" % (k, v) for k, v in sorted(r["lanes"].items()))
        print("%-46s %-6d %-16s %s" % (r["rule"][:46], r["files"], r["kind"], lanes))
    print("\nFiles per rule, not occurrences: one file with a broken table can "
          "produce hundreds of hits of the same rule.")
