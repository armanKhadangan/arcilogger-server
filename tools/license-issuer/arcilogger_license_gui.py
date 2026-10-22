"""ARCILOGGER Lisans Yöneticisi: the owner's graphical licence issuer (tkinter + ttk).

Run it on the owner's offline Windows PC from the zip (``ARCILOGGER Lisans Yöneticisi.cmd``) or
from a repository checkout::

    uv run python tools/license-issuer/arcilogger_license_gui.py
    uv run python tools/license-issuer/arcilogger_license_gui.py --self-test

The window is a thin view: every decision is made by :mod:`license_manager_model`, which calls
the issuer's shared functions (``arcilogger_license_issuer``) and ``arcilogger_core.license``.
No network call, no telemetry, no update check. The passphrase is typed into masked entries
only, passed to the call that needs it and cleared at once; it is never stored, logged or put on
the clipboard.

``--self-test`` builds the window off-screen and runs a full cycle through it with a temporary
key, registry and request (generate key → decode → issue → verify), then exits 0 (1 on a
failure). The Windows workflow runs it from the built zip.

Only standard Tk 8.6 / ttk widgets are used (python-build-standalone for Windows ships Tk 8.6;
a development Python may have Tk 9).
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import secrets
import subprocess
import sys
import tempfile
import tkinter as tk
import traceback
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk
from typing import Any, Final

import license_manager_model as model

from arcilogger_core.license.fingerprint import ServerIdentity
from arcilogger_core.license.request import make_request_code, new_request

GREEN: Final = "#1a7f37"
ORANGE: Final = "#b35900"
RED: Final = "#c62828"
GREY: Final = "#6e6e6e"
SOON_BACKGROUND: Final = "#ffe2bf"
ENDED_FOREGROUND: Final = "#8a8a8a"
PAD: Final = 8
TEXT_WIDTH: Final = 92


class _Scale:
    """Pixels per 96-dpi pixel. The process is DPI aware on Windows, so Tk scales its fonts;
    every size given in pixels (window, wrap widths) is scaled with :func:`px`."""

    factor = 1.0


def set_scale(root: tk.Misc) -> None:
    """From the screen's resolution, in quarter steps (100%, 125%, 150%, …), never below 1."""
    factor = float(root.winfo_fpixels("1i")) / 96.0
    _Scale.factor = max(1.0, round(factor * 4) / 4)


def px(size: int) -> int:
    return round(size * _Scale.factor)


def place_over(top: tk.Toplevel, master: tk.Misc) -> None:
    """Centre a dialog over the main window (Windows otherwise puts it in a corner)."""
    top.update_idletasks()
    window = master.winfo_toplevel()
    if not window.winfo_viewable():
        return
    width = max(top.winfo_reqwidth(), top.winfo_width())
    height = max(top.winfo_reqheight(), top.winfo_height())
    x = window.winfo_rootx() + (window.winfo_width() - width) // 2
    y = window.winfo_rooty() + (window.winfo_height() - height) // 2
    top.geometry(f"+{max(x, 0)}+{max(y, 0)}")


def focus_moves_on_tab(widget: tk.Text) -> None:
    """Tab and Shift+Tab leave a text box (instead of typing a tab character)."""

    def forward(event: Any) -> str:
        event.widget.tk_focusNext().focus_set()
        return "break"

    def backward(event: Any) -> str:
        event.widget.tk_focusPrev().focus_set()
        return "break"

    widget.bind("<Tab>", forward)
    widget.bind("<Shift-Tab>", backward)
    if widget.tk.call("tk", "windowingsystem") == "x11":
        # Shift+Tab arrives as this keysym on X11; Tk on Windows and macOS does not know the name.
        widget.bind("<ISO_Left_Tab>", backward)


# -- small widgets --------------------------------------------------------------------------------


class Tooltip:
    """A delayed tooltip (the English text of a Turkish label)."""

    def __init__(self, widget: tk.Misc, text: str) -> None:
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        self._after: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: object = None) -> None:
        self._cancel()
        self._after = self.widget.after(600, self._show)

    def _cancel(self) -> None:
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self) -> None:
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tip, text=self.text, justify="left", background="#ffffe0", relief="solid",
            borderwidth=1, wraplength=px(420), padx=6, pady=3,
        )  # fmt: skip
        label.pack()
        self._tip = tip

    def _hide(self, _event: object = None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def tip(widget: tk.Misc, text: str) -> None:
    Tooltip(widget, text)


class ScrollFrame(ttk.Frame):
    """A vertically scrolling frame (``self.body`` holds the content)."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        background = str(ttk.Style(self).lookup("TFrame", "background"))
        if background:
            self.canvas.configure(background=background)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.body = ttk.Frame(self.canvas, padding=PAD)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_body(self, _event: object) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event: Any) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, _event: object) -> None:
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", self._wheel_up)
        self.canvas.bind_all("<Button-5>", self._wheel_down)

    def _unbind_wheel(self, _event: object) -> None:
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(sequence)

    def _wheel(self, event: Any) -> None:
        delta = int(event.delta)
        steps = -delta // 120 if abs(delta) >= 120 else -delta
        self.canvas.yview_scroll(steps, "units")

    def _wheel_up(self, _event: object) -> None:
        self.canvas.yview_scroll(-3, "units")

    def _wheel_down(self, _event: object) -> None:
        self.canvas.yview_scroll(3, "units")


def readonly_text(master: tk.Misc, height: int, width: int = TEXT_WIDTH) -> tk.Text:
    text = tk.Text(master, height=height, width=width, wrap="word", relief="solid", borderwidth=1)
    text.configure(state="disabled")
    return text


def set_text(widget: tk.Text, value: str) -> None:
    disabled = str(widget.cget("state")) == "disabled"
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", value)
    if disabled:
        widget.configure(state="disabled")


def get_text(widget: tk.Text) -> str:
    return widget.get("1.0", "end-1c")


def section(master: tk.Misc, title_tr: str, title_en: str) -> ttk.LabelFrame:
    frame = ttk.LabelFrame(master, text=f"{title_tr} / {title_en}", padding=PAD)
    frame.columnconfigure(1, weight=1)
    return frame


# -- dialogs --------------------------------------------------------------------------------------


class Dialogs:
    """The window's questions and answers (replaced by a scripted set in tests/--self-test)."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root

    def error(self, title: str, messages: Sequence[model.Message]) -> None:
        body = "\n\n".join(message.text() for message in messages)
        messagebox.showerror(title, body, parent=self.root)

    def info(self, title: str, text: str) -> None:
        messagebox.showinfo(title, text, parent=self.root)

    def ask_yes_no(self, title: str, message: model.Message) -> bool:
        return bool(messagebox.askyesno(title, message.text(), parent=self.root))

    def ask_passphrase(self, prompt_tr: str, prompt_en: str) -> str | None:
        return PassphraseDialog(self.root, prompt_tr, prompt_en).result()

    def ask_open(self, title: str, filetypes: Sequence[tuple[str, str]]) -> Path | None:
        chosen = filedialog.askopenfilename(parent=self.root, title=title, filetypes=filetypes)
        return Path(chosen) if chosen else None

    def ask_directory(self, title: str) -> Path | None:
        chosen = filedialog.askdirectory(parent=self.root, title=title, mustexist=True)
        return Path(chosen) if chosen else None

    def ask_save(
        self, title: str, initial: Path, filetypes: Sequence[tuple[str, str]]
    ) -> Path | None:
        chosen = filedialog.asksaveasfilename(
            parent=self.root, title=title, initialdir=str(initial.parent),
            initialfile=initial.name, filetypes=filetypes, defaultextension=initial.suffix,
            # No "replace?" question: every save of the window refuses an existing file itself
            # (.lic, new registry, export), with a text saying to choose another name.
            confirmoverwrite=False,
        )  # fmt: skip
        return Path(chosen) if chosen else None

    def issue_result(self, outcome: model.IssueOutcome) -> None:
        IssueResultDialog(self.root, outcome)


class PassphraseDialog:
    """A modal, masked passphrase entry; the value is cleared from the widget after reading."""

    def __init__(self, root: tk.Misc, prompt_tr: str, prompt_en: str) -> None:
        self._value: str | None = None
        self.top = tk.Toplevel(root)
        self.top.title("Parola / Passphrase")
        self.top.transient(root.winfo_toplevel())
        self.top.resizable(False, False)
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=prompt_tr, wraplength=px(380)).pack(anchor="w")
        ttk.Label(frame, text=prompt_en, foreground=GREY, wraplength=px(380)).pack(anchor="w")
        self.variable = tk.StringVar(self.top)
        entry = ttk.Entry(frame, textvariable=self.variable, show="•", width=44)
        entry.pack(fill="x", pady=(PAD, PAD))
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e")
        ttk.Button(buttons, text="Tamam / OK", command=self._ok).pack(side="left", padx=4)
        ttk.Button(buttons, text="İptal / Cancel", command=self._cancel).pack(side="left")
        self.top.bind("<Return>", lambda _event: self._ok())
        self.top.bind("<Escape>", lambda _event: self._cancel())
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        place_over(self.top, root)
        entry.focus_set()

    def _ok(self) -> None:
        self._value = self.variable.get()
        self.variable.set("")
        self.top.destroy()

    def _cancel(self) -> None:
        self.variable.set("")
        self.top.destroy()

    def result(self) -> str | None:
        self.top.wait_visibility()
        self.top.grab_set()
        self.top.wait_window()
        value, self._value = self._value, None
        return value


