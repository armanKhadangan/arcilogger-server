"""The Lisans Yöneticisi's Tk window (``tools/license-issuer/arcilogger_license_gui.py``).

These tests build the real window, so they need a display: they run on Windows and where
``DISPLAY`` is set (locally ``xvfb-run -a uv run pytest tests/test_license_manager_gui.py``) and
are skipped otherwise (``ARCILOGGER_REQUIRE_DISPLAY=1`` turns the skip into a failure). Dialogs
are answered by the window's scripted dialog set, as in ``--self-test``.

With ``ARCILOGGER_SCREENSHOTS=<folder>`` one more test fills the window with fictional data and
saves a PNG of every tab there (X11 with ImageMagick's ``import``): the pictures of
``tools/license-issuer/README.md``. Tk on X11 uses core X fonts; ``ARCILOGGER_SCREENSHOT_FONT``
(e.g. ``DejaVu Sans``, served to Xvfb with ``-fp``) names a family with the Turkish letters.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from arcilogger_core.license.fingerprint import ServerIdentity, server_id_of
from arcilogger_core.license.request import make_request_code, new_request

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools" / "license-issuer"
GUI = TOOLS / "arcilogger_license_gui.py"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

# A Python built without Tk (the runner's system Python on Linux CI) cannot run the window at
# all; Windows and the uv-managed Python have it.
pytest.importorskip("tkinter", reason="this Python has no tkinter")

import tkinter as tk  # noqa: E402
from tkinter import font, ttk  # noqa: E402

import arcilogger_license_gui as gui  # noqa: E402
import license_manager_model as model  # noqa: E402


def _display() -> bool:
    return sys.platform == "win32" or bool(os.environ.get("DISPLAY"))


pytestmark = [
    pytest.mark.license_live,
    pytest.mark.skipif(
        not _display() and os.environ.get("ARCILOGGER_REQUIRE_DISPLAY") != "1",
        reason="no display (run under xvfb-run)",
    ),
]

PASSPHRASE = "a long offline passphrase"
WINDOWS = ServerIdentity(
    "windows", "0f8fad5b-d9cb-469f-a165-70867728950e", "6F9619FF-8B86-D011-B42D-00C04FC964FF"
)
LINUX = ServerIdentity("linux", "4c4c4544004c4b108039b3c04f4b3732", "")
INSTALLATION = "3f1c2b6e-8a51-4c43-9d0f-5b7e2a61c9d4"


def request_code(
    identity: ServerIdentity = WINDOWS,
    *,
    organization: str = "Örnek Belediyesi",
    installation: str = INSTALLATION,
) -> str:
    req = new_request(
        identity, installation_id=installation, organization=organization, now=datetime.now(UTC)
    )
    return make_request_code(req)


@dataclass
class Window:
    root: tk.Tk
    app: gui.App
    dialogs: gui.ScriptedDialogs
    folder: Path

    def setup(self) -> Path:
        """A key pair generated in the "Anahtar üret" tab, used in "Ayarlar"."""
        keygen = self.app.keygen_tab
        keygen.key_id.set("arci-2026a")
        keygen.folder.set(str(self.folder / "anahtar"))
        keygen.passphrase.set(PASSPHRASE)
        keygen.again.set(PASSPHRASE)
        keygen.generate()
        assert keygen.result is not None, self.dialogs.errors
        keygen.use_key()
        self.app.settings_tab.create_registry(self.folder / "kayit-defteri.csv")
        (self.folder / "cikti").mkdir(exist_ok=True)
        self.app.settings_tab.set_output(self.folder / "cikti")
        assert self.app.manager.settings_problems() == []
        return keygen.result.private_path


def new_root() -> tk.Tk:
    """A Tk root, or a skip when Tk cannot open the display (a failure when it is required)."""
    try:
        return tk.Tk()
    except tk.TclError as exc:
        if os.environ.get("ARCILOGGER_REQUIRE_DISPLAY") == "1":
            pytest.fail(f"no Tk display: {exc}")
        pytest.skip(f"no Tk display: {exc}")


@pytest.fixture
def window(tmp_path: Path) -> Iterator[Window]:
    root = new_root()
    root.withdraw()
    manager = model.LicenseManager(tmp_path / "appdata" / model.SETTINGS_FILE)
    dialogs = gui.ScriptedDialogs(root, PASSPHRASE)
    app = gui.App(root, manager, dialogs=dialogs)
    root.update_idletasks()
    try:
        yield Window(root, app, dialogs, tmp_path / "offline")
    finally:
        root.destroy()


def mapped(widget: tk.Misc) -> bool:
    """Placed by its geometry manager (``grid_remove`` leaves it unmanaged)."""
    return widget.winfo_manager() != ""


def test_the_window_has_the_five_tabs_and_the_about_box(window: Window) -> None:
    notebook = window.app.notebook
    tabs = notebook.tabs()  # type: ignore[no-untyped-call]
    titles = [notebook.tab(tab, "text") for tab in tabs]  # type: ignore[no-untyped-call]
    assert titles == ["Ayarlar", "Anahtar üret", "Lisans ver", "Kayıtlar", "Doğrula"]
    assert window.root.title() == "ARCILOGGER Lisans Yöneticisi"
    window.app.about()
    (about,) = window.dialogs.infos
    assert "arman@khadangan.com" in about
    assert "ağ bağlantısı kurmaz" in about


def test_module_boxes_and_weak_binding_box(window: Window) -> None:
    issue = window.app.issue_tab
    core = issue.module_buttons["core_5651"]
    assert core.instate(["disabled"])
    assert issue.module_vars["core_5651"].get()
    core.invoke()
    assert issue.module_vars["core_5651"].get()
    issue.module_buttons["usage_reports"].invoke()
    assert issue.module_vars["usage_reports"].get()
    assert issue.module_vars["usage_analytics"].get()
    issue.module_buttons["usage_analytics"].invoke()
    assert not issue.module_vars["usage_analytics"].get()
    assert not issue.module_vars["usage_reports"].get()

    assert not mapped(issue.weak_box)
    issue.set_request(request_code(LINUX))
    assert issue.decode() is not None
    assert mapped(issue.weak_box)
    assert mapped(issue.weak_warning)
    issue.set_request(request_code())
    assert issue.decode() is not None
    assert not mapped(issue.weak_box)
    assert issue.licensee.get() == "Örnek Belediyesi"
    issue.set_request("ARCIREQ1-XXXX")
    assert issue.decode() is None
    assert "request_" in str(issue.checksum.cget("text"))


def test_limits_dates_and_the_unlimited_box(window: Window) -> None:
    issue = window.app.issue_tab
    today = window.app.manager.today
    assert issue.maintenance.get() == model.maintenance_for(today).isoformat()
    issue.quick_maintenance(2)
    assert issue.maintenance.get() == model.maintenance_for(today, 2).isoformat()
    assert not issue.sources_box.instate(["disabled"])
    window.root.update_idletasks()
    unlimited = next(
        child
        for child in issue.sources_box.master.winfo_children()
        if isinstance(child, ttk.Checkbutton)
    )
    unlimited.invoke()
    assert issue.sources_unlimited.get()
    assert issue.sources_box.instate(["disabled"])
    assert issue.valid_entry.instate(["disabled"])
    issue.valid_enabled.set(True)
    issue.toggle_valid()
    assert not issue.valid_entry.instate(["disabled"])


def test_fill_decode_issue_verify_and_renew(window: Window) -> None:
    pem = window.setup()
    keygen = window.app.keygen_tab
    assert (keygen.passphrase.get(), keygen.again.get()) == ("", "")
    assert keygen.line.get() == keygen.result.line if keygen.result else False
    settings = window.app.settings_tab
    settings.test_key()
    assert settings.key_id.get() == "arci-2026a"
    assert settings.fingerprint.get() == keygen.result.public_sha256 if keygen.result else False
    assert str(settings.key_status.cget("text")).startswith("Uygun")

    issue = window.app.issue_tab
    code = request_code()
    issue.set_request(code)
    issue.decode()
    issue.module_buttons["usage_reports"].invoke()
    issue.users.set("25")
    issue.note.set("Sözleşme 2026/17")
    outcome = issue.create()
    assert outcome is not None, window.dialogs.errors
    assert window.dialogs.results == [outcome]
    assert outcome.result.path.parent == window.folder / "cikti"
    assert outcome.result.document.payload.modules == (
        "core_5651", "usage_analytics", "usage_reports"
    )  # fmt: skip
    assert outcome.result.document.payload.users == 25
    assert window.dialogs.asked == 2  # "Anahtarı sına" and "Lisansı oluştur"
    assert get_request(issue) == ""  # the next licence needs a new request code

    verify = window.app.verify_tab
    verify.file.set(str(outcome.result.path))
    verify.mode.set("public")
    verify.public_key.set(keygen.result.public_key if keygen.result else "")
    verify.server_id.set(server_id_of(WINDOWS))
    assert verify.verify().ok
    assert str(verify.result.cget("background")) == gui.GREEN
    verify.server_id.set(server_id_of(LINUX))
    refused = verify.verify()
    assert refused.message.code == "license_other_server"
    assert str(verify.result.cget("background")) == gui.RED
    verify.inspect()
    assert outcome.license_id in gui.get_text(verify.output)
    verify.request.insert("1.0", request_code(LINUX))
    verify.decode_request()
    assert server_id_of(LINUX) in gui.get_text(verify.output)

    registry = window.app.registry_tab
    assert registry.tree.get_children() == (outcome.license_id,)
    registry.tree.selection_set(outcome.license_id)
    registry.renew()
    assert window.app.notebook.select() == str(issue)  # type: ignore[no-untyped-call]
    assert issue.licensee.get() == "Örnek Belediyesi"
    assert issue.users.get() == "25"
    assert (
        issue.maintenance.get()
        == model.add_years(outcome.result.document.payload.maintenance_until, 1).isoformat()
    )
    assert mapped(issue.earlier_frame)
    assert issue.supersede_vars[outcome.license_id].get()
    assert issue.earlier_mode.get() == "supersedes"
    assert issue.create() is None  # a new request code is still required
    assert any("request code" in message.en for message in window.dialogs.errors)

    issue.set_request(code)
    issue.decode()
    assert issue.supersede_vars[outcome.license_id].get()
    renewed = issue.create()
    assert renewed is not None, window.dialogs.errors
    assert renewed.result.row["supersedes"] == outcome.license_id
    ((_, line, note),) = renewed.revocations
    assert line == f'"{outcome.license_id}",'
    assert "Aynı sunucuda" in note.tr
    assert len(registry.tree.get_children()) == 2
    assert pem.is_file()
    settings_text = window.app.manager.settings_path.read_text(encoding="utf-8")
    assert PASSPHRASE not in settings_text


def get_request(issue: gui.IssueTab) -> str:
    return gui.get_text(issue.request)


def test_earlier_licences_need_a_choice_in_the_window(window: Window) -> None:
    window.setup()
    issue = window.app.issue_tab
    code = request_code()
    issue.set_request(code)
    issue.decode()
    first = issue.create()
    assert first is not None
    issue.set_request(code)
    issue.decode()
    assert mapped(issue.earlier_frame)
    assert not issue.supersede_vars[first.license_id].get()
    errors = len(window.dialogs.errors)
    assert issue.create() is None
    assert "Yenileme / değiştirme" in window.dialogs.errors[errors].tr
    issue.earlier_mode.set("additional")
    second = issue.create()
    assert second is not None
    assert second.result.row["supersedes"] == "additional"


def test_a_weak_request_is_refused_until_the_box_is_ticked(window: Window) -> None:
    window.setup()
    issue = window.app.issue_tab
    issue.set_request(request_code(LINUX, organization="Deneme Okulu"))
    issue.decode()
    assert issue.create() is None
    assert "Zayıf bağlama" in window.dialogs.errors[-1].tr
    issue.weak.set(True)
    outcome = issue.create()
    assert outcome is not None, window.dialogs.errors
    assert outcome.result.row["binding"] == "weak"


def test_cancelling_the_passphrase_writes_nothing(window: Window) -> None:
    window.setup()
    window.dialogs.passphrase = None
    issue = window.app.issue_tab
    issue.set_request(request_code())
    issue.decode()
    assert issue.create() is None
    assert list((window.folder / "cikti").iterdir()) == []
    assert window.app.manager.registry_rows() == ()
    assert issue.create_button.instate(["!disabled"])


SECOND = ServerIdentity(
    "windows", "11111111-2222-4333-8444-555555555555", "4C4C4544-0042-3510-8058-B3C04F4B3732"
)
OTHER_INSTALLATION = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
EXTRA = "AAAA-BBBB-CCCC-DDDD"


def beta_request() -> str:
    return request_code(SECOND, organization="Beta Ltd.", installation=OTHER_INSTALLATION)


def test_the_next_customer_starts_from_an_empty_form(window: Window) -> None:
    window.setup()
    issue = window.app.issue_tab
    issue.set_request(request_code(organization="Alfa A.Ş."))
    issue.decode()
    issue.extra.insert("1.0", EXTRA)
    issue.note.set("Sözleşme 2026/3")
    issue.module_buttons["bulk_import"].invoke()
    issue.users.set("40")
    first = issue.create()
    assert first is not None, window.dialogs.errors
    assert first.result.document.payload.server_ids[1:] == (EXTRA,)
    # Every field is back to its default, not only the request box.
    assert issue.licensee.get() == ""
    assert gui.get_text(issue.extra) == ""
    assert issue.note.get() == ""
    assert issue.users.get() == model.DEFAULT_LIMIT
    assert not issue.module_vars["bulk_import"].get()
    assert issue.maintenance.get() == model.maintenance_for(window.app.manager.today).isoformat()

    issue.set_request(beta_request())
    issue.decode()
    assert issue.licensee.get() == "Beta Ltd."
    assert not mapped(issue.earlier_frame)  # Beta has no earlier licence
    second = issue.create()
    assert second is not None, window.dialogs.errors
    payload = second.result.document.payload
    assert payload.licensee == "Beta Ltd."
    assert payload.server_ids == (server_id_of(SECOND),)
    assert second.result.row["note"] == ""


def test_decoding_another_request_replaces_the_prefilled_licensee(window: Window) -> None:
    window.setup()
    issue = window.app.issue_tab
    issue.set_request(request_code(organization="Alfa A.Ş."))
    issue.decode()
    assert issue.licensee.get() == "Alfa A.Ş."
    issue.set_request(beta_request())
    issue.decode()
    assert issue.licensee.get() == "Beta Ltd."
    issue.licensee.set("Beta Limited Şirketi")
    issue.set_request(beta_request())
    issue.decode()
    assert issue.licensee.get() == "Beta Limited Şirketi"  # typed by the owner: kept


def test_a_tick_is_not_carried_over_to_another_customer(window: Window) -> None:
    window.setup()
    issue = window.app.issue_tab
    alfa = request_code(organization="Alfa A.Ş.")
    issue.set_request(alfa)
    issue.decode()
    first = issue.create()
    assert first is not None, window.dialogs.errors
    issue.set_request(alfa)
    issue.decode()
    issue.supersede_vars[first.license_id].set(True)
    issue._ticked()
    assert issue.earlier_mode.get() == "supersedes"
    issue.set_request(beta_request())
    issue.decode()
    assert issue.licensee.get() == "Beta Ltd."
    assert not mapped(issue.earlier_frame)
    assert issue.supersede_vars == {}
    assert issue.earlier_mode.get() == ""
    beta = issue.create()
    assert beta is not None, window.dialogs.errors
    assert beta.result.row["supersedes"] == ""
    assert beta.revocations == ()


def test_renew_keeps_the_extra_servers_and_asks_about_a_past_maintenance(
    window: Window,
) -> None:
    window.setup()
    issue = window.app.issue_tab
    code = request_code()
    issue.set_request(code)
    issue.decode()
    issue.extra.insert("1.0", EXTRA)
    first = issue.create()
    assert first is not None, window.dialogs.errors
    registry = window.app.registry_tab
    registry.tree.selection_set(first.license_id)
    registry.renew()
    assert gui.get_text(issue.extra) == EXTRA
    assert "Yenilenen lisanstan alındı" in str(issue.extra_hint.cget("text"))
    issue.set_request(code)
    issue.decode()
    issue.maintenance.set((window.app.manager.today - timedelta(days=1)).isoformat())
    window.dialogs.answers = [False]
    asked = window.dialogs.asked
    assert issue.create() is None
    (question,) = window.dialogs.questions
    assert question.tr.startswith("Bakım bitişi bugünden önce")
    assert window.dialogs.asked == asked  # no passphrase was asked
    assert len(window.app.manager.registry_rows()) == 1
    issue.quick_maintenance(1)
    renewed = issue.create()
    assert renewed is not None, window.dialogs.errors
    assert renewed.result.document.payload.server_ids == (server_id_of(WINDOWS), EXTRA)
    assert len(window.dialogs.questions) == 1
    registry.reload()
    assert registry.tree.item(first.license_id, "tags") == ("replaced",)
    columns = [name for name, _, _ in model.REGISTRY_COLUMNS]
    values = registry.tree.item(first.license_id, "values")
    assert values[columns.index("replaced_by")] == renewed.license_id


def test_a_moved_registry_shows_in_settings_and_stops_the_issue(window: Window) -> None:
    window.setup()
    registry = window.folder / "kayit-defteri.csv"
    registry.rename(registry.with_name("tasindi.csv"))
    settings = window.app.settings_tab
    settings.refresh()
    assert str(settings.registry_status.cget("text")).startswith("Kayıt defteri bulunamadı")
    assert str(settings.registry_status.cget("foreground")) == gui.RED
    issue = window.app.issue_tab
    issue.set_request(request_code())
    issue.decode()
    assert issue.create() is None
    assert any("registry not found" in m.en for m in window.dialogs.errors)
    assert not registry.exists()
    assert list((window.folder / "cikti").iterdir()) == []


def test_an_unexpected_error_becomes_a_dialog(window: Window) -> None:
    def broken() -> None:
        raise RuntimeError("boom")

    button = ttk.Button(window.root, command=broken)
    button.invoke()
    first, second = window.dialogs.errors
    assert first.tr.startswith("Beklenmeyen bir hata oluştu")
    assert "RuntimeError" in first.tr
    assert "boom" in second.tr
    assert "Traceback" not in first.text() + second.text()


def test_a_notepad_unicode_request_file_is_loaded(window: Window) -> None:
    window.setup()
    path = window.folder / "istek.txt"
    code = request_code()
    path.write_bytes(b"\xff\xfe" + code.encode("utf-16-le"))
    window.dialogs.open_paths.append(path)
    issue = window.app.issue_tab
    issue.load_file()
    assert get_request(issue) == code
    assert window.app.manager.request_view is not None
    garbage = window.folder / "bozuk.txt"
    garbage.write_bytes(b"\xff\x00\xc3")
    window.dialogs.open_paths.append(garbage)
    issue.load_file()
    assert "UTF-8 bekleniyor" in window.dialogs.errors[-1].tr


def test_key_passphrases_are_cleared_when_leaving_the_tab(window: Window) -> None:
    app = window.app
    app.notebook.select(app.keygen_tab)  # type: ignore[no-untyped-call]
    window.root.update()
    app.keygen_tab.passphrase.set(PASSPHRASE)
    app.keygen_tab.again.set(PASSPHRASE)
    app.notebook.select(app.issue_tab)  # type: ignore[no-untyped-call]
    window.root.update()
    assert (app.keygen_tab.passphrase.get(), app.keygen_tab.again.get()) == ("", "")
    app.keygen_tab.passphrase.set(PASSPHRASE)
    app.clear_passphrases()
    assert app.keygen_tab.passphrase.get() == ""


def test_keyboard_shortcuts(window: Window) -> None:
    notebook = window.app.notebook
    underlines = {
        notebook.tab(tab, "text"): notebook.tab(tab, "underline")  # type: ignore[no-untyped-call]
        for tab in notebook.tabs()  # type: ignore[no-untyped-call]
    }
    letters = {text[index].casefold() for text, index in underlines.items()}
    assert len(letters) == len(underlines)  # every tab has its own Alt+letter
    issue = window.app.issue_tab
    for text in (issue.request, issue.extra, window.app.verify_tab.request):
        assert text.bind("<Tab>")
        assert text.bind("<Shift-Tab>")
    assert issue.request.bind("<Control-Return>")
    assert window.app.registry_tab.tree.bind("<Return>")


def test_export_refuses_an_existing_file(window: Window) -> None:
    window.setup()
    old = window.folder / "gecen-ay.csv"
    old.write_text("keep", encoding="utf-8")
    window.dialogs.save_paths.append(old)
    window.app.registry_tab.export()
    assert "choose another name" in window.dialogs.errors[-1].en
    assert old.read_text(encoding="utf-8") == "keep"


def test_self_test_exits_zero() -> None:
    new_root().destroy()
    completed = subprocess.run(
        [sys.executable, str(GUI), "--self-test"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # The Windows console and the CI log pipe are cp1252: the window must still print its
        # Turkish result (the zip's self-test once failed on "ı" there).
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (completed.stdout or "") + (completed.stderr or "")
    assert "self-test passed" in completed.stdout


# -- screenshots for the README -------------------------------------------------------------------

SCREENSHOTS = os.environ.get("ARCILOGGER_SCREENSHOTS")
WINDOWS_FOLDER = r"E:\arcilogger-lisans"
CUSTOMERS = (
    # licensee, maintenance offset (days from today), modules, sources, users ("" = unlimited)
    ("Deniz Lojistik A.Ş.", 12, ("usage_analytics",), "40", "10"),
    ("Kuzey Üniversitesi", -20, ("usage_analytics", "usage_reports", "bulk_import"), "", ""),
    ("Yıldız Hastanesi", 300, ("bulk_import",), "15", "5"),
)


def fictional_identity(name: str) -> ServerIdentity:
    """A made-up Windows server of a fictional customer (stable across runs)."""
    guid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{name} machine")
    smbios = uuid.uuid5(uuid.NAMESPACE_DNS, f"{name} smbios")
    return ServerIdentity("windows", str(guid), str(smbios).upper())


def capture(widget: tk.Misc, path: Path) -> None:
    widget.update()
    time.sleep(0.4)
    widget.update()
    frame = widget.winfo_toplevel().wm_frame()
    subprocess.run(["import", "-window", frame, str(path)], check=True, timeout=60)
    if path.stat().st_size > 190_000:
        subprocess.run(["convert", str(path), "-colors", "192", f"PNG8:{path}"], check=True)
    assert path.stat().st_size < 200_000, path


@pytest.mark.skipif(not SCREENSHOTS, reason="ARCILOGGER_SCREENSHOTS not set")
@pytest.mark.skipif(shutil.which("import") is None, reason="ImageMagick import not found")
def test_screenshots_of_every_tab(window: Window, request: pytest.FixtureRequest) -> None:
    out = Path(SCREENSHOTS or ".")
    out.mkdir(parents=True, exist_ok=True)
    folder = Path("/tmp/arcilogger-lisans")  # noqa: S108 - a readable path for the pictures
    shutil.rmtree(folder, ignore_errors=True)
    request.addfinalizer(lambda: shutil.rmtree(folder, ignore_errors=True))
    window.folder = folder
    root, app, manager = window.root, window.app, window.app.manager
    family = os.environ.get("ARCILOGGER_SCREENSHOT_FONT")
    if family:  # X11 core fonts: a family with the Turkish letters (e.g. DejaVu Sans)
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont",
                     "TkCaptionFont", "TkSmallCaptionFont", "TkTooltipFont"):  # fmt: skip
            font.nametofont(name).configure(family=family)
        font.nametofont("TkFixedFont").configure(family=f"{family} Mono")
        app.registry_tab._italic.configure(family=family)  # the "replaced" rows
        line = font.nametofont("TkDefaultFont").metrics("linespace")
        ttk.Style(root).configure("Treeview", rowheight=int(line * 1.5))
    root.deiconify()
    root.geometry("1120x780+0+0")
    window.setup()
    keygen_line = app.keygen_tab.result.line if app.keygen_tab.result else ""
    today = manager.today
    for name, offset, modules, sources, users in CUSTOMERS:
        identity = fictional_identity(name)
        form = model.IssueForm(
            request_text=request_code(identity, organization=name,
                                      installation=str(uuid.uuid5(uuid.NAMESPACE_DNS, name))),
            licensee=name, modules=model.ModuleChoice(modules), sources=sources or "10",
            sources_unlimited=not sources, users=users or "10", users_unlimited=not users,
            maintenance_until=(today + timedelta(days=offset)).isoformat(),
        )  # fmt: skip
        plan = manager.validate(form)
        signed = manager.sign(plan, PASSPHRASE)
        manager.write(signed, manager.suggested_path(signed))
    issue = app.issue_tab
    issue.set_request(request_code(organization="Örnek Belediyesi"))
    issue.decode()
    issue.module_buttons["usage_reports"].invoke()
    issue.users.set("25")
    issue.note.set("Sözleşme 2026/41")
    first = issue.create()
    assert first is not None, window.dialogs.errors
    app.registry_tab.reload()

    app.settings_tab.test_key()
    app.notebook.select(app.settings_tab)  # type: ignore[no-untyped-call]
    # The pictures show the paths of the owner's Windows PC (README: E:\arcilogger-lisans\…).
    settings = app.settings_tab
    settings.key.set(WINDOWS_FOLDER + r"\anahtar\arci-2026a.private.pem")
    settings.registry.set(WINDOWS_FOLDER + r"\kayit-defteri.csv")
    settings.output.set(WINDOWS_FOLDER + r"\cikti")
    settings.settings_file.configure(
        text=r"Ayar dosyası / Settings file: %APPDATA%\ARCILOGGER Lisans Yöneticisi\settings.json"
    )
    capture(root, out / "1-ayarlar.png")
    app.notebook.select(app.keygen_tab)  # type: ignore[no-untyped-call]
    app.keygen_tab.key_id.set("arci-2026a")
    app.keygen_tab.folder.set(WINDOWS_FOLDER + r"\anahtar")
    app.keygen_tab.files.configure(
        text=f"{WINDOWS_FOLDER}\\anahtar\\arci-2026a.private.pem\n"
        f"{WINDOWS_FOLDER}\\anahtar\\arci-2026a.public.txt"
    )
    assert keygen_line
    capture(root, out / "2-anahtar-uret.png")

    app.registry_tab.tree.selection_set(first.license_id)
    app.registry_tab.renew()
    issue.set_request(request_code(organization="Örnek Belediyesi"))
    issue.decode()
    capture(root, out / "3-lisans-ver.png")
    issue_scroll = issue.winfo_children()[0]
    assert isinstance(issue_scroll, gui.ScrollFrame)
    issue_scroll.canvas.yview_moveto(1.0)
    capture(root, out / "4-lisans-ver-alt.png")
    renewed = issue.create()
    assert renewed is not None, window.dialogs.errors
    dialog = gui.IssueResultDialog(root, renewed)
    dialog.top.geometry("+40+40")
    entry = dialog.entries["Dosya / File"]
    entry.configure(state="normal")
    entry.delete(0, "end")
    entry.insert(0, f"{WINDOWS_FOLDER}\\cikti\\{renewed.result.path.name}")
    entry.configure(state="readonly")
    capture(dialog.top, out / "5-lisans-sonuc.png")
    dialog.top.destroy()

    app.notebook.select(app.registry_tab)  # type: ignore[no-untyped-call]
    app.registry_tab.reload()
    capture(root, out / "6-kayitlar.png")

    verify = app.verify_tab
    app.notebook.select(verify)  # type: ignore[no-untyped-call]
    verify.file.set(str(renewed.result.path))
    verify.mode.set("private")
    verify.server_id.set(server_id_of(WINDOWS))
    verify.at.set(date.today().isoformat())  # noqa: DTZ011 - the owner's day
    assert verify.verify().ok
    verify.file.set(f"{WINDOWS_FOLDER}\\cikti\\{renewed.result.path.name}")
    capture(root, out / "7-dogrula.png")
