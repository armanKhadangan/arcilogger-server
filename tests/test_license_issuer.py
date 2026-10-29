"""The licence issuer ``tools/license-issuer`` (licensing contract section 10; test A-T16).

Every command runs as a subprocess with this interpreter (as the owner runs it); passphrases
come from files in ``tmp_path``. The key pairs are made by the tests: no key is committed.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path
from types import MappingProxyType, ModuleType, SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from arcilogger_core.license import keys, revoked
from arcilogger_core.license.document import LICENSE_ID_PATTERN, load_license
from arcilogger_core.license.fingerprint import ServerIdentity, server_id_of
from arcilogger_core.license.request import make_request_code, new_request, request_sha256
from arcilogger_core.license.state import check_applicable
from arcilogger_core.license.testing import public_key_b64, tamper, test_private_key
from arcilogger_core.license.trusted_time import TrustedTime

pytestmark = pytest.mark.license_live

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "license-issuer" / "arcilogger_license_issuer.py"
KEY_ID = "arci-test1"
PASSPHRASE = "a long offline passphrase"
HEADER = (
    "issued_at;license_id;licensee;organization;server_ids;installation_id;product_version;"
    "platform;binding;modules;max_sources;max_users;maintenance_until;valid_until;key_id;"
    "file_sha256;payload_sha256;request_sha256;supersedes;note"
)

WINDOWS = ServerIdentity(
    "windows", "0f8fad5b-d9cb-469f-a165-70867728950e", "6F9619FF-8B86-D011-B42D-00C04FC964FF"
)
SECOND = ServerIdentity(
    "windows", "11111111-2222-4333-8444-555555555555", "4C4C4544-0042-3510-8058-B3C04F4B3732"
)
LINUX = ServerIdentity("linux", "4c4c4544004c4b108039b3c04f4b3732", "")
NO_SMBIOS = ServerIdentity("windows", "22222222-3333-4444-8555-666666666666", "")
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


def run(*args: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
        cwd=cwd,
    )


def output(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stdout + completed.stderr


@dataclass
class Owner:
    """The owner's offline folder: key pair, passphrase file, registry."""

    root: Path

    @property
    def keys(self) -> Path:
        return self.root / "keys"

    @property
    def pem(self) -> Path:
        return self.keys / f"{KEY_ID}.private.pem"

    @property
    def public_line(self) -> Path:
        return self.keys / f"{KEY_ID}.public.txt"

    @property
    def passphrase(self) -> Path:
        return self.root / "passphrase.txt"

    @property
    def registry(self) -> Path:
        return self.root / "registry.csv"

    def public_key(self) -> str:
        match = re.fullmatch(
            r'"([a-z0-9-]+)": "([A-Za-z0-9_-]{43})",\n', self.public_line.read_text()
        )
        assert match is not None
        assert match.group(1) == KEY_ID
        return match.group(2)

    def private_key(self) -> Ed25519PrivateKey:
        key = load_pem_private_key(self.pem.read_bytes(), password=PASSPHRASE.encode())
        assert isinstance(key, Ed25519PrivateKey)
        return key

    def issue(
        self, out: str = "a.lic", *extra: str | Path, code: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        defaults: dict[str, str] = {
            "--licensee": "Örnek A.Ş.",
            "--modules": "usage_analytics,usage_reports",
            "--max-sources": "10",
            "--max-users": "unlimited",
            "--maintenance-until": "2027-10-20",
        }
        given = {str(item) for item in extra}
        args: list[str | Path] = ["issue", "--key", self.pem, "--request", code or request_code()]
        for option, value in defaults.items():
            if option not in given:
                args += [option, value]
        args += ["--registry", self.registry, "--out", self.root / out]
        args += ["--passphrase-file", self.passphrase, *extra]
        return run(*args)

    def rows(self) -> list[dict[str, str]]:
        text = self.registry.read_text(encoding="utf-8-sig")
        return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=";"))


@pytest.fixture
def owner(tmp_path: Path) -> Owner:
    folder = Owner(tmp_path / "offline")
    folder.root.mkdir()
    folder.passphrase.write_text(PASSPHRASE + "\n", encoding="utf-8")
    completed = run(
        "keygen", "--key-id", KEY_ID, "--out", folder.keys, "--passphrase-file", folder.passphrase
    )
    assert completed.returncode == 0, output(completed)
    return folder