def show_in_folder(path: Path) -> None:
    """Open the file's folder in the file manager (Explorer selects the file)."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{path}"])  # noqa: S603, S607 - fixed program
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])  # noqa: S603, S607 - fixed program


class IssueResultDialog:
    """The licence id, the file's SHA-256, the e-mail text and the revoked.py lines."""

    def __init__(self, root: tk.Misc, outcome: model.IssueOutcome) -> None:
        self.top = tk.Toplevel(root)
        self.top.title("Lisans oluşturuldu / Licence created")
        self.top.transient(root.winfo_toplevel())
        self.top.minsize(px(700), px(520))
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(
            frame, text="Lisans oluşturuldu ve imzası doğrulandı. / Licence created and verified.",
            foreground=GREEN,
        ).grid(row=0, column=0, columnspan=2, sticky="w")  # fmt: skip
        rows = (
            ("Lisans kimliği / Licence id", outcome.license_id),
            ("Dosya / File", str(outcome.result.path)),
            ("Dosya SHA-256 / File SHA-256", outcome.file_sha256),
        )
        self.entries: dict[str, ttk.Entry] = {}
        for index, (label, value) in enumerate(rows, start=1):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", padx=(0, PAD))
            entry = ttk.Entry(frame, width=72)
            entry.insert(0, value)
            entry.configure(state="readonly")
            entry.grid(row=index, column=1, columnspan=2, sticky="ew", pady=2)
            self.entries[label] = entry
        ttk.Button(
            frame, text="Dosyayı klasörde göster / Show in folder",
            command=lambda: show_in_folder(outcome.result.path),
        ).grid(row=4, column=1, sticky="w", pady=(2, 0))  # fmt: skip
        mail_bar = ttk.Frame(frame)
        mail_bar.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(PAD, 2))
        ttk.Label(mail_bar, text="Müşteriye e-posta (Türkçe) / E-mail for the customer").pack(
            side="left"
        )
        ttk.Button(
            mail_bar, text="Kopyala / Copy", command=lambda: copy(self.top, outcome.email)
        ).pack(side="right")
        mail = readonly_text(frame, height=13, width=86)
        set_text(mail, outcome.email)
        mail.grid(row=6, column=0, columnspan=2, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=mail.yview)
        scrollbar.grid(row=6, column=2, sticky="ns")
        mail.configure(yscrollcommand=scrollbar.set)
        self.mail = mail
        frame.rowconfigure(6, weight=1)
        row = 7
        for license_id, line, note in outcome.revocations:
            bar = ttk.Frame(frame)
            bar.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(PAD, 0))
            ttk.Label(bar, text=f"revoked.py ({license_id}):").pack(side="left")
            entry = ttk.Entry(bar, width=34, font="TkFixedFont")
            entry.insert(0, line)
            entry.configure(state="readonly")
            entry.pack(side="left", padx=PAD)
            ttk.Button(
                bar, text="Kopyala / Copy", command=functools.partial(copy, self.top, line)
            ).pack(side="left")
            ttk.Label(frame, text=note.tr, wraplength=px(680), foreground=ORANGE).grid(
                row=row + 1, column=0, columnspan=3, sticky="w"
            )
            ttk.Label(frame, text=note.en, wraplength=px(680), foreground=GREY).grid(
                row=row + 2, column=0, columnspan=3, sticky="w"
            )
            row += 3
        close = ttk.Button(frame, text="Kapat / Close", command=self.top.destroy)
        close.grid(row=row, column=1, columnspan=2, sticky="e", pady=(PAD, 0))
        self.top.bind("<Escape>", lambda _event: self.top.destroy())
        place_over(self.top, root)
        close.focus_set()


def copy(widget: tk.Misc, text: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(text)


# -- tabs -----------------------------------------------------------------------------------------


class SettingsTab(ttk.Frame):
    def __init__(self, app: App) -> None:
        super().__init__(app.notebook, padding=PAD)
        self.app = app
        settings = app.manager.settings
        self.key = tk.StringVar(self, settings.private_key)
        self.registry = tk.StringVar(self, settings.registry)
        self.output = tk.StringVar(self, settings.output_dir)
        self.columnconfigure(1, weight=1)
        ttk.Label(
            self,
            text="Anahtar, kayıt defteri ve çıktı klasörü depo dışında, çevrimdışı bir yerde "
            "olmalı. Bu yollar hatırlanır; parola hiçbir zaman kaydedilmez.",
            wraplength=px(820),
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(
            self,
            text="Key, registry and output folder must be offline, outside every repository. "
            "These paths are remembered; the passphrase is never stored.",
            foreground=GREY, wraplength=px(820),
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, PAD))  # fmt: skip
        self.key_status = self._row(
            2, "Özel anahtar (*.private.pem)", "Private key", self.key, self.choose_key
        )
        self.registry_status = self._row(
            4, "Kayıt defteri (CSV)", "Registry (CSV)", self.registry, self.choose_registry
        )
        ttk.Button(self, text="Yeni oluştur… / New…", command=self.new_registry).grid(
            row=4, column=3, padx=(4, 0)
        )
        self.output_status = self._row(
            6, "Lisans dosyaları klasörü", "Output folder for .lic files", self.output,
            self.choose_output,
        )  # fmt: skip
        test = section(self, "Anahtarı sına", "Test key")
        test.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(PAD * 2, 0))
        self.test_button = ttk.Button(test, text="Anahtarı sına / Test key", command=self.test_key)
        self.test_button.grid(row=0, column=0, sticky="w")
        tip(self.test_button, "Asks the passphrase once and shows the key id and fingerprint.")
        self.key_id = tk.StringVar(self, "-")
        self.fingerprint = tk.StringVar(self, "-")
        ttk.Label(test, text="Anahtar kimliği / Key id").grid(row=1, column=0, sticky="w")
        ttk.Label(test, textvariable=self.key_id).grid(row=1, column=1, sticky="w")
        ttk.Label(test, text="Açık anahtar SHA-256 / Public key SHA-256").grid(
            row=2, column=0, sticky="w", padx=(0, PAD)
        )
        ttk.Label(test, textvariable=self.fingerprint).grid(row=2, column=1, sticky="w")
        self.settings_file = ttk.Label(
            self, text=f"Ayar dosyası / Settings file: {app.manager.settings_path}", foreground=GREY
        )
        self.settings_file.grid(row=9, column=0, columnspan=4, sticky="w", pady=(PAD * 2, 0))
        self.refresh()

    def _row(
        self, row: int, label_tr: str, label_en: str, variable: tk.StringVar,
        command: Callable[[], None],
    ) -> ttk.Label:  # fmt: skip
        label = ttk.Label(self, text=label_tr)
        label.grid(row=row, column=0, sticky="w", padx=(0, PAD), pady=(PAD, 0))
        tip(label, label_en)
        entry = ttk.Entry(self, textvariable=variable, state="readonly")
        entry.grid(row=row, column=1, sticky="ew", pady=(PAD, 0))
        ttk.Button(self, text="Seç… / Choose…", command=command).grid(
            row=row, column=2, padx=(4, 0), pady=(PAD, 0)
        )
        status = ttk.Label(self, text="", wraplength=px(820))
        status.grid(row=row + 1, column=1, columnspan=3, sticky="w")
        return status

    def refresh(self) -> None:
        settings = self.app.manager.settings
        self.key.set(settings.private_key)
        self.registry.set(settings.registry)
        self.output.set(settings.output_dir)
        checks: list[tuple[ttk.Label, str, Callable[[], object]]] = [
            (self.key_status, settings.private_key,
             lambda: model.check_private_key_path(Path(settings.private_key))),
            (self.registry_status, settings.registry,
             lambda: model.check_registry_path(Path(settings.registry))),
            (self.output_status, settings.output_dir,
             lambda: model.check_output_dir(Path(settings.output_dir))),
        ]  # fmt: skip
        for label, value, check in checks:
            if not value:
                label.configure(text="Seçilmedi / not chosen", foreground=GREY)
                continue
            try:
                result = check()
            except (model.ToolError, model.LicenseError, OSError) as exc:
                message = model.message_of(exc)
                label.configure(text=f"{message.tr}\n{message.en}", foreground=RED)
                continue
            extra = ""
            if isinstance(result, model.issuer.Registry):
                extra = f" ({len(result.rows)} kayıt / rows)"
            label.configure(text=f"Uygun / OK{extra}", foreground=GREEN)

    def _apply(self, action: Callable[[], object]) -> None:
        try:
            action()
        except (model.ToolError, model.LicenseError, OSError) as exc:
            self.app.dialogs.error("Ayarlar / Settings", [model.message_of(exc)])
        self.refresh()
        self.app.refresh_registry()

    def choose_key(self) -> None:
        path = self.app.dialogs.ask_open(
            "Özel anahtar / Private key", [("Özel anahtar", "*.private.pem"), ("PEM", "*.pem")]
        )
        if path is not None:
            self.set_key(path)

    def set_key(self, path: Path) -> None:
        self.key_id.set("-")
        self.fingerprint.set("-")
        self._apply(lambda: self.app.manager.set_private_key(str(path)))

    def choose_registry(self) -> None:
        path = self.app.dialogs.ask_open("Kayıt defteri / Registry", [("CSV", "*.csv")])
        if path is not None:
            self.set_registry(path)

    def set_registry(self, path: Path) -> None:
        self._apply(lambda: self.app.manager.set_registry(str(path)))

    def new_registry(self) -> None:
        start = Path(self.output.get() or Path.home()) / "kayit-defteri.csv"
        path = self.app.dialogs.ask_save("Yeni kayıt defteri / New registry", start,
                                         [("CSV", "*.csv")])  # fmt: skip
        if path is not None:
            self.create_registry(path)

    def create_registry(self, path: Path) -> None:
        self._apply(lambda: self.app.manager.create_registry(str(path)))

    def choose_output(self) -> None:
        path = self.app.dialogs.ask_directory("Lisans dosyaları klasörü / Output folder")
        if path is not None:
            self.set_output(path)

    def set_output(self, path: Path) -> None:
        self._apply(lambda: self.app.manager.set_output_dir(str(path)))

    def test_key(self) -> None:
        with self.app.busy(self.test_button):
            passphrase = self.app.dialogs.ask_passphrase(
                "Özel anahtarın parolası (yalnızca sınamak için).",
                "Passphrase of the private key (only to test it).",
            )
            if passphrase is None:
                return
            try:
                info = self.app.manager.test_key(passphrase)
            except (model.ToolError, model.LicenseError, OSError) as exc:
                self.app.dialogs.error("Anahtarı sına / Test key", [model.message_of(exc)])
                return
            finally:
                passphrase = ""
            self.key_id.set(info.key_id)
            self.fingerprint.set(info.public_sha256)


