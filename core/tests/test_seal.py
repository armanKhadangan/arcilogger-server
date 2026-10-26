"""Daily sealing: manifest, chain, time stamp, verification, lock, retries."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from arcilogger_core import seal as seal_module
from arcilogger_core.alerts import read_alerts
from arcilogger_core.canonical import is_canonical
from arcilogger_core.chain import sealed_days, verify_day, verify_range
from arcilogger_core.evidence import RawRef, build_package
from arcilogger_core.evidence.archive import ArchivePackage
from arcilogger_core.evidence.standalone import verify as sv
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.seal import Sealer, sha256_file, summarize_spool
from arcilogger_core.timeutil import day_window
from arcilogger_core.tsa.mock import MockFailure
from arcilogger_core.tsa.verify import verify_token


def _manifest(world: Any, day: date) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(world.layout.manifest(day).read_bytes())
    return data


def _seal_all(world: Any, days: list[date]) -> Sealer:
    for day in days:
        world.busy_day(day)
    world.set_now(days[-1] + timedelta(days=1))
    sealer: Sealer = world.sealer()
    outcomes = sealer.seal_due()
    assert [o.status for o in outcomes] == ["sealed"] * len(days), outcomes
    return sealer


def test_three_days_are_sealed_chained_and_verified(world: Any, days: list[date]) -> None:
    _seal_all(world, days)
    layout = world.layout
    assert sealed_days(layout) == days
    checks = verify_range(layout, days, [world.tsa.root_certificate], deep=True)
    assert [c.errors for c in checks] == [[], [], []]
    assert all(c.serial for c in checks)
    first, second, third = (_manifest(world, d) for d in days)
    assert first["previous"] is None
    assert second["previous"] == {
        "day": "2026-10-10",
        "manifest_sha256": sha256_file(layout.manifest(days[0])),
        "token_sha256": sha256_file(layout.token(days[0])),
    }
    assert third["previous"]["day"] == "2026-10-11"
    for day in days:
        assert is_canonical(layout.manifest(day).read_bytes())
        assert all(is_locked(p) for p in layout.vault_day(day).iterdir())
        assert layout.spool_files_for(day) == []
        assert world.sealer().state(day)["status"] == "sealed"


def test_manifest_contents(world: Any, days: list[date]) -> None:
    day = days[0]
    _seal_all(world, [day])
    manifest = _manifest(world, day)
    assert manifest["schema"] == "arcilogger.manifest/1"
    assert manifest["tz"] == "Europe/Istanbul"
    assert manifest["window"] == {
        "from": "2026-10-10T00:00:00+03:00",
        "to": "2026-10-11T00:00:00+03:00",
        "partition": "receive_time",
    }
    files = {f["source"]: f for f in manifest["files"]}
    assert set(files) == {"fortigate", "core-r1"}
    fortigate = files["fortigate"]
    assert fortigate["lines"] == 144
    assert fortigate["sender_ips"] == ["172.31.255.1"]
    assert fortigate["first_received"] == "2026-10-10T00:00:00.000+03:00"
    assert fortigate["last_received"] == "2026-10-10T23:50:00.000+03:00"
    assert fortigate["path"] == "2026/10/10/fortigate.log.gz"
    gaps = {(g["source"], g["reason"]) for g in manifest["gaps"]}
    assert ("switch-1", "no messages") in gaps
    assert ("core-r1", "no messages > 60 min") in gaps
    assert not any(g["source"] == "fortigate" for g in manifest["gaps"])
    assert manifest["tsa"] == {"provider": "mock", "legal_grade": False, "digest": "sha256"}
    assert manifest["clock"]["status"] == "test clock status"
    assert manifest["audit_log"]["entries"] == 0
    assert manifest["stamped_late"] is False


def test_compressed_file_is_deterministic_gzip_without_name(world: Any, days: list[date]) -> None:
    _seal_all(world, [days[0]])
    path = world.layout.vault_day(days[0]) / "fortigate.log.gz"
    header = path.read_bytes()[:10]
    assert header[3] & 0x08 == 0  # FNAME flag not set
    assert header[4:8] == b"\x00\x00\x00\x00"  # mtime 0
    assert gzip.decompress(path.read_bytes()).count(b"\n") == 144


def test_clock_samples_and_audit_log_are_included(world: Any, days: list[date]) -> None:
    day = days[0]
    world.busy_day(day)
    samples = world.layout.clock_samples(day)
    samples.parent.mkdir(parents=True)
    samples.write_text('{"offset_us": 3200}\n{"offset_us": -4100}\n{"offset_us": null}\n')
    audit = world.layout.audit_spool(day)
    audit.parent.mkdir(parents=True)
    audit.write_text('{"action":"login","hash":"aa"}\n{"action":"lookup","hash":"bb"}\n')
    world.set_now(day + timedelta(days=1))
    assert world.sealer().seal_day(day).status == "sealed"
    manifest = _manifest(world, day)
    assert manifest["clock"]["samples"] == 2
    assert manifest["clock"]["max_offset_us"] == 4100
    # No local time source configured (the default): the server follows the fallback.
    assert manifest["clock"]["ntp_server"] == "time.ume.tubitak.gov.tr"
    assert manifest["system"]["site"] == ""  # no organisation name by default
    assert manifest["audit_log"]["entries"] == 2
    assert manifest["audit_log"]["last_entry_hash"] == "bb"
    assert not audit.exists()
    world.reconfigure(site_name="Örnek A.Ş.", clock={"ntp_server": "172.20.10.1"})
    world.busy_day(days[1])
    world.set_now(days[1] + timedelta(days=1))
    assert world.sealer().seal_day(days[1]).status == "sealed"
    later = _manifest(world, days[1])
    assert (later["clock"]["ntp_server"], later["system"]["site"]) == ("172.20.10.1", "Örnek A.Ş.")


def test_torn_audit_spool_is_retried_not_sealed_or_crashed(world: Any, days: list[date]) -> None:
    """A crash mid-append leaves a line without '\\n': refuse (retry later), never seal it."""
    day = days[0]
    world.busy_day(day)
    audit = world.layout.audit_spool(day)
    audit.parent.mkdir(parents=True)
    complete = b'{"action":"login","hash":"aa"}\n'
    audit.write_bytes(complete + b'{"action":"look')
    world.set_now(day + timedelta(days=1))
    outcome = world.sealer().seal_day(day)
    assert outcome.status == "pending"
    assert "incomplete" in outcome.message
    assert audit.read_bytes() == complete + b'{"action":"look'  # the sealer never edits it
    assert world.sealer().state(day)["status"] == "pending"
    audit.write_bytes(complete)  # what the API's next flush does
    world.set_now(day + timedelta(days=2))
    assert world.sealer().seal_day(day).status == "sealed"
    assert _manifest(world, day)["audit_log"]["entries"] == 1


@pytest.mark.parametrize("last", [b"not json\n", b'["a"]\n', b'{"hash": 5}\n'])
def test_audit_spool_with_a_foreign_last_line_needs_a_person(
    world: Any, days: list[date], last: bytes
) -> None:
    day = days[0]
    world.busy_day(day)
    audit = world.layout.audit_spool(day)
    audit.parent.mkdir(parents=True)
    audit.write_bytes(b'{"action":"login","hash":"aa"}\n' + last)
    world.set_now(day + timedelta(days=1))
    outcome = world.sealer().seal_day(day)
    assert outcome.status == "failed"  # not retryable
    assert "not an audit entry" in outcome.message
    assert audit.exists()


def test_empty_day_in_the_middle_is_sealed_with_gaps(world: Any, days: list[date]) -> None:
    world.busy_day(days[0])
    world.busy_day(days[2])
    world.set_now(days[2] + timedelta(days=1))
    outcomes = world.sealer().seal_due()
    assert [o.status for o in outcomes] == ["sealed", "sealed", "sealed"]
    middle = _manifest(world, days[1])
    assert middle["files"] == []
    assert {g["source"] for g in middle["gaps"]} == {"fortigate", "core-r1", "switch-1"}
    assert verify_range(world.layout, days, [world.tsa.root_certificate])[2].ok


def test_today_is_never_sealed(world: Any, days: list[date]) -> None:
    world.busy_day(days[2])
    world.set_now(days[2], 18, 0)
    sealer = world.sealer()
    assert sealer.closed_unsealed_days() == []
    outcome = sealer.seal_day(days[2])
    assert outcome.status == "pending"
    assert "not closed" in outcome.message


def test_tsa_down_then_recovery_follows_the_retry_schedule(world: Any, days: list[date]) -> None:
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1), 0, 10)
    world.tsa.failure = MockFailure.UNREACHABLE
    sealer = world.sealer()
    first = sealer.seal_due()
    assert [o.status for o in first] == ["pending"]
    state = sealer.state(days[0])
    assert state["attempts"] == 1
    assert state["next_attempt_at"] == "2026-10-11T00:15:00+03:00"  # +5 min
    assert not world.layout.token(days[0]).exists()
    assert not any(is_locked(p) for p in world.layout.vault_day(days[0]).iterdir())
    alerts = read_alerts(world.layout.alerts)
    assert alerts[-1]["rule"] == "R20-seal"
    assert "15" not in alerts[-1]["message"] or "dk" in alerts[-1]["message"]

    world.now += timedelta(minutes=3)
    assert [o.status for o in sealer.seal_due()] == ["waiting"]

    world.now += timedelta(minutes=3)
    world.tsa.failure = MockFailure.NONE
    assert [o.status for o in sealer.seal_due()] == ["sealed"]
    assert sealer.state(days[0])["attempts"] == 2


def test_later_days_wait_for_an_unsealed_day(world: Any, days: list[date]) -> None:
    for day in days:
        world.busy_day(day)
    world.set_now(days[2] + timedelta(days=1))
    world.tsa.failure = MockFailure.NO_CREDIT
    outcomes = world.sealer().seal_due()
    assert [(o.day, o.status) for o in outcomes] == [(days[0], "pending")]
    assert world.layout.spool_files_for(days[1])
    assert world.sealer().seal_day(days[1]).status == "pending"  # previous day not sealed


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (MockFailure.NO_CREDIT, "Not enough credit"),
        (MockFailure.WRONG_IMPRINT, "imprint"),
        (MockFailure.EXPIRED_CERT, "verification failed"),
        (MockFailure.BAD_SIGNATURE, "verification failed"),
        (MockFailure.REJECTION, "rejected"),
    ],
)
def test_bad_stamps_never_seal_a_day(
    world: Any, days: list[date], failure: MockFailure, message: str
) -> None:
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1))
    world.tsa.failure = failure
    outcome = world.sealer().seal_day(days[0])
    assert outcome.status == "pending"
    assert message in outcome.message
    assert not world.layout.token(days[0]).exists()
    assert world.layout.spool_files_for(days[0])  # raw data still in the spool


def test_late_stamp_is_marked_with_the_reason(world: Any, days: list[date]) -> None:
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1))
    world.tsa.failure = MockFailure.UNREACHABLE
    sealer = world.sealer()
    sealer.seal_day(days[0])
    world.tsa.failure = MockFailure.NONE
    world.now = day_window(days[0])[1] + timedelta(hours=50)
    alerts_before = len(read_alerts(world.layout.alerts))
    assert sealer.seal_day(days[0]).status == "sealed"
    manifest = _manifest(world, days[0])
    assert manifest["stamped_late"] is True
    assert "unreachable" in manifest["late_reason"]
    assert sealer.state(days[0])["stamped_late"] is True
    assert len(read_alerts(world.layout.alerts)) == alerts_before


def test_overdue_failures_raise_critical_alerts(world: Any, days: list[date]) -> None:
    world.busy_day(days[0])
    world.now = day_window(days[0])[1] + timedelta(hours=49)
    world.tsa.failure = MockFailure.UNREACHABLE
    world.sealer().seal_day(days[0])
    assert read_alerts(world.layout.alerts)[-1]["severity"] == "critical"


def test_crash_after_stamping_resumes_without_a_new_stamp(
    world: Any, days: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1))
    sealer = world.sealer()
    original = Sealer._finish
    calls = {"n": 0}

    def crash_once(self: Sealer, *args: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("power cut")
        return original(self, *args)

    monkeypatch.setattr(Sealer, "_finish", crash_once)
    assert sealer.seal_day(days[0]).status == "pending"
    credits = world.tsa.credits
    assert sealer.seal_day(days[0]).status == "sealed"
    assert world.tsa.credits == credits  # no second stamp was bought
    assert verify_day(world.layout, days[0], [world.tsa.root_certificate], deep=True).ok


def test_spool_still_being_written_is_not_sealed(world: Any, days: list[date]) -> None:
    world.reconfigure(seal={"quiet_seconds": 3600})
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1))
    outcome = world.sealer().seal_day(days[0])
    assert outcome.status == "pending"
    assert "still being written" in outcome.message


def test_summarize_spool_counts_unparseable_lines_without_failing(world: Any) -> None:
    day = date(2026, 10, 10)
    path = world.write_spool("fortigate", day, [(time(1, 0), b"<1>a")])
    with path.open("ab") as handle:
        handle.write(b"corrupt line without time\n")
    stats = summarize_spool(path, timedelta(minutes=60), day_window(day))
    assert stats["lines"] == 2
    assert stats["sender_ips"] == ["172.31.255.1"]


def test_unlocked_vault_file_is_reported_by_verification(world: Any, days: list[date]) -> None:
    _seal_all(world, [days[0]])
    path = world.layout.vault_day(days[0]) / "core-r1.log.gz"
    unlock(path)
    check = verify_day(world.layout, days[0], [world.tsa.root_certificate])
    assert check.ok
    assert any("not read-only" in warning for warning in check.warnings)


# -- a torn last line (crash or full disk while writing) -----------------------------------------

TORN = b"2026-10-10T23:59:59.000000+03:00 172.31.255.1 <189>date=2026-10-10 time=23:59:59 devname="


def _tear(world: Any, day: date) -> bytes:
    path = world.layout.spool_file("fortigate", day)
    with path.open("ab") as handle:
        handle.write(TORN)  # no "\n": the write stopped in the middle of the line
    data: bytes = path.read_bytes()
    return data


def test_a_torn_last_line_is_sealed_as_it_is_and_later_days_follow(
    world: Any, days: list[date]
) -> None:
    for day in days[:2]:
        world.busy_day(day)
    raw = _tear(world, days[0])
    world.set_now(days[1] + timedelta(days=1))
    outcomes = world.sealer().seal_due()
    assert [o.status for o in outcomes] == ["sealed", "sealed"], outcomes
    files = {f["source"]: f for f in _manifest(world, days[0])["files"]}
    assert files["fortigate"]["lines"] == 145  # 144 complete lines + the torn one
    assert files["fortigate"]["unterminated_last_line"] is True
    assert "unterminated_last_line" not in files["core-r1"]
    sealed = world.layout.vault_day(days[0]) / "fortigate.log.gz"
    assert gzip.decompress(sealed.read_bytes()) == raw  # the bytes exactly as they were
    checks = verify_range(world.layout, days[:2], [world.tsa.root_certificate], deep=True)
    assert [c.errors for c in checks] == [[], []]
    # The standalone verifier counts lines by the same rule and says what it found.
    info = build_package(
        world.layout,
        days[:2],
        roots=[world.tsa.root_certificate],
        extract=[RawRef(days[0], "fortigate", 145)],
        out_dir=world.root / "out",
    )
    with ArchivePackage(info.path, None) as package:
        report = sv.verify_package(package, deep=True)
    assert report.passed, [(f.code, f.en) for f in report.findings if f.level == "fail"]
    assert "file-torn-last-line" in report.codes("info")
    assert "extract-ok" in report.codes("ok")


def test_a_day_stuck_on_a_torn_line_is_finished_without_a_new_stamp(
    world: Any, days: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Days stamped by the earlier code (which then refused its own manifest) are finished."""
    world.busy_day(days[0])
    _tear(world, days[0])
    world.set_now(days[0] + timedelta(days=1))
    original = seal_module.gunzip_sha256

    def old_count(path: Any) -> tuple[str, int]:
        digest, lines = original(path)
        return digest, lines - 1  # the old rule: only "\n" counted

    monkeypatch.setattr(seal_module, "gunzip_sha256", old_count)
    sealer = world.sealer()
    stuck = sealer.seal_day(days[0])
    assert stuck.status == "pending"
    assert "content does not match the spool" in stuck.message
    assert world.layout.token(days[0]).exists()  # stamped, but not finished
    monkeypatch.undo()
    credits = world.tsa.credits
    world.now += timedelta(hours=7)
    assert [o.status for o in sealer.seal_due()] == ["sealed"]
    assert world.tsa.credits == credits  # the existing stamp was used
    assert verify_day(world.layout, days[0], [world.tsa.root_certificate], deep=True).ok