@pytest.fixture
def committed_key(owner: Owner, monkeypatch: pytest.MonkeyPatch) -> None:
    """The generated public key as if the owner had committed it in keys.py."""
    monkeypatch.setattr(keys, "TRUSTED_KEYS", MappingProxyType({KEY_ID: owner.public_key()}))


# -- keygen ---------------------------------------------------------------------------------------


def test_keygen_writes_an_encrypted_key_and_the_public_line(owner: Owner) -> None:
    public = owner.public_key()
    key = owner.private_key()
    raw = key.public_key().public_bytes_raw()
    from arcilogger_core.license.codec import b64url_encode

    assert b64url_encode(raw) == public
    # Split as in core/tests/test_license_sources.py, whose guard finds no key header in the repo.
    header = b"-----BEGIN ENCRYPTED " + b"PRIVATE" + b" KEY-----\n"
    assert owner.pem.read_bytes().startswith(header)
    with pytest.raises(TypeError):
        load_pem_private_key(owner.pem.read_bytes(), password=None)  # encrypted
    # The same bytes opened above with the right passphrase; a wrong one never opens them. Its
    # error text depends on keygen's random salt and IV: CBC decryption with the wrong key fails
    # its padding check, but about once in 256 the garbage ends in valid padding and the PKCS#8
    # parser refuses it instead ("Could not deserialize key data").
    with pytest.raises(ValueError, match=r"(?i)password|decrypt|deserialize"):
        load_pem_private_key(owner.pem.read_bytes(), password=b"wrong passphrase!")
    if os.name == "posix":
        assert owner.pem.stat().st_mode & 0o777 == 0o600


def test_keygen_prints_the_public_key_and_its_hash(tmp_path: Path) -> None:
    passphrase = tmp_path / "p.txt"
    passphrase.write_text(PASSPHRASE, encoding="utf-8")
    completed = run(
        "keygen", "--key-id", "arci-2026a", "--out", tmp_path / "k", "--passphrase-file", passphrase
    )
    assert completed.returncode == 0, output(completed)
    line = (tmp_path / "k" / "arci-2026a.public.txt").read_text()
    public = line.split('"')[3]
    from arcilogger_core.license.codec import b64url_decode_strict

    digest = hashlib.sha256(b64url_decode_strict(public, 32)).hexdigest()
    assert public in completed.stdout
    assert digest in completed.stdout
    assert "keys.py" in completed.stdout


def readme_keygen_lines(public: str) -> list[str]:
    """The ``verify:`` line of keygen as the owner guide shows it (Turkish, then English part)."""
    text = (ROOT / "tools" / "license-issuer" / "README.md").read_text(encoding="utf-8")
    shown = re.findall(r"`(verify: --public-key=<43 (?:karakter|characters)>)`", text)
    return [re.sub(r"<43 \w+>", public, line) for line in shown]


def test_keygen_prints_the_verify_option_for_a_key_that_starts_with_a_dash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # About one key in 64 starts with "-"; argparse takes "--public-key -…" for a missing value.
    key = test_private_key(b"arcilogger test key 18")
    public = public_key_b64(key)
    assert public.startswith("-")
    issuer = issuer_module()
    monkeypatch.setattr(issuer, "Ed25519PrivateKey", SimpleNamespace(generate=lambda: key))
    owner = Owner(tmp_path / "offline")
    owner.root.mkdir()
    owner.passphrase.write_text(PASSPHRASE + "\n", encoding="utf-8")
    args = ["keygen", "--key-id", KEY_ID, "--out", str(owner.keys)]
    assert issuer.main([*args, "--passphrase-file", str(owner.passphrase)]) == 0
    assert owner.public_key() == public
    printed = [line.strip() for line in capsys.readouterr().out.splitlines()]
    line = f"verify: --public-key={public}"
    assert line in printed
    assert readme_keygen_lines(public) == [line, line]
    completed = owner.issue("dash.lic", "--valid-until", "2099-01-31")
    assert completed.returncode == 0, output(completed)
    option = line.removeprefix("verify: ")
    completed = run("verify", owner.root / "dash.lic", option)
    assert completed.returncode == 0, output(completed)
    assert "geçerli" in completed.stdout
    assert run("verify", owner.root / "dash.lic", "--public-key", public).returncode == 2


def test_keygen_refuses_existing_files(owner: Owner) -> None:
    before = owner.pem.read_bytes()
    completed = run(
        "keygen", "--key-id", KEY_ID, "--out", owner.keys, "--passphrase-file", owner.passphrase
    )
    assert completed.returncode == 1, output(completed)
    assert owner.pem.read_bytes() == before