class KeygenTab(ttk.Frame):
    def __init__(self, app: App) -> None:
        super().__init__(app.notebook, padding=PAD)
        self.app = app
        self.columnconfigure(1, weight=1)
        self.key_id = tk.StringVar(self, "")
        self.folder = tk.StringVar(self, "")
        self.passphrase = tk.StringVar(self, "")
        self.again = tk.StringVar(self, "")
        self.result: model.issuer.KeyResult | None = None
        ttk.Label(
            self,
            text="Yeni bir Ed25519 anahtar çifti üretir. Bunu yalnızca bir kez (ya da anahtar "
            "ele geçirildiğinde) yapın.",
            wraplength=px(820),
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            self, text="Makes a new Ed25519 key pair: once, or when a key is compromised.",
            foreground=GREY,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, PAD))  # fmt: skip
        ttk.Label(self, text="Anahtar kimliği / Key id").grid(row=2, column=0, sticky="w")
        entry = ttk.Entry(self, textvariable=self.key_id, width=34)
        entry.grid(row=2, column=1, sticky="w", pady=2)
        tip(entry, "e.g. arci-2026a: ^[a-z0-9][a-z0-9-]{2,31}$, never test-…")
        self.key_id_status = ttk.Label(self, text="")
        self.key_id_status.grid(row=3, column=1, columnspan=2, sticky="w")
        ttk.Label(self, text="Hedef klasör / Target folder").grid(
            row=4, column=0, sticky="w", padx=(0, PAD)
        )
        ttk.Entry(self, textvariable=self.folder).grid(row=4, column=1, sticky="ew", pady=2)
        ttk.Button(self, text="Seç… / Choose…", command=self.choose_folder).grid(
            row=4, column=2, padx=(4, 0)
        )
        ttk.Label(self, text="Parola / Passphrase").grid(row=5, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.passphrase, show="•", width=40).grid(
            row=5, column=1, sticky="w", pady=2
        )
        ttk.Label(self, text="Parola, tekrar / Again").grid(row=6, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.again, show="•", width=40).grid(
            row=6, column=1, sticky="w", pady=2
        )
        strength = ttk.Frame(self)
        strength.grid(row=7, column=1, sticky="w")
        self.meter = ttk.Progressbar(strength, length=px(200), maximum=4, mode="determinate")
        self.meter.pack(side="left")
        self.strength = ttk.Label(strength, text="")
        self.strength.pack(side="left", padx=PAD)
        self.generate_button = ttk.Button(
            self, text="Anahtar çiftini üret / Generate key pair", command=self.generate
        )
        self.generate_button.grid(row=8, column=1, sticky="w", pady=PAD)
        result = section(self, "Sonuç", "Result")
        result.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(PAD, 0))
        ttk.Label(result, text="keys.py satırı / line for keys.py").grid(
            row=0, column=0, sticky="w"
        )
        self.line = ttk.Entry(result, width=80, state="readonly")
        self.line.grid(row=0, column=1, sticky="ew", padx=(PAD, 0))
        self.copy_button = ttk.Button(
            result, text="Kopyala / Copy", state="disabled",
            command=lambda: copy(self, self.result.line if self.result else ""),
        )  # fmt: skip
        self.copy_button.grid(row=0, column=2, padx=(4, 0))
        ttk.Label(result, text="SHA-256").grid(row=1, column=0, sticky="w")
        self.sha = ttk.Label(result, text="-")
        self.sha.grid(row=1, column=1, sticky="w", padx=(PAD, 0))
        self.files = ttk.Label(result, text="", foreground=GREY)
        self.files.grid(row=2, column=0, columnspan=3, sticky="w")
        self.use_button = ttk.Button(
            result, text="Bu anahtarı Ayarlar'da kullan / Use in Settings", state="disabled",
            command=self.use_key,
        )  # fmt: skip
        self.use_button.grid(row=3, column=1, sticky="w", padx=(PAD, 0), pady=(4, 0))
        warning = (
            "Özel anahtarı iki ayrı çevrimdışı ortama (örneğin iki USB bellek, ayrı yerlerde) "
            "yedekleyin; parolayı ayrı saklayın. Özel anahtarı asla e-postayla göndermeyin. "
            "Geliştiriciye yalnızca yukarıdaki keys.py satırını gönderin."
        )
        ttk.Label(result, text=warning, foreground=RED, wraplength=px(820)).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(PAD, 0)
        )
        ttk.Label(
            result,
            text="Back up the private key on two separate offline media and keep the passphrase "
            "apart. Never e-mail the private key. Send only the keys.py line to the developer.",
            foreground=GREY, wraplength=px(820),
        ).grid(row=5, column=0, columnspan=3, sticky="w")  # fmt: skip
        self.key_id.trace_add("write", lambda *_: self.check_key_id())
        self.passphrase.trace_add("write", lambda *_: self.check_strength())
        self.check_key_id()
        self.check_strength()

    def check_key_id(self) -> bool:
        problem = model.key_id_problem(self.key_id.get())
        if problem is None:
            self.key_id_status.configure(text="Uygun / OK", foreground=GREEN)
            return True
        self.key_id_status.configure(text=f"{problem.tr} [{problem.en}]", foreground=ORANGE)
        return False

    def check_strength(self) -> None:
        score, message = model.passphrase_strength(self.passphrase.get())
        self.meter.configure(value=score)
        colour = (RED, ORANGE, ORANGE, GREEN, GREEN)[score]
        self.strength.configure(text=f"{message.tr} / {message.en}", foreground=colour)

    def choose_folder(self) -> None:
        path = self.app.dialogs.ask_directory("Anahtar klasörü / Key folder")
        if path is not None:
            self.folder.set(str(path))

    def generate(self) -> None:
        with self.app.busy(self.generate_button):
            try:
                if not self.folder.get().strip():
                    raise model.ToolError("Hedef klasörü seçin.", "choose the target folder")
                result = model.generate_key(
                    self.key_id.get(), Path(self.folder.get()), self.passphrase.get(),
                    self.again.get(),
                )  # fmt: skip
            except (model.ToolError, model.LicenseError, ValueError, OSError) as exc:
                self.app.dialogs.error("Anahtar üret / Generate key", [model.message_of(exc)])
                return
            finally:
                self.passphrase.set("")
                self.again.set("")
            self.result = result
            self.line.configure(state="normal")
            self.line.delete(0, "end")
            self.line.insert(0, result.line)
            self.line.configure(state="readonly")
            self.sha.configure(text=result.public_sha256)
            self.files.configure(text=f"{result.private_path}\n{result.public_path}")
            self.copy_button.configure(state="normal")
            self.use_button.configure(state="normal")

    def use_key(self) -> None:
        if self.result is not None:
            self.app.settings_tab.set_key(self.result.private_path)
            self.app.notebook.select(self.app.settings_tab)  # type: ignore[no-untyped-call]


