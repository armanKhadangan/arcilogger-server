"""Revocation in the core verifier (Wave 5 D10): the same code and level as the standalone
verifier on the shared case table (rule 5), and ``crl-revoked`` fails the day check."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from asn1crypto import cms, tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from arcilogger_core.chain import verify_day, verify_range
from arcilogger_core.crl import INDEX_NAME, INDEX_SCHEMA, Crl, load_stored, parse_crl
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site
from arcilogger_core.tsa.asn1 import build_request
from arcilogger_core.tsa.mock import MockTsa
from arcilogger_core.tsa.verify import (
    CRL_LEVELS,
    check_revocation,
    fingerprint,
    token_chain,
    verify_token,
)

from .revocation_cases import (
    CASES,
    DAY,
    HOUR,
    CrlSpec,
    Entry,
    PrivateKey,
    RevocationCase,
    core_verdict,
    make_lists,
    standalone_verdict,
)

DATA = b'{"day":"2026-10-12","schema":"arcilogger.manifest/1"}'
#: The stamp time of every token here (whole seconds: a CRL date has no fractions).
MOMENT = datetime(2026, 10, 12, 21, 10, 5, tzinfo=UTC)
NOW = MOMENT + HOUR


def _key_of(tsa: MockTsa, name: str) -> PrivateKey:
    key = serialization.load_pem_private_key((tsa.state_dir / name).read_bytes(), None)
    assert isinstance(key, rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey)
    return key


def _stamp(tsa: MockTsa, data: bytes = DATA) -> bytes:
    request, _ = build_request(hashlib.sha256(data).digest(), "sha256")
    return tsa.respond(request)


@pytest.fixture(scope="module", params=["ec", "rsa"])
def tsa(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> MockTsa:
    folder = tmp_path_factory.mktemp(f"tsa-{request.param}")
    return MockTsa(folder, key_type=request.param, clock=lambda: MOMENT)


@pytest.fixture(scope="module")
def token(tsa: MockTsa) -> bytes:
    return _stamp(tsa)


# -- the shared table: both verifiers, one verdict -------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_both_verifiers_give_the_code_and_level_of_the_case(
    tsa: MockTsa, token: bytes, case: RevocationCase
) -> None:
    lists = make_lists(
        case.lists, tsa.root_certificate, _key_of(tsa, "root.key"), tsa.tsa_certificate, MOMENT
    )
    roots = [tsa.root_certificate]
    expected = (case.code, case.level)
    assert core_verdict(token, DATA, roots, lists, NOW) == expected
    if case.standalone_before_c6 is None:
        assert standalone_verdict(token, DATA, roots, lists, NOW) == [expected]
        return
    # Interim (C6, see revocation_cases): exactly what the standalone verifier gives today.
    today: list[tuple[str, str]] | str
    try:
        today = standalone_verdict(token, DATA, roots, lists, NOW)
    except TypeError as exc:
        today = type(exc).__name__
    before = case.standalone_before_c6
    assert today == (before if isinstance(before, str) else [before]), (
        f"The standalone verifier now gives {today!r} for {case.name!r}. Once W5-A applies C6 "
        f"to the standalone verifier, this case must agree with the core ({expected!r}): "
        "remove standalone_before_c6 from the case in core/tests/revocation_cases.py; when no "
        "case has it, remove the field, the module-docstring paragraph and this branch (W5-B-9)."
    )


def test_the_table_covers_every_code_with_its_level() -> None:
    assert {(case.code, case.level) for case in CASES} == set(CRL_LEVELS.items())
    assert set(CRL_LEVELS) == {
        "crl-revoked",
        "crl-revoked-later",
        "crl-hold",
        "crl-none",
        "crl-stale",
        "crl-ok",
    }


def test_revocation_changes_no_other_part_of_the_result(tsa: MockTsa, token: bytes) -> None:
    """Rule 4: ``ok``, the errors, the root and its fingerprint stay as they were."""
    digest = hashlib.sha256(DATA).digest()
    revoked = next(case for case in CASES if case.code == "crl-revoked")
    lists = make_lists(
        revoked.lists, tsa.root_certificate, _key_of(tsa, "root.key"), tsa.tsa_certificate, MOMENT
    )
    roots = [tsa.root_certificate]
    plain = verify_token(token, roots=roots, digest=digest, now=NOW)
    checked = verify_token(
        token, roots=roots, digest=digest, now=NOW, crls=[parse_crl(der) for der in lists]
    )
    assert plain.details["crl"] == "not checked"
    assert "crl_level" not in plain.details
    assert checked.details["crl"] == "crl-revoked"
    assert (checked.ok, checked.errors, checked.warnings, checked.info) == (
        plain.ok,
        plain.errors,
        plain.warnings,
        plain.info,
    )
    assert {k: v for k, v in checked.details.items() if not k.startswith("crl")} == {
        k: v for k, v in plain.details.items() if not k.startswith("crl")
    }
    assert checked.details["root_sha256"] == fingerprint(tsa.root_certificate)
    assert checked.details["crl_subject"] == tsa.tsa_certificate.subject.rfc4514_string()
    assert checked.details["crl_at"] == (MOMENT - DAY).isoformat()


def test_no_list_given_is_crl_none_in_both(tsa: MockTsa, token: bytes) -> None:
    """An empty sequence is a check without lists (``crl-none``); None is no check at all."""
    roots = [tsa.root_certificate]
    assert core_verdict(token, DATA, roots, [], NOW) == ("crl-none", "info")
    assert standalone_verdict(token, DATA, roots, [], NOW) == [("crl-none", "info")]
    result = verify_token(token, roots=roots, digest=hashlib.sha256(DATA).digest(), crls=[])
    assert result.details["crl_at"] == ""


def test_a_token_that_does_not_verify_is_not_checked(tsa: MockTsa, token: bytes) -> None:
    """The standalone verifier checks revocation only for a token that verifies."""
    revoked = next(case for case in CASES if case.code == "crl-revoked")
    lists = make_lists(
        revoked.lists, tsa.root_certificate, _key_of(tsa, "root.key"), tsa.tsa_certificate, MOMENT
    )
    result = verify_token(
        token,
        roots=[tsa.root_certificate],
        digest=hashlib.sha256(b"other data").digest(),
        now=NOW,
        crls=[parse_crl(der) for der in lists],
    )
    assert not result.ok
    assert result.details["crl"] == "not checked"


def test_a_pinned_tsa_certificate_has_nothing_to_check(tsa: MockTsa, token: bytes) -> None:
    """A chain that ends at the TSA certificate itself has no certificate below its root."""
    roots = [tsa.root_certificate, tsa.tsa_certificate]
    revoked = next(case for case in CASES if case.code == "crl-revoked")
    lists = make_lists(
        revoked.lists, tsa.root_certificate, _key_of(tsa, "root.key"), tsa.tsa_certificate, MOMENT
    )
    assert standalone_verdict(token, DATA, roots, lists, NOW) == []
    result = verify_token(
        token,
        roots=roots,
        digest=hashlib.sha256(DATA).digest(),
        now=NOW,
        crls=[parse_crl(der) for der in lists],
    )
    assert result.ok
    assert result.details["crl"] == "not checked"


# -- a chain with an intermediate CA ---------------------------------------------------------------


def _name(common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "TR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Örnek A.Ş. TEST"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def _certificate(
    common_name: str,
    key: PrivateKey,
    issuer: tuple[x509.Certificate, PrivateKey] | None,
    *,
    ca: bool = True,
) -> x509.Certificate:
    """A CA certificate, or (``ca`` false) a TSA certificate."""
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(issuer[0].subject if issuer else _name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(MOMENT - 400 * DAY)
        .not_valid_after(MOMENT + 3650 * DAY)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if not ca:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True
        )
    return builder.sign(issuer[1] if issuer else key, hashes.SHA256())


def _with_certificate(token: bytes, certificate: x509.Certificate) -> bytes:
    """``token`` with ``certificate`` added to its (unsigned) certificate set."""
    response = tsp.TimeStampResp.load(token)
    signed = response["time_stamp_token"]["content"]
    der = certificate.public_bytes(Encoding.DER)
    signed["certificates"] = [
        *signed["certificates"],
        cms.CertificateChoices(name="certificate", value=asn1_x509.Certificate.load(der)),
    ]
    return bytes(response.dump(force=True))


class ThreeLevels:
    """Root → intermediate CA → TSA certificate, and a token signed by the TSA certificate."""

    def __init__(self, folder: Path) -> None:
        self.root_key = ec.generate_private_key(ec.SECP256R1())
        self.root = _certificate("Örnek Root (TEST)", self.root_key, None)
        self.ca_key = ec.generate_private_key(ec.SECP256R1())
        self.ca = _certificate("Örnek TSA CA (TEST)", self.ca_key, (self.root, self.root_key))
        self.tsa_key = ec.generate_private_key(ec.SECP256R1())
        self.tsa = _certificate("Örnek TSA (TEST)", self.tsa_key, (self.ca, self.ca_key), ca=False)
        mock = MockTsa(folder, key_type="ec", clock=lambda: MOMENT)
        mock.signer = (self.tsa, self.tsa_key)
        self.token = _with_certificate(_stamp(mock), self.ca)

    def lists(self, tsa_lists: tuple[CrlSpec, ...], ca_lists: tuple[CrlSpec, ...]) -> list[bytes]:
        return [
            *make_lists(tsa_lists, self.ca, self.ca_key, self.tsa, MOMENT),
            *make_lists(ca_lists, self.root, self.root_key, self.ca, MOMENT),
        ]


@pytest.fixture(scope="module")
def three(tmp_path_factory: pytest.TempPathFactory) -> ThreeLevels:
    return ThreeLevels(tmp_path_factory.mktemp("three"))


@pytest.mark.parametrize(
    ("tsa_lists", "ca_lists", "findings", "worst"),
    [
        pytest.param(
            (CrlSpec(DAY),),
            (CrlSpec(DAY, (Entry(DAY),)),),
            [("crl-ok", "ok"), ("crl-revoked-later", "warn")],
            ("crl-revoked-later", "Örnek TSA CA (TEST)"),
            id="the CA revoked later",
        ),
        pytest.param(
            (),
            (CrlSpec(DAY),),
            [("crl-none", "info"), ("crl-ok", "ok")],
            ("crl-none", "Örnek TSA (TEST)"),
            id="no list for the TSA certificate",
        ),
        pytest.param(
            (CrlSpec(-HOUR),),
            (),
            [("crl-stale", "info"), ("crl-none", "info")],
            ("crl-stale", "Örnek TSA (TEST)"),
            id="equal levels: the first of the chain",
        ),
        pytest.param(
            (CrlSpec(DAY, (Entry(-DAY),)),),
            (CrlSpec(DAY, (Entry(-DAY, x509.ReasonFlags.certificate_hold),)),),
            [("crl-revoked", "fail"), ("crl-hold", "warn")],
            ("crl-revoked", "Örnek TSA (TEST)"),
            id="a failure outranks a warning",
        ),
    ],
)
def test_every_certificate_below_the_root_is_checked(
    three: ThreeLevels,
    tsa_lists: tuple[CrlSpec, ...],
    ca_lists: tuple[CrlSpec, ...],
    findings: list[tuple[str, str]],
    worst: tuple[str, str],
) -> None:
    lists = three.lists(tsa_lists, ca_lists)
    assert standalone_verdict(three.token, DATA, [three.root], lists, NOW) == findings
    core = check_revocation(
        [three.tsa, three.ca, three.root], [parse_crl(d) for d in lists], MOMENT
    )
    assert [(finding.code, finding.level) for finding in core] == findings
    result = verify_token(
        three.token,
        roots=[three.root],
        digest=hashlib.sha256(DATA).digest(),
        now=NOW,
        crls=[parse_crl(der) for der in lists],
    )
    assert result.ok, result.errors
    code, common_name = worst
    assert result.details["crl"] == code
    assert result.details["crl_level"] == CRL_LEVELS[code]
    assert f"CN={common_name}" in result.details["crl_subject"]


def test_token_chain_is_the_chain_the_revocation_check_walks(three: ThreeLevels) -> None:
    """The builder ships the lists with an entry for these certificates (C8)."""
    assert token_chain(three.token, [three.root]) == [three.tsa, three.ca, three.root]
    assert token_chain(three.token, []) == []  # no pinned root reached
    assert token_chain(b"not a token", [three.root]) == []


def test_a_list_without_its_file_hash_is_checked_too(three: ThreeLevels) -> None:
    lists = three.lists((CrlSpec(DAY, (Entry(-DAY),)),), ())
    crls = [replace(parse_crl(der), sha256="") for der in lists]
    findings = check_revocation([three.tsa, three.ca, three.root], crls, MOMENT)
    assert [finding.code for finding in findings] == ["crl-revoked", "crl-none"]


def test_the_remembered_signature_check_is_per_issuer_key(three: ThreeLevels) -> None:
    """A list verified once with its issuer is not taken as signed by a certificate of the
    same name with another key."""
    (crl,) = [parse_crl(der) for der in three.lists((CrlSpec(DAY, (Entry(-DAY),)),), ())]
    impostor_key = ec.generate_private_key(ec.SECP256R1())
    impostor = _certificate("Örnek TSA CA (TEST)", impostor_key, (three.root, three.root_key))
    assert crl.signed_by(three.ca)
    assert not crl.signed_by(impostor)
    assert crl.signed_by(three.ca)
    assert check_revocation([three.tsa, impostor, three.root], [crl], MOMENT)[0].code == (
        "crl-none"
    )


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if "(C6)" in case.name],
    ids=lambda case: case.name,
)
def test_c6_a_revocation_before_the_stamp_outranks_a_hold_in_any_order(
    tsa: MockTsa, case: RevocationCase
) -> None:
    """C6, in the core: the order of the lists does not change the verdict (a hold kept on an
    older list, the revocation for good on a newer one, or the other way round)."""
    lists = make_lists(
        case.lists, tsa.root_certificate, _key_of(tsa, "root.key"), tsa.tsa_certificate, MOMENT
    )
    for order in (lists, lists[::-1]):
        findings = check_revocation(
            [tsa.tsa_certificate, tsa.root_certificate], [parse_crl(der) for der in order], MOMENT
        )
        assert [(finding.code, finding.level) for finding in findings] == [("crl-revoked", "fail")]


# -- the day check ---------------------------------------------------------------------------------


def _seal(world: Any, day: date) -> datetime:
    """Seal ``day`` and return its stamp time."""
    world.busy_day(day)
    world.set_now(day + timedelta(days=1))
    assert all(o.status == "sealed" for o in world.sealer().seal_due())
    info = verify_token(
        world.layout.token(day).read_bytes(),
        roots=[world.tsa.root_certificate],
        file_path=world.layout.manifest(day),
    ).info
    assert info is not None
    return info.gen_time


def _store(layout: Layout, lists: list[bytes], issuer: x509.Certificate) -> None:
    """Stored as ``crl fetch`` stores them (§2.2): the files and their index entries."""
    layout.crl_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for der in lists:
        crl = replace(parse_crl(der), issuer_sha256=fingerprint(issuer))
        (layout.crl_dir / crl.file_name).write_bytes(der)
        entries.append(
            {
                "url": "http://crl.example.com/test.crl",
                "file": crl.file_name,
                "issuer": crl.issuer,
                "issuer_sha256": crl.issuer_sha256,
                "this_update": crl.this_update.isoformat(),
                "next_update": crl.next_update.isoformat() if crl.next_update else None,
                "sha256": crl.sha256,
                "fetched_at": crl.this_update.isoformat(),
            }
        )
    index = {"schema": INDEX_SCHEMA, "lists": entries, "failures": []}
    (layout.crl_dir / INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")


def _day_with_lists(world: Any, day: date, case: RevocationCase) -> datetime:
    moment = _seal(world, day)
    tsa = world.tsa
    lists = make_lists(
        case.lists, tsa.root_certificate, _key_of(tsa, "root.key"), tsa.tsa_certificate, moment
    )
    _store(world.layout, lists, tsa.root_certificate)
    return moment


def _case(code: str) -> RevocationCase:
    return next(case for case in CASES if case.code == code)


def test_a_certificate_revoked_before_the_stamp_fails_the_day(world: Any, days: list[date]) -> None:
    _day_with_lists(world, days[0], _case("crl-revoked"))
    roots = [world.tsa.root_certificate]
    check = verify_day(world.layout, days[0], roots)
    assert not check.ok
    (error,) = check.errors
    subject = world.tsa.tsa_certificate.subject.rfc4514_string()
    # The text of the contract (§2.6 certificate_revoked); the API gives the Turkish one.
    assert error == (
        f"A time-stamp certificate is on a revocation list: {subject}. Contact the provider; "
        "legal review is recommended."
    )
    # The rest of the day's verdict is as before: the token verifies and chains to the root.
    assert check.root_sha256 == fingerprint(world.tsa.root_certificate)
    (ranged,) = verify_range(world.layout, [days[0]], roots)
    assert ranged.errors == check.errors


@pytest.mark.parametrize(
    ("code", "warning"),
    [
        (
            "crl-revoked-later",
            "a certificate of the time stamp's chain was revoked after the stamp, on {when}: "
            "{name}; the reason for the revocation should be reviewed",
        ),
        (
            "crl-hold",
            "a certificate of the time stamp's chain is on hold on a revocation list since "
            "{when}: {name}; check whether the hold covers the stamp time",
        ),
        ("crl-ok", None),
        ("crl-stale", None),
        ("crl-none", None),
    ],
)
def test_warnings_and_information_do_not_fail_the_day(
    world: Any, days: list[date], code: str, warning: str | None
) -> None:
    moment = _day_with_lists(world, days[0], _case(code))
    check = verify_day(world.layout, days[0], [world.tsa.root_certificate])
    assert check.ok, check.errors
    revocation = [text for text in check.warnings if "time stamp's chain" in text]
    if warning is None:
        assert revocation == []
    else:
        (entry,) = _case(code).lists[0].entries
        # The date in site time to the second.
        when = isoformat_site(moment + entry.after, "seconds")
        name = world.tsa.tsa_certificate.subject.rfc4514_string()
        assert revocation == [warning.format(when=when, name=name)]


def test_without_stored_lists_the_day_is_as_before(world: Any, days: list[date]) -> None:
    _seal(world, days[0])
    check = verify_day(world.layout, days[0], [world.tsa.root_certificate])
    assert check.ok
    assert not any("iptal" in warning for warning in check.warnings)


def test_an_unreadable_index_is_a_warning(world: Any, days: list[date]) -> None:
    _seal(world, days[0])
    world.layout.crl_dir.mkdir(parents=True)
    (world.layout.crl_dir / INDEX_NAME).write_text("{not json", encoding="utf-8")
    roots = [world.tsa.root_certificate]
    for check in (
        verify_day(world.layout, days[0], roots),
        *verify_range(world.layout, [days[0]], roots),
    ):
        assert check.ok
        assert any(
            warning.startswith("revocation not checked: ") and "index.json" in warning
            for warning in check.warnings
        )


def test_the_range_reads_the_lists_once(
    world: Any, days: list[date], monkeypatch: pytest.MonkeyPatch
) -> None:
    for day in days:
        world.busy_day(day)
    world.set_now(days[-1] + timedelta(days=1))
    assert all(o.status == "sealed" for o in world.sealer().seal_due())
    calls: list[Layout] = []

    def counting(layout: Layout) -> list[Crl]:
        calls.append(layout)
        return load_stored(layout)

    monkeypatch.setattr("arcilogger_core.chain.load_stored", counting)
    checks = verify_range(world.layout, days, [world.tsa.root_certificate])
    assert all(check.ok for check in checks)
    assert len(calls) == 1