def test_keygen_refuses_a_folder_inside_the_repository(tmp_path: Path) -> None:
    passphrase = tmp_path / "p.txt"
    passphrase.write_text(PASSPHRASE, encoding="utf-8")
    target = ROOT / "core" / "keys-must-not-be-here"
    completed = run(
        "keygen", "--key-id", "arci-2026a", "--out", target, "--passphrase-file", passphrase
    )
    assert completed.returncode == 1, output(completed)
    assert "git" in output(completed)
    assert not target.exists()


@pytest.mark.parametrize("key_id", ["test-key-9", "Arci", "ab", "a" * 33, "-arci"])
def test_keygen_refuses_bad_key_ids(tmp_path: Path, key_id: str) -> None:
    passphrase = tmp_path / "p.txt"
    passphrase.write_text(PASSPHRASE, encoding="utf-8")
    completed = run(
        "keygen", "--key-id", key_id, "--out", tmp_path / "k", "--passphrase-file", passphrase
    )
    assert completed.returncode == 2, output(completed)
    assert not (tmp_path / "k").exists()


def test_keygen_wants_a_passphrase_of_twelve_characters(tmp_path: Path) -> None:
    passphrase = tmp_path / "p.txt"
    passphrase.write_text("eleven char\n", encoding="utf-8")
    completed = run(
        "keygen", "--key-id", "arci-2026a", "--out", tmp_path / "k", "--passphrase-file", passphrase
    )
    assert completed.returncode == 2, output(completed)
    assert "12" in output(completed)


# -- issue ----------------------------------------------------------------------------------------


def test_issue_from_a_core_request_code_verifies_in_core(owner: Owner, committed_key: None) -> None:
    code = request_code()
    completed = owner.issue("a.lic", code=code)
    assert completed.returncode == 0, output(completed)
    data = (owner.root / "a.lic").read_bytes()
    document = load_license(data)  # the committed (swapped) key set, as the product does
    payload = document.payload
    req = new_request(
        WINDOWS, installation_id=INSTALLATION, organization="Örnek A.Ş.", now=datetime.now(UTC)
    )
    assert payload.server_ids == (server_id_of(WINDOWS),)
    assert payload.installation_id == INSTALLATION
    assert payload.request_sha256 == request_sha256(req)
    assert payload.modules == ("core_5651", "usage_analytics", "usage_reports")
    assert (payload.log_sources, payload.users) == (10, None)
    assert payload.maintenance_until == date(2027, 10, 20)
    assert payload.valid_until is None
    assert payload.licensee == "Örnek A.Ş."
    assert LICENSE_ID_PATTERN.fullmatch(payload.license_id)
    assert payload.license_id.startswith(f"ARCI-L-{payload.issued_at.strftime('%Y%m%d')}-")
    assert document.key_id == KEY_ID
    assert data == (json.dumps(json.loads(data), ensure_ascii=False, indent=2) + "\n").encode()
    trusted = TrustedTime(
        now=payload.issued_at,
        system=payload.issued_at,
        tsa=None,
        tsa_day=None,
        vault_day_end=None,
        release_floor=payload.issued_at,
        license_issued=None,
    )
    check_applicable(
        document,
        server_id=server_id_of(WINDOWS),
        server_id_error=None,
        trusted=trusted,
        release_date=date(2027, 10, 20),
    )
    text = output(completed)
    assert document.sha256 in text
    assert payload.license_id in text
    assert "smbios" in text
    assert "platform" in text


