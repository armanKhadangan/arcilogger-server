"""The desktop status window: tkinter + ttk, a thin view of :class:`~.status.Snapshot`.

Every decision (levels, texts, when to flash) is made in :mod:`arcilogger_core.desktop.status`;
this module only lays it out. A background thread takes a new snapshot every
:data:`INTERVAL_MS` (and at once on "Yenile") and hands it to the Tk thread through a queue, so a
slow health request never freezes the window. The window has no control that changes anything:
"Web arayüzünü aç" opens ``https://localhost/`` in the default browser, "Yenile" looks again.

With the notification-area icon (:mod:`.tray`, Wave 5 addendum C) closing the window only hides
it; the icon's menu shows it again, opens the web interface, lists the backups
(:class:`BackupsWindow`, :mod:`.backups`) or ends the program. The icon's commands come from its
own thread through a queue that the Tk thread reads with the snapshots.

Only standard Tk 8.6 / ttk widgets are used (python-build-standalone for Windows ships Tk 8.6).
``--self-test`` (:func:`self_test`) builds the window off-screen, shows a snapshot of every
level and checks that the taskbar flashes once on the rise to critical, that the icon follows
the level and that the Backups list shows the dumps.
"""

from __future__ import annotations

import hashlib
import queue
import tempfile
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk
from typing import Final

from arcilogger_core.desktop import backups, tray, winapi
from arcilogger_core.desktop.status import (
    COLOURS,
    LEVEL_TEXTS,
    Level,
    Row,
    Snapshot,
    clock,
    flash_due,
    window_title,
)

INTERVAL_MS: Final = 10_000
#: Tk start-up attempts. On a busy Windows CI runner Tk's start-up script now and then cannot read
#: one of its own library files ("couldn't read file …/tk8.6/icons.tcl", another file each time);
#: the next interpreter reads them all. When the unread file is the one that defines
#: ``tcl_findLibrary``, the same failure reads 'invalid command name "tcl_findLibrary"'. Any other
#: start-up error (no display) is raised at once.
TK_ATTEMPTS: Final = 3
#: The start-up messages :data:`TK_ATTEMPTS` tries again on.
TK_RETRY_MESSAGES: Final = ("couldn't read file", 'invalid command name "tcl_findLibrary"')
POLL_MS: Final = 250
WEB_URL: Final = "https://localhost/"
PAD: Final = 8
GREY: Final = COLOURS["unknown"]


class _Scale:
    """Pixels per 96-dpi pixel (the process is DPI aware on Windows; Tk scales its fonts)."""

    factor = 1.0


def px(size: int) -> int:
    return round(size * _Scale.factor)


class Dot(tk.Canvas):
    """A filled circle in a level's colour, drawn (a "●" character depends on the font)."""

    def __init__(self, master: tk.Misc, diameter: int) -> None:
        background = ttk.Style(master).lookup(
            "TFrame", "background"
        ) or master.winfo_toplevel().cget("background")
        super().__init__(
            master, width=diameter, height=diameter, highlightthickness=0, background=background
        )
        self._oval = self.create_oval(1, 1, diameter - 1, diameter - 1, fill=GREY, outline=GREY)

    def set(self, level: Level) -> None:
        self.itemconfigure(self._oval, fill=COLOURS[level], outline=COLOURS[level])


def _dot(master: tk.Misc, level: Level) -> tk.PhotoImage:
    """A filled circle in the level's colour, for the window icon."""
    size = 32
    image = tk.PhotoImage(master=master, width=size, height=size)
    colour = COLOURS[level]
    centre, radius = (size - 1) / 2, size / 2 - 1
    for y in range(size):
        span = [x for x in range(size) if (x - centre) ** 2 + (y - centre) ** 2 <= radius**2]
        if span:
            image.put(colour, to=(span[0], y, span[-1] + 1, y + 1))
    return image