def test_line_counts_agree_for_torn_and_empty_files(tmp_path: Any) -> None:
    window = day_window(date(2026, 10, 10))
    for content, lines in ((b"", 0), (b"a\n", 1), (b"a\nb", 2), (b"\n\n", 2)):
        spool = tmp_path / "spool.log"
        spool.write_bytes(content)
        target = tmp_path / "spool.log.gz"
        seal_module.compress_deterministic(spool, target, 6)
        assert summarize_spool(spool, timedelta(minutes=60), window)["lines"] == lines
        assert seal_module.gunzip_sha256(target)[1] == lines
        with target.open("rb") as handle:
            assert sv.gunzip_digest(handle)[1] == lines


# -- one verdict with the standalone verifier; the EKU rule of the site (K6) ---------------------


def _tsa_certificate(world: Any, *, eku_critical: bool = True, signing: bool = True) -> Any:
    """A TSA certificate of the world's mock root (optionally a non-critical EKU, or a key
    usage that does not allow signing)."""
    root_key = serialization.load_pem_private_key(
        (world.tsa.state_dir / "root.key").read_bytes(), None
    )
    assert isinstance(root_key, ec.EllipticCurvePrivateKey)
    key = ec.generate_private_key(ec.SECP256R1())
    start = datetime.now(UTC) - timedelta(days=30)
    usage = x509.KeyUsage(
        digital_signature=signing,
        content_commitment=False,
        key_encipherment=not signing,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=False,
        crl_sign=False,
        encipher_only=False,
        decipher_only=False,
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Rule TSA")]))
        .issuer_name(world.tsa.root_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), eku_critical)
        .add_extension(usage, True)
        .sign(root_key, hashes.SHA256())
    )
    return certificate, key