def test_the_registry_gets_one_row_per_licence_and_one_header(owner: Owner) -> None:
    assert owner.issue("a.lic").returncode == 0
    first = owner.registry.read_bytes()
    assert first.startswith(b"\xef\xbb\xbf" + HEADER.encode() + b"\r\n")
    second = owner.issue(
        "b.lic",
        "--licensee",
        "Başka Kurum",
        "--note",
        "=cmd|' /C calc'!A0",
        code=request_code(SECOND, installation=OTHER_INSTALLATION, organization="Başka Kurum"),
    )
    assert second.returncode == 0, output(second)
    data = owner.registry.read_bytes()
    assert data.startswith(first)  # nothing is ever rewritten
    assert data.count(HEADER.encode()) == 1
    assert data.count(b"\r\n") == 3
    assert b"\n" not in data.replace(b"\r\n", b"")
    rows = owner.rows()
    assert len(rows) == 2
    one, two = rows
    a = load_license((owner.root / "a.lic").read_bytes(), keys={KEY_ID: owner.public_key()})
    assert one["license_id"] == a.payload.license_id
    assert one["licensee"] == "Örnek A.Ş."
    assert one["organization"] == "Örnek A.Ş."
    assert one["server_ids"] == server_id_of(WINDOWS)
    assert one["installation_id"] == INSTALLATION
    assert one["platform"] == "windows"
    assert one["binding"] == "strong"
    assert one["modules"] == "core_5651 usage_analytics usage_reports"
    assert (one["max_sources"], one["max_users"]) == ("10", "")
    assert (one["maintenance_until"], one["valid_until"]) == ("2027-10-20", "")
    assert one["key_id"] == KEY_ID
    assert one["file_sha256"] == a.sha256
    assert one["payload_sha256"] == a.payload_sha256
    assert one["request_sha256"] == a.payload.request_sha256
    assert one["supersedes"] == ""
    assert one["product_version"] == "0.1.0"
    assert one["issued_at"].endswith("Z")
    assert two["licensee"] == "Başka Kurum"
    assert two["note"] == "'=cmd|' /C calc'!A0"  # a formula is never a formula in Excel


def test_the_registry_header_must_match(owner: Owner) -> None:
    owner.registry.write_text("﻿issued_at;license_id\r\n", encoding="utf-8")
    completed = owner.issue("a.lic")
    assert completed.returncode == 1, output(completed)
    assert not (owner.root / "a.lic").exists()


def test_the_registry_must_be_outside_the_repository(owner: Owner) -> None:
    target = ROOT / "core" / "registry-must-not-be-here.csv"
    completed = run(
        "issue", "--key", owner.pem, "--request", request_code(), "--licensee", "X",
        "--modules", "", "--max-sources", "1", "--max-users", "1",
        "--maintenance-until", "2027-10-20", "--registry", target, "--out", owner.root / "x.lic",
        "--passphrase-file", owner.passphrase,
    )  # fmt: skip
    assert completed.returncode == 1, output(completed)
    assert not target.exists()
    assert not (owner.root / "x.lic").exists()


def test_a_wrong_passphrase_is_refused(owner: Owner, tmp_path: Path) -> None:
    wrong = tmp_path / "wrong.txt"
    wrong.write_text("not the right passphrase\n", encoding="utf-8")
    completed = owner.issue("a.lic", "--passphrase-file", wrong)
    assert completed.returncode == 1, output(completed)
    assert not (owner.root / "a.lic").exists()
    assert not owner.registry.exists()


@pytest.mark.parametrize(
    ("modules", "message"),
    [("usage_analytics,rule_engine", "rule_engine"), ("usage_reports", "usage_analytics")],
)
def test_unknown_modules_and_missing_requires_are_usage_errors(
    owner: Owner, modules: str, message: str
) -> None:
    completed = owner.issue("a.lic", "--modules", modules)
    assert completed.returncode == 2, output(completed)
    assert message in output(completed)
    assert not owner.registry.exists()


@pytest.mark.parametrize(
    "extra",
    [
        ("--max-users", "0"),
        ("--max-users", "100001"),
        ("--max-sources", "lots"),
        ("--maintenance-until", "2027-02-30"),
        ("--maintenance-until", "20271020"),
        ("--valid-until", "2020-01-01"),
        ("--licensee", " Örnek"),
        ("--licensee", ""),
        ("--extra-server-id", "XW68"),
        ("--supersedes", "ARCI-L-1"),
    ],
)
def test_bad_values_are_usage_errors(owner: Owner, extra: tuple[str, str]) -> None:
    completed = owner.issue("a.lic", *extra)
    assert completed.returncode == 2, output(completed)
    assert not owner.registry.exists()


def test_an_existing_licence_file_is_not_overwritten(owner: Owner) -> None:
    (owner.root / "a.lic").write_text("keep", encoding="utf-8")
    completed = owner.issue("a.lic")
    assert completed.returncode == 1, output(completed)
    assert (owner.root / "a.lic").read_text(encoding="utf-8") == "keep"
    assert not owner.registry.exists()
    forced = owner.issue("a.lic", "--force")
    assert forced.returncode == 0, output(forced)
    assert (owner.root / "a.lic").read_text(encoding="utf-8") != "keep"


