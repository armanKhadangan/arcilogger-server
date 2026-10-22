"""Evidence packages: contents, the standalone verifier run as an auditor runs it, tampering.

Every package here is checked by ``python -I -S verify.py`` in a separate process (no
site-packages: proves the verifier needs only the standard library) and, for coverage and exact
findings, by the same code in-process.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest
import pyzipper  # type: ignore[import-untyped]
from asn1crypto import tsp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from arcilogger_core.canonical import canonical_json, is_canonical
from arcilogger_core.config import Config
from arcilogger_core.evidence import (
    EvidenceError,
    PackageInfo,
    RawRef,
    build_package,
    extract_archive,
    generate_password,
    is_encrypted,
    load_refs,
    package_days,
    purge_expired,
    run_verifier,
)
from arcilogger_core.evidence.standalone import VERIFIER_FILES, VERIFY_PY
from arcilogger_core.evidence.standalone import verify as sv
from arcilogger_core.immutability import is_locked, lock, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line, parse_line
from arcilogger_core.retention import run_retention
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import SITE_TZ, isoformat_site
from arcilogger_core.tsa.base import TsaError
from arcilogger_core.tsa.mock import MockFailure, MockTsa, MockTsaProvider
from arcilogger_core.tsa.verify import inspect_token, verify_token

FORMAT = Path(__file__).parents[2] / "tests" / "fixtures" / "format"
FORTIGATE = (FORMAT / "fortigate-sample.log").read_bytes().splitlines()
#: Sealed days in the past (the mock TSA's stamp times must not lie in the future).
DAYS = [date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)]
SOURCES = [
    {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1"},
    {"name": "core-r1", "type": "mikrotik", "address": "172.20.10.1"},
    {"name": "switch-1", "type": "mikrotik", "address": "172.20.10.21"},
]
SENDERS = {source["name"]: source["address"] for source in SOURCES}
#: The fictional example organisation; the product default ``site_name`` is empty.
SITE_NAME = "Örnek A.Ş."
#: Messages that need the reversible escaping of the spool format, or are not UTF-8.
SPECIAL = [
    b"multi\nline message",
    b"tab\there #hash \x7f del",
    b"non-utf8 \xff\xfe\xc3\x28 bytes",
    b"\x00nul and \r\n crlf",
]
BUILT_AT = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
KeyType = Literal["rsa", "ec"]


def fixed_clock(moment: datetime = BUILT_AT) -> Callable[[], datetime]:
    return lambda: moment


@dataclass
class TsaClock:
    """The mock TSA's clock. It follows the sealer's clock (a few seconds later), so the stamp
    times in a test vault are what they would be in production and do not depend on when the
    tests run."""

    now: datetime

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Vault:
    """A data directory with sealed days, the mock TSA that stamped them and what was sent."""

    root: Path
    config: Config
    tsa: MockTsa
    days: list[date]
    sent: dict[tuple[date, str], list[bytes]]
    tsa_clock: TsaClock

    @property
    def layout(self) -> Layout:
        return Layout(self.config.data_dir)

    @property
    def roots(self) -> list[x509.Certificate]:
        return [self.tsa.root_certificate]

    def build(self, days: list[date] | None = None, **kwargs: Any) -> PackageInfo:
        kwargs.setdefault("clock", fixed_clock())
        kwargs.setdefault("out_dir", self.root / "out" / os.urandom(4).hex())
        return build_package(
            self.layout, self.days if days is None else days, roots=self.roots, **kwargs
        )

    def sealer(self, now: datetime) -> Sealer:
        self.tsa_clock.now = now + timedelta(seconds=5)
        return Sealer(
            self.config,
            provider=MockTsaProvider(self.tsa),
            roots=self.roots,
            clock=lambda: now,
            clock_status=lambda: "test clock status",
        )

    def write_day(self, day: date) -> None:
        plan = {
            "fortigate": [
                (time(h, m), FORTIGATE[(h * 2 + m // 30) % len(FORTIGATE)])
                for h in range(24)
                for m in (0, 30)
            ],
            "core-r1": [
                (
                    time(h, 5),
                    b"<30>dhcp,info assigned 172.20.60.%d for 02:00:5E:00:53:%02X" % (100 + h, h),
                )
                for h in range(24)
            ],
            "switch-1": [(time(12, n), message) for n, message in enumerate(SPECIAL)],
        }
        for source, entries in plan.items():
            path = self.layout.spool_file(source, day)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                for at, message in entries:
                    received = datetime.combine(day, at, tzinfo=SITE_TZ)
                    handle.write(format_line(received, SENDERS[source], message))
            self.sent.setdefault((day, source), []).extend(message for _, message in entries)


def seal_vault(root: Path, key_type: KeyType, days: list[date] = DAYS) -> Vault:
    config = Config.model_validate(
        {
            "data_dir": str(root / "data"),
            "secrets_dir": str(root),
            "site_name": SITE_NAME,
            "sources": SOURCES,
            "seal": {"quiet_seconds": 0, "gzip_level": 6},
            "tsa": {"provider": "mock"},
        }
    )
    now = datetime.combine(days[-1] + timedelta(days=1), time(0, 30), tzinfo=SITE_TZ)
    clock = TsaClock(now)
    tsa = MockTsa(Layout(config.data_dir).state / "mock-tsa", key_type=key_type, clock=clock)
    vault = Vault(root=root, config=config, tsa=tsa, days=list(days), sent={}, tsa_clock=clock)
    for day in days:
        vault.write_day(day)
    outcomes = vault.sealer(now).seal_due()
    assert [outcome.status for outcome in outcomes] == ["sealed"] * len(days), outcomes
    return vault


def unlock_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


def tree_state(root: Path) -> dict[str, tuple[int, str, int, int]]:
    """Name → (size, SHA-256, mode, mtime) of every file: proves nothing was touched."""
    state = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            state[path.relative_to(root).as_posix()] = (
                stat.st_size,
                digest,
                stat.st_mode,
                stat.st_mtime_ns,
            )
    return state


def unpack(info: PackageInfo, target: Path) -> Path:
    with zipfile.ZipFile(info.path) as archive:
        archive.extractall(target)
    return target


def isolated(target: Path, *args: str) -> tuple[int, dict[str, Any]]:
    """The auditor's way: ``python -I -S verify.py <target> --json`` in a new process.

    The same code also runs in this process (for coverage); both must find exactly the same.
    """
    result = run_verifier(target, "--json", *args, timeout=300)
    assert result.stdout, result.stderr
    report: dict[str, Any] = json.loads(result.stdout)
    roots = Path(args[args.index("--kokler") + 1]) if "--kokler" in args else None
    local = in_process(target, roots_dir=roots, deep="--hizli" not in args)
    assert {(f.level, f.code, f.where) for f in local.findings} == {
        (f["level"], f["code"], f["where"]) for f in report["findings"]
    }
    assert result.returncode == (0 if local.passed else 1)
    return result.returncode, report


def in_process(target: Path, **kwargs: Any) -> sv.Report:
    with sv.open_package(target) as package:
        return sv.verify_package(package, **kwargs)


def codes(report: dict[str, Any], level: str = "fail") -> set[str]:
    return {finding["code"] for finding in report["findings"] if finding["level"] == level}


def findings(report: dict[str, Any], code: str) -> list[dict[str, str]]:
    return [finding for finding in report["findings"] if finding["code"] == code]


def rewrite_index(package: Path) -> None:
    """Make paket.json consistent with the (tampered) files again, like a careful forger."""
    index = json.loads((package / "paket.json").read_bytes())
    for entry in index["files"]:
        path = package / entry["path"]
        if path.exists():
            data = path.read_bytes()
            entry["bytes"], entry["sha256"] = len(data), hashlib.sha256(data).hexdigest()
    for day in index["days"]:
        manifest, token = package / day["manifest"], package / day["token"]
        if manifest.exists():
            day["manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        try:
            info = inspect_token(token.read_bytes())
        except (OSError, TsaError, ValueError):
            continue  # a deleted or garbage token: leave the old values
        day["token_sha256"] = hashlib.sha256(token.read_bytes()).hexdigest()
        day["gen_time"] = isoformat_site(info.gen_time, "microseconds")
        day["serial"] = info.serial
    (package / "paket.json").write_bytes(canonical_json(index))


def add_to_index(package: Path, *names: str) -> None:
    """List files added to a package in paket.json, like a careful forger (it is not signed)."""
    index = json.loads((package / "paket.json").read_bytes())
    for name in names:
        data = (package / name).read_bytes()
        index["files"].append(
            {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    index["files"].sort(key=lambda entry: entry["path"])
    (package / "paket.json").write_bytes(canonical_json(index))


def manifest_path(package: Path, day: date) -> Path:
    return package / "vault" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}" / f"manifest-{day}.json"


def restamp(
    package: Path,
    day: date,
    tsa: MockTsa,
    change: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Optionally change a manifest (kept canonical), then time-stamp it again."""
    path = manifest_path(package, day)
    if change is not None:
        manifest = json.loads(path.read_bytes())
        change(manifest)
        path.write_bytes(canonical_json(manifest))
    MockTsaProvider(tsa).stamp(path)


@dataclass
class Built:
    vault: Vault
    info: PackageInfo  # 3 days with an extract
    single: PackageInfo  # the middle day alone
    folder: Path  # the 3-day package, unpacked
    single_folder: Path
    refs: list[RawRef]

    def copy(self, target: Path, single: bool = False) -> Path:
        return Path(shutil.copytree(self.single_folder if single else self.folder, target / "pkg"))


def _built(root: Path, key_type: KeyType) -> Built:
    vault = seal_vault(root, key_type)
    refs = [
        RawRef(DAYS[2], "switch-1", 1),
        RawRef(DAYS[2], "switch-1", 3),
        RawRef(DAYS[2], "switch-1", 4),
        RawRef(DAYS[1], "fortigate", 7),
        RawRef(DAYS[1], "fortigate", 2),
    ]
    info = vault.build(extract=refs, created_by="tolga", lookup_id="L-42", site_name=SITE_NAME)
    single = vault.build([DAYS[1]])
    return Built(
        vault=vault,
        info=info,
        single=single,
        folder=unpack(info, root / "unpacked-3"),
        single_folder=unpack(single, root / "unpacked-1"),
        refs=refs,
    )


@pytest.fixture(scope="module")
def ec_built(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Built]:
    root = tmp_path_factory.mktemp("evidence-ec")
    yield _built(root, "ec")
    unlock_tree(root)


@pytest.fixture(scope="module")
def rsa_built(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Built]:
    root = tmp_path_factory.mktemp("evidence-rsa")
    yield _built(root, "rsa")
    unlock_tree(root)


@pytest.fixture(params=["ec", "rsa"])
def built(request: pytest.FixtureRequest) -> Built:
    value: Built = request.getfixturevalue(f"{request.param}_built")
    return value


# =================================================================================================
# The standalone verifier accepts good packages (RSA and EC TSAs, folder and ZIP)
# =================================================================================================


def test_one_day_package_verifies_in_an_isolated_python(built: Built) -> None:
    code, report = isolated(built.single_folder)
    assert code == 0, codes(report)
    assert report["passed"] is True
    assert codes(report, "ok") >= {"index-files", "token-ok", "file-ok"}
    assert codes(report, "info") >= {"chain-start", "root", "crl-absent"}
    assert [day["day"] for day in report["days"]] == ["2026-09-11"]
    # the same verdict straight from the (unencrypted) ZIP file
    code, report = isolated(built.single.path)
    assert code == 0, codes(report)


def test_three_chained_days_verify_and_agree_with_the_core_verifier(built: Built) -> None:
    code, report = isolated(built.folder)
    assert code == 0, codes(report)
    assert len(findings(report, "chain-ok")) == 2
    assert [f["where"] for f in findings(report, "chain-first")] == ["2026-09-10"]
    assert codes(report, "ok") >= {"extract-ok", "token-ok"}
    assert {f["where"] for f in findings(report, "legal-test-stamp")} == {
        day.isoformat() for day in DAYS
    }
    roots = [
        sv.parse_certificate(
            built.vault.tsa.root_certificate.public_bytes(serialization.Encoding.DER)
        )
    ]
    for day in DAYS:
        manifest = manifest_path(built.folder, day)
        token = manifest.with_name(manifest.name + ".zd").read_bytes()
        core = verify_token(token, roots=built.vault.roots, file_path=manifest)
        standalone = sv.verify_timestamp(token, manifest.read_bytes(), roots)
        assert core.ok
        assert standalone.ok
        assert core.info is not None
        assert standalone.serial_hex == core.info.serial
        assert standalone.gen_time == core.info.gen_time
    assert [day["day"] for day in report["days"]] == [day.isoformat() for day in DAYS]
    assert all(day["ok"] for day in report["days"])