def test_a_report_stamped_with_a_non_critical_eku_keeps_its_root_when_the_site_accepts_it(
    world: Any, days: list[date], tmp_path: Any
) -> None:
    """The package applies the sealed EKU rule to the report's token too (finding 7): its root
    is bundled, and the standalone verifier accepts it with a warning."""
    from types import SimpleNamespace

    from arcilogger_core.evidence import requires_critical_eku
    from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
    from arcilogger_core.tsa.verify import fingerprint

    world.reconfigure(tsa={"provider": "mock", "require_critical_eku": False})
    world.tsa.signer = _tsa_certificate(world, eku_critical=False)
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1))
    assert world.sealer().seal_day(days[0]).status == "sealed"
    assert requires_critical_eku(world.layout, [days[0]]) is False
    assert requires_critical_eku(world.layout, [days[1]]) is True  # nothing sealed: strict
    other = MockTsa(tmp_path / "report-tsa", key_type="ec")  # the report's TSA, its own root
    other.signer = _tsa_certificate(SimpleNamespace(tsa=other), eku_critical=False)
    report = tmp_path / "rapor.pdf"
    report.write_bytes(b"%PDF-1.7 report")
    token = MockTsaProvider(other).stamp(report).token_path.read_bytes()
    info = build_package(
        world.layout,
        [days[0]],
        roots=[world.tsa.root_certificate, other.root_certificate],
        report_pdf=report.read_bytes(),
        report_token=token,
        out_dir=tmp_path / "out",
    )
    with ArchivePackage(info.path, None) as package:
        index = json.loads(package.read("paket.json"))
        report_check = sv.verify_package(package, deep=False)
    assert fingerprint(other.root_certificate) in {root["sha256"] for root in index["roots"]}
    assert report_check.passed, [(f.code, f.en) for f in report_check.findings if f.level == "fail"]
    assert "token-eku-not-critical" in report_check.codes("warn")