@pytest.mark.parametrize("identity", [LINUX, NO_SMBIOS], ids=["linux", "no smbios"])
def test_weak_bindings_need_the_flag(owner: Owner, identity: ServerIdentity) -> None:
    code = request_code(identity)
    refused = owner.issue("a.lic", code=code)
    assert refused.returncode == 1, output(refused)
    assert "--allow-weak-binding" in output(refused)
    assert not owner.registry.exists()
    issued = owner.issue("a.lic", "--allow-weak-binding", code=code)
    assert issued.returncode == 0, output(issued)
    (row,) = owner.rows()
    assert row["binding"] == "weak"
    assert row["platform"] == identity.platform


def test_a_strong_request_with_the_flag_is_still_strong(owner: Owner) -> None:
    assert owner.issue("a.lic", "--allow-weak-binding").returncode == 0
    assert owner.rows()[0]["binding"] == "strong"


def test_a_second_licence_for_the_same_server_needs_a_reason(owner: Owner) -> None:
    assert owner.issue("a.lic").returncode == 0
    (first,) = owner.rows()
    again = owner.issue("b.lic", "--licensee", "Yeni Ad A.Ş.")
    assert again.returncode == 1, output(again)
    assert first["license_id"] in output(again)
    assert "--supersedes" in output(again)
    assert not (owner.root / "b.lic").exists()
    assert len(owner.rows()) == 1

    replaced = owner.issue("b.lic", "--supersedes", first["license_id"])
    assert replaced.returncode == 0, output(replaced)
    assert f'"{first["license_id"]}",' in replaced.stdout  # the line for revoked.py
    assert owner.rows()[1]["supersedes"] == first["license_id"]


@pytest.mark.parametrize(
    ("identity", "installation", "licensee", "organization"),
    [
        (SECOND, INSTALLATION, "Başka", "Başka"),  # same installation id
        (SECOND, OTHER_INSTALLATION, "örnek a.ş.", "Başka"),  # same licensee
        (SECOND, OTHER_INSTALLATION, "Başka", "Örnek A.Ş."),  # same organisation
    ],
)
def test_earlier_licences_are_found_by_customer_and_installation(
    owner: Owner, identity: ServerIdentity, installation: str, licensee: str, organization: str
) -> None:
    assert owner.issue("a.lic").returncode == 0
    code = request_code(identity, installation=installation, organization=organization)
    completed = owner.issue("b.lic", "--licensee", licensee, code=code)
    assert completed.returncode == 1, output(completed)
    assert "Önceki lisanslar" in output(completed)
    additional = owner.issue("b.lic", "--licensee", licensee, "--additional-server", code=code)
    assert additional.returncode == 0, output(additional)
    assert owner.rows()[1]["supersedes"] == "additional"


def test_an_extra_server_id_is_matched_too(owner: Owner) -> None:
    extra = server_id_of(SECOND)
    assert owner.issue("a.lic", "--extra-server-id", extra).returncode == 0
    assert owner.rows()[0]["server_ids"] == f"{server_id_of(WINDOWS)} {extra}"
    code = request_code(SECOND, installation=OTHER_INSTALLATION, organization="Başka")
    assert owner.issue("b.lic", "--licensee", "Başka", code=code).returncode == 1


def test_supersedes_must_name_a_registered_licence(owner: Owner) -> None:
    assert owner.issue("a.lic").returncode == 0
    completed = owner.issue("b.lic", "--supersedes", "ARCI-L-20200101-AAAAAAAA")
    assert completed.returncode == 1, output(completed)
    assert "ARCI-L-20200101-AAAAAAAA" in output(completed)


def test_a_mistyped_request_code_is_refused(owner: Owner) -> None:
    code = request_code()
    index = len(code) // 2
    broken = code[:index] + ("Z" if code[index] != "Z" else "Y") + code[index + 1 :]
    completed = owner.issue("a.lic", code=broken)
    assert completed.returncode == 1, output(completed)
    assert "request_checksum" in output(completed) or "request_malformed" in output(completed)


def test_a_request_file_from_an_e_mail(owner: Owner) -> None:
    code = request_code()
    mail = owner.root / "mail.txt"
    mail.write_text("\n".join(code[i : i + 23] for i in range(0, len(code), 23)).lower(), "utf-8")
    completed = run(
        "issue", "--key", owner.pem, "--request-file", mail, "--licensee", "Örnek A.Ş.",
        "--modules", "bulk_import", "--max-sources", "unlimited", "--max-users", "3",
        "--maintenance-until", "2027-10-20", "--valid-until", "2099-01-31",
        "--registry", owner.registry, "--out", owner.root / "m.lic",
        "--passphrase-file", owner.passphrase,
    )  # fmt: skip
    assert completed.returncode == 0, output(completed)
    document = load_license((owner.root / "m.lic").read_bytes(), keys={KEY_ID: owner.public_key()})
    assert document.payload.modules == ("core_5651", "bulk_import")
    assert document.payload.valid_until == date(2099, 1, 31)
    assert (document.payload.log_sources, document.payload.users) == (None, 3)