class IssueTab(ttk.Frame):
    """The main screen: request code, earlier licences, licensee, modules, limits, dates."""

    def __init__(self, app: App) -> None:
        super().__init__(app.notebook)
        self.app = app
        self.form = model.IssueForm(maintenance_until=self._default_maintenance())
        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        body = scroll.body
        body.columnconfigure(0, weight=1)
        self._request_section(body).grid(row=0, column=0, sticky="ew")
        self.earlier_frame = section(body, "Önceki lisanslar", "Earlier licences")
        self.earlier_frame.grid(row=1, column=0, sticky="ew", pady=(PAD, 0))
        self.earlier_frame.grid_remove()
        self._licensee_section(body).grid(row=2, column=0, sticky="ew", pady=(PAD, 0))
        self._modules_section(body).grid(row=3, column=0, sticky="ew", pady=(PAD, 0))
        self._limits_section(body).grid(row=4, column=0, sticky="ew", pady=(PAD, 0))
        self._dates_section(body).grid(row=5, column=0, sticky="ew", pady=(PAD, 0))
        self._other_section(body).grid(row=6, column=0, sticky="ew", pady=(PAD, 0))
        self.create_button = ttk.Button(
            body, text="Lisansı oluştur / Create licence", command=self.create
        )
        self.create_button.grid(row=7, column=0, sticky="e", pady=PAD * 2)
        self.earlier_mode = tk.StringVar(self, "")
        self.supersede_vars: dict[str, tk.BooleanVar] = {}
        self.show_request(None)

    def _default_maintenance(self) -> str:
        return model.maintenance_for(self.app.manager.today).isoformat()

    # sections -------------------------------------------------------------------------------

    def _request_section(self, body: ttk.Frame) -> ttk.LabelFrame:
        frame = section(body, "İstek kodu", "Request code")
        self.request = tk.Text(frame, height=6, width=TEXT_WIDTH, wrap="char", relief="solid",
                               borderwidth=1, font="TkFixedFont")  # fmt: skip
        self.request.grid(row=0, column=0, columnspan=3, sticky="ew")
        focus_moves_on_tab(self.request)
        self.request.bind("<Control-Return>", lambda _event: (self.decode(), "break")[1])
        tip(self.request, "Paste the request code; Ctrl+Enter decodes it.")
        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Button(buttons, text="Dosyadan yükle (.txt) / Load file", command=self.load_file).pack(
            side="left"
        )
        self.decode_button = ttk.Button(buttons, text="Çöz / Decode", command=self.decode)
        self.decode_button.pack(side="left", padx=4)
        self.request_info = ttk.Frame(frame)
        self.request_info.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(PAD, 0))
        self.binding = ttk.Label(frame, text="", wraplength=px(820))
        self.binding.grid(row=3, column=0, columnspan=3, sticky="w")
        self.checksum = ttk.Label(frame, text="")
        self.checksum.grid(row=4, column=0, columnspan=3, sticky="w")
        return frame

    def _licensee_section(self, body: ttk.Frame) -> ttk.LabelFrame:
        frame = section(body, "Lisans sahibi", "Licensee")
        self.licensee = tk.StringVar(self, "")
        entry = ttk.Entry(frame, textvariable=self.licensee, width=70)
        entry.grid(row=0, column=0, sticky="w")
        entry.bind("<FocusOut>", lambda _event: self.refresh_earlier())
        tip(entry, "Prefilled from the request's organisation; one line, 1-200 characters.")
        ttk.Label(frame, text="İstek kodundaki kuruluş adıyla doldurulur; tek satır, 1-200 "
                  "karakter.", foreground=GREY).grid(row=1, column=0, sticky="w")  # fmt: skip
        return frame

    def _modules_section(self, body: ttk.Frame) -> ttk.LabelFrame:
        frame = section(body, "Modüller", "Modules")
        small = font.nametofont("TkSmallCaptionFont")
        self.module_vars: dict[str, tk.BooleanVar] = {}
        self.module_buttons: dict[str, ttk.Checkbutton] = {}
        for index, item in enumerate(model.ModuleChoice.modules()):
            variable = tk.BooleanVar(self, self.form.modules.is_on(item.key))
            button = ttk.Checkbutton(
                frame, text=item.name_tr, variable=variable,
                command=functools.partial(self.toggle_module, item.key),
            )  # fmt: skip
            button.grid(row=index * 2, column=0, sticky="w", pady=(4, 0))
            if model.ModuleChoice.locked(item.key):
                button.state(["disabled"])
            tip(button, f"{item.name_en}: {item.description_en}")
            ttk.Label(
                frame, text=item.description_tr, foreground=GREY, font=small, wraplength=px(800)
            ).grid(row=index * 2 + 1, column=0, sticky="w", padx=(24, 0))
            self.module_vars[item.key] = variable
            self.module_buttons[item.key] = button
        return frame

    def _limits_section(self, body: ttk.Frame) -> ttk.LabelFrame:
        frame = section(body, "Sınırlar", "Limits")
        self.sources = tk.StringVar(self, model.DEFAULT_LIMIT)
        self.users = tk.StringVar(self, model.DEFAULT_LIMIT)
        self.sources_unlimited = tk.BooleanVar(self, False)
        self.users_unlimited = tk.BooleanVar(self, False)
        self.sources_box = self._limit_row(frame, 0, "Log kaynağı", "Log sources", self.sources,
                                           self.sources_unlimited)  # fmt: skip
        self.users_box = self._limit_row(frame, 1, "Kullanıcı", "Users", self.users,
                                         self.users_unlimited)  # fmt: skip
        frame.columnconfigure(1, weight=0)
        frame.columnconfigure(3, weight=1)
        return frame

    def _limit_row(
        self, frame: tk.Misc, row: int, label_tr: str, label_en: str, value: tk.StringVar,
        unlimited: tk.BooleanVar,
    ) -> ttk.Spinbox:  # fmt: skip
        label = ttk.Label(frame, text=label_tr, width=14)
        label.grid(row=row, column=0, sticky="w", pady=2)
        tip(label, label_en)
        box = ttk.Spinbox(frame, from_=1, to=model.MAX_LIMIT, textvariable=value, width=10)
        box.grid(row=row, column=1, sticky="w")

        def toggled() -> None:
            box.state(["disabled"] if unlimited.get() else ["!disabled"])

        ttk.Checkbutton(frame, text="Sınırsız / Unlimited", variable=unlimited,
                        command=toggled).grid(row=row, column=2, sticky="w", padx=PAD)  # fmt: skip
        return box

    def _dates_section(self, body: ttk.Frame) -> ttk.LabelFrame:
        frame = section(body, "Tarihler", "Dates")
        frame.columnconfigure(1, weight=0)
        self.maintenance = tk.StringVar(self, self.form.maintenance_until)
        label = ttk.Label(frame, text="Bakım bitişi (YYYY-AA-GG)")
        label.grid(row=0, column=0, sticky="w", padx=(0, PAD))
        tip(label, "Maintenance until (inclusive): versions released later run with 5651 Temel "
            "only; the licence itself never expires.")  # fmt: skip
        ttk.Entry(frame, textvariable=self.maintenance, width=14).grid(row=0, column=1, sticky="w")
        frame.columnconfigure(4, weight=1)
        ttk.Button(frame, text="+1 yıl", width=8, command=lambda: self.quick_maintenance(1)).grid(
            row=0, column=2, padx=(PAD, 2)
        )
        ttk.Button(frame, text="+2 yıl", width=8, command=lambda: self.quick_maintenance(2)).grid(
            row=0, column=3
        )
        self.valid_enabled = tk.BooleanVar(self, False)
        self.valid_until = tk.StringVar(self, "")
        check = ttk.Checkbutton(frame, text="Geçerlilik sonu (demo) / Valid until",
                                variable=self.valid_enabled, command=self.toggle_valid)  # fmt: skip
        check.grid(row=1, column=0, sticky="w", pady=(4, 0))
        tip(check, "Only for a demo or time-limited trial licence; not for a normal sale.")
        self.valid_entry = ttk.Entry(frame, textvariable=self.valid_until, width=14)
        self.valid_entry.grid(row=1, column=1, sticky="w", pady=(4, 0))
        self.valid_entry.state(["disabled"])
        return frame

    def _other_section(self, body: ttk.Frame) -> ttk.LabelFrame:
        frame = section(body, "Diğer", "Other")
        label = ttk.Label(frame, text="Ek sunucu kimlikleri\n(satır başına bir)")
        label.grid(row=0, column=0, sticky="nw", padx=(0, PAD))
        tip(label, "Extra server ids, one per line (XXXX-XXXX-XXXX-XXXX).")
        self.extra = tk.Text(frame, height=3, width=30, relief="solid", borderwidth=1,
                             font="TkFixedFont")  # fmt: skip
        self.extra.grid(row=0, column=1, sticky="w")
        focus_moves_on_tab(self.extra)
        self.extra_hint = ttk.Label(frame, text="", foreground=GREY, wraplength=px(560))
        self.extra_hint.grid(row=0, column=2, sticky="nw", padx=(PAD, 0))
        self.note = tk.StringVar(self, "")
        ttk.Label(frame, text="Not (kayıt defteri)").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(frame, textvariable=self.note, width=70).grid(
            row=1, column=1, sticky="w", pady=(4, 0)
        )
        self.weak = tk.BooleanVar(self, False)
        self.weak_box = ttk.Checkbutton(
            frame, text="Zayıf bağlamaya izin ver / Allow weak binding", variable=self.weak
        )
        self.weak_box.grid(row=2, column=0, columnspan=2, sticky="w", pady=(PAD, 0))
        self.weak_warning = ttk.Label(
            frame, foreground=ORANGE, wraplength=px(800),
            text="Bu istek zayıf bağlı: lisans tek bir düzenlemeyle başka bir makinede de "
            "çalışabilir. Yalnızca bilerek ve müşteriyle anlaşarak verin. [Weak binding: the "
            "licence can work on another machine after one edit; issue it on purpose only.]",
        )  # fmt: skip
        self.weak_warning.grid(row=3, column=0, columnspan=2, sticky="w")
        return frame

    # behaviour ------------------------------------------------------------------------------

    def toggle_module(self, key: str) -> None:
        self.form.modules.set(key, on=self.module_vars[key].get())
        for item, variable in self.module_vars.items():
            variable.set(self.form.modules.is_on(item))

    def quick_maintenance(self, years: int) -> None:
        self.maintenance.set(model.maintenance_for(self.app.manager.today, years).isoformat())

    def toggle_valid(self) -> None:
        self.valid_entry.state(["!disabled"] if self.valid_enabled.get() else ["disabled"])

    def load_file(self) -> None:
        path = self.app.dialogs.ask_open("İstek kodu / Request code", [("Metin", "*.txt")])
        if path is None:
            return
        try:
            text = model.load_request_file(path)
        except (model.ToolError, OSError) as exc:
            self.app.dialogs.error("İstek kodu / Request code", [model.message_of(exc)])
            return
        self.set_request(text)
        self.decode()

    def set_request(self, text: str) -> None:
        self.request.delete("1.0", "end")
        self.request.insert("1.0", text.strip())

    def read_form(self) -> model.IssueForm:
        form = self.form
        form.request_text = get_text(self.request)
        form.licensee = self.licensee.get()
        form.sources = self.sources.get()
        form.sources_unlimited = self.sources_unlimited.get()
        form.users = self.users.get()
        form.users_unlimited = self.users_unlimited.get()
        form.maintenance_until = self.maintenance.get()
        form.valid_until_enabled = self.valid_enabled.get()
        form.valid_until = self.valid_until.get()
        form.extra_server_ids = get_text(self.extra)
        form.note = self.note.get()
        form.allow_weak_binding = self.weak.get()
        mode = self.earlier_mode.get()
        form.earlier_mode = "supersedes" if mode == "supersedes" else (
            "additional" if mode == "additional" else ""
        )  # fmt: skip
        form.supersedes = [key for key, variable in self.supersede_vars.items() if variable.get()]
        return form

    def write_form(self, form: model.IssueForm) -> None:
        """Show a form (a renewal from "Kayıtlar")."""
        self.form = form
        self.set_request(form.request_text)
        self.licensee.set(form.licensee)
        self.sources.set(form.sources)
        self.sources_unlimited.set(form.sources_unlimited)
        self.users.set(form.users)
        self.users_unlimited.set(form.users_unlimited)
        for box, flag in ((self.sources_box, form.sources_unlimited),
                          (self.users_box, form.users_unlimited)):  # fmt: skip
            box.state(["disabled"] if flag else ["!disabled"])
        self.maintenance.set(form.maintenance_until or self._default_maintenance())
        self.valid_enabled.set(form.valid_until_enabled)
        self.valid_until.set(form.valid_until)
        self.toggle_valid()
        self.extra.delete("1.0", "end")
        self.extra.insert("1.0", form.extra_server_ids)
        self.extra_hint.configure(
            text="Yenilenen lisanstan alındı; sunucu artık kullanılmıyorsa silin. [Taken from the "
            "renewed licence; remove a server that is no longer used.]"
            if form.renewal_of and form.extra_server_ids
            else ""
        )
        self.note.set(form.note)
        self.weak.set(form.allow_weak_binding)
        for key, variable in self.module_vars.items():
            variable.set(form.modules.is_on(key))
        self.earlier_mode.set(form.earlier_mode)
        self.show_request(None)
        self.show_earlier(self._marked_rows(form))

    def _marked_rows(self, form: model.IssueForm) -> list[Mapping[str, str]]:
        try:
            rows = self.app.manager.registry_rows()
        except (model.ToolError, OSError):
            return []
        return [row for row in rows if row["license_id"] in form.supersedes]

    def decode(self) -> model.RequestView | None:
        form = self.read_form()
        try:
            view = self.app.manager.decode(form)
        except (model.ToolError, model.LicenseError) as exc:
            message = model.message_of(exc)
            self.show_request(None, message)
            return None
        self.licensee.set(form.licensee)
        self.show_request(view)
        self.refresh_earlier()
        return view

    def show_request(
        self, view: model.RequestView | None, error: model.Message | None = None
    ) -> None:
        for child in self.request_info.winfo_children():
            child.destroy()
        if view is None:
            self.binding.configure(text="")
            if error is not None:
                self.checksum.configure(text=error.text().replace("\n\n", " / "), foreground=RED)
            else:
                hint = "İstek kodunu yapıştırıp Çöz'e basın. / Paste the code and press Decode."
                self.checksum.configure(text=hint, foreground=GREY)
            self.weak.set(False)
            self.weak_box.grid_remove()
            self.weak_warning.grid_remove()
            return
        for index, (label, value) in enumerate(view.fields()):
            ttk.Label(self.request_info, text=label).grid(row=index, column=0, sticky="w",
                                                          padx=(0, PAD))  # fmt: skip
            ttk.Label(self.request_info, text=value, font="TkFixedFont").grid(
                row=index, column=1, sticky="w"
            )
        message = view.binding_message()
        self.binding.configure(text=f"{message.tr}\n{message.en}",
                               foreground=GREEN if view.strong else ORANGE)  # fmt: skip
        self.checksum.configure(text="Denetim toplamı doğru / checksum OK", foreground=GREEN)
        if view.strong:
            self.weak.set(False)
            self.weak_box.grid_remove()
            self.weak_warning.grid_remove()
        else:
            self.weak_box.grid()
            self.weak_warning.grid()

    def refresh_earlier(self) -> None:
        form = self.read_form()
        try:
            rows = self.app.manager.earlier(form)
        except (model.ToolError, model.LicenseError, OSError) as exc:
            self.app.dialogs.error("Kayıt defteri / Registry", [model.message_of(exc)])
            return
        self.show_earlier(rows)

    def show_earlier(self, rows: Sequence[Mapping[str, str]]) -> None:
        """The earlier licences of the request. A tick is kept only for a row still shown: a
        licence ticked for another customer's request is dropped, never carried over."""
        frame = self.earlier_frame
        ticked = set(self.form.supersedes) | {
            key for key, variable in self.supersede_vars.items() if variable.get()
        }
        shown = {row["license_id"] for row in rows}
        kept = [key for key in dict.fromkeys(ticked) if key in shown]
        self.form.supersedes = kept
        if ticked and not kept and self.earlier_mode.get() == "supersedes":
            self.earlier_mode.set("")
        for child in frame.winfo_children():
            child.destroy()
        self.supersede_vars = {}
        if not rows:
            frame.grid_remove()
            self.earlier_mode.set("")
            return
        frame.grid()
        ttk.Label(
            frame, wraplength=px(820),
            text="Kayıt defterinde aynı lisans sahibi, kuruluş, kurulum ya da sunucu için lisans "
            "var. Birini seçin: [The registry has a licence for the same licensee, organisation, "
            "installation or server. Choose one:]",
        ).grid(row=0, column=0, columnspan=6, sticky="w")  # fmt: skip
        table = ttk.Frame(frame)
        table.grid(row=1, column=0, columnspan=6, sticky="w", pady=(4, 0))
        headings = ("Yerine geçer", "Lisans kimliği", "Tarih (UTC)", "Lisans sahibi",
                    "Sunucular", "Bakım bitişi")  # fmt: skip
        for column, heading in enumerate(headings):
            ttk.Label(table, text=heading, font="TkHeadingFont").grid(
                row=0, column=column, sticky="w", padx=(0, PAD * 2)
            )
        for index, row in enumerate(rows, start=1):
            variable = tk.BooleanVar(self, row["license_id"] in ticked)
            self.supersede_vars[row["license_id"]] = variable
            ttk.Checkbutton(table, variable=variable, command=self._ticked).grid(
                row=index, column=0
            )
            values = (row["license_id"], row["issued_at"][:10], row["licensee"],
                      row["server_ids"], row["maintenance_until"])  # fmt: skip
            for column, value in enumerate(values, start=1):
                ttk.Label(table, text=value).grid(row=index, column=column, sticky="w",
                                                  padx=(0, PAD * 2))  # fmt: skip
        choices = ttk.Frame(frame)
        choices.grid(row=2, column=0, columnspan=6, sticky="w", pady=(PAD, 0))
        supersedes = ttk.Radiobutton(
            choices, text="Yenileme / değiştirme (supersedes): işaretli lisansların yerine geçer",
            variable=self.earlier_mode, value="supersedes",
        )  # fmt: skip
        supersedes.pack(anchor="w")
        tip(supersedes, "Renewal or replacement (server change, correction).")
        additional = ttk.Radiobutton(
            choices, text="Ek sunucu (additional server): bilerek satılmış ikinci bir sunucu",
            variable=self.earlier_mode, value="additional", command=self._additional,
        )  # fmt: skip
        additional.pack(anchor="w")
        tip(additional, "A second server sold on purpose; nothing is replaced.")

    def _ticked(self) -> None:
        if any(variable.get() for variable in self.supersede_vars.values()):
            self.earlier_mode.set("supersedes")

    def _additional(self) -> None:
        for variable in self.supersede_vars.values():
            variable.set(False)
        self.form.supersedes = []

    def create(self) -> model.IssueOutcome | None:
        """Validate everything, ask the passphrase, sign, ask where to save, write."""
        with self.app.busy(self.create_button):
            form = self.read_form()
            try:
                plan = self.app.manager.validate(form)
            except model.FormError as exc:
                self.app.dialogs.error("Lisans ver / Issue licence", exc.messages)
                return None
            if plan.inputs.maintenance_until < self.app.manager.today and not (
                self.app.dialogs.ask_yes_no(
                    "Bakım bitişi / Maintenance until",
                    model.Message(
                        "Bakım bitişi bugünden önce: bu lisansla yeni sürümler yalnızca 5651 Temel "
                        "ile çalışır. Devam edilsin mi?",
                        "Maintenance ends before today: newer versions run with 5651 Temel only "
                        "under this licence. Continue?",
                    ),
                )
            ):
                return None
            passphrase = self.app.dialogs.ask_passphrase(
                f"Özel anahtarın parolası ({plan.inputs.key_id}).",
                f"Passphrase of the private key ({plan.inputs.key_id}).",
            )
            if passphrase is None:
                return None
            try:
                signed = self.app.manager.sign(plan, passphrase)
            except (model.ToolError, model.LicenseError, OSError) as exc:
                self.app.dialogs.error("Lisans ver / Issue licence", [model.message_of(exc)])
                return None
            finally:
                passphrase = ""
            path = self.app.dialogs.ask_save(
                "Lisans dosyasını kaydet / Save licence file",
                self.app.manager.suggested_path(signed), [("Lisans", "*.lic")],
            )  # fmt: skip
            if path is None:
                self.app.dialogs.info("Lisans ver / Issue licence",
                                      "Lisans yazılmadı (iptal edildi).\nNo licence was written "
                                      "(cancelled).")  # fmt: skip
                return None
            try:
                outcome = self.app.manager.write(signed, path)
            except (model.ToolError, model.LicenseError, OSError) as exc:
                self.app.dialogs.error("Lisans ver / Issue licence", [model.message_of(exc)])
                return None
            self.app.refresh_registry()
            self.after_issue()
            self.app.dialogs.issue_result(outcome)
            return outcome

    def after_issue(self) -> None:
        """The next licence starts from an empty form: a new request code, and no licensee,
        extra server, note, module, limit or date left over from this customer."""
        self.app.manager.forget_request()
        self.supersede_vars = {}
        self.write_form(model.IssueForm(maintenance_until=self._default_maintenance()))