def test_a_token_the_package_verifier_would_refuse_is_refused_at_sealing(
    world: Any, days: list[date]
) -> None:
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1))
    world.tsa.signer = _tsa_certificate(world, signing=False)  # keyEncipherment only
    outcome = world.sealer().seal_day(days[0])
    assert outcome.status == "pending"
    assert "key usage does not allow signing" in outcome.message
    assert not world.layout.token(days[0]).exists()
    assert world.layout.spool_files_for(days[0])


def test_a_non_critical_eku_is_accepted_only_when_the_site_says_so(
    world: Any, days: list[date], tmp_path: Any
) -> None:
    world.tsa.signer = _tsa_certificate(world, eku_critical=False)
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1))
    strict = world.sealer().seal_day(days[0])
    assert strict.status == "pending"
    assert "not critical" in strict.message
    world.reconfigure(tsa={"provider": "mock", "require_critical_eku": False})
    world.now += timedelta(hours=7)
    assert world.sealer().seal_day(days[0]).status == "sealed"
    manifest = _manifest(world, days[0])
    assert manifest["tsa"]["require_critical_eku"] is False  # sealed with the day
    roots = [world.tsa.root_certificate]
    check = verify_day(world.layout, days[0], roots)
    assert check.ok, check.errors
    assert any("not critical" in warning for warning in check.warnings)
    # The package's verifier takes the rule from the sealed manifest: a warning, not a failure.
    info = build_package(world.layout, [days[0]], roots=roots, out_dir=tmp_path / "out")
    with ArchivePackage(info.path, None) as package:
        report = sv.verify_package(package, deep=False)
    assert report.passed, [(f.code, f.en) for f in report.findings if f.level == "fail"]
    assert "token-eku-not-critical" in report.codes("warn")
    # Without that sealed statement the same token fails in both verifiers.
    token = world.layout.token(days[0]).read_bytes()
    manifest_bytes = world.layout.manifest(days[0]).read_bytes()
    assert not verify_token(token, roots=roots, file_path=world.layout.manifest(days[0])).ok
    standalone_roots = [
        sv.parse_certificate(r.public_bytes(serialization.Encoding.DER)) for r in roots
    ]
    assert not sv.verify_timestamp(token, manifest_bytes, standalone_roots).ok