# -- inspect and verify ---------------------------------------------------------------------------


@pytest.fixture
def issued(owner: Owner) -> Path:
    completed = owner.issue("a.lic", "--valid-until", "2099-01-31")
    assert completed.returncode == 0, output(completed)
    return owner.root / "a.lic"


def test_inspect_a_licence_and_a_request(issued: Path) -> None:
    completed = run("inspect", issued)
    assert completed.returncode == 0, output(completed)
    document = json.loads(issued.read_bytes())
    assert document["payload"]["license_id"] in completed.stdout
    assert "signature not checked" in completed.stdout
    code = request_code(LINUX)
    shown = run("inspect", "--request", code)
    assert shown.returncode == 0, output(shown)
    assert server_id_of(LINUX) in shown.stdout
    assert "linux" in shown.stdout
    assert "weak" in shown.stdout
    assert run("inspect", "--request", code[:-3]).returncode == 1
    garbage = issued.parent / "garbage.lic"
    garbage.write_bytes(b"\xff\x00")
    assert run("inspect", garbage).returncode == 1


def test_verify_with_the_public_key_or_the_private_key(owner: Owner, issued: Path) -> None:
    completed = run("verify", issued, f"--public-key={owner.public_key()}")
    assert completed.returncode == 0, output(completed)
    assert "geçerli" in completed.stdout
    completed = run("verify", issued, "--key", owner.pem, "--passphrase-file", owner.passphrase)
    assert completed.returncode == 0, output(completed)
    completed = run(
        "verify", issued, f"--public-key={owner.public_key()}",
        "--server-id", server_id_of(WINDOWS), "--at", "2027-10-20",
    )  # fmt: skip
    assert completed.returncode == 0, output(completed)


def test_verify_refuses_tampered_other_server_and_dates(owner: Owner, issued: Path) -> None:
    public = owner.public_key()
    tampered = issued.parent / "tampered.lic"
    tampered.write_bytes(tamper(issued.read_bytes(), "limits.users", 100_000))
    completed = run("verify", tampered, f"--public-key={public}")
    assert completed.returncode == 1
    assert "license_signature_invalid" in output(completed)
    completed = run("verify", issued, f"--public-key={public}", "--server-id", server_id_of(SECOND))
    assert completed.returncode == 1
    assert "license_other_server" in output(completed)
    completed = run("verify", issued, f"--public-key={public}", "--at", "2099-02-01")
    assert completed.returncode == 1
    assert "license_expired" in output(completed)
    completed = run("verify", issued, f"--public-key={public}", "--at", "2027-10-21")
    assert completed.returncode == 1
    assert "license_version_not_covered" in output(completed)
    other_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    from arcilogger_core.license.codec import b64url_encode

    completed = run("verify", issued, f"--public-key={b64url_encode(other_key)}")
    assert completed.returncode == 1
    assert "license_signature_invalid" in output(completed)
    assert run("verify", issued, "--public-key", "short").returncode == 2


def test_verify_with_the_committed_empty_key_set(issued: Path) -> None:
    completed = run("verify", issued, "--committed-keys")
    assert completed.returncode == 1
    assert "license_unknown_key" in output(completed)
    assert KEY_ID in output(completed)


@cache
def issuer_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arcilogger_license_issuer", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_verify_refuses_a_revoked_licence(
    owner: Owner, issued: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    license_id = json.loads(issued.read_bytes())["payload"]["license_id"]
    main = issuer_module().main
    assert main(["verify", str(issued), f"--public-key={owner.public_key()}"]) == 0
    monkeypatch.setattr(revoked, "REVOKED_LICENSE_IDS", frozenset({license_id}))
    assert main(["verify", str(issued), f"--public-key={owner.public_key()}"]) == 1
    captured = capsys.readouterr()
    assert "license_revoked" in captured.err
    assert license_id in captured.err


def test_the_issuer_is_not_part_of_the_product() -> None:
    members = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "license-issuer" not in members.split("[tool.uv.workspace]")[1].split("\n\n")[0]
    for package in ("core", "db", "api", "worker", "collector", "ingest"):
        for path in (ROOT / package / "src").rglob("*.py"):
            assert "license_issuer" not in path.read_text(encoding="utf-8"), path
