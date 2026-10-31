"""Terminal verbs for the Documents area.

    python -m courseforge docs list     --course ID --kind pptx|docx|pdf-text|office-text|triage
    python -m courseforge docs fetch    --course ID --kind K [--file ID] [--pattern REGEX]
    python -m courseforge docs describe --course ID --kind pptx|docx [--file ID]
    python -m courseforge docs push     --course ID --kind K [--file ID] [--apply]
    python -m courseforge docs triage   --course ID [--file ID]

push is a dry run without --apply; with it, the same sentence the web UI shows
is printed and a typed yes is required (cli.confirm_apply). Exit codes: 0 ok,
1 error, 2 usage or verify failure, 3 refused.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import gateway

KIND_CHOICES = list(gateway.KINDS)


def register(sub) -> None:
    p = sub.add_parser("docs", help="PowerPoint, Word, PDF text, Office text and PDF triage")
    p.set_defaults(area="docs")
    v = p.add_subparsers(dest="verb", required=True)

    def common(parser, kind_required=True):
        parser.add_argument("--course", required=True, help="Canvas course id")
        parser.add_argument("--kind", choices=KIND_CHOICES, required=kind_required,
                            help="which document kind")
        parser.add_argument("--file", help="limit to one Canvas file id")

    common(v.add_parser("list", help="refresh the file list from Canvas"))
    f = v.add_parser("fetch", help="download the originals and scan them")
    common(f)
    f.add_argument("--pattern", help="regex to look for (pdf-text, office-text)")
    common(v.add_parser("describe", help="alt text (and slide titles) from Claude"))
    u = v.add_parser("push", help="apply, verify and upload over the originals")
    common(u)
    u.add_argument("--apply", action="store_true", help="really upload (dry run without it)")
    t = v.add_parser("triage", help="download every PDF and rank them worst first")
    common(t, kind_required=False)


class _CliApp:
    """The slice of App the gateway needs, without starting the server."""

    def __init__(self, cfg):
        from ..canvas import CanvasClient
        from ..store import Store
        self.cfg = cfg
        self.store = Store(cfg.data)
        self._client = None

    @property
    def content(self):
        if self._client is None:
            from ..canvas import CanvasClient
            self._client = CanvasClient(self.cfg.base_url, self.cfg.token(), scope="content",
                                        allowed_hosts=getattr(self.cfg, "canvas_hosts", None))
        return self._client

    def course_dir(self, course_id) -> Path:
        return self.store.course_dir(course_id)


def run(args, cfg) -> int:
    from .. import cli, ledger
    app = _CliApp(cfg)
    log = gateway.Printer()
    cid = args.course
    kind_id = getattr(args, "kind", None) or ("triage" if args.verb == "triage" else None)
    if not kind_id:
        print("--kind is required", file=sys.stderr)
        return 2
    kind = gateway.kind_of(kind_id)
    ids = [args.file] if getattr(args, "file", None) else None
    needs = gateway.tool_needs(app, kind)
    if needs and args.verb in ("fetch", "describe", "push", "triage"):
        print(f"Install {', '.join(needs)} first; the {kind.label} tools need it.")
        return 1

    if args.verb == "list":
        gateway.list_files(app, cid, kind, log)
        _print_state(gateway.state(app, cid, kind))
        return 0

    if args.verb in ("fetch", "triage"):
        if args.verb == "triage":
            kind = gateway.KINDS["triage"]
        pattern = getattr(args, "pattern", None)
        if kind.needs_pattern and not pattern and not gateway.current_pattern(app, cid, kind):
            print("fetch for this kind needs --pattern REGEX (what to look for).", file=sys.stderr)
            return 2
        out = gateway.fetch(app, cid, kind, ids, pattern, log)
        st = gateway.state(app, cid, kind)
        _print_state(st)
        if kind.report_only:
            _print_triage(st.get("triage") or [])
        return 1 if out.get("failures") else 0

    if args.verb == "describe":
        if not kind.has_alt:
            print(f"{kind.label} has no pictures to describe.", file=sys.stderr)
            return 2
        from . import describe
        out = describe.describe(app, cid, kind, ids, log)
        print(f"described {out['described']} picture(s), titled {out['titles']} slide(s), "
              f"{out['no_picture']} with no usable picture; about ${out['cost_usd']:.2f}")
        for e in out["errors"]:
            print("  problem:", e)
        return 0

    if args.verb == "push":
        if kind.report_only:
            print(f"{kind.label} is report-only; nothing is uploaded.", file=sys.stderr)
            return 2
        p = gateway.plan(app, cid, kind, ids, log=log)
        _print_plan(p)
        if not args.apply:
            print("Dry run. Add --apply to upload.")
            return 0 if not p["blocked"] else 2
        if p["count"] == 0:
            print("Nothing to upload.")
            return 0

        def gate(payload, sentence, detail):
            if not cli.confirm_apply(sentence):
                raise gateway.Refused(sentence)
        try:
            out = gateway.push(app, cid, kind, ids, apply=True, gate=gate, log=log)
        except gateway.Refused:
            print("Not applied.")
            return 3
        if out.get("uploaded"):
            ledger.record(app.course_dir(cid), "docs", out["sentence_done"],
                          url=f"{cfg.base_url}/courses/{cid}/files", count=len(out["uploaded"]),
                          kind=kind.id,
                          undo={"route": f"/a11y/{cid}/{kind.id}/restore",
                                "body": {"file_ids": [u["file_id"] for u in out["uploaded"]]}})
        for u in out.get("uploaded", []):
            print(f"uploaded {u['name']}" + ("" if u.get("verified_back") else " (not read back)"))
        for f in out.get("failed", []):
            print(f"FAILED {f['name']}: {f['error']}")
        return 1 if out.get("failed") else 0

    print(f"unknown verb {args.verb!r}", file=sys.stderr)
    return 2


def _print_state(st: dict) -> None:
    print(f"{st['label']}: {st['summary']['files']} file(s)"
          + (f", pattern {st['pattern']!r}" if st.get("needs_pattern") else ""))
    for r in st["items"]:
        extra = []
        if r.get("hard_issues"):
            extra.append(f"{r['hard_issues']} issue(s)")
        if r.get("alt_todo"):
            extra.append(f"{r['alt_todo']} need alt")
        if r.get("hits"):
            extra.append(f"{r['hits']} match(es)")
        if r.get("hazards"):
            extra.append("; ".join(r["hazards"]))
        print(f"  {r['id']:>10}  {r['state']:<12} {r['name']}"
              + (f"  ({', '.join(extra)})" if extra else ""))


def _print_plan(p: dict) -> None:
    print(p["sentence"])
    for r in p["rows"]:
        tag = "already on Canvas" if r["already_pushed"] else "will upload"
        print(f"  {tag:<18} {r['name']}  {gateway._kb(r['from'])} -> {gateway._kb(r['to'])}"
              f"  {', '.join(r['changes'])}"
              + (f"  ({r['remaining_hard']} issue(s) remain)" if r.get("remaining_hard") else ""))
    for r in p["blocked"]:
        print(f"  WILL NOT PUSH      {r['name']}: {'; '.join(r['problems']) or 'verify failed'}")


def _print_triage(rows: list[dict]) -> None:
    print("%-40s %-14s %-6s %s" % ("FILE", "CLASS", "PAGES", "NOTE"))
    for r in rows:
        print("%-40s %-14s %-6s %s" % ((r.get("name") or "")[:40], r.get("cls"),
                                     r.get("pages") if r.get("pages") is not None else "?",
                                     r.get("note", "")))
