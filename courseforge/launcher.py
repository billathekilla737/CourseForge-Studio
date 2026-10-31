"""Tiny desktop launcher for CourseForge Studio.

Double-click, get a small window that says the server is running and offers a
button to open it. Close the window and everything stops: the HTTP server, its
worker threads, and any Claude subprocesses still grading.

Run with pythonw.exe (or the bundled .vbs) so no console appears.
"""
from __future__ import annotations

import queue
import socket
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox

from . import __version__, blender, llm, update
from .canvas import CanvasClient
from .config import APP_DIR, Config
from .server import build_server

BG = "#1c2026"
CARD = "#232830"
INK = "#e7eaee"
MUTED = "#98a2b1"
OK = "#6fbf95"
WARN = "#d9a441"
BAD = "#e08078"
NEW = "#6f9fd8"        # "there is a newer version", which is news, not a fault


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class Launcher:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.server = None
        self.thread: threading.Thread | None = None
        self.events: queue.Queue = queue.Queue()
        self.quitting = False
        self.update_status = update.Status()

        self.root = tk.Tk()
        self.root.title("CourseForge Studio")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._center(460, 350)

        self.f_title = tkfont.Font(family="Segoe UI", size=15, weight="bold")
        self.f_body = tkfont.Font(family="Segoe UI", size=9)
        self.f_mono = tkfont.Font(family="Consolas", size=9)
        self.f_btn = tkfont.Font(family="Segoe UI", size=10, weight="bold")

        self._build()
        # The first-run offer comes before the server, so the answer is already
        # in when the areas first report what they can do. It is an offer, not a
        # gate: whatever the person says, the app starts.
        self.root.after(40, self._first_run)
        self.root.after(120, self._drain)

    # ------------------------------------------------------------------ ui
    def _center(self, w: int, h: int) -> None:
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 3
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _build(self) -> None:
        pad = {"padx": 22}

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", anchor="w", pady=(18, 0), **pad)
        tk.Label(head, text="CourseForge Studio", font=self.f_title,
                 bg=BG, fg=INK).pack(side="left")

        # Hidden until there is something to say. A badge that is always there
        # saying "up to date" trains people to stop reading it.
        self.update_btn = tk.Button(
            head, text="\u2b07 Update", font=self.f_body, command=self.open_update,
            bg=CARD, fg=NEW, activebackground="#333a44", activeforeground=NEW,
            relief="flat", cursor="hand2", padx=9, pady=3, borderwidth=0)

        self.version_lbl = tk.Label(self.root, text=f"version {__version__}",
                                    font=self.f_body, bg=BG, fg=MUTED)
        self.version_lbl.pack(anchor="w", **pad)

        row = tk.Frame(self.root, bg=BG)
        row.pack(anchor="w", pady=(6, 0), **pad)
        self.dot = tk.Canvas(row, width=11, height=11, bg=BG, highlightthickness=0)
        self.dot.pack(side="left", pady=(1, 0))
        self.dot_id = self.dot.create_oval(1, 1, 10, 10, fill=WARN, outline="")
        self.status = tk.Label(row, text="Starting…", font=self.f_body, bg=BG, fg=MUTED)
        self.status.pack(side="left", padx=(8, 0))

        self.url = tk.Label(self.root, text=f"http://127.0.0.1:{self.cfg.port}",
                            font=self.f_mono, bg=BG, fg=MUTED)
        self.url.pack(anchor="w", pady=(2, 0), **pad)

        # Which copy of the code this window is actually running. Worth the two
        # lines: a second folder somewhere (a re-download, a copy on a stick)
        # runs its own code and looks for its own token, and every symptom of
        # that looks like a bug in the app instead of a wrong shortcut.
        self.where = tk.Label(self.root, text=f"from {APP_DIR}", font=self.f_mono,
                              bg=BG, fg=MUTED, anchor="w", justify="left",
                              wraplength=380)
        self.where.pack(anchor="w", pady=(1, 0), **pad)

        self.open_btn = tk.Button(
            self.root, text="Open CourseForge Studio", font=self.f_btn, command=self.open_browser,
            bg="#2f6f4f", fg="white", activebackground="#3d8a63", activeforeground="white",
            relief="flat", cursor="hand2", padx=14, pady=9, state="disabled",
            disabledforeground="#8fa39a",
        )
        self.open_btn.pack(fill="x", pady=(16, 0), **pad)

        card = tk.Frame(self.root, bg=CARD, highlightthickness=0)
        card.pack(fill="x", pady=(16, 0), **pad)
        self.canvas_lbl = tk.Label(card, text="Canvas   checking…", font=self.f_body,
                                   bg=CARD, fg=MUTED, anchor="w", justify="left",
                                   wraplength=350)
        self.canvas_lbl.pack(anchor="w", padx=12, pady=(9, 1))
        self.claude_lbl = tk.Label(card, text="Claude   checking…", font=self.f_body,
                                   bg=CARD, fg=MUTED, anchor="w", justify="left",
                                   wraplength=350)
        self.claude_lbl.pack(anchor="w", padx=12, pady=(1, 9))

        foot = tk.Frame(self.root, bg=BG)
        foot.pack(fill="x", side="bottom", pady=(0, 14), **pad)
        tk.Label(foot, text="Closing this window stops the server.",
                 font=self.f_body, bg=BG, fg=MUTED).pack(side="left")
        tk.Button(foot, text="Quit", font=self.f_body, command=self.on_close,
                  bg=CARD, fg=INK, activebackground="#333a44", activeforeground=INK,
                  relief="flat", cursor="hand2", padx=14, pady=5).pack(side="right")

    def _set_state(self, colour: str, text: str) -> None:
        self.dot.itemconfig(self.dot_id, fill=colour)
        self.status.config(text=text, fg=INK if colour == OK else MUTED)

    # ----------------------------------------------------------- first run
    def _first_run(self) -> None:
        """Offer the optional tools once, then start the server either way."""
        try:
            from . import setup_window
            self._set_state(WARN, "Checking what this machine has…")
            self.root.update_idletasks()
            answer = setup_window.offer(self.cfg, self.root)
            if answer == "installed":
                # winget puts a new tool on the PATH of programs started after
                # it, so this process still cannot see it. Say so rather than
                # reporting it missing and letting them think it failed.
                self.canvas_lbl.config(
                    text="A tool was just installed. Restart this window for it "
                         "to be found.", fg=WARN)
        except Exception:  # noqa: BLE001
            pass          # never let the offer stop the app starting
        self._start_server()

    # -------------------------------------------------------------- server
    def _start_server(self) -> None:
        if port_in_use(self.cfg.port):
            self._set_state(WARN, f"Already running on port {self.cfg.port}")
            self.open_btn.config(state="normal")
            self.canvas_lbl.config(text="Another copy of CourseForge Studio is already running.\n"
                                        "This window will just open it.", fg=WARN)
            self.claude_lbl.config(text="Quit the other window to shut the server down.", fg=MUTED)
            return
        try:
            self.server, _app = build_server(self.cfg)
        except OSError as exc:
            self._set_state(BAD, "Could not start")
            self.canvas_lbl.config(text=f"Port {self.cfg.port} is unavailable: {exc}", fg=BAD)
            self.claude_lbl.config(text='Change "port" in config.json and try again.', fg=MUTED)
            return

        # The same background pieces `serve` starts, so the window and the
        # terminal run the same app.
        _app.start_handoff_worker()
        _app.start_browse_refresh()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self._set_state(OK, "Running")
        self.open_btn.config(state="normal")
        threading.Thread(target=self._checks, daemon=True).start()
        if getattr(self.cfg, "check_updates", True):
            threading.Thread(target=self._check_update, daemon=True).start()

    def _checks(self) -> None:
        try:
            me = CanvasClient(self.cfg.base_url, self.cfg.token()).whoami()
            self.events.put(("canvas", OK, f"Canvas   {me.get('name')}"))
        except Exception as exc:  # noqa: BLE001
            # A missing token is not a failure to explain here: the setup screen
            # in the browser takes it and writes the file itself. Say that,
            # rather than repeating the whole list of paths it looked in.
            note = str(exc)
            if "token" in note.lower():
                note = ("no token yet — click Open CourseForge Studio and paste "
                        "one on the setup screen")
            self.events.put(("canvas", BAD, f"Canvas   {note}"))

        info = llm.doctor()
        if info.get("logged_in"):
            self.events.put(("claude", OK, f"Claude   logged in · model {self.cfg.model}"))
        else:
            self.events.put(("claude", BAD,
                             "Claude   not logged in — auto-grading is off.\n"
                             "Run  claude  then  /login  in a terminal, then reopen this."))

    def _check_update(self) -> None:
        """Ask GitHub, on a thread, and never let the answer break the window."""
        try:
            status = update.check(self.cfg)
        except Exception as exc:  # noqa: BLE001
            status = update.Status(error=str(exc)[:160])
        self.events.put(("update", "", status))

    def _show_update_badge(self, status) -> None:
        self.update_status = status
        if status.error:
            # Offline, or the source is private and this copy cannot see it.
            # Not worth a badge; the version line carries it quietly.
            self.version_lbl.config(text=f"version {__version__} \u00b7 "
                                        f"update check failed", fg=MUTED)
            return
        rev = f" ({status.current})" if status.current else ""
        if status.available:
            self.update_btn.pack(side="left", padx=(10, 0))
            self.version_lbl.config(
                text=f"version {__version__}{rev} \u00b7 a newer version is available",
                fg=NEW)
        else:
            self.version_lbl.config(text=f"version {__version__}{rev} \u00b7 up to date",
                                    fg=MUTED)

    def _drain(self) -> None:
        try:
            while True:
                which, colour, text = self.events.get_nowait()
                if which == "update":
                    self._show_update_badge(text)
                    continue
                label = self.canvas_lbl if which == "canvas" else self.claude_lbl
                label.config(text=text, fg=colour)
        except queue.Empty:
            pass
        if not self.quitting:
            self.root.after(200, self._drain)

    # --------------------------------------------------------------- actions
    def open_browser(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.cfg.port}")

    # --------------------------------------------------------------- updating
    def open_update(self) -> None:
        """What is new, and one button to take it.

        A window rather than a message box because the interesting part is the
        list of what changed, and because the same window has to be able to
        turn into a progress log without a second dialog appearing on top.
        """
        status = self.update_status
        win = tk.Toplevel(self.root)
        win.title("Update CourseForge Studio")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        w, h = 480, 430
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + 40
        win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")

        pad = {"padx": 20}
        tk.Label(win, text="A newer version is available", font=self.f_title,
                 bg=BG, fg=INK).pack(anchor="w", pady=(18, 2), **pad)
        tk.Label(win, text=f"You have {status.current or 'an unknown version'} \u00b7 "
                           f"newest is {status.latest}", font=self.f_body,
                 bg=BG, fg=MUTED).pack(anchor="w", **pad)

        card = tk.Frame(win, bg=CARD)
        card.pack(fill="both", expand=True, pady=(12, 0), **pad)
        notes = "\n".join("\u2022 " + n for n in status.notes) or \
            "No description of the changes was available."
        log = tk.Text(card, bg=CARD, fg=INK, font=self.f_body, relief="flat",
                      wrap="word", height=11, borderwidth=0,
                      highlightthickness=0, padx=12, pady=10)
        log.insert("1.0", notes)
        log.config(state="disabled")
        log.pack(fill="both", expand=True)

        note = tk.Label(win, text="", font=self.f_body, bg=BG, fg=MUTED,
                        wraplength=430, justify="left", anchor="w")
        note.pack(fill="x", anchor="w", pady=(10, 0), **pad)

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", side="bottom", pady=(0, 16), **pad)
        close = tk.Button(foot, text="Not now", font=self.f_body, command=win.destroy,
                          bg=CARD, fg=INK, activebackground="#333a44",
                          activeforeground=INK, relief="flat", cursor="hand2",
                          padx=14, pady=6)
        close.pack(side="right")
        go = tk.Button(foot, text="Update now", font=self.f_btn,
                       bg="#2f6f4f", fg="white", activebackground="#3d8a63",
                       activeforeground="white", relief="flat", cursor="hand2",
                       padx=14, pady=7, disabledforeground="#8fa39a")
        go.pack(side="right", padx=(0, 8))

        if not status.can_apply:
            # A checkout with uncommitted work. Say why, and do not offer it.
            go.config(state="disabled")
            note.config(text=status.why_not, fg=WARN)
            return
        if status.why_not:
            note.config(text=status.why_not, fg=MUTED)

        def say(line: str, *_a, **_k) -> None:
            self.root.after(0, lambda: note.config(text=line, fg=MUTED))

        def finish(result: dict) -> None:
            go.config(state="normal", text="Update now")
            close.config(text="Close")
            if not result.get("ok"):
                note.config(text=result.get("message") or "The update failed.", fg=BAD)
                return
            note.config(text=result["message"], fg=OK)
            go.config(text="Restart now", command=self.restart,
                      bg="#2f6f4f", state="normal")

        def start() -> None:
            go.config(state="disabled", text="Updating\u2026")
            close.config(state="disabled")

            def work() -> None:
                try:
                    out = update.apply(self.cfg, status, log=say)
                except Exception as exc:  # noqa: BLE001
                    out = {"ok": False, "message": f"The update failed: {exc}"}
                self.root.after(0, lambda: (close.config(state="normal"), finish(out)))
            threading.Thread(target=work, daemon=True).start()

        go.config(command=start)

    def restart(self) -> None:
        """Start a fresh copy of the launcher, then close this one.

        The new files are already on disk; this process is still running the
        old ones because Python read them at import. Only a new process picks
        the update up, so the button that says restart has to actually do it.
        """
        import subprocess
        try:
            exe = sys.executable
            # pythonw keeps the console away, matching how the .vbs starts it.
            quiet = Path(exe).with_name("pythonw.exe")
            if quiet.exists():
                exe = str(quiet)
            subprocess.Popen([exe, "-m", "courseforge.launcher"],
                             cwd=str(update.install_dir()), close_fds=True)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "CourseForge Studio",
                f"Could not start the new copy automatically:\n\n{exc}\n\n"
                f"Close this window and open CourseForge Studio again.")
            return
        self.on_close()

    def on_close(self) -> None:
        if self.quitting:
            return
        killed = 0
        try:
            killed = llm.shutdown_all()
        except Exception:  # noqa: BLE001
            pass
        if killed and not messagebox.askokcancel(
            "Grading in progress",
            f"{killed} student{'s' if killed != 1 else ''} still being graded.\n\n"
            "Quit anyway? Scores already saved are kept; the rest can be re-run.",
        ):
            return

        self.quitting = True
        self._set_state(WARN, "Shutting down…")
        self.status.update_idletasks()

        def stop() -> None:
            if self.server is not None:
                try:
                    self.server.shutdown()
                    self.server.server_close()
                except Exception:  # noqa: BLE001
                    pass
            if self.thread is not None:
                self.thread.join(timeout=5)
            for mod in (llm, blender):
                try:
                    mod.shutdown_all()
                except Exception:  # noqa: BLE001
                    pass
            self.root.after(0, self.root.destroy)

        threading.Thread(target=stop, daemon=True).start()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    try:
        cfg = Config.load()
    except Exception as exc:  # noqa: BLE001
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("CourseForge Studio", f"Could not load configuration:\n\n{exc}")
        return 1
    Launcher(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
