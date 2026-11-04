"""The frozen Wave 5 vault (``tests/fixtures/vault-w5``, contract D15): formats across releases.

Three sealed days, one deletion record, one renewal record and one revocation list, as the
product wrote them (``tools/dev/make_vault_fixture.py`` says how). The bytes are pinned like
evidence, and today's code must read them as the code that wrote them did: the core verifier,
the retention, revocation and renewal readers, and the standalone verifier on an evidence
package built from them. A failure here means a release would read an existing vault
differently; change the reader, or regenerate the fixture on purpose with the reason in the
commit (never to make this file pass).

Every moment of the fixture is before November 2026, so each result here is the same at any
later real date; the renewal results that depend on the date are judged at pinned moments.
"""

from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
import json
import re
import shutil
import subprocess
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509

import arcilogger_core.evidence.standalone.verify as sv
from arcilogger_core import PRODUCT_NAME, sealedfile
from arcilogger_core.canonical import canonical_json
from arcilogger_core.chain import verify_day, verify_range
from arcilogger_core.config import Config
from arcilogger_core.crl import load_stored, read_index
from arcilogger_core.evidence.builder import build_package
from arcilogger_core.evidence.runner import run_verifier
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import parse_line
from arcilogger_core.renewal import RecordEntry, build_record, protects, renewal_status
from arcilogger_core.retention import records
from arcilogger_core.timeutil import SITE_TZ, utc_now
from arcilogger_core.tsa.verify import inspect_token, verify_token

