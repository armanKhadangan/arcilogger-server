"""The desktop status window: tkinter + ttk, a thin view of :class:`~.status.Snapshot`.

Every decision (levels, texts, when to flash) is made in :mod:`arcilogger_core.desktop.status`;
this module only lays it out. A background thread takes a new snapshot every
:data:`INTERVAL_MS` (and at once on "Yenile") and hands it to the Tk thread through a queue, so a
slow health request never freezes the window. The window has no control that changes anything:
"Web arayüzünü aç" opens ``https://localhost/`` in the default browser, "Yenile" looks again.

Only standard Tk 8.6 / ttk widgets are used (python-build-standalone for Windows ships Tk 8.6).
``--self-test`` (:func:`self_test`) builds the window off-screen, shows a snapshot of every
level and checks that the taskbar flashes once on the rise to critical.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from tkinter import font, messagebox, ttk
from typing import Final

from arcilogger_core.desktop import winapi
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
    ) -> None:
        self.root = root
        self._refresh = refresh
        self._interval = interval_ms / 1000
        self._flash = flash
        self._open_url = open_url
        self._background = background
        self.level: Level | None = None
        self._queue: queue.Queue[Snapshot] = queue.Queue()
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
        self._stop.set()
        self._wake.set()
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
        if flash_due(self.level, level):
            self._flash(self.root.winfo_id())
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


def run(refresh: Callable[[], Snapshot]) -> int:
    root = new_root()
    _Scale.factor = max(1.0, round(float(root.winfo_fpixels("1i")) / 96.0 * 4) / 4)
    try:
        StatusWindow(root, refresh)
    except Exception:
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
        for _ in order[1:]:
            window.refresh_now()
        window.open_web()
        root.update_idletasks()
        problems = []
        if len(flashed) != 2:
            problems.append(f"flashed {len(flashed)} times, expected 2")
        if opened != [WEB_URL]:
            problems.append(f"opened {opened}")
        if not root.title().startswith(window_title("critical")):
            problems.append(f"title {root.title()!r}")
        if len(window.check_box.winfo_children()) != 5:
            problems.append("the status box does not show one row")
        root.destroy()
        if problems:
            print("self-test failed: " + "; ".join(problems))
            return 1
    except Exception:
        traceback.print_exc()
        return 1
    print("self-test ok")
    return 0