# -- seal requests of the browser administration ("Şimdi mühürle", ADR-0014) --------------------

REQUEST = "5e" * 16


def test_a_requested_day_does_not_wait_for_its_retry_time(world: Any, days: list[date]) -> None:
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1), 0, 10)
    world.tsa.failure = MockFailure.UNREACHABLE
    sealer = world.sealer()
    assert [o.status for o in sealer.seal_due()] == ["pending"]
    world.tsa.failure = MockFailure.NONE
    world.now += timedelta(minutes=1)
    assert [o.status for o in sealer.seal_due()] == ["waiting"]  # retry in 4 more minutes
    assert [o.status for o in sealer.seal_due(requested={days[0]: REQUEST})] == ["sealed"]
    state = sealer.state(days[0])
    assert state["attempts"] == 2
    assert state["requested"] == REQUEST


def test_a_request_keeps_the_quiet_window(world: Any, days: list[date]) -> None:
    world.reconfigure(seal={"quiet_seconds": 600})
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1), 0, 5)
    outcomes = world.sealer().seal_due(requested={days[0]: REQUEST})
    assert [(o.status, o.message) for o in outcomes] == [
        ("waiting", f"{days[0]} ended less than 600 s ago")
    ]
    assert not world.layout.seal_state(days[0]).exists()  # no attempt


