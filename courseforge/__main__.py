"""Command line entry point.

    python -m courseforge serve       start the local web UI (default)
    python -m courseforge doctor      check Canvas token and Claude login
    python -m courseforge courses     list your courses
    python -m courseforge tools       the optional tools, and install them
    python -m courseforge record      what the Studio did, and whether it adds up
    python -m courseforge students    what it knows about a student, by tag
"""
from __future__ import annotations

import argparse
import sys

from . import blender, humanize, llm
from .canvas import CanvasClient
from .config import Config
from .server import serve


def _indent(text, pad: str = "                 ") -> str:
    """Line up a multi-line message under the label it belongs to."""
    return "\n".join(pad + line if line.strip() else line
                     for line in str(text).splitlines())


def cmd_doctor(cfg: Config) -> int:
    ok = True
    print("CourseForge Studio doctor\n")

    print(f"Canvas host    : {cfg.base_url}")
    token = None
    try:
        token = cfg.token()
        print(f"Canvas token   : found ({len(token)} chars)")
        # The encrypted copy is what is read; an old plaintext file left
        # beside it is still a token on disk, often in a synced folder.
        plain = [c for c in cfg.token_candidates()
                 if c.is_file() and not c.name.endswith(".enc")]
        for c in plain:
            print(f"                 also a plaintext copy at {c}; delete it once "
                  "this says OK.")
    except Exception as exc:  # noqa: BLE001
        # A token that was never found is not a failed login, and saying so
        # sends people off to regenerate a perfectly good token instead of
        # looking at where they put the file.
        ok = False
        print(f"Canvas token   : NOT FOUND\n{_indent(exc)}")
    if token:
        try:
            me = CanvasClient(cfg.base_url, token).whoami()
            print(f"Canvas login   : OK -- {me.get('name')} (id {me.get('id')})")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"Canvas login   : FAILED\n{_indent(exc)}")
            print("                 The token was read but Canvas rejected it. "
                  "Either the file\n"
                  "                 has something extra in it, or the token was "
                  "revoked;\n"
                  "                 generate a new one under Account -> Settings.")

    print()
    info = llm.doctor()
    print(f"Claude CLI     : {info.get('cli') or 'NOT FOUND'}")
    if info.get("version"):
        print(f"Claude version : {info['version']}")
    if info.get("logged_in"):
        print("Claude login   : OK")
    else:
        ok = False
        print(f"Claude login   : FAILED\n                 {info.get('detail','')}")
        print("\n                 Fix: open a normal terminal, run `claude`, then `/login`.")
        print("                 If you launched this from inside a Claude Code session,")
        print("                 start it from a plain terminal instead.")

    print()
    # Check GitHub first, so the line below names the copy that will actually
    # be used from now on, not the one that was current a minute ago.
    fresh: dict = {}
    if humanize.enabled(cfg) and humanize.auto_update_enabled(cfg):
        fresh = humanize.refresh(cfg)
    hz = humanize.doctor(cfg)
    if not hz.get("enabled"):
        print("Humanizer      : OFF (humanize is false in config.json)")
    elif hz.get("ok"):
        print(f"Humanizer      : on, {hz['model']} edits with {hz['label']}")
        if not hz.get("auto_update"):
            print("                 automatic updates are off (humanize_auto_update);")
            print("                 the skill changes only when a new copy is installed.")
        elif fresh.get("result") in ("updated", "unchanged"):
            print(f"                 kept current from GitHub: v{fresh.get('version') or '?'} "
                  f"({fresh['result']} just now; checked again once a day)")
        else:
            print(f"                 GitHub check failed: {fresh.get('error') or fresh.get('result') or '?'}")
            print("                 Grading goes on with the copy named above.")
    else:
        ok = False
        print(f"Humanizer      : {hz['label']}")
        print("                 Every comment, rationale and summary would be shown as")
        print("                 the grading model wrote it. Reinstall the app, or set")
        print('                 "humanize_skill_path" in config.json to a SKILL.md.')

    print()
    bl = blender.probe(cfg.blender_path)
    print(f"Blender        : {bl.get('path') or 'NOT FOUND'}")
    if bl.get("version"):
        print(f"Blender version: {bl['version']}")
    if not bl.get("ok"):
        print(f"                 {bl.get('detail', '')}")
        print("                 .blend grading is unavailable until this is fixed.")
        print("                 Install from https://www.blender.org/download/, or set")
        print('                 "blender_path" in config.json.')

    # The optional tools behind the accessibility areas. Missing ones are not a
    # failure: each area disables the verbs that need them and says how to install.
    print()
    from . import tools as _tools
    found = _tools.detect(cfg)
    for name, label in (("tesseract", "Tesseract OCR"), ("verapdf", "veraPDF"),
                        ("java", "Java runtime"), ("pdf_engine", "PDF engine"),
                        ("python_pptx", "python-pptx"), ("python_docx", "python-docx")):
        info = found.get(name, {})
        state = "OK" if info.get("ok") else "not found"
        extra = info.get("version") or info.get("path") or ""
        print(f"{label:15s}: {state}" + (f"  ({extra})" if info.get("ok") and extra else ""))
        if not info.get("ok"):
            print(f"                 enables: {info.get('enables', '')}")
            if info.get("install"):
                print(f"                 install: {info['install']}")

    print()
    try:
        from .server import App
        areas = App(cfg).area_status
        bad = {k: v.get("error") for k, v in areas.items() if not v.get("ok")}
        print(f"Areas          : {', '.join(k for k, v in areas.items() if v.get('ok')) or 'none'}")
        for name, err in bad.items():
            ok = False
            print(f"                 {name}: FAILED to load\n{_indent(err)}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"Areas          : FAILED\n{_indent(exc)}")

    print(f"\nData directory : {cfg.data}")
    print(f"Model          : {cfg.model}  (backend: {llm.backend_name()})")
    print(f"Pseudonymize   : {cfg.pseudonymize}")
    print(f"Canvas writes  : {'allowed, each one confirmed first'
                                 if cfg.allow_canvas_writes else 'LOCKED OFF'}")
    print("\n" + ("All good." if ok else "Some checks failed -- see above."))
    return 0 if ok else 1


def cmd_courses(cfg: Config) -> int:
    client = CanvasClient(cfg.base_url, cfg.token())
    for course in client.courses(cfg.enrollment_types):
        mark = "  (excluded)" if cfg.is_excluded(course) else ""
        term = (course.get("term") or {}).get("name", "")
        print(f"{course['id']:>8}  {course.get('name','')}  [{term}]{mark}")
    return 0


def cmd_tools(cfg: Config, install: bool = False, yes: bool = False,
              ask_again: bool = False) -> int:
    from . import tools as _tools
    if ask_again:
        _tools.forget_setup()
        print("Forgotten. The next launch will offer to install what is missing.\n")
    if install:
        return _tools.install(cfg, yes=yes)
    found = _tools.detect(cfg)
    plan = _tools.install_plan(cfg)
    print("CourseForge Studio tools\n")
    for name, info in found.items():
        mark = "OK " if info.get("ok") else "-- "
        extra = info.get("version") or info.get("path") or ""
        print(f"{mark}{name:14s} {extra[:48]}")
        if not info.get("ok"):
            print(f"   {info.get('enables', '')}")
    if plan["rows"]:
        print("\nMissing. To get them:\n")
        for row in plan["rows"]:
            print(f"  {row['label']}")
            steps = row.get("command") or row.get("steps", "")
            print("   " + steps.replace("\n", "\n   "))
        print("\nOr let this do the ones a package manager knows about:")
        print("  python -m courseforge tools --install --yes")
    else:
        print("\nEverything optional is here.")
    return 0


def cmd_record(cfg: Config, course: str, verify: bool, sync: bool,
               student: str = "", limit: int = 30) -> int:
    """The account of what the Studio did, from a terminal.

    Deliberately usable without the web UI: the person who needs this a year
    from now may be an administrator with the data folder and no Studio.
    """
    from pathlib import Path
    from . import audit
    root = (Path(cfg.data) if course in ("", "account")
            else Path(cfg.data) / str(course))
    where = "the account-wide record" if course in ("", "account") else f"course {course}"
    print(f"CourseForge Studio record -- {where}\n")
    info = audit.summary(root)
    if not info["entries"]:
        print(f"Nothing recorded yet. Looked in {audit.folder(root)}")
        return 0
    print(f"Entries        : {info['entries']}")
    print(f"Months         : {', '.join(info['months'])}")
    print(f"On this PC     : {audit.folder(root)}")
    saved = [k for k in (info["uploaded"] or {}) if k != "readme"]
    print(f"In Canvas      : {', '.join(saved) if saved else 'not saved yet'}"
          f"  (Files / {audit.FOLDER})")

    if verify:
        out = audit.verify(root)
        print(f"\nChain          : {'UNBROKEN' if out['ok'] else 'BROKEN'} over "
              f"{out['entries']} entries")
        print(_indent(out["why"]))
        if not out["ok"]:
            broke = out["broke_at"] or {}
            print(_indent(f"at entry {broke.get('seq')} in {broke.get('file')}: "
                          f"{broke.get('sentence', '')}"))
            return 2

    if sync:
        from .canvas import CanvasClient
        client = CanvasClient(cfg.base_url, cfg.token())
        out = audit.sync(client, root, None if course in ("", "account") else course,
                         force=True, say=lambda t: print(_indent(t)))
        print(f"\nSaved to Canvas: {out['detail']}")
        for bad in out["failed"]:
            print(_indent(f"{bad['name']}: {bad['error']}"))

    rows = audit.read(root, limit=limit, student=student)
    print(f"\nLast {len(rows)}:\n")
    for row in rows:
        people = ", ".join(p.get("name") or p.get("id", "")
                           for p in (row.get("students") or []))
        mark = " " if row.get("result") == "ok" else "!"
        print(f"{mark} {row.get('at', '')[:16].replace('T', ' ')}  "
              f"{row.get('area', ''):15s} {row.get('sentence', '')}")
        if people:
            print(f"{'':21s}{'':15s} for {people}")
    return 0


def cmd_students(cfg: Config, course: str, who: str = "") -> int:
    """What the Studio knows about a student, by tag, with the name taken out.

    The Assistant runs this instead of reading Canvas, which it cannot do. The
    pseudonymising happens inside `students.py`, so there is no form of this
    command that prints a name.
    """
    import json

    from . import students
    from .server import App
    app = App(cfg)
    try:
        out = (students.student_view(app, course, who) if who
               else students.class_view(app, course))
    except students.NotOnThisRoster as exc:
        print(str(exc).strip("\"'"))
        return 2
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    from . import cli as area_cli
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(
        prog="courseforge",
        description="CourseForge Studio: grading, accessibility, content and course tools for Canvas. "
                    "With no command it starts the local web UI.")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--port", type=int, help="override the listen port (serve)")
    sub = parser.add_subparsers(dest="command", metavar="command")
    p_serve = sub.add_parser("serve", help="start the local web UI (default)")
    p_serve.add_argument("--port", type=int, dest="serve_port", help="override the listen port")
    sub.add_parser("gui", help="the tkinter status window that owns the server")
    sub.add_parser("doctor", help="check the Canvas token, Claude login and tools")
    sub.add_parser("courses", help="list your courses")
    p_tools = sub.add_parser("tools", help="the optional tools, and how to get the missing ones")
    p_tools.add_argument("--install", action="store_true",
                         help="install what a package manager can fetch")
    p_tools.add_argument("--yes", action="store_true",
                         help="with --install, run the installers rather than printing them")
    p_tools.add_argument("--ask-again", action="store_true",
                         help="forget the saved answer so the first-run offer returns")
    p_students = sub.add_parser(
        "students", help="what the Studio knows about a student, by tag")
    sv = p_students.add_subparsers(dest="verb", required=True)
    sl = sv.add_parser("list", help="every student in the course as a tag and a line of state")
    sl.add_argument("--course", required=True, help="Canvas course id")
    ss = sv.add_parser("show", help="one student, by tag")
    ss.add_argument("--course", required=True, help="Canvas course id")
    ss.add_argument("--who", required=True, help="a tag, for example Student-14")

    p_record = sub.add_parser("record", help="the account of what the Studio did")
    p_record.add_argument("--course", default="account",
                          help="Canvas course id, or 'account' for what belongs to no course")
    p_record.add_argument("--verify", action="store_true",
                          help="walk the chain and say whether it has been changed")
    p_record.add_argument("--sync", action="store_true",
                          help="save every month to your Canvas user files now")
    p_record.add_argument("--student", default="",
                          help="only entries naming this Canvas user id or name")
    p_record.add_argument("--limit", type=int, default=30, help="how many entries to print")
    area_cli.register_all(sub)
    args = parser.parse_args(argv)

    cfg = Config.load(args.config)
    port = getattr(args, "serve_port", None) or args.port
    if port:
        cfg.port = port

    if args.command == "doctor":
        return cmd_doctor(cfg)
    if args.command == "courses":
        return cmd_courses(cfg)
    if args.command == "tools":
        return cmd_tools(cfg, install=args.install, yes=args.yes,
                         ask_again=args.ask_again)
    if args.command == "students":
        return cmd_students(cfg, args.course, getattr(args, "who", "") or "")
    if args.command == "record":
        return cmd_record(cfg, args.course, args.verify, args.sync,
                          args.student, args.limit)
    if args.command == "gui":
        from .launcher import main as gui_main
        return gui_main()
    if args.command in (None, "serve"):
        serve(cfg)
        return 0
    return area_cli.dispatch(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