class RegistryTab(ttk.Frame):
    def __init__(self, app: App) -> None:
        super().__init__(app.notebook, padding=PAD)
        self.app = app
        self.views: list[model.RegistryRowView] = []
        self.sort_column = "issued_at"
        self.descending = True
        self.query = tk.StringVar(self, "")
        self.due_only = tk.BooleanVar(self, False)
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="Ara / Search").pack(side="left")
        search = ttk.Entry(bar, textvariable=self.query, width=28)
        search.pack(side="left", padx=(4, PAD))
        tip(search, "Searches every column of every row.")
        ttk.Checkbutton(bar, text="Bakımı yaklaşanlar / Due for renewal", variable=self.due_only,
                        command=self.show).pack(side="left")  # fmt: skip
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(4, 0))
        self.renew_button = ttk.Button(actions, text="Yenile… / Renew…", command=self.renew)
        self.renew_button.pack(side="left")
        tip(
            self.renew_button,
            "Opens Lisans ver with this row's licensee, modules and limits; maintenance + 1 year; "
            "the row is superseded. A new request code is still needed.",
        )
        ttk.Button(actions, text="Dışa aktar (CSV) / Export", command=self.export).pack(
            side="left", padx=4
        )
        ttk.Button(actions, text="Yeniden oku / Reload", command=self.reload).pack(side="left")
        self.query.trace_add("write", lambda *_: self.show())
        table = ttk.Frame(self)
        table.pack(fill="both", expand=True, pady=(PAD, 0))
        columns = [name for name, _, _ in model.REGISTRY_COLUMNS]
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        for name, heading_tr, _heading_en in model.REGISTRY_COLUMNS:
            self.tree.heading(name, text=heading_tr, command=functools.partial(self.sort, name))
            self.tree.column(name, width=100, minwidth=50, stretch=False)
        self.tree.tag_configure("soon", background=SOON_BACKGROUND)
        self.tree.tag_configure("ended", foreground=ENDED_FOREGROUND)
        italic = font.nametofont("TkDefaultFont").copy()
        italic.configure(slant="italic")
        self._italic = italic  # kept: Tk forgets a font whose Python object is gone
        self.tree.tag_configure("replaced", foreground=ENDED_FOREGROUND, font=italic)
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda _event: self.renew())
        self.tree.bind("<Return>", lambda _event: self.renew())
        legend = ttk.Frame(self)
        legend.pack(fill="x", pady=(4, 0))
        tk.Label(legend, text=" Bakım 30 gün içinde bitiyor / ending within 30 days ",
                 background=SOON_BACKGROUND).pack(side="left")  # fmt: skip
        tk.Label(legend, text=" Bakım bitti / ended ", foreground=ENDED_FOREGROUND).pack(
            side="left", padx=PAD
        )
        tk.Label(legend, text=" Yerine başkası geçti / replaced ", foreground=ENDED_FOREGROUND,
                 font=italic).pack(side="left")  # fmt: skip
        self.status = ttk.Label(legend, text="", foreground=GREY)
        self.status.pack(side="right")
        self.reload()

    def reload(self) -> None:
        try:
            self.views = self.app.manager.registry_views()
        except (model.ToolError, OSError) as exc:
            self.views = []
            message = model.message_of(exc)
            self.status.configure(text=f"{message.tr} [{message.en}]", foreground=RED)
        else:
            self.status.configure(text=f"{len(self.views)} kayıt / rows", foreground=GREY)
        self.show()

    def visible(self) -> list[model.RegistryRowView]:
        views = model.filter_views(self.views, self.query.get(), due_only=self.due_only.get())
        return model.sort_views(views, self.sort_column, descending=self.descending)

    def show(self) -> None:
        self.tree.delete(*self.tree.get_children())
        visible = self.visible()
        for view in visible:
            tags = (view.highlight,) if view.highlight else ()
            self.tree.insert("", "end", iid=view.license_id, values=view.values(), tags=tags)
        self._fit_columns(visible)

    def _fit_columns(self, views: Sequence[model.RegistryRowView]) -> None:
        """Each column as wide as its heading or its widest cell (at most 360 pixels)."""
        body = font.nametofont("TkDefaultFont")
        heading = font.nametofont("TkHeadingFont")
        for index, (name, heading_tr, _) in enumerate(model.REGISTRY_COLUMNS):
            widest = max((body.measure(view.values()[index]) for view in views), default=0)
            width = max(widest, heading.measure(heading_tr) + 18) + 16
            self.tree.column(name, width=min(width, 360))

    def sort(self, column: str) -> None:
        if self.sort_column == column:
            self.descending = not self.descending
        else:
            self.sort_column, self.descending = column, False
        self.show()

    def selected(self) -> model.RegistryRowView | None:
        chosen = self.tree.selection()
        return next((view for view in self.views if view.license_id in chosen), None)

    def renew(self) -> None:
        view = self.selected()
        if view is None:
            self.app.dialogs.info("Yenile / Renew", "Önce bir satır seçin.\nSelect a row first.")
            return
        self.app.issue_tab.write_form(self.app.manager.renewal_form(view.row))
        self.app.notebook.select(self.app.issue_tab)  # type: ignore[no-untyped-call]

    def export(self) -> None:
        start = Path(self.app.manager.settings.output_dir or Path.home())
        name = f"kayit-defteri-{self.app.manager.today.isoformat()}.csv"
        target = self.app.dialogs.ask_save("Dışa aktar / Export", start / name, [("CSV", "*.csv")])
        if target is None:
            return
        try:
            self.app.manager.export_registry(target)
        except (model.ToolError, OSError) as exc:
            self.app.dialogs.error("Dışa aktar / Export", [model.message_of(exc)])
            return
        self.app.dialogs.info("Dışa aktar / Export", f"Kopyalandı / copied: {target}")