class StatusWindow:
    def __init__(
        self,
        root: tk.Tk,
        refresh: Callable[[], Snapshot],
        *,
        interval_ms: int = INTERVAL_MS,
        flash: Callable[[int], None] = winapi.flash,
        open_url: Callable[[str], object] = webbrowser.open,
        background: bool = True,
        backup_folders: Sequence[backups.Folder] = (),
    ) -> None:
        self.root = root
        self._refresh = refresh
        self._interval = interval_ms / 1000
        self._flash = flash
        self._open_url = open_url
        self._background = background
        self._backup_folders = tuple(backup_folders)
        self.level: Level | None = None
        self.tray: tray.TrayIcon | None = None
        self.backups: BackupsWindow | None = None
        self._queue: queue.Queue[Snapshot] = queue.Queue()
        self._commands: queue.Queue[tray.Command] = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._icons = {level: _dot(root, level) for level in COLOURS}
        self._rows: dict[
            str, list[tuple[str, Dot, ttk.Label, ttk.Label, ttk.Label, ttk.Label]]
        ] = {}

        base = font.nametofont("TkDefaultFont")
        self._bold = base.copy()
        self._bold.configure(weight="bold")
        self._small = base.copy()
        self._small.configure(size=max(int(base.cget("size")) - 1, 7))
        self._big = base.copy()
        self._big.configure(size=int(base.cget("size")) + 5, weight="bold")

        root.title(window_title("unknown"))
        root.minsize(px(600), px(420))
        root.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(root, padding=PAD)
        outer.pack(fill=tk.BOTH, expand=True)
        header = ttk.Frame(outer)
        header.pack(fill=tk.X)
        self.badge = Dot(header, px(22))
        self.badge.pack(side=tk.LEFT, padx=(0, PAD))
        self.headline = ttk.Label(header, text="ARCILOGGER", font=self._big)
        self.headline.pack(side=tk.LEFT)
        self.service_box = ttk.LabelFrame(outer, text="Servisler / Services", padding=PAD)
        self.service_box.pack(fill=tk.X, pady=(PAD, 0))
        self.check_box = ttk.LabelFrame(outer, text="Durum / Status", padding=PAD)
        self.check_box.pack(fill=tk.X, pady=(PAD, 0))
        footer = ttk.Frame(outer)
        footer.pack(fill=tk.X, pady=(PAD, 0))
        ttk.Button(footer, text="Yenile / Refresh", command=self.refresh_now).pack(side=tk.RIGHT)
        ttk.Button(
            footer, text="Web arayüzünü aç / Open the web interface", command=self.open_web
        ).pack(side=tk.RIGHT, padx=(0, PAD))
        self.updated = ttk.Label(outer, text="", foreground=GREY, font=self._small)
        self.updated.pack(fill=tk.X, pady=(PAD // 2, 0))
        ttk.Label(
            outer,
            text=(
                "Yalnızca okur; hiçbir ayarı ya da servisi değiştirmez. / "
                "Read-only; it changes no setting and no service."
            ),
            foreground=GREY,
            font=self._small,
        ).pack(fill=tk.X, pady=(PAD // 2, 0))

        if background:
            threading.Thread(target=self._work, name="arcilogger-status", daemon=True).start()
            root.after(POLL_MS, self._poll)
        else:
            self.show(self._take())

    # -- the notification-area icon -----------------------------------------------------------

    def attach(self, icon: tray.TrayIcon) -> None:
        """Use ``icon`` from now on: it follows the level, and closing the window hides it."""
        self.tray = icon
        if self.level is not None:
            icon.set(self.level, tray.tooltip(self.level))

    def post_command(self, command: tray.Command) -> None:
        """Called from the icon's thread: the Tk thread runs the command (:meth:`run_commands`)."""
        self._commands.put(command)

    def run_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if command == "show":
                self.reveal()
            elif command == "web":
                self.open_web()
            elif command == "backups":
                self.open_backups()
            elif command == "quit":
                self.quit()
                return

    def reveal(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def open_backups(self) -> None:
        if self.backups is not None and self.backups.top.winfo_exists():
            self.backups.top.deiconify()
            self.backups.top.lift()
            return
        self.backups = BackupsWindow(self.root, self._backup_folders)

    # -- data ---------------------------------------------------------------------------------

    def _take(self) -> Snapshot:
        try:
            return self._refresh()
        except Exception as exc:  # the window must stay up; show what went wrong instead
            text = "".join(traceback.format_exception_only(exc)).strip()
            row = Row("error", "unknown", "Pencere", "Window", f"durum alınamadı: {text}", text)
            return Snapshot(at=datetime.now(UTC), services=(), checks=(row,))

    def _work(self) -> None:
        while not self._stop.is_set():
            self._queue.put(self._take())
            self._wake.wait(self._interval)
            self._wake.clear()

    def _poll(self) -> None:
        latest = None
        while True:
            try:
                latest = self._queue.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self.show(latest)
        self.run_commands()
        if not self._stop.is_set():
            self.root.after(POLL_MS, self._poll)

    def refresh_now(self) -> None:
        if self._background:
            self._wake.set()
        else:
            self.show(self._take())

    def open_web(self) -> None:
        self._open_url(WEB_URL)

    def close(self) -> None:
        """The window's close button: with the icon the window is only hidden."""
        if self.tray is not None:
            self.root.withdraw()
            return
        self.quit()

    def quit(self) -> None:
        self._stop.set()
        self._wake.set()
        if self.tray is not None:
            self.tray.close()
            self.tray = None
        self.root.destroy()

    # -- view ---------------------------------------------------------------------------------

    def show(self, snapshot: Snapshot) -> None:
        level = snapshot.level
        tr, en = LEVEL_TEXTS[level]
        self.root.title(window_title(level))
        self.root.iconphoto(True, self._icons[level])
        self.badge.set(level)
        self.headline.configure(text=f"ARCILOGGER — {tr} / {en}", foreground=COLOURS[level])
        self._render(self.service_box, snapshot.services)
        self._render(self.check_box, snapshot.checks)
        self.updated.configure(
            text=(
                f"Son bakış / Last look: {clock(snapshot.at)} · her {round(self._interval)} sn / "
                f"every {round(self._interval)} s"
            )
        )
        if self.tray is not None:
            self.tray.set(level, tray.tooltip(level))
        if flash_due(self.level, level):
            self._flash(self.root.winfo_id())
            if self.tray is not None:
                self.tray.notify(*_critical_notice(snapshot))
        self.level = level

    def _render(self, box: ttk.LabelFrame, rows: Sequence[Row]) -> None:
        name = str(box)
        current = self._rows.get(name)
        if current is None or [entry[0] for entry in current] != [row.key for row in rows]:
            for child in box.winfo_children():
                child.destroy()
            current = []
            wrap = px(430)
            for index, row in enumerate(rows):
                dot = Dot(box, px(12))
                dot.grid(row=2 * index, column=0, sticky="n", padx=(0, PAD // 2), pady=(px(3), 0))
                title_tr = ttk.Label(box, font=self._bold)
                title_tr.grid(row=2 * index, column=1, sticky="nw", padx=(0, PAD))
                text_tr = ttk.Label(box, wraplength=wrap)
                text_tr.grid(row=2 * index, column=2, sticky="nw")
                title_en = ttk.Label(box, foreground=GREY, font=self._small)
                title_en.grid(row=2 * index + 1, column=1, sticky="nw", padx=(0, PAD))
                text_en = ttk.Label(box, foreground=GREY, font=self._small, wraplength=wrap)
                text_en.grid(row=2 * index + 1, column=2, sticky="nw", pady=(0, PAD // 2))
                current.append((row.key, dot, title_tr, text_tr, title_en, text_en))
            box.columnconfigure(2, weight=1)
            self._rows[name] = current
        for row, (_, dot, title_tr, text_tr, title_en, text_en) in zip(rows, current, strict=True):
            dot.set(row.level)
            title_tr.configure(text=row.title_tr)
            text_tr.configure(text=row.text_tr)
            title_en.configure(text=row.title_en)
            text_en.configure(text=row.text_en)


def _critical_notice(snapshot: Snapshot) -> tuple[str, str]:
    """The notification on the rise to critical: the first critical row, in both languages."""
    rows = [row for row in (*snapshot.services, *snapshot.checks) if row.level == "critical"]
    if not rows:
        return tray.critical_notice("", "")
    row = rows[0]
    return tray.critical_notice(f"{row.title_tr}: {row.text_tr}", f"{row.title_en}: {row.text_en}")


class BackupsWindow:
    """ "Yedekler / Backups": the database dumps, and a verified copy where the person chooses.

    The copy runs in a thread (a dump can be gigabytes); its result comes back through a queue
    read by the Tk thread, as the snapshots do.
    """

    COLUMNS: Final = ("name", "folder", "time", "size")

    def __init__(
        self,
        master: tk.Misc,
        folders: Sequence[backups.Folder],
        *,
        choose_folder: Callable[[], str] | None = None,
        report: Callable[[str, str, bool], object] | None = None,
    ) -> None:
        self.folders = tuple(folders)
        self.top = tk.Toplevel(master)
        self.top.title("ARCILOGGER — Yedekler / Backups")
        self.top.minsize(px(640), px(320))
        self._choose_folder = choose_folder or self._ask_folder
        self._report = report or self._message
        self._results: queue.Queue[tuple[str, str, bool]] = queue.Queue()
        self._busy = False
        self.dumps: list[backups.Dump] = []

        outer = ttk.Frame(self.top, padding=PAD)
        outer.pack(fill=tk.BOTH, expand=True)
        places = "\n".join(
            f"{folder.title_tr} / {folder.title_en}: {folder.path}" for folder in self.folders
        )
        ttk.Label(outer, text=places or "Yedek klasörü bilinmiyor / Backup folder unknown").pack(
            fill=tk.X
        )
        self.note = ttk.Label(outer, foreground=GREY, wraplength=px(620), justify=tk.LEFT)
        self.note.pack(fill=tk.X, pady=(PAD // 2, PAD // 2))
        frame = ttk.Frame(outer)
        frame.pack(fill=tk.BOTH, expand=True)
        self.list = ttk.Treeview(frame, columns=self.COLUMNS, show="headings", height=10)
        headings = ("Dosya / File", "Klasör / Folder", "Zaman / Time", "Boyut / Size")
        widths = (260, 150, 120, 80)
        for column, heading, width in zip(self.COLUMNS, headings, widths, strict=True):
            self.list.heading(column, text=heading)
            self.list.column(column, width=px(width), stretch=column == "name")
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        self.list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        footer = ttk.Frame(outer)
        footer.pack(fill=tk.X, pady=(PAD, 0))
        ttk.Button(footer, text="Kapat / Close", command=self.top.destroy).pack(side=tk.RIGHT)
        ttk.Button(footer, text="Yenile / Refresh", command=self.refresh).pack(
            side=tk.RIGHT, padx=(0, PAD)
        )
        self.copy_button = ttk.Button(
            footer, text="Kopyasını kaydet… / Save a copy…", command=self.save_copy
        )
        self.copy_button.pack(side=tk.RIGHT, padx=(0, PAD))
        ttk.Button(
            footer, text="Klasör yolunu kopyala / Copy the folder path", command=self.copy_path
        ).pack(side=tk.LEFT)
        self.status = ttk.Label(outer, foreground=GREY, font=font.nametofont("TkSmallCaptionFont"))
        self.status.pack(fill=tk.X, pady=(PAD // 2, 0))
        self.refresh()
        self.top.after(POLL_MS, self._poll)

    def refresh(self) -> None:
        self.dumps = backups.list_dumps(self.folders)
        self.list.delete(*self.list.get_children())
        for index, dump in enumerate(self.dumps):
            self.list.insert("", tk.END, iid=str(index), values=tuple(backups.describe(dump)))
        if self.dumps:
            self.list.selection_set("0")
        blocked = backups.unreadable(self.folders)
        notes = [
            "Bu klasörleri yalnızca yöneticiler okuyabilir; dosya gezgininde izinlerini "
            "değiştirmeyin, kopyayı buradan alın. / Only administrators may read these folders: "
            "do not change their permissions in File Explorer, take a copy here."
        ]
        if blocked:
            notes.append(
                "Okunamayan klasör / Folder not readable: "
                + ", ".join(str(folder.path) for folder in blocked)
            )
        elif not self.dumps:
            notes.append("Henüz yedek yok. / No backup yet.")
        self.note.configure(text="\n".join(notes))

    def selected(self) -> backups.Dump | None:
        chosen = self.list.selection()
        return self.dumps[int(chosen[0])] if chosen else None

    def copy_path(self) -> None:
        if self.folders:
            self.top.clipboard_clear()
            self.top.clipboard_append(str(self.folders[0].path))
            self.status.configure(text="Yol kopyalandı / Path copied")

    def save_copy(self) -> None:
        dump = self.selected()
        if dump is None or self._busy:
            return
        target = self._choose_folder()
        if not target:
            return
        self._busy = True
        self.copy_button.state(["disabled"])
        self.status.configure(text=f"Kopyalanıyor / Copying: {dump.path.name}")
        threading.Thread(
            target=self._copy, args=(dump, Path(target)), name="arcilogger-backup-copy", daemon=True
        ).start()

    def _copy(self, dump: backups.Dump, target: Path) -> None:
        try:
            copied = backups.save_copy(dump, target)
        except backups.CopyError as exc:
            self._results.put(("Kopyalanmadı / Not copied", str(exc), False))
            return
        check = (
            "SHA-256 kayıtlı özetle aynı / matches the recorded hash"
            if copied.recorded
            else "SHA-256 kaynakla aynı / matches the source"
        )
        self._results.put(("Kopyalandı / Copied", f"{copied.path}\n{check}\n{copied.sha256}", True))

    def _poll(self) -> None:
        if not self.top.winfo_exists():
            return
        try:
            title, text, ok = self._results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._busy = False
            self.copy_button.state(["!disabled"])
            self.status.configure(text=title)
            self._report(title, text, ok)
        self.top.after(POLL_MS, self._poll)

    def _ask_folder(self) -> str:
        return filedialog.askdirectory(
            parent=self.top, title="Kopyanın kaydedileceği klasör / Folder for the copy"
        )

    def _message(self, title: str, text: str, ok: bool) -> object:
        show = messagebox.showinfo if ok else messagebox.showerror
        return show(title, text, parent=self.top)


def new_root() -> tk.Tk:
    """A Tk root, tried again when Tk could not read a library file (:data:`TK_ATTEMPTS`)."""
    for attempt in range(1, TK_ATTEMPTS + 1):
        try:
            return tk.Tk()
        except tk.TclError as exc:
            if attempt == TK_ATTEMPTS or not any(m in str(exc) for m in TK_RETRY_MESSAGES):
                raise
            time.sleep(0.2 * attempt)
    raise RuntimeError("no Tk attempt was made")


def run(
    refresh: Callable[[], Snapshot],
    *,
    backup_folders: Sequence[backups.Folder] = (),
    hidden: bool = False,
) -> int:
    """The window with its notification-area icon. ``hidden`` (the logon task): only the icon
    shows until it is clicked; without an icon the window is shown anyway."""
    root = new_root()
    _Scale.factor = max(1.0, round(float(root.winfo_fpixels("1i")) / 96.0 * 4) / 4)
    if hidden:
        root.withdraw()
    try:
        window = StatusWindow(root, refresh, backup_folders=backup_folders)
        icon = tray.start(window.post_command)
        if icon is not None:
            window.attach(icon)
        elif hidden:
            root.deiconify()
    except Exception:
        root.deiconify()
        messagebox.showerror("ARCILOGGER Durum", traceback.format_exc(limit=3), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


def sample(level: Level) -> Snapshot:
    """A snapshot of fictional rows at ``level`` (self-test, screenshots)."""
    services = (
        Row(
            "service:ArciCollector",
            "ok",
            "Toplayıcı (syslog) · ArciCollector",
            "Collector (syslog)",
            "çalışıyor",
            "running",
        ),
        Row(
            "service:ArciApi",
            "ok",
            "API · ArciApi",
            "API",
            "çalışıyor; yanıt veriyor",
            "running; answering",
        ),
    )
    texts: dict[Level, tuple[str, str]] = {
        "ok": ("son kayıt 14:32:05 (12 sn önce); 2 kaynak", "last message 14:32:05 (12 s ago)"),
        "warn": ("2 kaynak, 1 sessiz: fw-ornek", "2 sources, 1 silent: fw-ornek"),
        "unknown": ("henüz bilgi yok", "no information yet"),
        "critical": ("ham kayıtlar diske yazılamıyor (fw-ornek)", "raw logs cannot be written"),
    }
    tr, en = texts[level]
    return Snapshot(
        at=datetime.now(UTC),
        services=services,
        checks=(Row("flow", level, "Log akışı", "Log flow", tr, en),),
    )


class _RecordingTray:
    """A notification-area icon that only records (self-test, tests)."""

    def __init__(self) -> None:
        self.levels: list[Level] = []
        self.notices: list[tuple[str, str]] = []
        self.closed = False

    def set(self, level: Level, tip: str) -> None:
        self.levels.append(level)

    def notify(self, title: str, text: str) -> None:
        self.notices.append((title, text))

    def close(self) -> None:
        self.closed = True


def _backups_self_test(root: tk.Tk, problems: list[str]) -> None:
    """A dump with its sidecar in a temporary folder: listed once, copied and verified."""
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary) / "scheduled"
        target = Path(temporary) / "usb"
        folder.mkdir()
        target.mkdir()
        dump = folder / "arcilogger-20261101-014000.dump"
        dump.write_bytes(b"PGDMP self-test")
        digest = hashlib.sha256(b"PGDMP self-test").hexdigest()
        (folder / (dump.name + ".json")).write_text(f'{{"sha256": "{digest}"}}', encoding="utf-8")
        reports: list[tuple[str, str, bool]] = []
        view = BackupsWindow(
            root,
            [backups.Folder(folder, "Zamanlanmış yedekler", "Scheduled backups")],
            choose_folder=lambda: str(target),
            report=lambda title, text, ok: reports.append((title, text, ok)),
        )
        if len(view.list.get_children()) != 1:
            problems.append(f"backups listed {len(view.list.get_children())}, expected 1")
        view.save_copy()
        deadline = time.monotonic() + 10
        while not reports and time.monotonic() < deadline:
            root.update()
            time.sleep(0.05)
        if not reports or not reports[0][2] or not (target / dump.name).is_file():
            problems.append(f"backup copy {reports}")
        view.top.destroy()


def self_test() -> int:
    """Build the window off-screen and show every level; 0 when it behaves, 1 otherwise."""
    try:
        root = new_root()
        root.withdraw()
        flashed: list[int] = []
        opened: list[str] = []
        order: list[Level] = ["ok", "warn", "critical", "critical", "unknown", "critical"]
        snapshots = iter(sample(level) for level in order)
        window = StatusWindow(
            root,
            refresh=lambda: next(snapshots),
            flash=flashed.append,
            open_url=opened.append,
            background=False,
        )
        icon = _RecordingTray()
        window.attach(icon)
        for _ in order[1:]:
            window.refresh_now()
        window.open_web()
        root.update_idletasks()
        problems = []
        if len(flashed) != 2:
            problems.append(f"flashed {len(flashed)} times, expected 2")
        if icon.levels != [sample(level).level for level in order] or len(icon.notices) != 2:
            problems.append(f"icon levels {icon.levels}, notices {len(icon.notices)}")
        if not root.title().startswith(window_title("critical")):
            problems.append(f"title {root.title()!r}")
        if len(window.check_box.winfo_children()) != 5:
            problems.append("the status box does not show one row")
        # The icon's commands: open the web interface, show the window; closing only hides it.
        for command in ("web", "show"):
            window.post_command(command)
        window.run_commands()
        if opened != [WEB_URL, WEB_URL]:
            problems.append(f"opened {opened}")
        if root.state() == "withdrawn":
            problems.append("the icon's 'show' left the window hidden")
        window.close()
        if root.state() != "withdrawn" or icon.closed:
            problems.append("closing the window with the icon did not just hide it")
        _backups_self_test(root, problems)
        window.post_command("quit")
        window.run_commands()
        if not icon.closed:
            problems.append("'quit' left the icon")
        if problems:
            print("self-test failed: " + "; ".join(problems))
            return 1
    except Exception:
        traceback.print_exc()
        return 1
    print("self-test ok")
    return 0