FIXTURE = Path(__file__).parent / "fixtures" / "vault-w5"
GENERATOR = Path(__file__).resolve().parents[1] / "tools" / "dev" / "make_vault_fixture.py"
ROOTS = FIXTURE / "kokler"
SITE = "Örnek A.Ş. Merkez"
DAYS = [date(2021, 10, 10), date(2021, 10, 11), date(2021, 10, 12)]
#: The day retention deleted (730 days, on 2023-10-10).
DELETED = date(2021, 10, 9)
DELETION = "deletions/2023-10-10-imha.json"
RENEWAL = "renewals/2026-06-01-1.json"
RENEWED_AT = datetime(2026, 6, 1, 6, tzinfo=UTC)
#: The mock TSA certificate of every stamp, and its root's file name in ``state/crl``.
NOT_AFTER = datetime(2026, 9, 30, 6, tzinfo=UTC)
CRL_FILE = "573f811115a0-20260601T040000Z-2f45a62e.crl"
#: Moments the renewal statuses are judged at: more than 180 days before NOT_AFTER, inside the
#: window (after the renewal), and after NOT_AFTER.
NOT_DUE_AT = datetime(2025, 1, 1, tzinfo=UTC)
DUE_AT = datetime(2026, 6, 2, tzinfo=UTC)
EXPIRED_AT = datetime(2026, 10, 1, tzinfo=UTC)
TOKENS = [
    *(f"{day:%Y/%m/%d}/manifest-{day}.json.zd" for day in DAYS),
    f"{DELETION}.zd",
]
#: The example addresses the fixture may carry: the site's 172.20.x, RFC 5737, and the
#: FortiGate's private WAN address of the project's examples (RFC 6598, ASSUMPTIONS O12).
EXAMPLE_NETWORKS = [
    ipaddress.ip_network(network)
    for network in (
        "172.20.0.0/16", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "100.64.0.0/10",
    )
]  # fmt: skip
_IPV4 = re.compile(rb"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_MAC = re.compile(rb"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")


def _pinned() -> list[tuple[str, str]]:
    lines = (FIXTURE / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    return [(name, digest) for digest, name in (line.split("  ", 1) for line in lines)]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _roots() -> list[x509.Certificate]:
    return x509.load_pem_x509_certificates((ROOTS / "mock-root.crt").read_bytes())


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    """A data directory holding a copy of the fixture (never the committed files)."""
    data = tmp_path / "data"
    for folder in ("vault", "state"):
        shutil.copytree(FIXTURE / folder, data / folder)
    return Layout(data)


def _config(layout: Layout) -> Config:
    return Config.model_validate(
        {
            "data_dir": str(layout.root),
            "secrets_dir": str(layout.root.parent),
            "site_name": SITE,
            "tsa": {"provider": "mock"},
        }
    )


# -- the bytes --------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "digest"), _pinned())
def test_fixture_bytes_unchanged(name: str, digest: str) -> None:
    assert _sha256(FIXTURE / name) == digest


def test_every_file_is_pinned() -> None:
    present = {
        path.relative_to(FIXTURE).as_posix()
        for path in FIXTURE.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    assert present == {name for name, _ in _pinned()}
    assert {name.split("/", 1)[0] for name in present} == {"kokler", "state", "vault"}


def test_every_pinned_file_is_in_the_repository() -> None:
    """A pinned file git ignores would pass here and be missing in CI."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", "."], cwd=FIXTURE, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    tracked = {name.decode() for name in listed.split(b"\0") if name}
    assert {name for name, _ in _pinned()} | {"SHA256SUMS"} <= tracked


def test_the_fixture_is_small_and_fictional() -> None:
    assert sum(path.stat().st_size for path in FIXTURE.rglob("*") if path.is_file()) < 64 * 1024
    layout = Layout(FIXTURE)
    for day in DAYS:
        manifest = _json(FIXTURE / "vault" / f"{day:%Y/%m/%d}" / f"manifest-{day}.json")
        assert manifest["system"]["site"] == SITE
        for entry in manifest["files"]:
            for sender in entry["sender_ips"]:
                assert ipaddress.ip_address(sender) in ipaddress.ip_network("172.20.0.0/16")
        log = sealedfile.find_log(layout, day, "fortigate")
        assert log is not None
        with sealedfile.open_sequential(log) as handle:
            content = handle.read()
        addresses = {ipaddress.ip_address(found.decode()) for found in _IPV4.findall(content)}
        assert addresses, "the lines carry addresses"
        for address in addresses:
            assert any(address in network for network in EXAMPLE_NETWORKS), address
        for mac in _MAC.findall(content):
            assert mac.upper().replace(b"-", b":").startswith(b"00:00:5E:00:53:"), mac
    (root,) = _roots()
    assert "TEST ONLY" in root.subject.rfc4514_string()
    (listed,) = _json(FIXTURE / "state" / "crl" / "index.json")["lists"]
    assert listed["url"].startswith("http://crl.example.com/")


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location("make_vault_fixture", GENERATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generator_still_describes_this_fixture(tmp_path: Path) -> None:
    """The generator is never run here (the fixture changes only on purpose), but its settings
    must stay valid and keep the pinned format: the gzip days sealed before W5-H's xz default,
    the 730-day retention and the mock provider."""
    generator = _generator()
    config = generator._config(tmp_path)
    assert (config.seal.compression, config.retention.days, config.tsa.provider) == (
        "gzip",
        730,
        "mock",
    )
    assert FIXTURE.resolve() == generator.FIXTURE
    assert [*generator.DAYS[1:]] == DAYS
    assert generator.DAYS[0] == DELETED
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "keep.txt").write_text("not a fixture", encoding="ascii")
    with pytest.raises(SystemExit, match="refused"):
        generator.write(tmp_path / "work")
    assert (tmp_path / "work" / "keep.txt").is_file()


# -- the formats ------------------------------------------------------------------------------


def test_the_manifests_keep_their_shape() -> None:
    previous: dict[str, str] | None = None
    for day in DAYS:
        folder = FIXTURE / "vault" / f"{day:%Y/%m/%d}"
        manifest = _json(folder / f"manifest-{day}.json")
        assert set(manifest) == {
            "schema", "system", "day", "tz", "window", "files", "audit_log", "gaps", "clock",
            "tsa", "previous", "deletions", "chain_check", "stamped_late", "created_at",
        }  # fmt: skip
        assert manifest["schema"] == "arcilogger.manifest/1"
        assert manifest["system"] == {
            "name": PRODUCT_NAME,
            "version": manifest["system"]["version"],
            "host": "ORNEK-LOG01",
            "site": SITE,
        }
        assert manifest["tsa"] == {"provider": "mock", "legal_grade": False, "digest": "sha256"}
        assert (manifest["day"], manifest["tz"]) == (day.isoformat(), "Europe/Istanbul")
        assert manifest["created_at"] == f"{day + timedelta(days=1)}T00:30:00.000+03:00"
        (entry,) = manifest["files"]
        assert set(entry) == {
            "source", "path", "bytes", "bytes_uncompressed", "sha256", "sha256_uncompressed",
            "lines", "first_received", "last_received", "sender_ips",
        }  # fmt: skip
        assert (entry["source"], entry["lines"], entry["sender_ips"]) == (
            "fortigate",
            24,
            ["172.20.0.1"],
        )
        assert entry["sha256"] == _sha256(FIXTURE / "vault" / entry["path"])
        audit = manifest["audit_log"]
        assert (audit["entries"], audit["last_entry_hash"]) == (0, None)
        assert audit["sha256"] == _sha256(FIXTURE / "vault" / audit["path"])
        if previous is not None:
            assert manifest["previous"] == previous
        previous = {
            "day": day.isoformat(),
            "manifest_sha256": _sha256(folder / f"manifest-{day}.json"),
            "token_sha256": _sha256(folder / f"manifest-{day}.json.zd"),
        }


def test_the_deletion_record_and_the_chain_anchor_keep_their_shape() -> None:
    record = _json(FIXTURE / "vault" / DELETION)
    assert set(record) == {
        "schema", "created_at", "retention_days", "cutoff", "days", "operator", "reason",
    }  # fmt: skip
    assert (record["schema"], record["retention_days"], record["cutoff"]) == (
        "arcilogger.deletion/1",
        730,
        "2021-10-10",
    )
    assert record["created_at"] == "2023-10-10T03:00:00.000+03:00"
    (deleted,) = record["days"]
    assert set(deleted) == {"day", "manifest_sha256", "token_sha256", "serial", "files"}
    assert deleted["day"] == DELETED.isoformat()
    assert [entry["path"].rsplit("/", 1)[0] for entry in deleted["files"]] == ["2021/10/09"] * 2
    anchor = _json(FIXTURE / "vault" / "deletions" / "chain-anchor.json")
    assert anchor == {
        "day": DELETED.isoformat(),
        "manifest_sha256": deleted["manifest_sha256"],
        "token_sha256": deleted["token_sha256"],
        "record": DELETION.rsplit("/", 1)[1],
    }
    first = _json(FIXTURE / "vault" / f"{DAYS[0]:%Y/%m/%d}" / f"manifest-{DAYS[0]}.json")
    link = ("day", "manifest_sha256", "token_sha256")
    assert first["previous"] == {key: anchor[key] for key in link}


def test_renewal_record_format_matches_the_frozen_fixture() -> None:
    """§2.1.1: the record's schema, keys, kinds, order and hashes; and ``build_record`` on the
    same facts, read from the fixture's files, gives the committed bytes exactly."""
    raw = (FIXTURE / "vault" / RENEWAL).read_bytes()
    record = json.loads(raw)
    assert set(record) == {"schema", "created_at", "system", "tsa", "reason", "tokens"}
    assert (record["schema"], record["reason"]) == ("arcilogger.renewal/1", "test")
    assert record["created_at"] == "2026-06-01T09:00:00.000+03:00"
    version = record["system"]["version"]  # the release that wrote it
    assert record["system"] == {"name": PRODUCT_NAME, "version": version, "site": SITE}
    assert record["tsa"] == {"provider": "mock", "legal_grade": False, "digest": "sha256"}
    assert [entry["path"] for entry in record["tokens"]] == TOKENS
    assert [entry["kind"] for entry in record["tokens"]] == ["day"] * 3 + ["deletion"]
    entries: list[RecordEntry] = []
    for path in TOKENS:
        token = FIXTURE / "vault" / path
        stamped = token.with_name(token.name.removesuffix(".zd"))
        info = inspect_token(token.read_bytes())
        assert info.tsa_cert_not_after == NOT_AFTER
        if path.startswith("deletions/"):
            entries.append(
                RecordEntry(
                    kind="deletion",
                    path=path,
                    token_sha256=_sha256(token),
                    cert_not_after=NOT_AFTER,
                    record_sha256=_sha256(stamped),
                )
            )
        else:
            entries.append(
                RecordEntry(
                    kind="day",
                    path=path,
                    token_sha256=_sha256(token),
                    cert_not_after=NOT_AFTER,
                    day=date.fromisoformat(stamped.stem.removeprefix("manifest-")),
                    manifest_sha256=_sha256(stamped),
                )
            )
    rebuilt = build_record(
        reversed(entries),  # sorted by the builder
        created_at=RENEWED_AT,
        system=record["system"],
        tsa=record["tsa"],
        reason="test",
    )
    assert canonical_json(rebuilt) == raw
    stamp = (FIXTURE / "vault" / f"{RENEWAL}.zd").read_bytes()
    check = verify_token(stamp, roots=_roots(), file_path=FIXTURE / "vault" / RENEWAL)
    assert check.ok, check.errors
    assert inspect_token(stamp).gen_time == RENEWED_AT


# -- today's code reads it --------------------------------------------------------------------


def test_the_core_verifier_reads_the_fixture(layout: Layout) -> None:
    checks = verify_range(layout, DAYS, _roots(), deep=True)
    assert [check.day for check in checks] == DAYS
    for check in checks:
        manifest = _json(layout.manifest(check.day))
        # the copy is not locked; nothing else is said of the day
        files = [*manifest["files"], manifest["audit_log"]]
        unlocked = [f"{entry['path']}: not read-only" for entry in files]
        assert (check.errors, check.warnings) == ([], unlocked)
        assert check.gen_time == f"{check.day}T21:30:00+00:00"
        assert check.serial == inspect_token(layout.token(check.day).read_bytes()).serial
        assert (check.legal_grade_claimed, check.legal_grade) == (False, False)


def test_the_sealed_files_read_through_the_shared_reader(layout: Layout) -> None:
    """The days sealed before W5-H (gzip) are found, digested and read by ``sealedfile``, the one
    reader of both formats (H3), as the manifest describes them."""
    for day in DAYS:
        manifest = _json(layout.manifest(day))
        (entry,) = manifest["files"]
        log = sealedfile.find_log(layout, day, "fortigate")
        audit = sealedfile.find_audit(layout, day)
        assert log is not None
        assert audit is not None
        assert log == layout.vault / entry["path"]
        assert audit == layout.vault / manifest["audit_log"]["path"]
        assert (sealedfile.format_of(log), sealedfile.format_of(audit)) == ("gzip", "gzip")
        assert sealedfile.digest(log) == (entry["sha256_uncompressed"], entry["lines"])
        assert sealedfile.digest(audit) == (hashlib.sha256(b"").hexdigest(), 0)
        with sealedfile.open_sequential(log) as handle:
            content = handle.read()
        assert len(content) == entry["bytes_uncompressed"]
        lines = content.splitlines(keepends=True)
        parsed = [parse_line(line) for line in lines]
        assert [record.sender for record in parsed] == ["172.20.0.1"] * 24
        assert [record.received.astimezone(SITE_TZ) for record in parsed] == [
            datetime(day.year, day.month, day.day, hour, 0, 30, tzinfo=SITE_TZ)
            for hour in range(24)
        ]
        assert parsed[0].received.isoformat(timespec="milliseconds") == (entry["first_received"])
        assert parsed[-1].received.isoformat(timespec="milliseconds") == entry["last_received"]


def test_the_retention_and_revocation_readers_read_the_fixture(layout: Layout) -> None:
    assert records(layout) == [layout.vault / DELETION]
    token = (layout.vault / f"{DELETION}.zd").read_bytes()
    assert verify_token(token, roots=_roots(), file_path=layout.vault / DELETION).ok
    (stored,) = load_stored(layout)
    (root,) = _roots()
    assert stored.file_name == CRL_FILE
    assert stored.issuer_name == root.subject
    assert stored.signed_by(root)
    assert (stored.this_update, stored.next_update) == (
        datetime(2026, 6, 1, 4, tzinfo=UTC),
        datetime(2026, 6, 8, 4, tzinfo=UTC),
    )
    assert stored.revoked == {0x5651: datetime(2026, 5, 15, tzinfo=UTC)}
    assert stored.reasons == {0x5651: 5}  # cessationOfOperation
    (entry,) = read_index(layout)["lists"]
    assert (entry["file"], entry["sha256"]) == (CRL_FILE, _sha256(layout.crl_dir / CRL_FILE))


@pytest.mark.parametrize(
    ("now", "status", "code"),
    [
        (NOT_DUE_AT, "not_due", "renewal-not-due"),
        (DUE_AT, "due", "renewal-due"),
        (EXPIRED_AT, "uncovered", "renewal-uncovered"),
        (None, "uncovered", "renewal-uncovered"),  # the real date: after NOT_AFTER
    ],
)
def test_the_renewal_statuses_of_the_fixture(
    layout: Layout, now: datetime | None, status: str, code: str
) -> None:
    """The mock renewal never counts (D5): every token is judged by its own certificate."""
    if now is None:
        assert utc_now() > NOT_AFTER
    report = renewal_status(layout, _config(layout), _roots(), now)
    assert report.as_json() == {
        "tokens": [
            {
                "path": path,
                "kind": "deletion" if path.startswith("deletions/") else "day",
                "cert_not_after": "2026-09-30T06:00:00Z",
                "status": status,
                "covered_by": None,
                "renewed_at": None,
            }
            for path in TOKENS
        ],
        "totals": {name: 4 if name == status else 0 for name in report.totals},
        "unreadable": [],
        "mismatched": [],
        "notes": [],
    }
    for day in DAYS:
        renewal = verify_day(layout, day, _roots(), renewals=True, now=now).renewal
        assert renewal is not None
        assert (renewal.status, renewal.code, renewal.covered_by) == (status, code, None)


def test_the_core_reads_the_renewal_record(layout: Layout) -> None:
    """The record is read, not skipped: what it protects, and how a changed or an unknown record
    is reported (it never counts either way, so the statuses alone would not show it)."""
    record = _json(layout.vault / RENEWAL)
    stamped = [
        _sha256(layout.vault / path.removesuffix(".zd")) for path in TOKENS
    ]  # 3 manifests and the deletion record
    assert protects(record) == sorted(
        [*(_sha256(layout.vault / path) for path in TOKENS), *stamped]
    )
    raw = (layout.vault / RENEWAL).read_bytes()
    assert raw.count(SITE.encode()) == 1
    config = _config(layout)
    (layout.vault / RENEWAL).write_bytes(raw.replace(SITE.encode(), b"Ornek A.S. Merkez"))
    changed = renewal_status(layout, config, _roots(), DUE_AT)
    assert (changed.mismatched, changed.unreadable) == ((f"{RENEWAL}.zd",), ())
    unknown = raw.replace(b'"arcilogger.renewal/1"', b'"arcilogger.renewal/9"')
    (layout.vault / RENEWAL).write_bytes(unknown)
    read = renewal_status(layout, config, _roots(), DUE_AT)
    assert (read.mismatched, read.unreadable) == ((), (RENEWAL,))
    for report in (changed, read):
        assert [token.status for token in report.tokens] == ["due"] * 4


# -- the standalone verifier on a package built from it ---------------------------------------

#: What the standalone verifier finds in the package, but the renewal codes of the days.
FINDINGS = [
    ("info", "index-hash", "paket"),
    ("ok", "index-files", "paket"),
    ("info", "index-scope", "paket"),
    ("info", "roots-external", "kökler"),
    ("info", "root", "kökler"),
    ("info", "root-trust", "kökler"),
    ("info", "crl-loaded", "kökler"),
    *(
        (level, code, day.isoformat())
        for day in DAYS
        for level, code in (
            ("info", "manifest"),
            ("ok", "token-ok"),
            ("ok", "crl-ok"),
            ("ok", "file-ok"),
            ("info", "audit-log-excluded"),
            ("warn", "legal-test-stamp"),
        )
    ),
    ("ok", "deletion-ok", DAYS[0].isoformat()),
    ("ok", "chain-anchor", DAYS[0].isoformat()),
    ("ok", "chain-ok", DAYS[1].isoformat()),
    ("ok", "chain-ok", DAYS[2].isoformat()),
    ("ok", "renewal-ok", "yenileme"),
    ("info", "renewal-covers", "yenileme"),
    ("ok", "contents-ok", "içerik"),
    ("info", "summary-days", "özet"),
    ("info", "summary-root", "özet"),
    ("warn", "summary-test-days", "özet"),
]


#: The pinned facts of each day's stamp, as the standalone verifier writes them.
STAMPS = {
    DAYS[0]: ("7FF3F79E694BCA5F", "2021-10-10 21:30:00 UTC (11.10.2021 00:30:00 TSİ)", 1767),
    DAYS[1]: ("74C2EDB86A8A5E32", "2021-10-11 21:30:00 UTC (12.10.2021 00:30:00 TSİ)", 1768),
    DAYS[2]: ("D8250AC1AF823F", "2021-10-12 21:30:00 UTC (13.10.2021 00:30:00 TSİ)", 1768),
}
MOCK_ROOT = "CN=ARCILOGGER Mock Root CA (TEST ONLY), O=ARCILOGGER TEST - NOT VALID, C=TR"
MOCK_TSA = "CN=ARCILOGGER Mock TSA (TEST ONLY) tsa, O=ARCILOGGER TEST - NOT VALID, C=TR"
ROOT_SHA256 = "573F811115A0B22225BBCECD44716042CCB35678E17314D694CD6B9597DDF02E"
ROOT_SHA1 = "6682327010A401D15F9219F2F19650D5EABE6308"


def _colons(fingerprint: str) -> str:
    return ":".join(fingerprint[i : i + 2] for i in range(0, len(fingerprint), 2))


def _texts(mode: str, folder: Path) -> list[dict[str, str]]:
    """Every finding of an isolated ``--json`` run at the real date (after NOT_AFTER), with its
    texts: on a package built from the fixture (``mode="package"``, where ``folder`` is the
    unpacked package) or on a copy of its vault (``mode="vault"``)."""
    packaged = mode == "package"
    vault = "vault/" if packaged else ""  # how a vault file is named in a finding
    found: list[tuple[str, str, str, str, str]] = []
    if packaged:
        index = _json(folder / "paket.json")
        files = len(index["files"])
        found += [
            ("info", "index-hash", "paket",
             f"paket.json SHA-256: {_sha256(folder / 'paket.json')} (ARCILOGGER denetim "
             "kaydındaki değerle karşılaştırılabilir)",
             "compare with the value in ARCILOGGER's audit log"),
            ("ok", "index-files", "paket",
             f"{files} dosyanın boyutu ve SHA-256 değeri paket dizinindekiyle aynı",
             f"size and SHA-256 of {files} files match the index"),
            ("info", "index-scope", "paket",
             f"Paket {index['package_id']}: oluşturan -, zaman 2026-06-02T12:00:00+03:00, günler "
             "2021-10-10, 2021-10-11, 2021-10-12, kaynaklar tümü, sorgu -",
             "package scope as recorded by ARCILOGGER"),
        ]  # fmt: skip
    else:
        found += [
            ("info", "vault-copy", "paket",
             "Paket dizini yok: bu bir kasa kopyası olarak doğrulanıyor; yalnızca mühürlü günler "
             "ve kayıtlar kontrol edilir",
             "no index: verified as a copy of the vault (sealed days and records only)"),
            ("warn", "index-missing", "paket",
             "paket.json yok: dosya listesi kontrol edilemiyor; yalnızca mühürlü günler "
             "doğrulanıyor",
             "paket.json missing: only the sealed days are verified"),
        ]  # fmt: skip
    found += [
        ("info", "roots-external", "kökler",
         f"Yalnızca komut satırında verilen klasördeki köklere güveniliyor: {ROOTS} (paketteki "
         "kökler kullanılmıyor)",
         "trusting only the roots from the given folder, not the package's"),
        ("info", "root", "kökler",
         f"{ROOTS / 'mock-root.crt'}: {MOCK_ROOT}; geçerlilik 2020-08-27 06:00:00 UTC "
         "(27.08.2020 09:00:00 TSİ) - 2031-09-29 06:00:00 UTC (29.09.2031 09:00:00 TSİ); SHA-256 "
         f"parmak izi {_colons(ROOT_SHA256)}; SHA-1 parmak izi {_colons(ROOT_SHA1)}",
         "root certificate and its fingerprints"),
        ("info", "root-trust", "kökler",
         "Bu program bir kökün gerçekten Kamu SM'ye ait olduğunu kanıtlayamaz: yukarıdaki parmak "
         "izlerini Kamu SM'nin yayımladığı değerlerle (sertifikalar.kamusm.gov.tr) karşılaştırın "
         "ya da kendi indirdiğiniz kökleri --kokler ile verin",
         "compare these fingerprints with Kamu SM's published values"),
        ("info", "crl-loaded", "kökler", "1 SİL yüklendi", "CRLs loaded")
        if packaged
        else ("info", "crl-absent", "kökler",
              "Pakette SİL (sertifika iptal listesi) yok; iptal durumu kontrol edilmeyecek.",
              "no CRLs in the package; revocation is not checked"),
    ]  # fmt: skip
    for day in DAYS:
        serial, at, size = STAMPS[day]
        folder_name = f"{day:%Y/%m/%d}"
        manifest = f"{folder_name}/manifest-{day}.json"
        log = f"{folder_name}/{sealedfile.log_name('fortigate', 'gzip')}"
        audit = f"{folder_name}/{sealedfile.audit_name('gzip')}"
        audit_sha = _json(FIXTURE / "vault" / manifest)["audit_log"]["sha256"]
        found += [
            ("info", "manifest", f"{day}",
             f"Manifest {vault}{manifest}, SHA-256 {_sha256(FIXTURE / 'vault' / manifest)}",
             "manifest file and hash"),
            ("ok", "token-ok", f"{day}",
             f"Zaman damgası geçerli: zaman {at}, seri no {serial}, TSA {MOCK_TSA}, imza RSA 2048 "
             f"bit PKCS#1 v1.5 / sha256, kök {MOCK_ROOT} (SHA-256 {_colons(ROOT_SHA256)}), "
             "politika 2.999.5651.1, biçim TimeStampResp",
             "time-stamp token valid: imprint, signature, ESSCertID, EKU and chain"),
        ]  # fmt: skip
        if packaged:
            found.append(
                ("ok", "crl-ok", f"{day}",
                 f"{MOCK_TSA}: SİL'de iptal kaydı yok (SİL tarihi 2026-06-01 04:00:00 UTC "
                 "(01.06.2026 07:00:00 TSİ))",
                 f"{MOCK_TSA}: not revoked according to the CRL of 2026-06-01T04:00:00+00:00")
            )  # fmt: skip
        found.append(
            ("ok", "file-ok", f"{day}",
             f"{log}: {size} bayt, SHA-256 manifestteki değerle aynı; açılmış hali 24 satır, "
             "SHA-256 aynı",
             f"{log}: matches the manifest")
        )  # fmt: skip
        found.append(
            ("info", "audit-log-excluded", f"{day}",
             f"{audit}: ARCILOGGER'ın denetim kaydı bu pakete alınmadı (varsayılan; başka "
             "sorguları ve personel bilgilerini içerir). Bu bir hata değildir; dosyanın "
             f"manifestteki SHA-256 değeri {audit_sha}",
             f"{audit}: ARCILOGGER's audit log is not included in this package (the default: it "
             "holds other requests and staff data); not a failure")
            if packaged
            else ("ok", "file-ok", f"{day}",
                  f"{audit}: 20 bayt, SHA-256 manifestteki değerle aynı",
                  f"{audit}: matches the manifest")
        )  # fmt: skip
        found.append(
            ("warn", "legal-test-stamp", f"{day}",
             "Bu gün yasal geçerliliği olmayan bir test/deneme zaman damgasıyla (sağlayıcı: mock) "
             "mühürlenmiş",
             "sealed with a test time stamp that has no legal validity")
        )  # fmt: skip
    first, second, third = (f"{day}" for day in DAYS)
    expired = "2026-09-30 06:00:00 UTC (30.09.2026 09:00:00 TSİ)"
    found += [
        ("ok", "deletion-ok", first,
         f"{vault}{DELETION}: zaman damgası geçerli (2023-10-10 00:00:00 UTC (10.10.2023 "
         "03:00:00 TSİ), seri no 23CB35DB6519E9D2)",
         f"{vault}{DELETION}: time stamp valid"),
        ("ok", "chain-anchor", first,
         f"{first}: önceki gün ({DELETED}) saklama süresi dolduğu için imha edilmiş; bağlantı "
         "zincir çapası ve zaman damgalı imha tutanağıyla eşleşiyor",
         "previous day deleted by retention; the link matches the chain anchor and the "
         "time-stamped deletion record"),
        *(
            ("ok", "chain-ok", day,
             f"{day} → {previous}: zincir bağlantısı doğru (önceki günün manifest ve damga "
             "özetleri aynı)",
             "chain link matches the previous day's manifest and token")
            for day, previous in ((second, first), (third, second))
        ),
        ("ok", "renewal-ok", "yenileme",
         f"{vault}{RENEWAL}: zaman damgası geçerli (2026-06-01 06:00:00 UTC (01.06.2026 09:00:00 "
         "TSİ), seri no 532769A878642F65)",
         f"{vault}{RENEWAL}: time stamp valid"),
        ("info", "renewal-covers", "yenileme",
         f"{vault}{RENEWAL} şunları yeniden damgalıyor: "
         + ", ".join(f"{day} damga, {day} manifest" for day in (first, second, third)),
         "what the renewal stamp covers"),
        *(
            ("warn", "renewal-uncovered", day,
             f"Damga sertifikasının süresi {expired} tarihinde doldu ve yeniden damgalama yok; "
             "damga, alındığı an için geçerlidir.",
             "The stamp certificate expired on 2026-09-30T06:00:00+00:00 with no re-stamping; "
             "the stamp is valid for the moment it was made.")
            for day in (first, second, third)
        ),
        ("ok", "contents-ok", "içerik",
         "Paketteki her dosya mühürlü bir güne, bir kayda ya da paketin bilinen bir parçasına ait",
         "every file belongs to a sealed day, a record or a known part of the package"),
        ("info", "summary-days", "özet", f"İncelenen gün: 3 ({first} … {third})",
         "3 days examined"),
        ("info", "summary-root", "özet",
         f"Damgalar şu köke bağlanıyor: SHA-256 {_colons(ROOT_SHA256)} — Kamu SM'nin yayımladığı "
         "parmak iziyle karşılaştırın",
         "the time stamps chain to this root: compare it with Kamu SM's published value"),
        ("warn", "summary-test-days", "özet",
         f"3 gün yasal geçerliliği olmayan test damgası taşıyor: {first}, {second}, {third}",
         "some days carry test time stamps without legal validity"),
    ]  # fmt: skip
    keys = ("level", "code", "where", "tr", "en")
    return [dict(zip(keys, finding, strict=True)) for finding in found]


def _days_block() -> list[dict[str, Any]]:
    """The ``days`` block of a ``--json`` run: each day's stamp, as the fixture pins it."""
    return [
        {
            "day": f"{day}",
            "ok": True,
            "gen_time": f"{day}T21:30:00+00:00",
            "serial": STAMPS[day][0],
            "tsa": MOCK_TSA,
            "root_sha256": ROOT_SHA256,
            "stamp_delay_hours": 0.5,
            "legal_grade": False,
            "provider": "mock",
        }
        for day in DAYS
    ]


def _package(layout: Layout, folder: Path) -> Path:
    info = build_package(
        layout,
        DAYS,
        roots=_roots(),
        out_dir=folder.parent / "out",
        crl_dir=layout.crl_dir,
        clock=lambda: DUE_AT + timedelta(hours=9),
    )
    with zipfile.ZipFile(info.path) as archive:
        names = archive.namelist()
        archive.extractall(folder)
    audit_logs = {
        f"vault/{_json(layout.manifest(day))['audit_log']['path']}" for day in DAYS
    }  # left out by default (KVKK)
    shipped = sorted(name for name in names if name.startswith("vault/"))
    assert shipped == [
        name for name, _ in _pinned() if name.startswith("vault/") and name not in audit_logs
    ]
    assert f"sil/{CRL_FILE}" in names
    return folder


def _found(report: sv.Report) -> list[tuple[str, str, str]]:
    return [(finding.level, finding.code, finding.where) for finding in report.findings]


def test_the_standalone_verifier_reads_a_package_built_from_the_fixture(
    layout: Layout, tmp_path: Path
) -> None:
    folder = _package(layout, tmp_path / "pkg")
    with sv.open_package(folder) as package:
        report = sv.verify_package(package, roots_dir=ROOTS, now=DUE_AT)
    assert report.passed
    due = [("warn", "renewal-due", day.isoformat()) for day in DAYS]
    assert _found(report) == FINDINGS[:-4] + due + FINDINGS[-4:]
    for finding in report.findings:
        if finding.code == "renewal-due":
            assert finding.en == (
                "The stamp certificate expires on 2026-09-30T06:00:00+00:00; re-stamping is due."
            )
    # the auditor's way, at the real date (after NOT_AFTER), in an isolated Python
    result = run_verifier(folder, "--json", "--kokler", str(ROOTS), timeout=300)
    assert result.returncode == 0, result.stderr
    isolated = json.loads(result.stdout)
    uncovered = [("warn", "renewal-uncovered", day.isoformat()) for day in DAYS]
    assert [(f["level"], f["code"], f["where"]) for f in isolated["findings"]] == (
        FINDINGS[:-4] + uncovered + FINDINGS[-4:]
    )
    assert isolated["verifier"] == f"ARCILOGGER verify.py {sv.VERIFIER_VERSION}"
    assert (isolated["mode"], isolated["passed"]) == ("package", True)
    assert isolated["findings"] == _texts("package", folder)
    assert isolated["days"] == _days_block()
    assert isolated["roots"] == [
        {
            "path": str(ROOTS / "mock-root.crt"),
            "subject": MOCK_ROOT,
            "sha256": ROOT_SHA256,
            "sha1": ROOT_SHA1,
        }
    ]


def test_the_standalone_verifier_reads_a_copy_of_the_vault(layout: Layout) -> None:
    """Vault mode (no ``paket.json``): every sealed day with its audit log, the deletion record
    and the renewal record are read where the vault keeps them, nothing is unknown."""
    result = run_verifier(layout.vault, "--json", "--kokler", str(ROOTS), timeout=300)
    assert result.returncode == 0, result.stderr
    isolated = json.loads(result.stdout)
    assert (isolated["mode"], isolated["passed"]) == ("vault", True)
    assert isolated["findings"] == _texts("vault", layout.vault)
    assert isolated["days"] == _days_block()
    assert isolated["counts"] == {"ok": 15, "info": 11, "warn": 8, "fail": 0}