class VerifyTab(ttk.Frame):
    def __init__(self, app: App) -> None:
        super().__init__(app.notebook, padding=PAD)
        self.app = app
        self.columnconfigure(1, weight=1)
        self.file = tk.StringVar(self, "")
        self.mode = tk.StringVar(self, "private")
        self.public_key = tk.StringVar(self, "")
        self.server_id = tk.StringVar(self, "")
        self.at = tk.StringVar(self, "")
        self.last: model.VerifyOutcome | None = None
        ttk.Label(self, text="Lisans dosyası / Licence file").grid(row=0, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.file).grid(row=0, column=1, sticky="ew", padx=(PAD, 0))
        ttk.Button(self, text="Aç… / Open…", command=self.choose).grid(row=0, column=2, padx=4)
        ttk.Button(self, text="İçeriği göster / Inspect", command=self.inspect).grid(
            row=0, column=3
        )
        keys = section(self, "Doğrulama anahtarı", "Verification key")
        keys.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(PAD, 0))
        ttk.Radiobutton(keys, text="Seçili özel anahtar (parola sorulur) / Selected private key",
                        variable=self.mode, value="private").grid(row=0, column=0, columnspan=2,
                                                                  sticky="w")  # fmt: skip
        ttk.Radiobutton(keys, text="Açık anahtar / Public key", variable=self.mode,
                        value="public").grid(row=1, column=0, sticky="w")  # fmt: skip
        ttk.Entry(keys, textvariable=self.public_key, width=50, font="TkFixedFont").grid(
            row=1, column=1, sticky="w", padx=(PAD, 0)
        )
        ttk.Radiobutton(keys, text="Depodaki anahtarlar (keys.py) / Committed key set",
                        variable=self.mode, value="committed").grid(row=2, column=0, columnspan=2,
                                                                    sticky="w")  # fmt: skip
        ttk.Label(keys, text="Sunucu kimliği (isteğe bağlı) / Server id").grid(
            row=3, column=0, sticky="w", pady=(PAD, 0)
        )
        ttk.Entry(keys, textvariable=self.server_id, width=24, font="TkFixedFont").grid(
            row=3, column=1, sticky="w", padx=(PAD, 0), pady=(PAD, 0)
        )
        ttk.Label(keys, text="Tarih YYYY-AA-GG (isteğe bağlı) / Date").grid(
            row=4, column=0, sticky="w"
        )
        ttk.Entry(keys, textvariable=self.at, width=14).grid(
            row=4, column=1, sticky="w", padx=(PAD, 0)
        )
        self.verify_button = ttk.Button(keys, text="Doğrula / Verify", command=self.verify)
        self.verify_button.grid(row=5, column=0, sticky="w", pady=(PAD, 0))
        for child in (*self.winfo_children(), *keys.winfo_children()):
            if isinstance(child, ttk.Entry):
                child.bind("<Return>", lambda _event: self.verify())
        self.result = tk.Label(self, text="", anchor="w", justify="left", padx=PAD, pady=4,
                               wraplength=px(900))  # fmt: skip
        self.result.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(PAD, 0))
        self.output = readonly_text(self, height=14)
        self.output.configure(font="TkFixedFont", wrap="none")
        self.output.grid(row=3, column=0, columnspan=4, sticky="nsew", pady=(4, 0))
        self.rowconfigure(3, weight=1)
        request = section(self, "İstek kodunu çöz", "Decode a request code")
        request.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(PAD, 0))
        self.request = tk.Text(request, height=2, width=TEXT_WIDTH, wrap="char", relief="solid",
                               borderwidth=1, font="TkFixedFont")  # fmt: skip
        self.request.grid(row=0, column=0, sticky="ew")
        focus_moves_on_tab(self.request)
        self.request.bind("<Control-Return>", self._decode_key)
        request.columnconfigure(0, weight=1)
        ttk.Button(request, text="Çöz / Decode", command=self.decode_request).grid(
            row=0, column=1, padx=(4, 0), sticky="n"
        )

    def choose(self) -> None:
        path = self.app.dialogs.ask_open("Lisans dosyası / Licence file", [("Lisans", "*.lic")])
        if path is not None:
            self.file.set(str(path))

    def _details(self, details: Sequence[tuple[str, str]]) -> None:
        width = max((len(label) for label, _ in details), default=0)
        set_text(self.output, "\n".join(f"{label:<{width}}  {value}" for label, value in details))

    def inspect(self) -> None:
        try:
            details = self.app.manager.inspect(Path(self.file.get()))
        except (model.ToolError, OSError) as exc:
            self._show(model.VerifyOutcome(ok=False, message=model.message_of(exc)))
            return
        self.result.configure(text="İçerik (imza denetlenmeden) / content, signature not checked",
                              background="#e8eef7", foreground="black")  # fmt: skip
        self._details(details)

    def _show(self, outcome: model.VerifyOutcome) -> None:
        self.last = outcome
        colour = GREEN if outcome.ok else RED
        self.result.configure(text=outcome.message.text().replace("\n\n", "\n"),
                              background=colour, foreground="white")  # fmt: skip
        self._details(outcome.details)

    def verify(self) -> model.VerifyOutcome:
        with self.app.busy(self.verify_button):
            passphrase = ""
            mode = self.mode.get()
            if mode == "private":
                asked = self.app.dialogs.ask_passphrase(
                    "Özel anahtarın parolası (doğrulama için).",
                    "Passphrase of the private key (for the verification).",
                )
                if asked is None:
                    outcome = model.VerifyOutcome(
                        ok=False, message=model.Message("İptal edildi.", "cancelled")
                    )
                    self._show(outcome)
                    return outcome
                passphrase = asked
            try:
                outcome = self.app.manager.verify(
                    Path(self.file.get()),
                    mode="private" if mode == "private" else (
                        "public" if mode == "public" else "committed"),
                    public_key=self.public_key.get(), passphrase=passphrase,
                    server_id=self.server_id.get(), at=self.at.get(),
                )  # fmt: skip
            finally:
                passphrase = ""
            self._show(outcome)
            return outcome

    def _decode_key(self, _event: object) -> str:
        self.decode_request()
        return "break"  # no new line in the box

    def decode_request(self) -> None:
        try:
            view = model.decode_request_text(get_text(self.request))
        except (model.ToolError, model.LicenseError) as exc:
            self._show(model.VerifyOutcome(ok=False, message=model.message_of(exc)))
            return
        binding = view.binding_message()
        self.result.configure(text=f"Denetim toplamı doğru / checksum OK\n{binding.tr}",
                              background=GREEN if view.strong else ORANGE,
                              foreground="white")  # fmt: skip
        self._details([*view.fields(), ("İstek SHA-256 / Request SHA-256", view.sha256)])