def test_a_request_never_seals_out_of_order(world: Any, days: list[date]) -> None:
    for day in days[:2]:
        world.busy_day(day)
    world.set_now(days[1] + timedelta(days=1), 0, 10)
    world.tsa.failure = MockFailure.UNREACHABLE
    assert [o.status for o in world.sealer().seal_due()] == ["pending"]
    world.tsa.failure = MockFailure.NONE
    outcomes = world.sealer().seal_due(requested={days[1]: REQUEST})
    assert [(o.day, o.status) for o in outcomes] == [(days[0], "waiting")]
    assert not world.layout.seal_state(days[1]).exists()


def test_a_request_never_stamps_a_day_of_a_long_empty_run(world: Any) -> None:
    world.reconfigure(seal={"quiet_seconds": 0, "gzip_level": 6, "max_consecutive_empty_days": 0})
    first, empty = date(2026, 10, 1), date(2026, 10, 2)
    world.busy_day(first)
    world.set_now(empty + timedelta(days=1))
    outcomes = world.sealer().seal_due(requested={empty: REQUEST})
    assert [(o.day, o.status) for o in outcomes] == [(first, "sealed"), (empty, "skipped")]
    assert "requested" not in world.sealer().state(empty)
    assert world.sealer().seal_day(empty, requested=REQUEST).status == "skipped"
    assert "requested" not in world.sealer().state(empty)


def test_a_later_attempt_without_a_request_forgets_it(world: Any, days: list[date]) -> None:
    world.busy_day(days[0])
    world.set_now(days[0] + timedelta(days=1), 0, 10)
    world.tsa.failure = MockFailure.UNREACHABLE
    sealer = world.sealer()
    assert sealer.seal_day(days[0], requested=REQUEST).status == "pending"
    assert sealer.state(days[0])["requested"] == REQUEST
    world.tsa.failure = MockFailure.NONE
    world.now += timedelta(minutes=10)
    assert [o.status for o in sealer.seal_due()] == ["sealed"]
    assert "requested" not in sealer.state(days[0])