def test_the_in_process_run_gives_the_same_verdict_and_human_output(
    ec_built: Built, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sv.main([str(ec_built.folder)]) == 0
    out = capsys.readouterr().out
    # the verdict asks to read the warnings (here: test stamps, one late day)
    assert "SONUÇ: DOĞRULANDI — 5 uyarıyı okuyun [RESULT: VERIFIED — read the 5 warnings]" in out
    assert "[TAMAM]" in out
    assert "[UYARI]" in out
    assert "zincir bağlantısı doğru" in out
    assert "Damgalar şu köke bağlanıyor: SHA-256" in out  # the root, repeated in the summary
    report = in_process(ec_built.folder)
    assert report.passed
    assert report.mode == "package"
    assert codes(report.as_json("x"), "ok") >= {"contents-ok"}
    assert (
        report.index_sha256
        == hashlib.sha256((ec_built.folder / "paket.json").read_bytes()).hexdigest()
    )
    # quick mode does not decompress the sealed files
    quick = in_process(ec_built.folder, deep=False)
    assert quick.passed
    assert not any("açılmış" in f.tr for f in quick.findings if f.code == "file-ok")
    assert any("açılmış" in f.tr for f in report.findings if f.code == "file-ok")


@pytest.mark.parametrize("version", ["3.9", "3.10", "3.11", "3.13"])
def test_older_and_newer_pythons_run_the_verifier(ec_built: Built, version: str) -> None:
    """The verifier must run on any Python >= 3.9 an auditor may have (skipped when absent)."""
    python = shutil.which(f"python{version}")
    if python is None:
        pytest.skip(f"python{version} not installed")
    result = run_verifier(ec_built.folder, "--json", python=python, timeout=300)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell wrapper")
def test_shell_wrapper_runs_the_verifier_on_its_own_folder(ec_built: Built) -> None:
    result = subprocess.run(
        ["sh", str(ec_built.folder / "verify.sh"), "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert Path(json.loads(result.stdout)["package"]).resolve() == ec_built.folder.resolve()


def test_powershell_wrapper_is_ascii_crlf_and_isolated() -> None:
    data = VERIFIER_FILES["verify.ps1"].read_bytes()
    assert data.isascii()  # PowerShell 5.1 reads a BOM-less script as ANSI
    assert b"\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n")
    assert b"-I -S $verifier @args" in data


def test_powershell_wrapper_runs_the_verifier(ec_built: Built) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell not installed (runs on the Windows CI runner)")
    script = ec_built.folder / "verify.ps1"
    command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "--json"]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False, timeout=300
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr
    assert json.loads(result.stdout)["passed"] is True


def test_a_copy_of_the_vault_verifies_with_external_roots(ec_built: Built, tmp_path: Path) -> None:
    """An auditor can point verify.py at a replica of the vault and bring their own roots."""
    copy = Path(shutil.copytree(ec_built.vault.layout.vault, tmp_path / "vault-copy"))
    roots = tmp_path / "roots"
    roots.mkdir()
    (roots / "kamusm.pem").write_bytes(
        ec_built.vault.tsa.root_certificate.public_bytes(serialization.Encoding.PEM)
    )
    (roots / "notes.txt").write_text("ignored", encoding="utf-8")
    code, report = isolated(copy, "--kokler", str(roots))
    assert code == 0, codes(report)
    assert codes(report, "warn") >= {"index-missing"}
    assert codes(report, "info") >= {"roots-external"}
    # with roots that did not issue the tokens nothing verifies
    other = MockTsa(tmp_path / "other-tsa", key_type="ec")
    (roots / "kamusm.pem").write_bytes(
        other.root_certificate.public_bytes(serialization.Encoding.PEM)
    )
    code, report = isolated(copy, "--kokler", str(roots))
    assert code == 1
    assert codes(report) == {"token-chain"}
    unlock_tree(copy)


# =================================================================================================
# What is inside
# =================================================================================================


def test_contents_index_and_order(ec_built: Built) -> None:
    folder, info = ec_built.folder, ec_built.info
    with zipfile.ZipFile(info.path) as archive:
        entries = archive.infolist()
    names = [entry.filename for entry in entries]
    assert names[-1] == "paket.json"
    assert names[:-1] == sorted(names[:-1])
    assert info.file_count == len(names)
    assert all(entry.date_time == (1980, 1, 1, 0, 0, 0) for entry in entries)
    modes = {entry.filename: entry.external_attr >> 16 for entry in entries}
    assert modes["verify.py"] == modes["verify.sh"] == 0o100755
    assert modes["verify.ps1"] == modes["paket.json"] == 0o100644
    stored = {entry.filename for entry in entries if entry.compress_type == zipfile.ZIP_STORED}
    assert {name for name in names if name.endswith(".log.gz")} <= stored

    data = (folder / "paket.json").read_bytes()
    assert is_canonical(data)
    assert hashlib.sha256(data).hexdigest() == info.index_sha256
    index = json.loads(data)
    assert index["schema"] == "arcilogger.evidence/1"
    assert index["package_id"] == info.package_id
    assert info.package_id.startswith("KP-20261013-120000-")
    assert index["created_at"] == "2026-10-13T12:00:00+03:00"
    assert index["created_by"] == "tolga"
    assert index["scope"]["days"] == [day.isoformat() for day in DAYS]
    assert index["scope"]["sources"] is None
    assert index["scope"]["lookup_id"] == "L-42"
    assert len(index["scope"]["extract"]) == len(ec_built.refs)
    assert "desteklemek için tasarlanmıştır" in index["notice"]
    listed = {entry["path"]: entry for entry in index["files"]}
    assert set(listed) == set(names) - {"paket.json"}
    for name, entry in listed.items():
        content = (folder / name).read_bytes()
        assert entry["bytes"] == len(content)
        assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    roles = {entry["role"] for entry in index["files"]}
    assert roles == {
        "extract", "guide", "log", "manifest", "root", "root_bundle", "token", "tsa_certificate",
        "verifier",
    }  # fmt: skip
    # ARCILOGGER's own audit log stays out (KVKK); paket.json says so, with its sealed hash.
    assert index["scope"]["audit_log"] is False
    assert not any(name.endswith("audit.jsonl.gz") for name in names)
    for day_index, day in zip(index["days"], DAYS, strict=True):
        manifest = json.loads(manifest_path(folder, day).read_bytes())
        (audit,) = [e for e in day_index["excluded"] if e.get("kind") == "audit_log"]
        assert audit["path"] == manifest["audit_log"]["path"]
        assert audit["sha256"] == manifest["audit_log"]["sha256"]
        assert audit["source"] is None
        assert "KVKK" in audit["reason"]
    # vault files are byte-identical copies
    layout = ec_built.vault.layout
    for name in names:
        if name.startswith("vault/"):
            assert (folder / name).read_bytes() == (layout.root / name).read_bytes(), name
    # roots and fingerprints
    root_der = ec_built.vault.tsa.root_certificate.public_bytes(serialization.Encoding.DER)
    assert [root["sha256"] for root in index["roots"]] == [hashlib.sha256(root_der).hexdigest()]
    assert (folder / index["roots"][0]["path"]).read_bytes() == (
        folder / "sertifikalar/kokler.pem"
    ).read_bytes()
    tsa_der = ec_built.vault.tsa.tsa_certificate.public_bytes(serialization.Encoding.DER)
    assert [cert["sha256"] for cert in index["tsa_certificates"]] == [
        hashlib.sha256(tsa_der).hexdigest()
    ]
    assert index["crls"] == []
    assert "SİL" in index["crl_note"]
    # each day entry
    for day, entry in zip(DAYS, index["days"], strict=True):
        assert entry["day"] == day.isoformat()
        assert entry["container"] == "TimeStampResp"
        assert entry["provider"] == "mock"
        assert entry["legal_grade"] is False
        assert [e["kind"] for e in entry["excluded"]] == ["audit_log"]  # only the audit log
        assert len(entry["files"]) == 3
    # the verifier files are the ones this ARCILOGGER ships
    for name, source in VERIFIER_FILES.items():
        assert (folder / name).read_bytes() == source.read_bytes()
    assert index["verifier"] == {"path": "verify.py", "version": sv.VERIFIER_VERSION}
    verifier_hash = hashlib.sha256(VERIFY_PY.read_bytes()).hexdigest()
    assert info.verifier_sha256 == verifier_hash == listed["verify.py"]["sha256"]
    assert info.as_json()["verifier_sha256"] == verifier_hash
    assert "DOGRULAMA.txt" in names  # ASCII: every ZIP tool unpacks it under this name
    assert info.legal_grade is False
    assert info.password_protected is False


def test_the_organisation_name_is_shown_only_when_configured(ec_built: Built) -> None:
    """``site_name`` is empty until the organisation sets it: the package then says ``-``."""
    named = (ec_built.folder / "DOGRULAMA.txt").read_text(encoding="utf-8-sig")
    assert f"Kurum             : {SITE_NAME}" in named.splitlines()
    unnamed = (ec_built.single_folder / "DOGRULAMA.txt").read_text(encoding="utf-8-sig")
    assert "Kurum             : -" in unnamed.splitlines()
    index = json.loads((ec_built.single_folder / "paket.json").read_text(encoding="utf-8"))
    assert index["site"] == ""


def test_guides_name_the_files_and_make_no_compliance_claim(ec_built: Built) -> None:
    turkish = (ec_built.folder / "DOGRULAMA.txt").read_bytes()
    english = (ec_built.folder / "VERIFY-EN.txt").read_bytes()
    for data in (turkish, english):
        assert data.startswith(b"\xef\xbb\xbf")
        assert data.count(b"\n") == data.count(b"\r\n")
    text = turkish.decode("utf-8-sig")
    assert "5651 sayılı Kanun" in text
    assert "desteklemek için tasarlanmıştır" in text
    assert "hukuk danışmanına" in text
    lowered = text.lower()
    assert "uyumludur" not in lowered
    assert "uyumlu olduğu" not in lowered
    assert "compliant" not in english.decode("utf-8-sig").replace("legal compliance", "")
    for day in DAYS:
        assert f"manifest-{day}.json manifest-{day}.json.zd" in text  # Zamane -CC line
        assert f"-data vault/{day:%Y/%m/%d}/manifest-{day}.json" in text  # OpenSSL line
    assert "-CC" in text
    assert '"-c <dosya> <dosya>.zd"' in text
    assert "sertifikalar.kamusm.gov.tr" in text
    assert "TEST" in text  # mock-stamped days are called out
    root_sha = hashlib.sha256(
        ec_built.vault.tsa.root_certificate.public_bytes(serialization.Encoding.DER)
    ).hexdigest()
    assert sv.colon_hex(root_sha.upper()) in text
    # the guide does not ask the reader to trust the verify.py that came with the package
    assert "ARCILOGGER'ın yayımladığı" not in text
    assert ec_built.info.verifier_sha256 in text
    assert "AYRI bir kanaldan" in text
    assert "Yöntem C verify.py'ye dayanmaz" in text
    assert ec_built.info.verifier_sha256 in english.decode("utf-8-sig")
    # the audit log is left out on purpose, and both guides say so
    assert "denetim kaydı (audit.jsonl.gz) bilinçli olarak pakete" in text
    assert "audit.jsonl.gz) is left out on purpose" in english.decode("utf-8-sig")

    for day in DAYS:
        assert f"sha256sum vault/{day:%Y/%m/%d}/*.gz" in text  # C2
    assert f"sha256sum vault/{DAYS[0]:%Y/%m/%d}/manifest-{DAYS[0]}.json " in text  # C3
    assert "gzip -dc vault/2026/09/12/switch-1.log.gz | sed -n 'Np'" in text  # C4


@pytest.mark.skipif(
    sys.platform == "win32" or any(shutil.which(tool) is None for tool in ("sh", "sha256sum")),
    reason="POSIX tools",
)
def test_method_c_of_the_guide_checks_without_verify_py(ec_built: Built, tmp_path: Path) -> None:
    """Method C's own commands (sha256sum, gzip, sed) check the files, the chain and the
    extract against the manifests, which C1 (openssl) checks against the time stamps."""
    package = ec_built.copy(tmp_path)
    text = (package / "DOGRULAMA.txt").read_text(encoding="utf-8-sig")

    def run(command: str) -> bytes:
        return subprocess.run(
            ["sh", "-c", command], cwd=package, capture_output=True, check=True
        ).stdout

    hashes: dict[str, str] = {}
    commands = [line.strip() for line in text.splitlines()]
    for command in commands:
        if command.startswith("sha256sum "):
            for row in run(command).decode().splitlines():
                digest, name = row.split(maxsplit=1)
                hashes[name.lstrip("*")] = digest
    for day in DAYS:
        manifest = json.loads(manifest_path(package, day).read_bytes())
        assert f"vault/{manifest['audit_log']['path']}" not in hashes  # left out (KVKK)
        for entry in manifest["files"]:
            assert hashes[f"vault/{entry['path']}"] == entry["sha256"]
        if day != DAYS[0]:
            before = manifest_path(package, day - timedelta(days=1)).relative_to(package)
            assert hashes[before.as_posix()] == manifest["previous"]["manifest_sha256"]
            assert hashes[f"{before.as_posix()}.zd"] == manifest["previous"]["token_sha256"]
    index = json.loads((package / "paket.json").read_bytes())
    for entry in index["extract"]:
        (command,) = [c for c in commands if c.startswith(f"gzip -dc {entry['sealed_file']} ")]
        rows = (package / entry["path"]).read_bytes().splitlines()
        for number, row in zip(entry["lines"], rows, strict=True):
            line = run(command.replace("'Np'", f"'{number}p'"))
            assert row == f"{number}\t".encode() + line.rstrip(b"\n")


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")
def test_openssl_commands_of_the_guide_work(rsa_built: Built, tmp_path: Path) -> None:
    """The exact `openssl ts -verify` lines of DOGRULAMA.txt, run in the package folder."""
    package = rsa_built.copy(tmp_path)
    text = (package / "DOGRULAMA.txt").read_text(encoding="utf-8-sig")
    commands = [
        line.strip() for line in text.splitlines() if line.strip().startswith("openssl ts -verify")
    ]
    assert len(commands) == len(DAYS)
    for command in commands:
        result = subprocess.run(
            command.split(), cwd=package, capture_output=True, text=True, check=False
        )
        assert "Verification: OK" in result.stdout, result.stdout + result.stderr
    manifest = manifest_path(package, DAYS[0])
    manifest.write_bytes(manifest.read_bytes().replace(SITE_NAME.encode(), "Örnek B.Ş.".encode()))
    result = subprocess.run(
        commands[0].split(), cwd=package, capture_output=True, text=True, check=False
    )
    assert "Verification: OK" not in result.stdout


def test_the_audit_log_goes_in_only_when_asked_for(ec_built: Built, tmp_path: Path) -> None:
    """``include_audit_log`` (admins only in the API) adds the days' audit logs, verified."""
    info = ec_built.vault.build([DAYS[1]], include_audit_log=True)
    folder = unpack(info, tmp_path / "pkg")
    audit = folder / f"vault/{DAYS[1]:%Y/%m/%d}/audit.jsonl.gz"
    assert (
        audit.read_bytes() == (ec_built.vault.layout.vault_day(DAYS[1]) / audit.name).read_bytes()
    )
    index = json.loads((folder / "paket.json").read_bytes())
    assert index["scope"]["audit_log"] is True
    assert index["days"][0]["excluded"] == []
    assert {e["role"] for e in index["files"] if e["path"].endswith("audit.jsonl.gz")} == {"audit"}
    text = (folder / "DOGRULAMA.txt").read_text(encoding="utf-8-sig")
    assert "audit.jsonl.gz           ARCILOGGER'ın o günkü denetim kaydı" in text
    assert "bilinçli olarak pakete" not in text
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert any(audit.name in f["tr"] for f in findings(report, "file-ok"))
    assert not findings(report, "audit-log-excluded")
    # removing it from such a package is found
    audit.unlink()
    index["files"] = [e for e in index["files"] if not e["path"].endswith("audit.jsonl.gz")]
    (folder / "paket.json").write_bytes(canonical_json(index))
    code, report = isolated(folder)
    assert code == 1
    assert "file-missing" in codes(report)


def test_source_subset_keeps_whole_files_and_lists_the_rest(
    ec_built: Built, tmp_path: Path
) -> None:
    info = ec_built.vault.build(sources=["fortigate"], out_dir=tmp_path)
    assert info.sources == ("fortigate",)
    folder = unpack(info, tmp_path / "pkg")
    logs = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.log.gz"))
    assert logs == [f"vault/{day:%Y/%m/%d}/fortigate.log.gz" for day in DAYS]
    for name in logs:  # always the whole original file
        assert (folder / name).read_bytes() == (ec_built.vault.layout.root / name).read_bytes()
    assert not any((folder / f"vault/{day:%Y/%m/%d}/audit.jsonl.gz").exists() for day in DAYS)
    index = json.loads((folder / "paket.json").read_bytes())
    assert [sorted(str(e["source"]) for e in day["excluded"]) for day in index["days"]] == [
        ["None", "core-r1", "switch-1"]
    ] * 3
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert len(findings(report, "file-excluded")) == 6
    assert len(findings(report, "audit-log-excluded")) == 3  # "not included", not a failure
    # a forger cannot hide a file of an included source by calling it "excluded"
    index["scope"]["sources"] = ["fortigate", "core-r1"]
    (folder / "paket.json").write_bytes(canonical_json(index))
    code, report = isolated(folder)
    assert code == 1
    assert "file-excluded-in-scope" in codes(report)
    # Nor hide the audit log of a package that says it contains it.
    index["scope"]["sources"] = ["fortigate"]
    index["scope"]["audit_log"] = True
    (folder / "paket.json").write_bytes(canonical_json(index))
    code, report = isolated(folder)
    assert code == 1
    assert len(findings(report, "file-excluded-in-scope")) == 3
    # A package of an earlier version (no scope.audit_log) always carried it: missing = failure.
    del index["scope"]["audit_log"]
    (folder / "paket.json").write_bytes(canonical_json(index))
    assert "file-excluded-in-scope" in codes(isolated(folder)[1])
    with pytest.raises(EvidenceError, match="no sealed file"):
        ec_built.vault.build(sources=["switch-99"], out_dir=tmp_path)
    with pytest.raises(EvidenceError, match="invalid source"):
        ec_built.vault.build(sources=["../x"], out_dir=tmp_path)


def test_extract_holds_exactly_the_requested_raw_lines(ec_built: Built) -> None:
    folder, layout = ec_built.folder, ec_built.vault.layout
    index = json.loads((folder / "paket.json").read_bytes())
    assert [entry["path"] for entry in index["extract"]] == [
        "extract/2026-09-11/fortigate.txt",
        "extract/2026-09-12/switch-1.txt",
    ]
    for entry in index["extract"]:
        day, source = date.fromisoformat(entry["day"]), entry["source"]
        wanted = sorted(
            ref.line for ref in ec_built.refs if ref.day == day and ref.source == source
        )
        assert entry["lines"] == wanted
        sealed = gzip.decompress((layout.vault / f"{day:%Y/%m/%d}/{source}.log.gz").read_bytes())
        spool_lines = sealed.split(b"\n")
        extract = (folder / entry["path"]).read_bytes()
        assert extract.endswith(b"\n")
        rows = extract[:-1].split(b"\n")
        assert [int(row.split(b"\t", 1)[0]) for row in rows] == wanted
        for row in rows:
            number, raw = row.split(b"\t", 1)
            assert raw == spool_lines[int(number) - 1]  # byte for byte
            record = parse_line(raw)  # and it is still the message that arrived
            assert record.message == ec_built.vault.sent[(day, source)][int(number) - 1]
    special = (folder / "extract/2026-09-12/switch-1.txt").read_bytes()
    assert b"multi#012line message" in special
    assert b"\xff\xfe\xc3\x28" in special  # not UTF-8, copied as is
    assert b"#000nul and #015#012 crlf" in special
    assert b"\t" not in special.replace(b"\t2026", b"")  # only the separator tabs


def test_extract_errors(ec_built: Built, tmp_path: Path) -> None:
    vault = ec_built.vault
    with pytest.raises(EvidenceError, match="outside the package days"):
        vault.build([DAYS[0]], extract=[RawRef(DAYS[1], "fortigate", 1)], out_dir=tmp_path)
    with pytest.raises(EvidenceError, match="no such sealed file"):
        vault.build(
            [DAYS[0]],
            sources=["fortigate"],
            extract=[RawRef(DAYS[0], "switch-1", 1)],
            out_dir=tmp_path,
        )
    with pytest.raises(EvidenceError, match="do not exist"):
        vault.build([DAYS[0]], extract=[RawRef(DAYS[0], "switch-1", 99)], out_dir=tmp_path)
    with pytest.raises(EvidenceError, match="start at 1"):
        RawRef(DAYS[0], "switch-1", 0)
    with pytest.raises(EvidenceError, match="invalid source"):
        RawRef(DAYS[0], "SW 01", 1)
    with pytest.raises(EvidenceError, match="invalid raw reference"):
        RawRef.from_json({"day": "2026-13-01", "source": "x", "line": 1})
    refs = tmp_path / "refs.json"
    refs.write_text(
        json.dumps({"refs": [{"day": "2026-09-10", "source": "switch-1", "line": 2}]}),
        encoding="utf-8",
    )
    assert load_refs(refs) == [RawRef(DAYS[0], "switch-1", 2)]
    refs.write_text('{"refs": 5}', encoding="utf-8")
    with pytest.raises(EvidenceError, match="expected a list"):
        load_refs(refs)
    with pytest.raises(EvidenceError, match="cannot read"):
        load_refs(tmp_path / "missing.json")


def test_same_inputs_give_the_same_zip(ec_built: Built, tmp_path: Path) -> None:
    vault = ec_built.vault
    first = vault.build(extract=ec_built.refs, out_dir=tmp_path / "a")
    second = vault.build(extract=ec_built.refs, out_dir=tmp_path / "b")
    assert first.sha256 == second.sha256
    assert first.path.read_bytes() == second.path.read_bytes()
    later = vault.build(
        extract=ec_built.refs,
        out_dir=tmp_path / "c",
        clock=fixed_clock(BUILT_AT + timedelta(hours=5)),
    )
    assert later.sha256 != first.sha256
    with zipfile.ZipFile(first.path) as one, zipfile.ZipFile(later.path) as two:
        assert one.namelist() == two.namelist()
        differing = {name for name in one.namelist() if one.read(name) != two.read(name)}
    # only what carries the creation time (and the package id derived from it) differs
    assert differing == {"paket.json", "DOGRULAMA.txt", "VERIFY-EN.txt"}
    with pytest.raises(EvidenceError, match="already exists"):
        vault.build(extract=ec_built.refs, out_dir=tmp_path / "a")


def test_encrypted_package(ec_built: Built, tmp_path: Path) -> None:
    vault = ec_built.vault
    before = tree_state(vault.layout.vault)
    plain = vault.build([DAYS[2]], out_dir=tmp_path / "plain")
    info = vault.build([DAYS[2]], encrypt=True, out_dir=tmp_path / "enc")
    password = info.password
    assert password is not None
    assert info.password_protected
    assert len(password) == 29
    assert password.count("-") == 5
    assert password not in repr(info)
    assert "password" not in info.as_json()
    assert is_encrypted(info.path)
    assert not is_encrypted(plain.path)
    with zipfile.ZipFile(plain.path) as archive:
        expected = {name: archive.read(name) for name in archive.namelist()}
    with pyzipper.AESZipFile(info.path) as archive:
        assert all(entry.flag_bits & 0x1 for entry in archive.infolist())
        with pytest.raises(RuntimeError):
            archive.read("paket.json")  # no password
        archive.setpassword(b"wrong-password-123")
        with pytest.raises(RuntimeError):
            archive.read("paket.json")
        archive.setpassword(password.encode())
        opened = {name: archive.read(name) for name in archive.namelist()}
    assert set(opened) == set(expected)
    for name, content in opened.items():
        if name not in ("paket.json", "DOGRULAMA.txt", "VERIFY-EN.txt"):
            assert content == expected[name], name
    assert json.loads(opened["paket.json"])["encrypted"] is True
    assert "7-Zip" in opened["DOGRULAMA.txt"].decode("utf-8-sig")
    assert all(password.encode() not in content for content in opened.values())
    with zipfile.ZipFile(info.path) as archive, pytest.raises((RuntimeError, NotImplementedError)):
        archive.read("paket.json")
    # the standalone verifier explains what to do instead of failing obscurely
    result = run_verifier(info.path)
    assert result.returncode == 2
    assert "şifrelenmiş" in result.stderr
    # once opened with the password, it verifies
    folder = tmp_path / "opened"
    extract_archive(info.path, folder, password)
    code, report = isolated(folder)
    assert code == 0, codes(report)
    with pytest.raises(EvidenceError, match="cannot decrypt"):
        extract_archive(info.path, tmp_path / "bad", "not-the-password")
    # a password of the caller's own choice
    chosen = vault.build([DAYS[2]], password="my own long password", out_dir=tmp_path / "own")
    assert chosen.password == "my own long password"
    assert chosen.password_protected
    with pytest.raises(EvidenceError, match="at least 12"):
        vault.build([DAYS[2]], password="short", out_dir=tmp_path / "short")
    assert tree_state(vault.layout.vault) == before


def test_generated_passwords_are_random_and_readable() -> None:
    passwords = {generate_password() for _ in range(50)}
    assert len(passwords) == 50
    assert not any(set(password) & set("0O1lI") for password in passwords)


def test_building_never_changes_the_vault(tmp_path: Path) -> None:
    vault = seal_vault(tmp_path, "ec")
    before = tree_state(vault.layout.vault)
    vault.build(extract=[RawRef(DAYS[0], "switch-1", 2)])
    vault.build(sources=["core-r1"], encrypt=True)
    vault.build([DAYS[1]], report_pdf=b"%PDF-1.7 test", report_name="rapor-5651.pdf")
    assert tree_state(vault.layout.vault) == before
    assert all(is_locked(tmp_path / "data" / "vault" / name) for name in before)


def test_refuses_unsealed_days_and_changed_vault_files(tmp_path: Path) -> None:
    vault = seal_vault(tmp_path, "ec", DAYS[:2])
    with pytest.raises(EvidenceError, match="not sealed"):
        vault.build([DAYS[2]])
    with pytest.raises(EvidenceError, match="no days"):
        vault.build([])
    with pytest.raises(EvidenceError, match="root"):
        build_package(vault.layout, DAYS[:2], roots=[])
    with pytest.raises(EvidenceError, match="report file name"):
        vault.build(report_pdf=b"%PDF", report_name="../x.pdf")
    # a token that does not verify against the roots
    other = MockTsa(tmp_path / "other", key_type="ec")
    with pytest.raises(EvidenceError, match="does not verify"):
        build_package(vault.layout, DAYS[:1], roots=[other.root_certificate])
    # a sealed file changed after sealing (an administrator unlocked and edited it)
    sealed = vault.layout.vault_day(DAYS[1]) / "fortigate.log.gz"
    unlock(sealed)
    data = bytearray(sealed.read_bytes())
    data[-12] ^= 0x01
    sealed.write_bytes(bytes(data))
    lock(sealed)
    out = tmp_path / "out"
    with pytest.raises(EvidenceError, match="changed after sealing"):
        vault.build(out_dir=out)
    assert list(out.iterdir()) == []  # neither the ZIP nor the .part file is left behind
    # the manifest of another day
    manifest = vault.layout.manifest(DAYS[0])
    unlock(manifest)
    manifest.write_bytes(
        manifest.read_bytes().replace(b'"day":"2026-09-10"', b'"day":"2026-09-09"')
    )
    with pytest.raises(EvidenceError, match="another day"):
        vault.build([DAYS[0]], out_dir=out)
    manifest.write_bytes(b"{not json")
    with pytest.raises(EvidenceError, match="not JSON"):
        vault.build([DAYS[0]], out_dir=out)


def test_refuses_an_inconsistent_vault(tmp_path: Path) -> None:
    """Whatever does not match the sealed records stops the build with a clear reason."""
    vault = seal_vault(tmp_path, "ec")
    vault.config = vault.config.model_copy(
        update={
            "retention": vault.config.retention.model_copy(update={"enabled": True, "days": 730})
        }
    )
    later = datetime.combine(DAYS[0] + timedelta(days=731), time(2, 0), tzinfo=SITE_TZ)
    result = run_retention(
        vault.config, provider=MockTsaProvider(vault.tsa), roots=vault.roots, clock=lambda: later
    )
    assert result.deleted == [DAYS[0]]
    new_day = DAYS[2] + timedelta(days=1)
    vault.write_day(new_day)
    vault.sealer(
        datetime.combine(new_day + timedelta(days=1), time(0, 30), tzinfo=SITE_TZ)
    ).seal_due()
    layout, out = vault.layout, tmp_path / "out"

    def edit(path: Path, data: bytes | None) -> None:
        if path.exists() and is_locked(path):
            unlock(path)
        if data is None:
            path.unlink()
        else:
            path.write_bytes(data)

    record = layout.deletions / str(result.record)
    record_bytes = record.read_bytes()
    edit(record, record_bytes + b" ")
    with pytest.raises(EvidenceError, match="differs from the hash sealed"):
        vault.build([new_day], out_dir=out)
    edit(record, record_bytes)
    token = record.with_name(record.name + ".zd")
    token_bytes = token.read_bytes()
    edit(token, None)
    with pytest.raises(EvidenceError, match="time stamp missing"):
        vault.build([new_day], out_dir=out)
    edit(token, token_bytes[:-1] + bytes([token_bytes[-1] ^ 1]))
    with pytest.raises(EvidenceError, match="zd: differs"):
        vault.build([new_day], out_dir=out)
    edit(token, token_bytes)
    edit(layout.chain_anchor, b"not json")
    with pytest.raises(EvidenceError, match="chain anchor is unreadable"):
        vault.build([DAYS[1]], out_dir=out)
    edit(record, None)
    with pytest.raises(EvidenceError, match="sealed record missing"):
        vault.build([new_day], out_dir=out)
    sealed = layout.vault_day(DAYS[1]) / "core-r1.log.gz"
    sealed_bytes = sealed.read_bytes()
    edit(sealed, sealed_bytes[:-1])
    with pytest.raises(EvidenceError, match="size differs"):
        vault.build([DAYS[1]], sources=["core-r1"], out_dir=out)
    edit(sealed, None)
    with pytest.raises(EvidenceError, match="not in the vault"):
        vault.build([DAYS[1]], sources=["core-r1"], out_dir=out)
    assert not out.exists() or list(out.iterdir()) == []


def test_report_pdf_is_included(ec_built: Built, tmp_path: Path) -> None:
    info = ec_built.vault.build(
        [DAYS[0]], report_pdf=b"%PDF-1.7\n%test\n", report_name="rapor-5651.pdf", out_dir=tmp_path
    )
    folder = unpack(info, tmp_path / "pkg")
    assert (folder / "rapor/rapor-5651.pdf").read_bytes() == b"%PDF-1.7\n%test\n"
    index = json.loads((folder / "paket.json").read_bytes())
    assert index["report"] == "rapor/rapor-5651.pdf"
    assert "rapor/rapor-5651.pdf" in (folder / "DOGRULAMA.txt").read_text(encoding="utf-8-sig")
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert codes(report, "info") >= {"report-hash", "report-not-stamped"}


def _stamped_report(vault: Vault, tmp_path: Path) -> tuple[bytes, bytes]:
    """A PDF and its RFC 3161 token from the vault's mock TSA (as the report store stamps)."""
    pdf = tmp_path / "stamp" / "ARCILOGGER-5651-rapor-7.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.7\n%stored report\n%%EOF\n")
    token = MockTsaProvider(vault.tsa).stamp(pdf).token_path.read_bytes()
    return pdf.read_bytes(), token