# -- the application ------------------------------------------------------------------------------


class App:
    def __init__(
        self, root: tk.Tk, manager: model.LicenseManager, *, dialogs: Dialogs | None = None
    ) -> None:
        self.root = root
        self.manager = manager
        self.dialogs = dialogs or Dialogs(root)
        # pythonw has no console: an error no handler expected becomes a dialog, never silence.
        root.report_callback_exception = self.unexpected
        set_scale(root)
        root.title(model.APP_NAME)
        root.minsize(px(900), px(600))
        width = min(px(1120), root.winfo_screenwidth() - 40)
        height = min(px(780), root.winfo_screenheight() - 80)
        root.geometry(f"{max(width, px(900))}x{max(height, px(600))}")
        style = ttk.Style(root)
        if "vista" not in style.theme_names() and "clam" in style.theme_names():
            style.theme_use("clam")
        line = font.nametofont("TkDefaultFont").metrics("linespace")
        style.configure("Treeview", rowheight=int(line * 1.5))
        menu = tk.Menu(root, tearoff=False)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Çıkış / Exit", command=self.close)
        menu.add_cascade(label="Dosya", menu=file_menu)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="Hakkında / About", command=self.about)
        menu.add_cascade(label="Yardım", menu=help_menu)
        root.configure(menu=menu)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self.notebook.enable_traversal()
        self.settings_tab = SettingsTab(self)
        self.keygen_tab = KeygenTab(self)
        self.issue_tab = IssueTab(self)
        self.registry_tab = RegistryTab(self)
        self.verify_tab = VerifyTab(self)
        self.notebook.add(self.settings_tab, text="Ayarlar", underline=0)
        self.notebook.add(self.keygen_tab, text="Anahtar üret", underline=1)  # Alt+N
        self.notebook.add(self.issue_tab, text="Lisans ver", underline=0)
        self.notebook.add(self.registry_tab, text="Kayıtlar", underline=0)
        self.notebook.add(self.verify_tab, text="Doğrula", underline=0)
        if not manager.settings.private_key:
            self.notebook.select(self.settings_tab)  # type: ignore[no-untyped-call]
        else:
            self.notebook.select(self.issue_tab)  # type: ignore[no-untyped-call]
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def clear_passphrases(self) -> None:
        """The key generation passphrases are not kept once the owner leaves that tab."""
        self.keygen_tab.passphrase.set("")
        self.keygen_tab.again.set("")

    def _tab_changed(self, _event: object = None) -> None:
        if self.notebook.select() != str(self.keygen_tab):  # type: ignore[no-untyped-call]
            self.clear_passphrases()

    def close(self) -> None:
        self.clear_passphrases()
        self.root.destroy()

    def unexpected(self, exc_type: type[BaseException], value: BaseException, tb: Any) -> None:
        """Tk's handler for an exception of a button or key handler: a dialog with the error's
        text (no traceback and no local values, so never a passphrase); the traceback goes to
        the console when there is one."""
        if sys.stderr is not None:
            traceback.print_exception(exc_type, value, tb)
        message = model.message_of(value)
        self.dialogs.error(
            "Beklenmeyen hata / Unexpected error",
            [
                model.Message(
                    f"Beklenmeyen bir hata oluştu; işlem tamamlanmadı ({exc_type.__name__}).",
                    f"an unexpected error occurred; the action did not complete "
                    f"({exc_type.__name__})",
                ),
                message,
            ],
        )

    @contextlib.contextmanager
    def busy(self, button: ttk.Button) -> Iterator[None]:
        """Disable the button while it works (signing is fast; no thread is needed)."""
        button.state(["disabled"])
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            yield
        finally:
            with contextlib.suppress(tk.TclError):
                button.state(["!disabled"])
                self.root.configure(cursor="")

    def refresh_registry(self) -> None:
        self.registry_tab.reload()

    def about_message(self) -> model.Message:
        return model.about_text(model.owner_guide(Path(__file__)))

    def about(self) -> None:
        message = self.about_message()
        self.dialogs.info("Hakkında / About", f"{message.tr}\n\n{message.en}")


