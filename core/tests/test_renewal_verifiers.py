"""The core and the standalone verifier agree on the long-term status of a day's stamp (Wave 5
rule 5, §2.1.4), and ``verify_day`` without ``renewals`` is what it was before (A2 on #132).

The cases are in ``renewal_cases.py``; each is run through both verifiers, which must give the
case's code and level and the same TR/EN texts.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core import renewal as renewal_module
from arcilogger_core.chain import DayCheck, verify_day, verify_range
from arcilogger_core.evidence.standalone import verify as sv
from arcilogger_core.immutability import unlock
from arcilogger_core.renewal import DAY_FINDINGS, finding_time
from arcilogger_core.tsa.client import token_path_for
from arcilogger_core.tsa.verify import inspect_token, verify_token

from .renewal_cases import (
    CASES,
    LEVELS,
    RenewalCase,
    core_status,
    core_verdict,
    renew_day,
    standalone_verdict,
)
from .renewal_world import CNA, DAY, RenewalWorld, make_world


@pytest.fixture
def rw(tmp_path: Path) -> RenewalWorld:
    return make_world(tmp_path / "site")


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_both_verifiers_give_the_code_and_level_of_the_case(
    rw: RenewalWorld, tmp_path: Path, case: RenewalCase
) -> None:
    built = case.build(rw)
    standalone = standalone_verdict(rw, built, tmp_path)
    core = core_verdict(rw, built)
    assert (standalone.code, standalone.level) == (case.code, case.level)
    assert (core.code, core.level) == (case.code, case.level)
    assert (core.message_tr, core.message_en) == (standalone.message_tr, standalone.message_en)
    # state/renewal.json says the same of the token
    assert f"renewal-{core_status(rw, built).replace('_', '-')}" == case.code


def test_the_table_covers_every_code_with_its_level() -> None:
    assert {(case.code, case.level) for case in CASES} == set(LEVELS.items())
    assert len({case.name for case in CASES}) == len(CASES)


def test_the_core_texts_are_the_standalone_texts() -> None:
    """One table of codes, levels and §2.6 texts in both verifiers, and one way to write a
    time in them."""
    assert DAY_FINDINGS == sv.RENEWAL_FINDINGS
    for moment in (CNA, CNA + timedelta(hours=22, minutes=5, seconds=7)):
        assert finding_time(moment) == sv._format_time(moment)


def test_a_renewal_stamped_at_its_own_not_after_verifies_but_does_not_count(
    rw: RenewalWorld, tmp_path: Path
) -> None:
    """Owner answer Q16: certificate validity includes notAfter itself, so a stamp made at that
    instant verifies in both verifiers (``tsa/verify.py:_valid_at``, ``Certificate.valid_at``);
    §2.1.2 counts the certificate as expired from notAfter on, so that renewal's own token is
    expired as soon as it is made and the renewal does not count (the case table's
    ``renewal-due``)."""
    at = CNA - timedelta(days=100)
    record = renew_day(rw, "2031-06-23-1", at, expires=at, not_before=at - timedelta(days=30))
    path = rw.layout.vault / record
    token = token_path_for(path).read_bytes()
    info = inspect_token(token)
    assert info.gen_time == info.tsa_cert_not_after == at
    now = at + timedelta(days=10)
    assert verify_token(token, roots=rw.roots, file_path=path, now=now).ok
    (case,) = [case for case in CASES if "its own certificate's notAfter" in case.name]
    assert case.code == "renewal-due"
    report = rw.status(now)
    assert rw.of(report, f"{record}.zd").status == "uncovered"
    roots = [sv.parse_certificate(root.public_bytes(Encoding.DER)) for root in rw.roots]
    assert sv.verify_timestamp(token, path.read_bytes(), roots, now=now).ok


# -- verify_day without renewals is what it was (A2) -----------------------------------------------

#: The keys of ``DayCheck.as_json`` before A2, in their order (``skipped`` only when set).
BEFORE_A2 = [
    "day",
    "ok",
    "errors",
    "warnings",
    "serial",
    "gen_time",
    "root",
    "root_sha256",
    "legal_grade",
]


def _bytes(checks: list[DayCheck]) -> bytes:
    """What ``arcilogger-core verify --json`` prints for ``checks``."""
    return json.dumps([check.as_json() for check in checks], ensure_ascii=False).encode()


def test_without_renewals_every_caller_sees_the_same_bytes(
    rw: RenewalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``renewals`` defaults to False, and then nothing looks at renewals: the output of
    ``verify_day`` and ``verify_range`` (``verify --json``, the API's seal check, the report
    inputs, retention) is byte for byte what it was before A2, renewal records or not. With
    ``renewals=True`` only the ``renewal`` key is added: no error and no warning (D6)."""
    now = CNA - timedelta(days=90)
    args: dict[str, Any] = {"legal_roots": [rw.legal.fingerprint], "now": now}
    before = _bytes([verify_day(rw.layout, DAY, rw.roots, **args)])
    before_range = _bytes(verify_range(rw.layout, [DAY], rw.roots, **args))
    renew_day(rw, "2031-06-23-1", CNA - timedelta(days=100))

    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("renewals read although renewals=False")

    monkeypatch.setattr(renewal_module, "day_renewal", refuse)
    plain = verify_day(rw.layout, DAY, rw.roots, **args)
    assert plain.renewal is None
    assert list(plain.as_json()) == BEFORE_A2
    assert _bytes([plain]) == before
    assert _bytes([verify_day(rw.layout, DAY, rw.roots, renewals=False, **args)]) == before
    assert _bytes(verify_range(rw.layout, [DAY], rw.roots, **args)) == before_range
    monkeypatch.undo()

    renewed = verify_day(rw.layout, DAY, rw.roots, renewals=True, **args)
    assert renewed.renewal is not None
    found = renewed.as_json()
    assert list(found) == [*BEFORE_A2, "renewal"]
    assert found.pop("renewal") == {
        "status": "covered",
        "code": "renewal-covered",
        "level": "ok",
        "cert_not_after": "2031-10-01T00:00:00Z",
        "covered_by": "renewals/2031-06-23-1.json",
        "renewed_at": "2031-06-23T00:00:00Z",
        "message_tr": DAY_FINDINGS["covered"][2].format(renewal="renewals/2031-06-23-1.json"),
        "message_en": DAY_FINDINGS["covered"][3].format(renewal="renewals/2031-06-23-1.json"),
    }
    assert json.dumps([found], ensure_ascii=False).encode() == before


def test_a_day_whose_token_does_not_verify_has_no_renewal(rw: RenewalWorld) -> None:
    """As in the standalone verifier, only a verified token gets a status: a day whose token
    fails has failed already."""
    token = rw.layout.token(DAY)
    unlock(token)
    token.write_bytes(token.read_bytes()[:-8])
    check = verify_day(rw.layout, DAY, rw.roots, now=CNA, renewals=True)
    assert not check.ok
    assert check.renewal is None
    assert "renewal" not in check.as_json()
