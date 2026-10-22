"""The Lisans Yöneticisi's controller and view-model (``tools/license-issuer``, no Tk).

Pure Python, no Tk: settings, module rules, limits, dates, request decoding, the earlier-licence
choice, the issue cycle with the test key of ``arcilogger_core.license.testing`` (the file verifies
with the public key; the registry row has the command line's format), renewal, the registry view
and verification. The issuer's refusals reach the owner as Turkish/English messages.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
)

from arcilogger_core.license.document import load_license
from arcilogger_core.license.fingerprint import ServerIdentity, server_id_of
from arcilogger_core.license.modules import MODULES
from arcilogger_core.license.request import make_request_code, new_request, request_sha256
from arcilogger_core.license.testing import (
    TEST_KEY_ID,
    public_key_b64,
    tamper,
    test_private_key,
)

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools" / "license-issuer"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import license_manager_model as model  # noqa: E402

pytestmark = pytest.mark.license_live

issuer = model.issuer
PASSPHRASE = "a long offline passphrase"
TODAY = datetime.now(UTC).date()
WINDOWS = ServerIdentity(
    "windows", "0f8fad5b-d9cb-469f-a165-70867728950e", "6F9619FF-8B86-D011-B42D-00C04FC964FF"
)
SECOND = ServerIdentity(
    "windows", "11111111-2222-4333-8444-555555555555", "4C4C4544-0042-3510-8058-B3C04F4B3732"
)
LINUX = ServerIdentity("linux", "4c4c4544004c4b108039b3c04f4b3732", "")
INSTALLATION = "3f1c2b6e-8a51-4c43-9d0f-5b7e2a61c9d4"
OTHER_INSTALLATION = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"


def request_code(
    identity: ServerIdentity = WINDOWS,
    *,
    installation: str = INSTALLATION,
    organization: str = "Örnek A.Ş.",
) -> str:
    req = new_request(
        identity, installation_id=installation, organization=organization, now=datetime.now(UTC)
    )
    return make_request_code(req)


@dataclass
class Owner:
    root: Path
    manager: model.LicenseManager

    @property
    def pem(self) -> Path:
        return self.root / "anahtar" / f"{TEST_KEY_ID}.private.pem"

    @property
    def registry(self) -> Path:
        return self.root / "kayit-defteri.csv"

    @property
    def out(self) -> Path:
        return self.root / "cikti"

    def form(self, code: str | None = None, **values: object) -> model.IssueForm:
        form = model.IssueForm(
            request_text=code or request_code(),
            licensee="Örnek A.Ş.",
            modules=model.ModuleChoice(["usage_reports"]),
            sources="10",
            users_unlimited=True,
            maintenance_until="2027-10-20",
        )
        for name, value in values.items():
            setattr(form, name, value)
        return form

    def issue(self, form: model.IssueForm) -> model.IssueOutcome:
        plan = self.manager.validate(form)
        signed = self.manager.sign(plan, PASSPHRASE)
        return self.manager.write(signed, self.manager.suggested_path(signed))

    def rows(self) -> list[dict[str, str]]:
        text = self.registry.read_text(encoding="utf-8-sig")
        return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=";"))


def write_test_key(path: Path) -> None:
    """The deterministic test key of ``arcilogger_core.license.testing`` as keygen writes keys."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = test_private_key().private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(PASSPHRASE.encode())
    )
    path.write_bytes(pem)


@pytest.fixture
def owner(tmp_path: Path) -> Owner:
    root = tmp_path / "offline"
    manager = model.LicenseManager(tmp_path / "appdata" / model.SETTINGS_FILE)
    folder = Owner(root, manager)
    write_test_key(folder.pem)
    folder.out.mkdir(parents=True)
    manager.set_private_key(str(folder.pem))
    manager.create_registry(str(folder.registry))
    manager.set_output_dir(str(folder.out))
    return folder


def errors_of(excinfo: pytest.ExceptionInfo[model.FormError]) -> str:
    return " | ".join(f"{m.tr} {m.en}" for m in excinfo.value.messages)


# -- settings -------------------------------------------------------------------------------------


def test_settings_folder_per_platform(tmp_path: Path) -> None:
    windows = model.settings_dir({"APPDATA": r"C:\Users\arman\AppData\Roaming"}, "win32", tmp_path)
    assert windows == Path(r"C:\Users\arman\AppData\Roaming") / "ARCILOGGER Lisans Yöneticisi"
    assert model.settings_dir({}, "linux", tmp_path) == (
        tmp_path / ".config" / "ARCILOGGER Lisans Yöneticisi"
    )
    xdg = model.settings_dir({"XDG_CONFIG_HOME": str(tmp_path / "x")}, "linux", tmp_path)
    assert xdg == tmp_path / "x" / "ARCILOGGER Lisans Yöneticisi"


def test_settings_are_saved_and_loaded_without_the_passphrase(owner: Owner) -> None:
    info = owner.manager.test_key(PASSPHRASE)
    assert info.key_id == TEST_KEY_ID
    assert info.public_key == public_key_b64(test_private_key())
    assert info.public_sha256 == issuer.public_sha256(info.public_key)
    owner.issue(owner.form())
    text = owner.manager.settings_path.read_text(encoding="utf-8")
    assert PASSPHRASE not in text
    assert set(json.loads(text)) == {"schema", "private_key", "registry", "output_dir"}
    again = model.load_settings(owner.manager.settings_path)
    assert again == model.Settings(str(owner.pem), str(owner.registry), str(owner.out))
    assert PASSPHRASE not in repr(vars(owner.manager))