# -- self-test ------------------------------------------------------------------------------------


class ScriptedDialogs(Dialogs):
    """Answers without a person: a passphrase, the suggested file name, recorded errors."""

    def __init__(self, root: tk.Tk, passphrase: str) -> None:
        super().__init__(root)
        self.passphrase: str | None = passphrase
        self.errors: list[model.Message] = []
        self.infos: list[str] = []
        self.results: list[model.IssueOutcome] = []
        self.open_paths: list[Path] = []
        self.save_paths: list[Path] = []
        self.questions: list[model.Message] = []
        self.answers: list[bool] = []
        self.asked = 0

    def error(self, title: str, messages: Sequence[model.Message]) -> None:
        self.errors.extend(messages)

    def info(self, title: str, text: str) -> None:
        self.infos.append(text)

    def ask_yes_no(self, title: str, message: model.Message) -> bool:
        self.questions.append(message)
        return self.answers.pop(0) if self.answers else True

    def ask_passphrase(self, prompt_tr: str, prompt_en: str) -> str | None:
        self.asked += 1
        return self.passphrase

    def ask_open(self, title: str, filetypes: Sequence[tuple[str, str]]) -> Path | None:
        return self.open_paths.pop(0) if self.open_paths else None

    def ask_directory(self, title: str) -> Path | None:
        return self.open_paths.pop(0) if self.open_paths else None

    def ask_save(
        self, title: str, initial: Path, filetypes: Sequence[tuple[str, str]]
    ) -> Path | None:
        return self.save_paths.pop(0) if self.save_paths else initial

    def issue_result(self, outcome: model.IssueOutcome) -> None:
        self.results.append(outcome)


def sample_request(organization: str = "Öz Deneme A.Ş.", *, strong: bool = True) -> str:
    """A fresh request code of a fictional Windows server (for the self-test)."""
    identity = ServerIdentity(
        "windows", str(uuid.uuid4()), str(uuid.uuid4()).upper() if strong else ""
    )
    return make_request_code(
        new_request(
            identity,
            installation_id=str(uuid.uuid4()),
            organization=organization,
            now=datetime.now(UTC),
        )
    )


def run_cycle(app: App, dialogs: ScriptedDialogs, folder: Path) -> list[str]:
    """Generate key → settings → decode → issue → verify, through the window; the problems."""
    problems: list[str] = []
    keys = folder / "anahtar"
    keys.mkdir()
    keygen = app.keygen_tab
    keygen.key_id.set("selftest-1")
    keygen.folder.set(str(keys))
    keygen.passphrase.set(dialogs.passphrase or "")
    keygen.again.set(dialogs.passphrase or "")
    keygen.generate()
    if keygen.result is None:
        return [f"keygen: {[m.en for m in dialogs.errors]}"]
    if keygen.passphrase.get() or keygen.again.get():
        problems.append("keygen: the passphrase entries were not cleared")
    keygen.use_key()
    app.settings_tab.create_registry(folder / "kayit-defteri.csv")
    out = folder / "cikti"
    out.mkdir()
    app.settings_tab.set_output(out)
    if app.manager.settings_problems():
        problems.append(f"settings: {[m.en for m in app.manager.settings_problems()]}")
    issue = app.issue_tab
    issue.set_request(sample_request())
    if issue.decode() is None:
        return [*problems, "decode failed"]
    if issue.licensee.get() != "Öz Deneme A.Ş.":
        problems.append("the licensee was not prefilled")
    issue.module_vars["usage_reports"].set(True)
    issue.toggle_module("usage_reports")
    if not issue.module_vars["usage_analytics"].get():
        problems.append("usage_reports did not tick usage_analytics")
    outcome = issue.create()
    if outcome is None:
        return [*problems, f"issue: {[m.en for m in dialogs.errors]}"]
    verify = app.verify_tab
    verify.file.set(str(outcome.result.path))
    verify.mode.set("private")
    verify.server_id.set(outcome.result.document.payload.server_ids[0])
    verify.at.set(date.today().isoformat())  # noqa: DTZ011 - a day for the maintenance check
    result = verify.verify()
    if not result.ok:
        problems.append(f"verify: {result.message.en}")
    verify.mode.set("committed")
    if verify.verify().ok:
        problems.append("the committed key set accepted a self-test key")
    app.registry_tab.reload()
    if [view.license_id for view in app.registry_tab.views] != [outcome.license_id]:
        problems.append("the registry tab does not show the licence")
    issue.set_request(sample_request())
    issue.decode()
    errors_before = len(dialogs.errors)
    if issue.create() is not None or len(dialogs.errors) == errors_before:
        problems.append("a second licence for the same customer was issued without a reason")
    return problems


def self_test() -> int:
    """Build the window off-screen and run a full cycle; 0 when everything worked."""
    passphrase = secrets.token_urlsafe(18)
    try:
        with tempfile.TemporaryDirectory(prefix="arcilogger-lisans-selftest-") as temporary:
            folder = Path(temporary)
            root = tk.Tk()
            root.withdraw()
            try:
                manager = model.LicenseManager(folder / "settings.json")
                dialogs = ScriptedDialogs(root, passphrase)
                app = App(root, manager, dialogs=dialogs)
                root.update_idletasks()
                problems = run_cycle(app, dialogs, folder)
                if passphrase in (folder / "settings.json").read_text(encoding="utf-8"):
                    problems.append("the passphrase reached the settings file")
            finally:
                root.destroy()
    except Exception:
        traceback.print_exc()
        return 1
    if problems:
        for problem in problems:
            print(f"Self-test: {problem}", file=sys.stderr)
        return 1
    print("Lisans Yöneticisi öz sınaması başarılı. [Licence Manager self-test passed.]")
    return 0


# -- entry point ----------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arcilogger_license_gui",
        description=f"{model.APP_NAME} [{model.APP_NAME_EN}], çevrimdışı [offline]",
    )
    parser.add_argument("--self-test", action="store_true", help="off-screen full cycle, exit 0")
    parser.add_argument("--settings", type=Path, help="settings file (default: app-data folder)")
    return parser


def dpi_aware() -> None:
    """Sharp text on a scaled Windows display (Tk scales itself once the process is aware)."""
    if sys.platform == "win32":
        import ctypes

        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.shcore.SetProcessDpiAwareness(1)


def utf8_console() -> None:
    """Turkish texts must print on a cp1252 Windows console or pipe too (the CI log, a .cmd)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # None under pythonw
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    utf8_console()
    args = build_parser().parse_args(argv)
    if args.self_test:
        return self_test()
    dpi_aware()
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # no display: nothing to show a dialog on
        print(f"Pencere açılamadı [cannot open the window]: {exc}", file=sys.stderr)
        return 1
    try:
        App(root, model.LicenseManager(args.settings))
    except Exception as exc:  # pythonw has no console: a failed start is shown, not lost
        if sys.stderr is not None:
            traceback.print_exc()
        message = model.message_of(exc)
        messagebox.showerror(
            model.APP_NAME,
            f"Pencere başlatılamadı. [The window could not start.]\n\n{message.text()}",
        )
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