def test_stamped_report_and_its_token_are_included_and_verified(
    ec_built: Built, tmp_path: Path
) -> None:
    pdf, token = _stamped_report(ec_built.vault, tmp_path)
    info = ec_built.vault.build(
        [DAYS[0]],
        report_pdf=pdf,
        report_name="ARCILOGGER-5651-rapor-7.pdf",
        report_token=token,
        out_dir=tmp_path / "out",
    )
    folder = unpack(info, tmp_path / "pkg")
    index = json.loads((folder / "paket.json").read_bytes())
    assert index["report"] == "rapor/ARCILOGGER-5651-rapor-7.pdf"
    assert index["report_token"] == "rapor/ARCILOGGER-5651-rapor-7.pdf.zd"
    listed = {entry["path"]: entry for entry in index["files"]}
    assert listed["rapor/ARCILOGGER-5651-rapor-7.pdf"]["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert listed["rapor/ARCILOGGER-5651-rapor-7.pdf"]["role"] == "report"
    assert listed["rapor/ARCILOGGER-5651-rapor-7.pdf.zd"]["role"] == "report_token"
    assert (folder / "rapor/ARCILOGGER-5651-rapor-7.pdf").read_bytes() == pdf
    guide = (folder / "DOGRULAMA.txt").read_text(encoding="utf-8-sig")
    assert "rapor/ARCILOGGER-5651-rapor-7.pdf.zd" in guide
    english = (folder / "VERIFY-EN.txt").read_text(encoding="utf-8")
    assert "openssl ts -verify -in rapor/ARCILOGGER-5651-rapor-7.pdf.zd" in english
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert "report-ok" in codes(report, "ok")
    (hash_line,) = findings(report, "report-hash")
    assert hashlib.sha256(pdf).hexdigest() in hash_line["tr"]

    # A changed report fails twice: against paket.json and against its time stamp.
    tampered = Path(shutil.copytree(folder, tmp_path / "tampered"))
    (tampered / "rapor/ARCILOGGER-5651-rapor-7.pdf").write_bytes(pdf + b"%changed\n")
    code, report = isolated(tampered)
    assert code != 0
    assert codes(report) == {"index-size-mismatch", "token-imprint"}
    # ... also when paket.json is rewritten to match: the token still binds the original bytes.
    rewrite_index(tampered)
    code, report = isolated(tampered)
    assert code != 0
    assert codes(report) == {"token-imprint"}


def _edit_index(folder: Path, **changes: Any) -> dict[str, Any]:
    """Set keys of paket.json (canonical again, as a careful forger would write it)."""
    index: dict[str, Any] = json.loads((folder / "paket.json").read_bytes())
    index.update(changes)
    (folder / "paket.json").write_bytes(canonical_json(index))
    return index


def _drop(folder: Path, name: str) -> None:
    """Remove ``name`` from the package and from paket.json's file list."""
    (folder / name).unlink()
    index = json.loads((folder / "paket.json").read_bytes())
    _edit_index(folder, files=[entry for entry in index["files"] if entry["path"] != name])


def test_paket_json_binds_the_report_and_its_token(ec_built: Built, tmp_path: Path) -> None:
    """A package cannot lose its report (or its token), or gain one, without paket.json being
    rewritten — and the "report"/"report_token" names in paket.json must match the files."""
    pdf, token = _stamped_report(ec_built.vault, tmp_path)
    info = ec_built.vault.build(
        [DAYS[0]],
        report_pdf=pdf,
        report_name="ARCILOGGER-5651-rapor-7.pdf",
        report_token=token,
        out_dir=tmp_path / "out",
    )
    folder = unpack(info, tmp_path / "pkg")
    name = "rapor/ARCILOGGER-5651-rapor-7.pdf"

    stripped = Path(shutil.copytree(folder, tmp_path / "no-report"))
    _drop(stripped, name)
    _drop(stripped, name + ".zd")
    code, report = isolated(stripped)
    assert code == 1
    assert codes(report) == {"report-missing", "report-token-missing"}

    no_token = Path(shutil.copytree(folder, tmp_path / "no-token"))
    _drop(no_token, name + ".zd")
    code, report = isolated(no_token)
    assert code == 1
    assert codes(report) == {"report-token-missing"}

    unnamed = Path(shutil.copytree(folder, tmp_path / "unnamed"))
    _edit_index(unnamed, report=None, report_token=None)
    code, report = isolated(unnamed)
    assert code == 1
    assert codes(report) == {"report-unlisted", "report-token-unlisted"}

    garbled = Path(shutil.copytree(folder, tmp_path / "garbled"))
    _edit_index(garbled, report_token=7)
    code, report = isolated(garbled)
    assert code == 1
    assert codes(report) == {"report-token-missing", "report-token-unlisted"}


def test_a_report_without_its_time_stamp_carries_a_note(ec_built: Built, tmp_path: Path) -> None:
    note = "Raporun zaman damgası doğrulanamadı. / The report's time stamp did not verify."
    pdf = b"%PDF-1.7\n%stored report\n%%EOF\n"
    info = ec_built.vault.build(
        [DAYS[0]],
        report_pdf=pdf,
        report_name="ARCILOGGER-5651-rapor-8.pdf",
        report_note=note,
        out_dir=tmp_path / "out",
    )
    folder = unpack(info, tmp_path / "pkg")
    index = json.loads((folder / "paket.json").read_bytes())
    assert (index["report"], index["report_token"], index["report_note"]) == (
        "rapor/ARCILOGGER-5651-rapor-8.pdf",
        None,
        note,
    )
    assert "Raporun zaman damgası pakette yok" in (folder / "DOGRULAMA.txt").read_text(
        encoding="utf-8-sig"
    )
    assert "time stamp is not in the package" in (folder / "VERIFY-EN.txt").read_text(
        encoding="utf-8"
    )
    code, report = isolated(folder)
    assert code == 0, codes(report)
    (warning,) = findings(report, "report-token-omitted")
    assert warning["level"] == "warn"
    assert note in warning["tr"]
    assert "report-not-stamped" not in codes(report, "info")
    # A note only explains a report that is in the package without its token.
    with pytest.raises(EvidenceError, match="report note"):
        ec_built.vault.build([DAYS[0]], report_note=note, out_dir=tmp_path / "out2")
    _, token = _stamped_report(ec_built.vault, tmp_path)
    with pytest.raises(EvidenceError, match="report note"):
        ec_built.vault.build(
            [DAYS[0]], report_pdf=pdf, report_token=token, report_note=note, out_dir=tmp_path / "o3"
        )
    # Packages without a report have no note.
    plain = json.loads(zipfile.ZipFile(ec_built.single.path).read("paket.json"))
    assert plain["report_note"] is None


def test_a_report_token_without_its_report_is_refused(ec_built: Built, tmp_path: Path) -> None:
    _, token = _stamped_report(ec_built.vault, tmp_path)
    with pytest.raises(EvidenceError, match="needs the report"):
        ec_built.vault.build([DAYS[0]], report_token=token, out_dir=tmp_path / "out")
    folder = unpack(ec_built.single, tmp_path / "pkg")
    (folder / "rapor").mkdir()
    (folder / "rapor/x.pdf.zd").write_bytes(token)
    code, report = isolated(folder)
    assert code != 0
    assert "report-token-orphan" in codes(report)


# =================================================================================================
# Records that belong with the days: deletion records, the chain anchor, renewals, CRLs
# =================================================================================================


def test_deletion_record_and_chain_anchor_are_shipped_and_verified(tmp_path: Path) -> None:
    vault = seal_vault(tmp_path, "ec")
    retention_config = vault.config.model_copy(
        update={
            "retention": vault.config.retention.model_copy(update={"enabled": True, "days": 730})
        }
    )
    vault.config = retention_config
    later = datetime.combine(DAYS[0] + timedelta(days=731), time(2, 0), tzinfo=SITE_TZ)
    result = run_retention(
        vault.config, provider=MockTsaProvider(vault.tsa), roots=vault.roots, clock=lambda: later
    )
    assert result.deleted == [DAYS[0]], result.error
    # a package of the oldest remaining day carries the chain anchor and the deletion record
    info = vault.build([DAYS[1]])
    folder = unpack(info, tmp_path / "pkg")
    index = json.loads((folder / "paket.json").read_bytes())
    assert index["records"] == [
        f"vault/deletions/{result.record}",
        f"vault/deletions/{result.record}.zd",
        "vault/deletions/chain-anchor.json",
    ]
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert codes(report, "ok") >= {"chain-anchor", "deletion-ok"}
    # the next sealed day lists the deletion record in its manifest
    new_day = DAYS[2] + timedelta(days=1)
    vault.write_day(new_day)
    outcomes = vault.sealer(
        datetime.combine(new_day + timedelta(days=1), time(0, 30), tzinfo=SITE_TZ)
    ).seal_due()
    assert [o.status for o in outcomes] == ["sealed"]
    info = vault.build([new_day])
    folder = unpack(info, tmp_path / "pkg-new")
    manifest = json.loads(manifest_path(folder, new_day).read_bytes())
    assert [entry["path"] for entry in manifest["deletions"]] == [f"deletions/{result.record}"]
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert "deletion-ok" in codes(report, "ok")
    # tampering with the shipped deletion record is detected
    record = folder / "vault" / "deletions" / str(result.record)
    record.write_bytes(record.read_bytes().replace(b"730", b"731"))
    rewrite_index(folder)
    code, report = isolated(folder)
    assert code == 1
    assert "deletion-hash-mismatch" in codes(report)
    # and a changed chain anchor breaks the link of the oldest remaining day
    folder = unpack(vault.build([DAYS[1]]), tmp_path / "pkg-anchor")
    anchor = folder / "vault/deletions/chain-anchor.json"
    data = json.loads(anchor.read_bytes())
    data["manifest_sha256"] = "0" * 64
    anchor.write_bytes(canonical_json(data))
    rewrite_index(folder)
    code, report = isolated(folder)
    assert code == 1
    assert "chain-broken" in codes(report)
    unlock_tree(tmp_path)


def test_renewal_stamps_covering_the_days_are_shipped(tmp_path: Path) -> None:
    vault = seal_vault(tmp_path, "rsa", DAYS[:2])
    renewals = vault.layout.vault / "renewals"
    renewals.mkdir()
    token = vault.layout.token(DAYS[0])
    covering = renewals / "2028-04-01-renewal.json"
    covering.write_bytes(
        canonical_json(
            {
                "schema": "arcilogger.renewal/1",
                "tokens": [
                    {
                        "day": "2026-09-10",
                        "token_sha256": hashlib.sha256(token.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )
    MockTsaProvider(vault.tsa).stamp(covering)
    unrelated = renewals / "2028-05-01-renewal.json"
    unrelated.write_bytes(canonical_json({"tokens": [{"token_sha256": "ab" * 32}]}))
    MockTsaProvider(vault.tsa).stamp(unrelated)
    folder = unpack(vault.build(), tmp_path / "pkg")
    index = json.loads((folder / "paket.json").read_bytes())
    assert index["records"] == [
        "vault/renewals/2028-04-01-renewal.json",
        "vault/renewals/2028-04-01-renewal.json.zd",
    ]
    code, report = isolated(folder)
    assert code == 0, codes(report)
    covers = findings(report, "renewal-covers")
    assert len(covers) == 1
    assert "2026-09-10 damga" in covers[0]["tr"]
    # a renewal whose token belongs to something else fails
    (folder / "vault/renewals/2028-04-01-renewal.json.zd").write_bytes(
        vault.layout.token(DAYS[1]).read_bytes()
    )
    rewrite_index(folder)
    code, report = isolated(folder)
    assert code == 1
    assert "token-imprint" in codes(report)
    unlock_tree(tmp_path)


def _crl(tsa: MockTsa, revoked: list[tuple[int, datetime]]) -> bytes:
    key = serialization.load_pem_private_key((tsa.state_dir / "root.key").read_bytes(), None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    now = datetime.now(UTC)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(tsa.root_certificate.subject)
        .last_update(now - timedelta(hours=1))
        .next_update(now + timedelta(days=7))
    )
    for serial, when in revoked:
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder().serial_number(serial).revocation_date(when).build()
        )
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)


def test_crls_are_shipped_and_checked_offline(ec_built: Built, tmp_path: Path) -> None:
    tsa, serial = ec_built.vault.tsa, ec_built.vault.tsa.tsa_certificate.serial_number
    crls = tmp_path / "crl"
    crls.mkdir()
    (crls / "kok.crl").write_bytes(_crl(tsa, [(12345, datetime.now(UTC) - timedelta(days=3))]))
    (crls / "readme.txt").write_text("not a CRL", encoding="utf-8")
    info = ec_built.vault.build([DAYS[1]], crl_dir=crls, out_dir=tmp_path / "out")
    folder = unpack(info, tmp_path / "pkg")
    index = json.loads((folder / "paket.json").read_bytes())
    assert index["crls"] == ["sil/kok.crl"]
    assert index["crl_note"] is None
    assert "SİL dosyaları verify.py tarafından kontrol edilir" in (
        folder / "DOGRULAMA.txt"
    ).read_text(encoding="utf-8-sig")
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert "crl-ok" in codes(report, "ok")
    # revoked before the time stamp: the stamp is not trustworthy
    (folder / "sil/kok.crl").write_bytes(
        _crl(tsa, [(serial, datetime.combine(DAYS[1], time(12), tzinfo=UTC))])
    )
    rewrite_index(folder)
    code, report = isolated(folder)
    assert code == 1
    assert codes(report) == {"crl-revoked"}
    # revoked after the time stamp: a warning, the reason must be examined
    (folder / "sil/kok.crl").write_bytes(
        _crl(tsa, [(serial, datetime.now(UTC) + timedelta(days=2))])
    )
    rewrite_index(folder)
    code, report = isolated(folder)
    assert code == 0
    assert "crl-revoked-later" in codes(report, "warn")
    # a CRL signed by someone else is ignored
    other = MockTsa(tmp_path / "other", key_type="ec")
    (folder / "sil/kok.crl").write_bytes(
        _crl(other, [(serial, datetime.combine(DAYS[1], time(12), tzinfo=UTC))])
    )
    rewrite_index(folder)
    code, report = isolated(folder)
    assert code == 0
    assert "crl-none" in codes(report, "info")
    (folder / "sil/kok.crl").write_bytes(b"garbage")
    rewrite_index(folder)
    code, report = isolated(folder)
    assert code == 0
    assert "crl-invalid" in codes(report, "warn")
    (crls / "bad name.crl").write_bytes(b"x")
    with pytest.raises(EvidenceError, match="CRL file name"):
        ec_built.vault.build([DAYS[1]], crl_dir=crls, out_dir=tmp_path / "out2")


# =================================================================================================
# Tampering: each change is found, with a precise message and a non-zero exit code
# =================================================================================================


def _check(package: Path, expected: set[str], *, exact: bool = False) -> dict[str, Any]:
    code, report = isolated(package)
    assert code == 1, report["findings"]
    found = codes(report)
    assert (found == expected) if exact else (found >= expected), found
    in_proc = in_process(package)  # same findings from the in-process run
    assert set(in_proc.codes()) == found
    return report


def test_tamper_log_file_byte(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path)
    log_file = package / "vault/2026/09/11/core-r1.log.gz"
    data = bytearray(log_file.read_bytes())
    data[len(data) // 2] ^= 0x20
    log_file.write_bytes(bytes(data))
    report = _check(package, {"file-hash-mismatch", "index-hash-mismatch"}, exact=True)
    (hit,) = findings(report, "file-hash-mismatch")
    assert hit["where"] == "2026-09-11"
    assert "2026/09/11/core-r1.log.gz" in hit["tr"]
    assert "değiştirilmiş" in hit["tr"]
    # even when the forger also updates paket.json, the manifest hash still catches it
    rewrite_index(package)
    _check(package, {"file-hash-mismatch"}, exact=True)


def test_tamper_extracted_line(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path)
    extract = package / "extract/2026-09-12/switch-1.txt"
    extract.write_bytes(extract.read_bytes().replace(b"multi#012line", b"multi#012LINE"))
    rewrite_index(package)
    report = _check(package, {"extract-mismatch"}, exact=True)
    assert "satır 1" in findings(report, "extract-mismatch")[0]["tr"]
    extract.write_bytes(b"not a number\tline\n")
    rewrite_index(package)
    _check(package, {"extract-format"}, exact=True)


def test_tamper_manifest(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path)
    path = manifest_path(package, DAYS[1])
    path.write_bytes(path.read_bytes().replace(SITE_NAME.encode(), "Örnek B.Ş.".encode()))
    report = _check(package, {"token-imprint", "chain-broken", "index-hash-mismatch"}, exact=True)
    assert findings(report, "token-imprint")[0]["where"] == "2026-09-11"
    assert findings(report, "chain-broken")[0]["where"] == "2026-09-12"
    assert not verify_token(
        path.with_name(path.name + ".zd").read_bytes(), roots=ec_built.vault.roots, file_path=path
    ).ok


def test_tamper_swapped_tokens(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path)
    first, second = (manifest_path(package, day) for day in DAYS[:2])
    first_token, second_token = (p.with_name(p.name + ".zd") for p in (first, second))
    a, b = first_token.read_bytes(), second_token.read_bytes()
    first_token.write_bytes(b)
    second_token.write_bytes(a)
    rewrite_index(package)
    report = _check(package, {"token-swapped", "chain-broken"}, exact=True)
    texts = {f["where"]: f["tr"] for f in findings(report, "token-swapped")}
    assert "2026-09-11 manifestini damgalıyor" in texts["2026-09-10"]
    assert "2026-09-10 manifestini damgalıyor" in texts["2026-09-11"]


def test_tamper_token_from_an_untrusted_root(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path, single=True)
    stranger = MockTsa(tmp_path / "stranger", key_type="ec")
    restamp(package, DAYS[1], stranger)
    rewrite_index(package)
    report = _check(package, {"token-chain"}, exact=True)
    assert "kök sertifikalardan hiçbirine ulaşmıyor" in findings(report, "token-chain")[0]["tr"]
    # a forger who also adds their own root to the package is shown by its fingerprint
    root_pem = stranger.root_certificate.public_bytes(serialization.Encoding.PEM)
    index = json.loads((package / "paket.json").read_bytes())
    (package / "sertifikalar/kokler/fake.pem").write_bytes(root_pem)
    index["roots"].append({"path": "sertifikalar/kokler/fake.pem"})
    (package / "paket.json").write_bytes(canonical_json(index))
    _code, report = isolated(package)
    fake = (
        hashlib.sha256(stranger.root_certificate.public_bytes(serialization.Encoding.DER))
        .hexdigest()
        .upper()
    )
    assert fake in [root["sha256"] for root in report["roots"]]
    assert {day["root_sha256"] for day in report["days"]} == {fake}


def test_tamper_broken_chain_link(ec_built: Built, tmp_path: Path) -> None:
    """A re-stamped manifest whose link to the previous day is wrong: only the chain fails."""
    package = ec_built.copy(tmp_path)

    def wrong_link(manifest: dict[str, Any]) -> None:
        manifest["previous"]["manifest_sha256"] = "0" * 64

    restamp(package, DAYS[1], ec_built.vault.tsa, wrong_link)
    rewrite_index(package)
    report = _check(package, {"chain-broken"}, exact=True)
    assert {f["where"] for f in findings(report, "chain-broken")} == {"2026-09-11", "2026-09-12"}

    def skipped_day(manifest: dict[str, Any]) -> None:
        manifest["previous"]["day"] = "2026-09-09"

    package = ec_built.copy(tmp_path / "second")
    restamp(package, DAYS[1], ec_built.vault.tsa, skipped_day)
    rewrite_index(package)
    _check(package, {"chain-not-previous-day", "chain-broken"}, exact=True)


def test_tamper_missing_file(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path)
    (package / "vault/2026/09/10/fortigate.log.gz").unlink()
    report = _check(package, {"file-missing", "index-file-missing"}, exact=True)
    assert findings(report, "file-missing")[0]["where"] == "2026-09-10"
    (manifest_path(package, DAYS[2])).unlink()
    _check(
        package,
        {"file-missing", "index-file-missing", "manifest-missing", "extract-source-unverified"},
    )


def test_tamper_index(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path)
    index = json.loads((package / "paket.json").read_bytes())
    entry = next(e for e in index["files"] if e["path"] == "verify.ps1")
    entry["sha256"] = "f" * 64
    (package / "paket.json").write_bytes(canonical_json(index))
    report = _check(package, {"index-hash-mismatch"}, exact=True)
    assert "verify.ps1" in findings(report, "index-hash-mismatch")[0]["tr"]
    # pretty-printed: no longer the canonical bytes that were hashed into the audit log
    entry["sha256"] = hashlib.sha256((package / "verify.ps1").read_bytes()).hexdigest()
    (package / "paket.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    _check(package, {"index-not-canonical"}, exact=True)
    # a different time stamp serial recorded for a day
    index["days"][0]["serial"] = "1234"
    (package / "paket.json").write_bytes(canonical_json(index))
    _check(package, {"token-index-mismatch"}, exact=True)
    # sizes, unlisted and invalid entries
    index["days"][0]["serial"] = inspect_token(
        (manifest_path(package, DAYS[0]).with_suffix(".json.zd")).read_bytes()
    ).serial
    entry["bytes"] += 1
    index["files"].append({"path": "../outside.txt", "bytes": 1, "sha256": "0" * 64})
    (package / "paket.json").write_bytes(canonical_json(index))
    (package / "notes.txt").write_text("added later", encoding="utf-8")
    # an added file is a failure in an evidence package: not listed, and not a known part
    report = _check(
        package,
        {"index-size-mismatch", "index-path-invalid", "index-unlisted-file", "file-unexpected"},
        exact=True,
    )
    assert "notes.txt" in findings(report, "index-unlisted-file")[0]["tr"]
    (package / "notes.txt").unlink()
    (package / "paket.json").write_bytes(b"[1, 2]")
    _check(package, {"index-invalid"}, exact=True)
    (package / "paket.json").write_bytes(b"[" * 50_000 + b"]" * 50_000)  # nesting bomb
    _check(package, {"index-invalid"}, exact=True)


def test_tamper_certificate_expired_at_gen_time(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path, single=True)
    tsa = ec_built.vault.tsa
    tsa.failure = MockFailure.EXPIRED_CERT
    try:
        restamp(package, DAYS[1], tsa)
    finally:
        tsa.failure = MockFailure.NONE
    rewrite_index(package)
    report = _check(package, {"token-cert-validity"}, exact=True)
    assert "geçerli değil" in findings(report, "token-cert-validity")[0]["tr"]
    manifest = manifest_path(package, DAYS[1])
    token = manifest.with_name(manifest.name + ".zd").read_bytes()
    assert not verify_token(token, roots=ec_built.vault.roots, file_path=manifest).ok


def _tsa_certificate_without_eku(
    tsa: MockTsa, *, eku: list[x509.ObjectIdentifier] | None, critical: bool = True
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    root_key = serialization.load_pem_private_key((tsa.state_dir / "root.key").read_bytes(), None)
    assert isinstance(root_key, ec.EllipticCurvePrivateKey)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.combine(DAYS[0], time(0), tzinfo=UTC)  # valid when the test vault is stamped
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TSA without EKU")]))
        .issuer_name(tsa.root_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if eku is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage(eku), critical=critical)
    return builder.sign(root_key, hashes.SHA256()), key


def test_tamper_missing_time_stamping_key_purpose(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path, single=True)
    tsa = ec_built.vault.tsa
    tsa.signer = _tsa_certificate_without_eku(tsa, eku=None)
    try:
        restamp(package, DAYS[1], tsa)
    finally:
        tsa.signer = None
    rewrite_index(package)
    report = _check(package, {"token-eku"}, exact=True)
    assert "genişletilmiş anahtar kullanımı" in findings(report, "token-eku")[0]["tr"]
    manifest = manifest_path(package, DAYS[1])
    token = manifest.with_name(manifest.name + ".zd").read_bytes()
    assert not verify_token(token, roots=ec_built.vault.roots, file_path=manifest).ok


def test_tamper_bad_signature(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path, single=True)
    tsa = ec_built.vault.tsa
    tsa.failure = MockFailure.BAD_SIGNATURE
    try:
        restamp(package, DAYS[1], tsa)
    finally:
        tsa.failure = MockFailure.NONE
    rewrite_index(package)
    _check(package, {"token-signature"}, exact=True)


def test_tamper_claimed_legal_grade(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path, single=True)

    def claim(manifest: dict[str, Any]) -> None:
        manifest["tsa"]["legal_grade"] = True

    restamp(package, DAYS[1], ec_built.vault.tsa, claim)
    rewrite_index(package)
    report = _check(package, {"legal-claim-mismatch"}, exact=True)
    assert "test (mock)" in findings(report, "legal-claim-mismatch")[0]["tr"]


def test_tamper_token_and_manifest_garbage(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path, single=True)
    manifest = manifest_path(package, DAYS[1])
    token = manifest.with_name(manifest.name + ".zd")
    token.write_bytes(b"\x30\x03\x02\x01")
    rewrite_index(package)
    _check(package, {"token-format"}, exact=True)
    token.unlink()
    rewrite_index(package)
    _check(package, {"file-missing", "index-file-missing"}, exact=True)
    manifest.write_bytes(b"[not json")
    rewrite_index(package)
    _check(package, {"manifest-invalid"})


def test_unreadable_packages_exit_with_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert sv.main([str(empty)]) == 2
    assert "bulunamadı" in capsys.readouterr().err
    assert sv.main([str(tmp_path / "missing")]) == 2
    (tmp_path / "x.zip").write_bytes(b"PK\x03\x04 broken")
    assert sv.main([str(tmp_path / "x.zip")]) == 2
    assert sv.main([str(empty), "--kokler", str(tmp_path / "nope")]) == 2
    duplicate = tmp_path / "dup.zip"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("paket.json", b"{}")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("paket.json", b"{}")
    assert sv.main([str(duplicate)]) == 2
    assert "aynı adla" in capsys.readouterr().err


# =================================================================================================
# More tampering: sizes, decompressed content, misplaced and forged manifests, extracts, ZIP CRC
# =================================================================================================


def test_tamper_file_size(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path, single=True)
    victim = package / "vault/2026/09/11/fortigate.log.gz"
    victim.write_bytes(victim.read_bytes()[:-1])
    report = _check(package, {"file-size-mismatch", "index-size-mismatch"}, exact=True)
    assert "bayt" in findings(report, "file-size-mismatch")[0]["tr"]


def test_tamper_decompressed_content_is_found_only_by_the_deep_check(
    ec_built: Built, tmp_path: Path
) -> None:
    """A forger with a trusted TSA (here: the mock) re-stamps a manifest whose line count lies."""
    package = ec_built.copy(tmp_path, single=True)

    def lie(manifest: dict[str, Any]) -> None:
        manifest["files"][0]["lines"] += 1

    restamp(package, DAYS[1], ec_built.vault.tsa, lie)
    rewrite_index(package)
    _check(package, {"file-content-mismatch"}, exact=True)
    code, report = isolated(package, "--hizli")  # quick mode does not decompress
    assert code == 0, codes(report)


def test_tamper_forged_manifest_fields(ec_built: Built, tmp_path: Path) -> None:
    def other_day(manifest: dict[str, Any]) -> None:
        manifest["day"] = "2026-09-01"

    def escape_path(manifest: dict[str, Any]) -> None:
        manifest["files"][0]["path"] = "../../outside.log.gz"

    for change, expected in (
        (other_day, {"manifest-day-mismatch"}),
        # the file the manifest no longer lists is in the day folder but was "never sealed"
        (escape_path, {"file-path-invalid", "file-not-sealed"}),
    ):
        package = ec_built.copy(tmp_path / change.__name__, single=True)
        restamp(package, DAYS[1], ec_built.vault.tsa, change)
        rewrite_index(package)
        _check(package, expected, exact=True)
    # a manifest that is valid JSON but not canonical was not written by ARCILOGGER
    package = ec_built.copy(tmp_path / "pretty", single=True)
    path = manifest_path(package, DAYS[1])
    path.write_text(json.dumps(json.loads(path.read_bytes()), indent=2), encoding="utf-8")
    restamp(package, DAYS[1], ec_built.vault.tsa)
    rewrite_index(package)
    _check(package, {"manifest-not-canonical"}, exact=True)
    # a key that appears twice: one parser would read the first, another the last
    text = canonical_json(json.loads(path.read_bytes())).decode()
    path.write_text(text.replace('{"audit_log":', '{"day":"2026-09-01","audit_log":', 1), "utf-8")
    restamp(package, DAYS[1], ec_built.vault.tsa)
    rewrite_index(package)
    report = _check(package, {"manifest-invalid"}, exact=True)
    assert "appears twice" in findings(report, "manifest-invalid")[0]["en"]
    path.write_text("[1, 2]", encoding="utf-8")
    rewrite_index(package)
    _check(package, {"manifest-invalid"}, exact=True)


def test_tamper_misplaced_manifest(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path, single=True)
    (package / "vault/2026/09/11").rename(package / "vault/2026/09/19")
    report = _check(package, {"manifest-misplaced", "manifest-missing", "index-file-missing"})
    assert findings(report, "manifest-misplaced")[0]["where"] == "2026-09-11"


def test_tamper_extract_index_and_sources(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path)
    index = json.loads((package / "paket.json").read_bytes())
    index["extract"][0]["lines"] = [1, 2, 3]
    (package / "paket.json").write_bytes(canonical_json(index))
    _check(package, {"extract-index-mismatch"}, exact=True)

    package = ec_built.copy(tmp_path / "no-source")
    (package / "vault/2026/09/12/switch-1.log.gz").unlink()
    _check(package, {"extract-no-source", "file-missing", "index-file-missing"}, exact=True)

    package = ec_built.copy(tmp_path / "order")
    extract = package / "extract/2026-09-12/switch-1.txt"
    rows = extract.read_bytes().split(b"\n")
    extract.write_bytes(b"\n".join([rows[1], rows[0], *rows[2:]]))
    rewrite_index(package)
    report = _check(package, {"extract-format"}, exact=True)
    assert "increase" in findings(report, "extract-format")[0]["en"]

    package = ec_built.copy(tmp_path / "unknown")
    (package / "extract/notes.md").write_text("added", encoding="utf-8")
    _check(package, {"extract-unknown", "index-unlisted-file"}, exact=True)
    # a forged extract whose name is outside the pattern, even when listed in the index
    package = ec_built.copy(tmp_path / "forged-name")
    (package / "extract/2026-09-12/SWITCH-2.TXT").write_bytes(b"1\tforged line\n")
    add_to_index(package, "extract/2026-09-12/SWITCH-2.TXT")
    report = _check(package, {"extract-unknown"}, exact=True)
    assert "SWITCH-2.TXT" in findings(report, "extract-unknown")[0]["tr"]


def test_tamper_inside_the_zip_file(ec_built: Built, tmp_path: Path) -> None:
    """A stored member changed in the ZIP itself: the ZIP's own CRC check already fails."""
    damaged = tmp_path / "damaged.zip"
    shutil.copyfile(ec_built.single.path, damaged)
    with zipfile.ZipFile(damaged) as archive:
        member = archive.getinfo("vault/2026/09/11/fortigate.log.gz")
    data = bytearray(damaged.read_bytes())
    name_length = int.from_bytes(
        data[member.header_offset + 26 : member.header_offset + 28], "little"
    )
    extra_length = int.from_bytes(
        data[member.header_offset + 28 : member.header_offset + 30], "little"
    )
    start = member.header_offset + 30 + name_length + extra_length
    data[start + member.compress_size // 2] ^= 0xFF
    damaged.write_bytes(bytes(data))
    code, report = isolated(damaged)
    assert code == 1
    assert "file-unreadable" in codes(report)


def test_tamper_roots(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path, single=True)
    root = next((package / "sertifikalar/kokler").glob("*.pem"))
    root.write_bytes(b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
    rewrite_index(package)
    _check(package, {"root-invalid", "root-none", "token-chain"}, exact=True)
    root.unlink()
    rewrite_index(package)
    _check(package, {"file-missing", "root-none", "token-chain", "index-file-missing"}, exact=True)


def test_tamper_index_gen_time(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path, single=True)
    index = json.loads((package / "paket.json").read_bytes())
    index["days"][0]["gen_time"] = "yesterday"
    (package / "paket.json").write_bytes(canonical_json(index))
    _check(package, {"token-index-mismatch"}, exact=True)


def test_human_output_of_a_failed_verification(
    ec_built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = ec_built.copy(tmp_path, single=True)
    (package / "vault/2026/09/11/audit.jsonl.gz").write_bytes(b"changed")
    assert sv.main([str(package)]) == 1
    out = capsys.readouterr().out
    assert "[HATA]" in out
    assert "SONUÇ: DOĞRULANAMADI — 2 hata [RESULT: NOT VERIFIED — 2 failures]" in out


def test_tamper_deletion_records(tmp_path: Path) -> None:
    vault = seal_vault(tmp_path, "ec")
    vault.config = vault.config.model_copy(
        update={
            "retention": vault.config.retention.model_copy(update={"enabled": True, "days": 730})
        }
    )
    later = datetime.combine(DAYS[0] + timedelta(days=731), time(2, 0), tzinfo=SITE_TZ)
    result = run_retention(
        vault.config, provider=MockTsaProvider(vault.tsa), roots=vault.roots, clock=lambda: later
    )
    assert result.deleted == [DAYS[0]]
    new_day = DAYS[2] + timedelta(days=1)
    vault.write_day(new_day)
    vault.sealer(
        datetime.combine(new_day + timedelta(days=1), time(0, 30), tzinfo=SITE_TZ)
    ).seal_due()
    record = f"vault/deletions/{result.record}"
    # the record listed by the manifest left out of the package: reported, not hidden
    folder = unpack(vault.build([new_day]), tmp_path / "absent")
    (folder / record).unlink()
    (folder / f"{record}.zd").unlink()
    index = json.loads((folder / "paket.json").read_bytes())
    index["files"] = [entry for entry in index["files"] if not entry["path"].startswith(record)]
    (folder / "paket.json").write_bytes(canonical_json(index))
    code, report = isolated(folder)
    assert code == 0
    assert "deletion-absent" in codes(report, "info")
    # its time stamp replaced
    folder = unpack(vault.build([new_day]), tmp_path / "token")
    (folder / f"{record}.zd").write_bytes(vault.layout.token(DAYS[1]).read_bytes())
    rewrite_index(folder)
    _check(folder, {"deletion-token-mismatch", "token-imprint"}, exact=True)
    # its time stamp removed
    (folder / f"{record}.zd").unlink()
    rewrite_index(folder)
    _check(folder, {"file-missing", "index-file-missing"}, exact=True)
    # the chain anchor unreadable, or naming a record that does not list the day
    folder = unpack(vault.build([DAYS[1]]), tmp_path / "anchor")
    anchor = folder / "vault/deletions/chain-anchor.json"
    anchor.write_bytes(b"not json")
    rewrite_index(folder)
    _check(folder, {"anchor-invalid"}, exact=True)
    folder = unpack(vault.build([DAYS[1]]), tmp_path / "record")
    (folder / record).write_bytes(b"[]")
    rewrite_index(folder)
    _check(folder, {"chain-broken", "token-imprint"}, exact=True)
    unlock_tree(tmp_path)


# =================================================================================================
# Nothing may be smuggled in: unsealed files, a missing index, unsafe or colliding ZIP names
# =================================================================================================

FORGED = b"2026-09-12T14:00:00.000000+03:00 172.31.255.1 srcip=172.20.60.61 FORGED LINE\n"
PEM = serialization.Encoding.PEM


def test_tamper_unsealed_file_in_a_day_folder(ec_built: Built, tmp_path: Path) -> None:
    """A never-sealed log next to the sealed ones fails, even when paket.json lists it and the
    auditor pins the genuine root."""
    package = ec_built.copy(tmp_path)
    planted = "vault/2026/09/12/fortigate-2.log.gz"
    (package / planted).write_bytes(gzip.compress(FORGED, mtime=0))
    add_to_index(package, planted)
    roots = tmp_path / "roots"
    roots.mkdir()
    (roots / "root.pem").write_bytes(ec_built.vault.tsa.root_certificate.public_bytes(PEM))
    code, report = isolated(package, "--kokler", str(roots))
    assert code == 1
    assert codes(report) == {"file-not-sealed"}
    (hit,) = findings(report, "file-not-sealed")
    assert planted in hit["tr"]
    assert "mühürlenmemiş" in hit["tr"]
    assert "contents-ok" not in codes(report, "ok")
    # the same file in a copy of the vault (no index): still a failure
    copy = Path(shutil.copytree(package / "vault", tmp_path / "vault-copy"))
    for day in DAYS:  # a real vault copy holds the audit logs, which packages leave out
        audit = ec_built.vault.layout.vault_day(day) / "audit.jsonl.gz"
        shutil.copyfile(audit, copy / audit.relative_to(ec_built.vault.layout.vault))
    code, report = isolated(copy, "--kokler", str(roots))
    assert code == 1
    assert report["mode"] == "vault"
    assert codes(report) == {"file-not-sealed"}
    # outside the day folders of a vault copy an unknown file is only reported
    (copy / "2026/09/12/fortigate-2.log.gz").unlink()
    (copy / "notes.txt").write_text("x", encoding="utf-8")
    code, report = isolated(copy, "--kokler", str(roots))
    assert code == 0, codes(report)
    assert "file-unexpected" in codes(report, "warn")
    assert "vault-copy" in codes(report, "info")


def test_tamper_missing_index_in_a_package(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path)
    (package / "paket.json").unlink()
    report = _check(package, {"index-missing"}, exact=True)
    assert report["mode"] == "package"
    # what was added is still found without the index
    (package / "vault/2026/09/11/extra.log.gz").write_bytes(gzip.compress(FORGED, mtime=0))
    (package / "ek").mkdir()
    (package / "ek/notes.txt").write_text("added", encoding="utf-8")
    report = _check(package, {"index-missing", "file-not-sealed", "file-unexpected"}, exact=True)
    assert [f["tr"].split(":")[0] for f in findings(report, "file-unexpected")] == ["ek/notes.txt"]


def test_files_the_operating_system_adds_are_listed_not_failed(
    ec_built: Built, tmp_path: Path
) -> None:
    """Opening the folder in Finder or copying it to a USB stick adds such files."""
    package = ec_built.copy(tmp_path)
    added = [".DS_Store", "vault/2026/09/12/._switch-1.log.gz", "extract/Thumbs.db"]
    for name in added:
        (package / name).write_bytes(b"\x00\x05\x16\x07 metadata")
    code, report = isolated(package)
    assert code == 0, codes(report)
    (hit,) = findings(report, "os-metadata")
    assert all(name in hit["tr"] for name in added)


def test_many_added_files_are_all_counted(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path)
    (package / "ek").mkdir()
    for number in range(sv.MAX_LISTED + 5):
        (package / f"ek/{number:02d}.txt").write_text("added", encoding="utf-8")
    report = in_process(package)
    for code in ("index-unlisted-file", "file-unexpected"):
        hits = [f for f in report.findings if f.code == code]
        assert len(hits) == sv.MAX_LISTED + 1  # 20 named, then "… and 5 more"
        assert "5" in hits[-1].en
        assert all(f.level == "fail" for f in hits)


def _zip_with(source: Path, target: Path, extra: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as copy:
        for info in original.infolist():
            copy.writestr(info, original.read(info))
        for name, data in extra:
            copy.writestr(name, data)
    return target


@pytest.mark.parametrize(
    "name",
    [
        "../../evil.txt",
        "/etc/evil.txt",  # an absolute path
        "../",  # a folder entry
        "EXTRACT/2026-09-12/SWITCH-1.TXT",  # the same file as extract/2026-09-12/switch-1.txt
        "extract/2026-09-12/switch-1.txt.",  # Windows drops the trailing dot
        "extract/2026-09-12/switch-1.txt ",
        "PAKET.JSON",
        "sertifikalar/CON.pem",
        "sil/a\x01b.crl",
        "sil/a?.crl",
        "verify.py/x.txt",  # verify.py would be a file and a folder
    ],
)
def test_tamper_unsafe_or_colliding_zip_names(ec_built: Built, tmp_path: Path, name: str) -> None:
    """Names an archive tool on Windows would change, skip or merge: the package is refused
    (exit code 2), because what is verified must be what is unpacked."""
    forged = f"1\t{FORGED.decode().strip()}\n".encode()
    archive = _zip_with(ec_built.info.path, tmp_path / "pkg.zip", [(name, forged)])
    result = run_verifier(archive)
    assert result.returncode == 2, result.stdout[-1000:]
    assert "HATA:" in result.stderr
    with pytest.raises(sv.PackageError):
        sv.ZipPackage(archive)
    with pytest.raises(EvidenceError, match="unsafe name"):
        extract_archive(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_extract_archive_refuses_what_does_not_fit(ec_built: Built, tmp_path: Path) -> None:
    with pytest.raises(EvidenceError, match="would unpack"):
        extract_archive(ec_built.info.path, tmp_path / "out", max_bytes=1000)
    assert list((tmp_path / "out").iterdir()) == []
    extract_archive(ec_built.info.path, tmp_path / "ok")
    assert (tmp_path / "ok" / "paket.json").is_file()


def test_a_package_without_days_verifies_nothing(ec_built: Built, tmp_path: Path) -> None:
    package = tmp_path / "empty"
    (package / "sertifikalar/kokler").mkdir(parents=True)
    root = package / "sertifikalar/kokler/root.pem"
    root.write_bytes(ec_built.vault.tsa.root_certificate.public_bytes(PEM))
    index = {"schema": sv.INDEX_SCHEMA, "days": [], "scope": {"days": []}, "files": []}
    (package / "paket.json").write_bytes(canonical_json(index))
    add_to_index(package, "sertifikalar/kokler/root.pem")
    _check(package, {"nothing-verified"}, exact=True)


# =================================================================================================
# When a day was stamped, and days missing from the chain
# =================================================================================================


def test_late_and_early_stamps_are_computed_from_the_token(ec_built: Built, tmp_path: Path) -> None:
    """Rewriting a day means stamping it again; the stamp time is the trace that remains."""
    vault, day = ec_built.vault, DAYS[1]
    day_end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=SITE_TZ)
    later = day_end + timedelta(hours=72)
    late = MockTsa(vault.tsa.state_dir, clock=lambda: later)  # the same, trusted TSA
    package = ec_built.copy(tmp_path / "late", single=True)
    restamp(package, day, late)
    rewrite_index(package)
    code, report = isolated(package)
    assert code == 0, codes(report)
    warnings = codes(report, "warn")
    assert {"stamped-late-computed", "stamp-time-differs"} <= warnings
    assert "stamped-late" not in warnings
    assert "72.0 saat" in findings(report, "stamped-late-computed")[0]["tr"]
    assert report["days"][0]["stamp_delay_hours"] == 72.0

    def written_then(manifest: dict[str, Any]) -> None:  # a careful forger moves created_at too
        manifest["created_at"] = isoformat_site(later, "milliseconds")

    restamp(package, day, late, written_then)
    rewrite_index(package)
    code, report = isolated(package)
    assert code == 0
    assert "stamped-late-computed" in codes(report, "warn")
    assert "stamp-time-differs" not in codes(report, "warn")

    def flagged(manifest: dict[str, Any]) -> None:  # the manifest itself says it was late
        manifest["stamped_late"] = True
        manifest["late_reason"] = "TSA unreachable"

    restamp(package, day, late, flagged)
    rewrite_index(package)
    code, report = isolated(package)
    assert code == 0
    assert "stamped-late-computed" not in codes(report, "warn")
    (hit,) = findings(report, "stamped-late")
    assert "TSA unreachable" in hit["tr"]
    assert "72.0 saat" in hit["tr"]
    # stamped before the day ended (the server clock was ahead of the TSA)
    last = DAYS[2]
    end = datetime.combine(last + timedelta(days=1), time(0), tzinfo=SITE_TZ)
    package = unpack(vault.build([last], out_dir=tmp_path / "early-out"), tmp_path / "early")
    restamp(package, last, MockTsa(vault.tsa.state_dir, clock=lambda: end - timedelta(hours=1)))
    rewrite_index(package)
    code, report = isolated(package)
    assert code == 0
    assert {"stamped-before-day-end", "stamp-time-differs"} <= codes(report, "warn")
    # a token that does not verify gives no stamp time to reason about
    stranger = MockTsa(tmp_path / "stranger", key_type="ec")
    restamp(package, last, stranger)
    rewrite_index(package)
    report = _check(package, {"token-chain"}, exact=True)
    assert not {"stamped-before-day-end", "stamp-time-differs"} & codes(report, "warn")


def test_days_missing_from_the_package_are_reported(ec_built: Built, tmp_path: Path) -> None:
    package = ec_built.copy(tmp_path)
    middle = DAYS[1].isoformat()
    shutil.rmtree(manifest_path(package, DAYS[1]).parent)
    shutil.rmtree(package / f"extract/{middle}")
    index = json.loads((package / "paket.json").read_bytes())
    index["days"] = [entry for entry in index["days"] if entry["day"] != middle]
    index["extract"] = [entry for entry in index["extract"] if entry["day"] != middle]
    index["files"] = [
        entry
        for entry in index["files"]
        if not entry["path"].startswith(("vault/2026/09/11/", f"extract/{middle}/"))
    ]
    (package / "paket.json").write_bytes(canonical_json(index))
    # the scope still names the day
    report = _check(package, {"manifest-missing"}, exact=True)
    assert findings(report, "manifest-missing")[0]["where"] == middle
    # a forger who also rewrites the scope: the gap in the chain still shows
    index["scope"]["days"].remove(middle)
    (package / "paket.json").write_bytes(canonical_json(index))
    code, report = isolated(package)
    assert code == 0, codes(report)
    (gap,) = findings(report, "chain-gap")
    assert (gap["level"], gap["where"]) == ("warn", DAYS[2].isoformat())
    assert DAYS[0].isoformat() in gap["tr"]
    # days that were never asked for together are reported the same way
    folder = unpack(
        ec_built.vault.build([DAYS[0], DAYS[2]], out_dir=tmp_path / "o"), tmp_path / "x"
    )
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert [f["where"] for f in findings(report, "chain-gap")] == [DAYS[2].isoformat()]
    # a chain that starts again although the day before is in the package
    folder = unpack(ec_built.vault.build(DAYS[:2], out_dir=tmp_path / "r"), tmp_path / "restart")

    def restart(manifest: dict[str, Any]) -> None:
        manifest["previous"] = None

    restamp(folder, DAYS[1], ec_built.vault.tsa, restart)
    rewrite_index(folder)
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert [f["where"] for f in findings(report, "chain-restart")] == [middle]


# =================================================================================================
# Hostile inputs: no crash, no unbounded memory, never a pass
# =================================================================================================


def test_hostile_inputs_do_not_crash_the_verifier(
    ec_built: Built,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # a .zd file of 3,000 nested constructed OCTET STRINGs
    package = ec_built.copy(tmp_path / "deep", single=True)
    inner = b"\x04\x01A"
    for _ in range(3000):
        inner = b"\x24" + sv.encode_length(len(inner)) + inner
    encapsulated = sv.encode(0x30, sv.encode_oid(sv.TST_INFO) + sv.encode(0xA0, inner))
    signed = sv.encode(
        0x30, sv.encode(0x02, b"\x03") + sv.encode(0x31, b"") + encapsulated + sv.encode(0x31, b"")
    )
    token = manifest_path(package, DAYS[1]).with_suffix(".json.zd")
    token.write_bytes(sv.encode(0x30, sv.encode_oid(sv.SIGNED_DATA) + sv.encode(0xA0, signed)))
    rewrite_index(package)
    report = _check(package, {"token-format"}, exact=True)
    assert "too deep" in findings(report, "token-format")[0]["en"]
    # a CRL naming the root as issuer, "signed" RSASSA-PSS with an empty parameter
    package = ec_built.copy(tmp_path / "crl", single=True)
    root_pem = next((package / "sertifikalar/kokler").glob("*.pem")).read_bytes()
    root = sv.load_certificates(root_pem)[0]
    algorithm = sv.encode(
        0x30, sv.encode_oid(sv.RSASSA_PSS) + sv.encode(0x30, sv.encode(0xA0, b""))
    )
    tbs = sv.encode(0x30, algorithm + root.subject + sv.encode(0x17, b"261001000000Z"))
    (package / "sil").mkdir()
    crl = sv.encode(0x30, tbs + algorithm + sv.encode(0x03, b"\x00" + b"\x01" * 64))
    (package / "sil/kamusm.crl").write_bytes(crl)
    add_to_index(package, "sil/kamusm.crl")
    code, report = isolated(package)
    assert code == 0, codes(report)
    assert "crl-none" in codes(report, "info")

    # an error no check foresaw is reported, and is never a pass
    def broken(*_args: Any, **_kwargs: Any) -> sv.Report:
        raise RuntimeError("unforeseen")

    monkeypatch.setattr(sv, "verify_package", broken)
    assert sv.main([str(package)]) == 2
    assert "beklenmeyen" in capsys.readouterr().err


def test_size_limits_protect_the_auditors_machine(
    ec_built: Built, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = ec_built.copy(tmp_path)
    monkeypatch.setattr(sv, "MAX_LINE", 40)
    report = in_process(package)
    assert "extract-mismatch" in report.codes()
    assert any("longer than 40" in f.tr for f in report.findings if f.code == "extract-mismatch")
    monkeypatch.setattr(sv, "MAX_READ", 100)
    report = in_process(package)
    assert "file-unreadable" in report.codes()
    # an extract of a sealed file that did not verify is not read at all
    monkeypatch.undo()
    log_file = package / "vault/2026/09/12/switch-1.log.gz"
    log_file.write_bytes(gzip.compress(b"x" * 1000, mtime=0))
    rewrite_index(package)
    found = _check(package, {"file-size-mismatch", "extract-source-unverified"}, exact=True)
    assert "extract-mismatch" not in codes(found)


# =================================================================================================
# The builder never hands out a package its own verifier would reject
# =================================================================================================


def test_builder_refuses_a_vault_with_a_broken_chain(tmp_path: Path) -> None:
    vault = seal_vault(tmp_path, "ec")
    manifest, token = vault.layout.manifest(DAYS[1]), vault.layout.token(DAYS[1])
    unlock(manifest)
    unlock(token)
    data = json.loads(manifest.read_bytes())
    data["note"] = "re-sealed"
    manifest.write_bytes(canonical_json(data))
    MockTsaProvider(vault.tsa).stamp(manifest)
    out = tmp_path / "out"
    with pytest.raises(EvidenceError, match="chain link"):
        vault.build(out_dir=out)
    assert not out.exists() or list(out.iterdir()) == []
    vault.build([DAYS[1]], out_dir=out)  # the day alone is consistent
    unlock_tree(tmp_path)


def test_builder_refuses_what_the_standalone_verifier_rejects(tmp_path: Path) -> None:
    """A SHA-1 imprint is refused by both verifiers (one verdict, ADR-0006): the builder stops
    before anything is written."""
    vault = seal_vault(tmp_path, "ec", DAYS[:1])
    manifest, token = vault.layout.manifest(DAYS[0]), vault.layout.token(DAYS[0])
    request = tsp.TimeStampReq(
        {
            "version": "v1",
            "message_imprint": {
                "hash_algorithm": {"algorithm": "sha1"},
                "hashed_message": hashlib.sha1(manifest.read_bytes()).digest(),  # noqa: S324
            },
            "cert_req": True,
        }
    ).dump()
    unlock(token)
    token.write_bytes(vault.tsa.respond(request))
    check = verify_token(token.read_bytes(), roots=vault.roots, file_path=manifest)
    assert any("weak digest algorithm sha1" in error for error in check.errors)
    out = tmp_path / "out"
    with pytest.raises(EvidenceError, match="weak digest algorithm sha1"):
        vault.build(out_dir=out)
    assert not out.exists() or list(out.iterdir()) == []  # neither the ZIP nor the .part file
    unlock_tree(tmp_path)


# =================================================================================================
# exports/ housekeeping
# =================================================================================================


def test_purge_expired_packages(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    assert purge_expired(layout) == []
    layout.exports.mkdir()
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    def make(name: str, age: timedelta) -> Path:
        path = layout.exports / name
        path.write_bytes(b"x")
        moment = (now - age).timestamp()
        os.utime(path, (moment, moment))
        return path

    old = make("ARCILOGGER-kanit-KP-1.zip", timedelta(days=31))
    fresh = make("ARCILOGGER-kanit-KP-2.zip", timedelta(days=29))
    stale_part = make("ARCILOGGER-kanit-KP-3.zip.part", timedelta(days=2))
    new_part = make("ARCILOGGER-kanit-KP-4.zip.part", timedelta(hours=2))
    other = make("search-export.csv", timedelta(days=400))
    odd = make("ARCILOGGER-kanit-notes.txt", timedelta(days=400))
    (layout.exports / "ARCILOGGER-kanit-dir.zip").mkdir()
    assert purge_expired(layout, now=now) == [old, stale_part]
    assert fresh.exists()
    assert new_part.exists()
    assert other.exists()
    assert odd.exists()
    assert purge_expired(layout, 0, now=now) == [fresh]
    with pytest.raises(ValueError, match="negative"):
        purge_expired(layout, -1)


def test_package_days_and_verifier_command() -> None:
    assert package_days(DAYS[0], DAYS[2]) == DAYS
    assert package_days(DAYS[1], DAYS[1]) == [DAYS[1]]
    with pytest.raises(EvidenceError, match="before"):
        package_days(DAYS[2], DAYS[0])
    assert VERIFY_PY.read_bytes().startswith(b"#!/usr/bin/env python3\n")