def test_damaged_settings_give_empty_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert model.load_settings(path) == model.Settings()
    path.write_text("[1, 2", encoding="utf-8")
    assert model.load_settings(path) == model.Settings()
    path.write_text('{"private_key": 3, "registry": "r.csv", "passphrase": "x"}', "utf-8")
    assert model.load_settings(path) == model.Settings(registry="r.csv")


def test_paths_inside_a_git_working_tree_are_refused(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    (repository / ".git").mkdir(parents=True)
    manager = model.LicenseManager(tmp_path / "settings.json")
    pem = repository / "keys" / "arci-2026a.private.pem"
    write_test_key(pem)
    with pytest.raises(model.ToolError) as key_error:
        manager.set_private_key(str(pem))
    assert key_error.value.tr == issuer.repository_texts(pem, "Özel anahtar", "private key")[0]
    with pytest.raises(model.ToolError) as registry_error:
        manager.create_registry(str(repository / "kayit.csv"))
    assert "git" in registry_error.value.tr
    with pytest.raises(model.ToolError):
        manager.set_registry(str(repository / "kayit.csv"))
    with pytest.raises(model.ToolError) as out_error:
        manager.set_output_dir(str(repository))
    assert "git çalışma ağacının içinde" in out_error.value.tr
    assert not (repository / "kayit.csv").exists()
    assert manager.settings == model.Settings()
    assert len(manager.settings_problems()) == 3


def test_a_new_registry_has_the_exact_header(owner: Owner) -> None:
    data = owner.registry.read_bytes()
    assert data == ("\ufeff" + ";".join(issuer.REGISTRY_HEADER) + "\r\n").encode()
    with pytest.raises(model.ToolError):
        owner.manager.create_registry(str(owner.registry))
    wrong = owner.root / "wrong.csv"
    wrong.write_text("a;b\r\n", encoding="utf-8")
    with pytest.raises(model.ToolError, match="header"):
        owner.manager.set_registry(str(wrong))


def test_the_private_key_must_have_keygen_s_name(tmp_path: Path) -> None:
    manager = model.LicenseManager(tmp_path / "settings.json")
    pem = tmp_path / "key.pem"
    pem.write_text("x", encoding="utf-8")
    with pytest.raises(model.ToolError) as excinfo:
        manager.set_private_key(str(pem))
    assert excinfo.value.exit_code == issuer.EXIT_USAGE


def test_a_wrong_passphrase_is_refused(owner: Owner) -> None:
    with pytest.raises(model.ToolError, match="wrong passphrase"):
        owner.manager.test_key("not the passphrase")


# -- key generation -------------------------------------------------------------------------------


@pytest.mark.parametrize("key_id", ["test-key-9", "Arci", "ab", "a" * 33, "-arci", ""])
def test_key_ids_are_checked_live(key_id: str) -> None:
    problem = model.key_id_problem(key_id)
    assert problem is not None
    assert "^[" not in problem.tr  # people's words, not the command line's regex
    if key_id:
        assert problem.tr.startswith("3-32 karakter")
        with pytest.raises(model.ToolError) as excinfo:
            model.generate_key(key_id, Path("unused"), PASSPHRASE, PASSPHRASE)
        assert excinfo.value.tr == problem.tr


def test_generate_key_writes_the_pair_and_refuses_to_overwrite(tmp_path: Path) -> None:
    assert model.key_id_problem("arci-2026a") is None
    with pytest.raises(model.ToolError, match="differ"):
        model.generate_key("arci-2026a", tmp_path, PASSPHRASE, PASSPHRASE + "!")
    with pytest.raises(model.ToolError, match="12"):
        model.generate_key("arci-2026a", tmp_path, "short", "short")
    result = model.generate_key("arci-2026a", tmp_path, PASSPHRASE, PASSPHRASE)
    assert result.private_path.is_file()
    assert result.public_path.read_text(encoding="ascii") == result.line + "\n"
    assert result.public_sha256 == issuer.public_sha256(result.public_key)
    assert model.test_key(result.private_path, PASSPHRASE).public_key == result.public_key
    with pytest.raises(model.ToolError, match="never overwritten"):
        model.generate_key("arci-2026a", tmp_path, PASSPHRASE, PASSPHRASE)


def test_passphrase_strength_hint() -> None:
    assert model.passphrase_strength("short")[0] == 0
    assert model.passphrase_strength("twelve chars")[0] == 1
    assert model.passphrase_strength("Uzun Bir Parola 2026!")[0] == 3
    assert model.passphrase_strength("x" * 30)[0] == 4


# -- modules, limits, dates -----------------------------------------------------------------------


def test_module_rules() -> None:
    choice = model.ModuleChoice()
    assert choice.selected() == ["core_5651"]
    assert model.ModuleChoice.locked("core_5651")
    choice.set("core_5651", on=False)
    assert choice.is_on("core_5651")
    choice.set("usage_reports", on=True)
    assert choice.selected() == ["core_5651", "usage_analytics", "usage_reports"]
    choice.set("usage_analytics", on=False)
    assert choice.selected() == ["core_5651"]
    choice.set("bulk_import", on=True)
    choice.set("usage_analytics", on=True)
    choice.set("usage_reports", on=False)
    assert choice.selected() == ["core_5651", "usage_analytics", "bulk_import"]
    assert issuer.parse_modules(choice.option()) == choice.selected()
    with pytest.raises(KeyError):
        choice.set("rule_engine", on=True)
    with pytest.raises(KeyError):
        choice.is_on("rule_engine")
    assert [item.key for item in model.ModuleChoice.modules()] == [item.key for item in MODULES]


def test_limits() -> None:
    assert model.limit_value(" 25 ", False, "x") == 25
    assert model.limit_value("abc", True, "x") is None
    assert model.limit_value(str(model.MAX_LIMIT), False, "x") == model.MAX_LIMIT
    for bad in ("0", "100001", "lots", "", "-1", "1.5"):
        with pytest.raises(model.ToolError) as excinfo:
            model.limit_value(bad, False, "Kullanıcı sınırı")
        assert excinfo.value.exit_code == issuer.EXIT_USAGE
        assert excinfo.value.tr.startswith("Kullanıcı sınırı 1-100000")
        assert "Sınırsız işaretlenmeli" in excinfo.value.tr
        assert "'unlimited'" not in excinfo.value.tr
    with pytest.raises(model.ToolError) as excinfo:
        model.limit_value("0", False, "Kullanıcı sınırı", "users limit")
    assert excinfo.value.en == "users limit must be 1-100000 or tick Unlimited"
    assert model.limit_text("") == "Sınırsız"
    assert model.limit_text("7") == "7"


def test_dates() -> None:
    assert model.maintenance_for(date(2026, 10, 21)) == date(2027, 10, 20)
    assert model.maintenance_for(date(2026, 10, 21), 2) == date(2028, 10, 20)
    assert model.maintenance_for(date(2028, 2, 29)) == date(2029, 2, 27)
    assert model.add_years(date(2027, 10, 20), 1) == date(2028, 10, 20)
    assert model.IssueForm().maintenance_until == ""
    label = ("Bakım bitişi", "maintenance until")
    assert model.day_value("21.10.2027", *label) == date(2027, 10, 21)
    assert model.day_value(" 1.2.2028 ", *label) == date(2028, 2, 1)
    assert model.day_value("2027-10-21", *label) == date(2027, 10, 21)
    with pytest.raises(model.ToolError) as excinfo:
        model.day_value("30.02.2027", *label)
    assert excinfo.value.tr == "Bakım bitişi geçerli bir tarih değil."
    assert excinfo.value.en == "maintenance until is not a valid date"
    with pytest.raises(model.ToolError) as excinfo:
        model.day_value("yarın", *label)
    assert excinfo.value.tr.startswith("Bakım bitişi YYYY-AA-GG olmalı")
    assert excinfo.value.en == "maintenance until must be YYYY-MM-DD"


# -- request --------------------------------------------------------------------------------------


def test_request_decoding_and_its_errors() -> None:
    view = model.decode_request_text(request_code())
    assert view.strong
    assert view.binding == "strong"
    assert dict(view.fields())["Sunucu kimliği / Server id"] == server_id_of(WINDOWS)
    assert view.binding_message().tr.startswith("Güçlü")
    weak = model.decode_request_text(request_code(LINUX, organization=""))
    assert not weak.strong
    assert "Zayıf" in weak.binding_message().tr
    assert dict(weak.fields())["Kuruluş / Organisation"].startswith("(ayarlanmamış")
    code = request_code()
    index = len(code) // 2
    broken = code[:index] + ("Z" if code[index] != "Z" else "Y") + code[index + 1 :]
    for text, codes in (
        ("", {"request_malformed"}),
        ("ARCIREQ1-!!!", {"request_malformed"}),
        (broken, {"request_checksum", "request_malformed"}),
        (code[:-4], {"request_checksum", "request_malformed"}),
    ):
        with pytest.raises(model.LicenseError) as excinfo:
            model.decode_request_text(text)
        message = model.message_of(excinfo.value)
        assert message.code in codes
        assert message.tr
        assert message.en
    mail = "\n".join(code[i : i + 23] for i in range(0, len(code), 23)).lower()
    assert model.decode_request_text(mail).request == view.request


def test_decode_prefills_the_licensee(owner: Owner) -> None:
    form = model.IssueForm(request_text=request_code(organization="Deneme Belediyesi"))
    owner.manager.decode(form)
    assert form.licensee == "Deneme Belediyesi"
    form.licensee = "Elle yazılan"
    owner.manager.decode(form)
    assert form.licensee == "Elle yazılan"


def test_the_next_request_replaces_the_prefilled_licensee(owner: Owner) -> None:
    form = model.IssueForm(request_text=request_code(organization="Alfa A.Ş."))
    owner.manager.decode(form)
    assert form.licensee == "Alfa A.Ş."
    form.request_text = request_code(SECOND, installation=OTHER_INSTALLATION,
                                     organization="Beta Ltd.")  # fmt: skip
    owner.manager.decode(form)
    assert form.licensee == "Beta Ltd."  # not the previous customer's name
    owner.manager.forget_request()
    form.licensee = "Alfa A.Ş."
    owner.manager.decode(form)
    assert form.licensee == "Alfa A.Ş."  # after forget_request it counts as typed


# -- the issue cycle ------------------------------------------------------------------------------


def test_the_issue_cycle_verifies_and_matches_the_command_line_row(
    owner: Owner, tmp_path: Path
) -> None:
    code = request_code()
    outcome = owner.issue(owner.form(code, note="=SUM(A1)"))
    path = outcome.result.path
    assert path.parent == owner.out
    assert path.name == f"ornek-a-s-{outcome.license_id}.lic"
    document = load_license(
        path.read_bytes(), keys={TEST_KEY_ID: public_key_b64(test_private_key())}
    )
    payload = document.payload
    assert payload.modules == ("core_5651", "usage_analytics", "usage_reports")
    assert (payload.log_sources, payload.users) == (10, None)
    assert payload.maintenance_until == date(2027, 10, 20)
    assert payload.server_ids == (server_id_of(WINDOWS),)
    assert payload.request_sha256 == request_sha256(model.decode_request_text(code).request)
    assert outcome.file_sha256 == document.sha256
    assert outcome.revocations == ()

    # The same licence through the command line, into its own registry.
    cli = tmp_path / "cli"
    cli.mkdir()
    passphrase = cli / "p.txt"
    passphrase.write_text(PASSPHRASE + "\n", encoding="utf-8")
    registry = cli / "registry.csv"
    issuer.main([
        "issue", "--key", str(owner.pem), "--request", code, "--licensee", "Örnek A.Ş.",
        "--modules", "usage_reports,usage_analytics", "--max-sources", "10",
        "--max-users", "unlimited", "--maintenance-until", "2027-10-20", "--note", "=SUM(A1)",
        "--registry", str(registry), "--out", str(cli / "a.lic"),
        "--passphrase-file", str(passphrase),
    ])  # fmt: skip
    gui_bytes, cli_bytes = owner.registry.read_bytes(), registry.read_bytes()
    assert gui_bytes.count(b"\r\n") == cli_bytes.count(b"\r\n") == 2
    assert gui_bytes.split(b"\r\n")[0] == cli_bytes.split(b"\r\n")[0]
    (gui_row,), (cli_row,) = (
        owner.rows(),
        list(
            csv.DictReader(io.StringIO(registry.read_text("utf-8-sig"), newline=""), delimiter=";")
        ),
    )
    differing = {"issued_at", "license_id", "file_sha256", "payload_sha256"}
    assert {k: v for k, v in gui_row.items() if k not in differing} == {
        k: v for k, v in cli_row.items() if k not in differing
    }
    assert gui_row["note"] == "'=SUM(A1)"
    assert gui_row["license_id"] == outcome.license_id
    assert gui_row["file_sha256"] == document.sha256
    assert gui_row["payload_sha256"] == document.payload_sha256


def test_the_e_mail_text(owner: Owner) -> None:
    outcome = owner.issue(owner.form(valid_until_enabled=True, valid_until="2099-01-31"))
    mail = outcome.email
    assert outcome.license_id in mail
    assert outcome.result.path.name in mail
    assert outcome.file_sha256 in mail
    assert "Ayarlar → Lisans → Lisans dosyası" in mail
    assert "Kullanım analizi, Zamanlanmış kullanım raporları" in mail
    assert "Kullanıcı sınırı: Sınırsız" in mail
    assert "Bakım bitişi: 20.10.2027" in mail
    assert "Geçerlilik sonu: 31.01.2099" in mail
    assert "arman@khadangan.com" in mail
    assert "Bu bir deneme lisansıdır: 31.01.2099 tarihinden sonra ücretli modüller kapanır" in mail
    assert "süresizdir" not in mail
    perpetual = owner.issue(owner.form(earlier_mode="additional")).email
    assert "Lisans süresizdir" in perpetual
    assert "deneme lisansı" not in perpetual
    assert "Geçerlilik sonu" not in perpetual


def test_every_error_is_shown_before_the_passphrase(owner: Owner) -> None:
    form = owner.form(
        request_text="",
        licensee=" Örnek",
        sources="0",
        users_unlimited=False,
        users="lots",
        maintenance_until="2027-02-30",
        valid_until_enabled=True,
        valid_until="2020-01-01",
        extra_server_ids="XW68\n",
        note="iki\nsatır",
    )
    with pytest.raises(model.FormError) as excinfo:
        owner.manager.validate(form)
    messages = excinfo.value.messages
    assert len(messages) == 8, errors_of(excinfo)
    text = errors_of(excinfo)
    for needle in ("İstek kodunu", "Log kaynağı", "Kullanıcı sınırı", "Lisans sahibi", "XW68",
                   "Not", "Bakım bitişi", "Geçerlilik sonu"):  # fmt: skip
        assert needle in text, needle
    for message in messages:  # each line in one language: no "Türkçe / English" labels
        assert " / " not in message.tr, message.tr
        assert not any(letter in message.en for letter in "çğıöşüÇĞİÖŞÜ"), message.en
    assert not owner.rows()


def test_the_settings_are_checked_before_signing(tmp_path: Path) -> None:
    manager = model.LicenseManager(tmp_path / "settings.json")
    with pytest.raises(model.FormError) as excinfo:
        manager.validate(model.IssueForm(request_text=request_code(), licensee="X",
                                         maintenance_until="2027-10-20"))  # fmt: skip
    assert "Ayarlar" in errors_of(excinfo)


def test_weak_bindings_need_the_box(owner: Owner) -> None:
    code = request_code(LINUX)
    with pytest.raises(model.FormError) as excinfo:
        owner.manager.validate(owner.form(code))
    assert "Zayıf bağlamaya izin ver" in errors_of(excinfo)
    outcome = owner.issue(owner.form(code, allow_weak_binding=True))
    assert owner.rows()[0]["binding"] == "weak"
    assert outcome.result.row["platform"] == "linux"
    other = request_code(SECOND, installation=OTHER_INSTALLATION, organization="Başka")
    strong = owner.manager.validate(owner.form(other, licensee="Başka", allow_weak_binding=True))
    assert strong.binding == "strong"
    assert not strong.inputs.allow_weak_binding


def test_the_earlier_licence_choice(owner: Owner) -> None:
    first = owner.issue(owner.form())
    again = owner.form(licensee="Yeni Ad A.Ş.")
    owner.manager.decode(again)
    earlier = owner.manager.earlier(again)
    assert [row["license_id"] for row in earlier] == [first.license_id]
    with pytest.raises(model.FormError) as excinfo:
        owner.manager.validate(again)
    assert "Yenileme / değiştirme" in errors_of(excinfo)
    with pytest.raises(model.FormError, match="tick"):
        owner.manager.validate(owner.form(licensee="Yeni Ad A.Ş.", earlier_mode="supersedes"))
    with pytest.raises(model.FormError, match="not in the registry"):
        owner.manager.validate(owner.form(earlier_mode="supersedes",
                                          supersedes=["ARCI-L-20200101-AAAAAAAA"]))  # fmt: skip
    assert len(owner.rows()) == 1

    additional = owner.issue(owner.form(earlier_mode="additional", supersedes=[first.license_id]))
    assert owner.rows()[1]["supersedes"] == "additional"
    assert additional.revocations == ()
    replaced = owner.issue(owner.form(earlier_mode="supersedes", supersedes=[first.license_id]))
    assert owner.rows()[2]["supersedes"] == first.license_id
    ((license_id, line, note),) = replaced.revocations
    assert (license_id, line) == (first.license_id, f'"{first.license_id}",')
    assert "Aynı sunucuda yenileme" in note.tr  # same server: nothing to send


def test_a_server_change_says_to_send_the_revoked_line(owner: Owner) -> None:
    first = owner.issue(owner.form())
    moved = owner.form(request_code(SECOND), earlier_mode="supersedes",
                       supersedes=[first.license_id])  # fmt: skip
    outcome = owner.issue(moved)
    ((_, line, note),) = outcome.revocations
    assert line == f'"{first.license_id}",'
    assert "Sunucu değişti" in note.tr
    assert "send this line" in note.en


def test_an_existing_file_is_never_overwritten(owner: Owner) -> None:
    plan = owner.manager.validate(owner.form())
    signed = owner.manager.sign(plan, PASSPHRASE)
    target = owner.out / "var.lic"
    target.write_text("keep", encoding="utf-8")
    with pytest.raises(model.ToolError, match="choose another name"):
        owner.manager.write(signed, target)
    assert target.read_text(encoding="utf-8") == "keep"
    assert owner.rows() == []
    with pytest.raises(model.ToolError, match="wrong passphrase"):
        owner.manager.sign(plan, "not the passphrase")


def test_extra_server_ids_are_validated_and_matched(owner: Owner) -> None:
    extra = server_id_of(SECOND)
    outcome = owner.issue(owner.form(extra_server_ids=f"\n {extra} \n"))
    assert outcome.result.document.payload.server_ids == (server_id_of(WINDOWS), extra)
    other = owner.form(request_code(SECOND, installation=OTHER_INSTALLATION, organization="B"),
                       licensee="B")  # fmt: skip
    owner.manager.decode(other)
    assert [row["license_id"] for row in owner.manager.earlier(other)] == [outcome.license_id]


@pytest.mark.parametrize(
    ("licensee", "slug"),
    [("Örnek A.Ş.", "ornek-a-s"), ("İSTANBUL Ğüşöç Ltd", "istanbul-gusoc-ltd"), ("***", "lisans")],
)
def test_file_name_slug(licensee: str, slug: str) -> None:
    assert model.licensee_slug(licensee) == slug


# -- registry view and renewal --------------------------------------------------------------------


def registry_row(license_id: str, maintenance: date, **values: str) -> dict[str, str]:
    row = dict.fromkeys(issuer.REGISTRY_HEADER, "")
    row.update(
        issued_at="2026-01-05T09:30:00Z", license_id=license_id, licensee="Örnek A.Ş.",
        organization="Örnek A.Ş.", server_ids=server_id_of(WINDOWS), installation_id=INSTALLATION,
        product_version="0.1.0", platform="windows", binding="strong",
        modules="core_5651 usage_analytics", max_sources="10", max_users="",
        maintenance_until=maintenance.isoformat(), key_id=TEST_KEY_ID,
    )  # fmt: skip
    row.update(values)
    return row


def test_registry_highlights_filter_and_sort() -> None:
    today = date(2026, 10, 21)
    rows = [
        registry_row("ARCI-L-20260105-AAAAAAAA", today + timedelta(days=10), max_sources="200"),
        registry_row("ARCI-L-20250105-BBBBBBBB", today - timedelta(days=1), licensee="Eski Kurum"),
        registry_row(
            "ARCI-L-20260106-CCCCCCCC",
            today + timedelta(days=31),
            licensee="Başka",
            max_sources="",
            supersedes="ARCI-L-20250105-BBBBBBBB",
        ),
        registry_row(
            "ARCI-L-20260107-DDDDDDDD",
            today + timedelta(days=30),
            licensee="Sınırda",
            max_sources="9",
            supersedes="additional",
        ),
    ]
    views = model.registry_views(rows, today)
    # The replaced row is grey italics, not "ended" (nor orange while its maintenance runs).
    assert [view.highlight for view in views] == ["soon", "replaced", "", "soon"]
    assert views[1].replaced_by == ("ARCI-L-20260106-CCCCCCCC",)
    columns = [name for name, _, _ in model.REGISTRY_COLUMNS]
    assert dict(zip(columns, views[1].values(), strict=True))["replaced_by"] == (
        "ARCI-L-20260106-CCCCCCCC"
    )
    assert dict(zip(columns, views[0].values(), strict=True))["replaced_by"] == ""
    assert {name: tr for name, tr, _ in model.REGISTRY_COLUMNS}["issued_at"] == "Tarih (UTC)"
    renewed_soon = model.registry_views(
        [rows[0], registry_row("ARCI-L-20260108-EEEEEEEE", today + timedelta(days=400),
                               supersedes=rows[0]["license_id"])], today
    )  # fmt: skip
    assert renewed_soon[0].highlight == "replaced"
    assert not renewed_soon[0].due
    due = model.filter_views(views, due_only=True)
    assert [view.license_id for view in due] == [
        "ARCI-L-20260105-AAAAAAAA",
        "ARCI-L-20260107-DDDDDDDD",
    ]
    assert [v.license_id for v in model.filter_views(views, "eski")] == ["ARCI-L-20250105-BBBBBBBB"]
    assert model.filter_views(views, "kullanım analizi sınırda")[0].license_id.endswith("DDDDDDDD")
    values = dict(
        zip([name for name, _, _ in model.REGISTRY_COLUMNS], views[2].values(), strict=True)
    )
    assert values["limits"] == "Sınırsız / Sınırsız"
    assert values["modules"] == "5651 Temel, Kullanım analizi"
    assert values["issued_at"] == "2026-01-05 09:30"
    assert values["binding"] == "güçlü"
    assert (
        dict(zip([n for n, _, _ in model.REGISTRY_COLUMNS], views[3].values(), strict=True))[
            "supersedes"
        ]
        == "ek sunucu"
    )
    by_sources = model.sort_views(views, "limits")
    assert [v.row["max_sources"] for v in by_sources] == ["9", "10", "200", ""]
    by_licensee = model.sort_views(views, "licensee", descending=True)
    assert by_licensee[0].row["licensee"] == "Örnek A.Ş."
    assert model.sort_views(views, "maintenance_until")[0].highlight == "replaced"


def test_renewal_prefill_from_a_registry_row(owner: Owner) -> None:
    extra = server_id_of(SECOND)
    first = owner.issue(owner.form(sources_unlimited=True, users_unlimited=False, users="25",
                                   extra_server_ids=extra))  # fmt: skip
    (row,) = owner.rows()
    form = owner.manager.renewal_form(row)
    assert form.licensee == "Örnek A.Ş."
    assert form.modules.selected() == ["core_5651", "usage_analytics", "usage_reports"]
    assert (form.sources_unlimited, form.users, form.users_unlimited) == (True, "25", False)
    assert form.maintenance_until == "2028-10-20"
    assert form.extra_server_ids == extra  # the second server keeps its licence
    assert (form.earlier_mode, form.supersedes) == ("supersedes", [first.license_id])
    assert form.renewal_of == first.license_id
    assert form.request_text == ""
    with pytest.raises(model.FormError, match="request code"):
        owner.manager.validate(form)
    form.request_text = request_code()
    owner.manager.decode(form)
    assert [r["license_id"] for r in owner.manager.earlier(form)] == [first.license_id]
    renewed = owner.issue(form)
    assert renewed.result.document.payload.maintenance_until == date(2028, 10, 20)
    assert renewed.result.document.payload.server_ids == (server_id_of(WINDOWS), extra)
    assert owner.rows()[1]["supersedes"] == first.license_id
    views = owner.manager.registry_views()
    assert views[0].replaced_by == (renewed.license_id,)


def test_a_renewal_after_maintenance_ended_starts_today(owner: Owner) -> None:
    today = owner.manager.today
    row = registry_row("ARCI-L-20250105-BBBBBBBB", today - timedelta(days=400),
                       server_ids=f"{server_id_of(WINDOWS)} {server_id_of(SECOND)}")  # fmt: skip
    form = owner.manager.renewal_form(row)
    assert form.maintenance_until == model.maintenance_for(today).isoformat()
    assert form.extra_server_ids == server_id_of(SECOND)
    ending = registry_row("ARCI-L-20250105-CCCCCCCC", today)
    assert owner.manager.renewal_form(ending).maintenance_until == (
        model.add_years(today, 1).isoformat()
    )


def test_export_copies_the_registry_unchanged(owner: Owner, tmp_path: Path) -> None:
    owner.issue(owner.form())
    before = owner.registry.read_bytes()
    target = tmp_path / "muhasebe.csv"
    assert owner.manager.export_registry(target) == target
    assert target.read_bytes() == before == owner.registry.read_bytes()
    with pytest.raises(model.ToolError):
        owner.manager.export_registry(owner.registry)


def test_export_never_overwrites_a_file(owner: Owner, tmp_path: Path) -> None:
    old = tmp_path / "gecen-ay.csv"
    old.write_text("keep", encoding="utf-8")
    with pytest.raises(model.ToolError, match="choose another name"):
        owner.manager.export_registry(old)
    assert old.read_text(encoding="utf-8") == "keep"
    key = owner.pem.read_bytes()
    with pytest.raises(model.ToolError, match="private key"):
        owner.manager.export_registry(owner.pem)
    with pytest.raises(model.ToolError, match="private key"):
        owner.manager.export_registry(tmp_path / "yedek.private.pem")
    assert owner.pem.read_bytes() == key
    assert not (tmp_path / "yedek.private.pem").exists()


# -- verify ---------------------------------------------------------------------------------------


def test_verify_results(owner: Owner) -> None:
    outcome = owner.issue(owner.form(valid_until_enabled=True, valid_until="2099-01-31"))
    path = outcome.result.path
    public = public_key_b64(test_private_key())
    ok = owner.manager.verify(path, mode="public", public_key=public,
                              server_id=server_id_of(WINDOWS), at="2027-10-20")  # fmt: skip
    assert ok.ok, ok.message
    assert "server" in ok.message.en
    assert ok.message.tr == (
        f"Lisans geçerli. Denetlenen: imza, iptal listesi, sunucu {server_id_of(WINDOWS)}, "
        "geçerlilik sonu ve bakım (20.10.2027)"
    )
    assert owner.manager.verify(path, mode="public", public_key=public, at="20.10.2027").ok
    empty = owner.manager.verify(Path(), mode="committed")
    assert empty.message.tr == "Önce bir lisans dosyası seçin."
    with pytest.raises(model.ToolError, match="choose a licence file"):
        owner.manager.inspect(Path())
    assert dict(ok.details)["Lisans kimliği / Licence id"] == outcome.license_id
    assert owner.manager.verify(path, mode="private", passphrase=PASSPHRASE).ok
    wrong = owner.manager.verify(path, mode="private", passphrase="not the passphrase")
    assert not wrong.ok
    assert wrong.message.code is None
    assert "parola yanlış" in wrong.message.tr
    committed = owner.manager.verify(path, mode="committed")
    assert (committed.ok, committed.message.code) == (False, "license_unknown_key")
    other = owner.manager.verify(path, mode="public", public_key=public,
                                 server_id=server_id_of(SECOND))  # fmt: skip
    assert other.message.code == "license_other_server"
    assert other.message.tr.startswith("Lisans bu sunucu için düzenlenmemiş")
    late = owner.manager.verify(path, mode="public", public_key=public, at="2027-10-21")
    assert late.message.code == "license_version_not_covered"
    tampered = path.with_name("tampered.lic")
    tampered.write_bytes(tamper(path.read_bytes(), "limits.users", 100_000))
    bad = owner.manager.verify(tampered, mode="public", public_key=public)
    assert bad.message.code == "license_signature_invalid"
    assert not owner.manager.verify(path, mode="public", public_key="short").ok
    assert not owner.manager.verify(path, mode="public", public_key=public, at="2027-13-01").ok
    assert not owner.manager.verify(path, mode="public", public_key=public, server_id="XW68").ok
    assert not owner.manager.verify(owner.root / "missing.lic", mode="committed").ok


def test_inspect_shows_the_content(owner: Owner) -> None:
    outcome = owner.issue(owner.form())
    details = dict(owner.manager.inspect(outcome.result.path))
    assert details["license_id"] == outcome.license_id
    assert details["Dosya SHA-256 / File SHA-256"] == outcome.file_sha256
    assert details["İmza / Signature"] == f"Ed25519 {TEST_KEY_ID}"
    garbage = owner.root / "garbage.lic"
    garbage.write_bytes(b"\xff\x00")
    with pytest.raises(model.ToolError):
        owner.manager.inspect(garbage)


def test_messages_and_about(tmp_path: Path) -> None:
    error = model.message_of(model.ToolError("tr", "en"))
    assert error.text() == "tr\n\nen"
    os_error = model.message_of(OSError("disk"))
    assert "disk" in os_error.tr
    locked = model.message_of(PermissionError(13, "Permission denied", "E:\\kayit.csv"))
    assert "Excel'de açıksa kapatın" in locked.tr
    assert "kayit.csv" in locked.tr
    assert "close it in Excel" in locked.en
    about = model.about_text(None)
    assert "arman@khadangan.com" in about.tr
    assert "ağ bağlantısı" in about.tr
    assert "no network" in about.en
    assert model.owner_guide(TOOLS / "license_manager_model.py") == TOOLS / "README.md"


# -- the reviews' cases ---------------------------------------------------------------------------


def test_a_moved_registry_is_never_recreated(owner: Owner) -> None:
    owner.issue(owner.form())
    with pytest.raises(model.FormError):  # a second licence needs a reason
        owner.manager.validate(owner.form())
    moved = owner.registry.with_name("tasindi.csv")
    owner.registry.rename(moved)
    with pytest.raises(model.FormError) as excinfo:
        owner.manager.validate(owner.form())
    assert "Kayıt defteri bulunamadı ya da boş" in errors_of(excinfo)
    assert not owner.registry.exists()
    problems = owner.manager.settings_problems()
    assert [problem.en.split(":")[0] for problem in problems] == ["registry not found or empty"]
    owner.registry.write_bytes(b"")  # an empty file is no registry either
    with pytest.raises(model.FormError, match="registry not found or empty"):
        owner.manager.validate(owner.form())
    owner.registry.unlink()

    # Moved between the checks and the write: nothing is written.
    moved.rename(owner.registry)
    plan = owner.manager.validate(owner.form(earlier_mode="additional"))
    signed = owner.manager.sign(plan, PASSPHRASE)
    owner.registry.rename(moved)
    target = owner.manager.suggested_path(signed)
    with pytest.raises(model.ToolError, match="registry not found or empty"):
        owner.manager.write(signed, target)
    assert not target.exists()
    assert not owner.registry.exists()
    assert len(list(owner.out.iterdir())) == 1


def test_a_ticked_licence_of_another_customer_is_refused(owner: Owner) -> None:
    alfa = owner.issue(owner.form(licensee="Alfa A.Ş."))
    beta = owner.form(request_code(SECOND, installation=OTHER_INSTALLATION, organization="Beta"),
                      licensee="Beta Ltd.", earlier_mode="supersedes",
                      supersedes=[alfa.license_id])  # fmt: skip
    owner.manager.decode(beta)
    assert owner.manager.earlier(beta) == []  # a tick is not carried over to another request
    with pytest.raises(model.FormError) as excinfo:
        owner.manager.validate(beta)
    assert "İşaretli lisans bu istekle ilgili değil" in errors_of(excinfo)
    assert alfa.license_id in errors_of(excinfo)
    assert len(owner.rows()) == 1
    beta.renewal_of = alfa.license_id  # "Yenile" on purpose: kept for any request
    assert [row["license_id"] for row in owner.manager.earlier(beta)] == [alfa.license_id]
    owner.manager.forget_request()
    assert [row["license_id"] for row in owner.manager.earlier(beta)] == [alfa.license_id]


def test_a_bad_registry_id_is_a_form_error(owner: Owner) -> None:
    with pytest.raises(model.FormError, match="invalid licence id"):
        owner.manager.validate(owner.form(earlier_mode="supersedes", supersedes=["hand-edited"]))


def test_a_file_error_is_a_form_error(owner: Owner, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(path: Path) -> object:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(issuer, "read_registry", unreadable)
    with pytest.raises(model.FormError) as excinfo:
        owner.manager.validate(owner.form())
    assert "Excel'de açıksa kapatın" in errors_of(excinfo)


def test_request_files_in_utf8_and_notepad_unicode(tmp_path: Path) -> None:
    code = request_code()
    utf8 = tmp_path / "istek.txt"
    utf8.write_text(code + "\n", encoding="utf-8")
    assert model.load_request_file(utf8).strip() == code
    unicode = tmp_path / "istek-unicode.txt"
    unicode.write_bytes(b"\xff\xfe" + (code + "\r\n").encode("utf-16-le"))
    assert model.load_request_file(unicode).strip() == code
    garbage = tmp_path / "istek.bin"
    garbage.write_bytes(b"\xff\x00\xc3")
    with pytest.raises(model.ToolError) as excinfo:
        model.load_request_file(garbage)
    assert "UTF-8 bekleniyor" in excinfo.value.tr
    with pytest.raises(model.ToolError):
        model.load_request_file(tmp_path / "yok.txt")


@pytest.mark.parametrize(
    ("system", "iso_left_tab"), [("x11", True), ("win32", False), ("aqua", False)]
)
def test_shift_tab_keysym_is_bound_on_x11_only(system: str, iso_left_tab: bool) -> None:
    """Tk on Windows rejects the X11 keysym ``ISO_Left_Tab`` (TclError, seen in the Windows zip
    self-test); text boxes still move the focus with Tab and Shift+Tab everywhere."""
    pytest.importorskip("tkinter", reason="this Python has no tkinter")
    import arcilogger_license_gui as gui

    bound: list[str] = []

    class FakeTk:
        def call(self, *args: str) -> str:
            assert args == ("tk", "windowingsystem")
            return system

    class FakeText:
        tk = FakeTk()

        def bind(self, sequence: str, func: object) -> None:
            bound.append(sequence)

    gui.focus_moves_on_tab(FakeText())  # type: ignore[arg-type]
    assert bound[:2] == ["<Tab>", "<Shift-Tab>"]
    assert ("<ISO_Left_Tab>" in bound) is iso_left_tab
