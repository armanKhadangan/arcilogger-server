"""Write the frozen Wave 5 vault of ``tests/fixtures/vault-w5`` (contract D15, W5-A).

The folder is a data directory as the product leaves it, cut down to what D15 names:

* ``vault/2021/10/10..12/``: three FortiGate days of Örnek A.Ş. (the lines of
  ``tests/fixtures/format/fortigate-sample.log``, one an hour from 172.20.0.1) sealed by the
  product's own ``Sealer`` and stamped by the mock TSA, the moment each day ended;
* ``vault/deletions/``: the deletion record of a fourth day, 2021-10-09, that the product's
  ``run_retention`` deleted two years later (730 days), and the chain anchor it left;
* ``vault/renewals/``: one renewal record written by the product's ``renew`` with the mock
  provider inside the 180-day window, so ``reason: "test"``: it never counts (D5);
* ``state/crl/``: one revocation list of the mock root, stored by the product's ``crl fetch``
  with its ``index.json``; it revokes a serial number no certificate here has;
* ``kokler/``: the mock TSA's root certificate (``.crt``: the repository ignores ``*.pem``).

Every moment is a fixed clock, all of them before November 2026, so no token is from the
future at any real date the tests run at. The run is deterministic: the mock TSA's three RSA
keys are derived from fixed labels (:func:`_test_key`), its certificate serials, the token
serials and the request nonces come from a seeded generator, and the host name is fixed. RSA
PKCS#1 v1.5 signatures are deterministic, so the same code gives the same bytes; only another
zlib build may compress the sealed files differently. The keys are **test-only material**: their
"randomness" is the public labels below, so anyone can make the same keys, which is what makes
them worthless for anything but this fixture. No key is written to the fixture.

``SHA256SUMS`` pins every file, and ``tests/test_vault_w5_fixture.py`` fails when today's code
reads the fixture differently. The tests never run this script: the fixture is regenerated only
on purpose (a format that changes for a reason), with the reason in the commit message.
The days are sealed with ``seal.compression = "gzip"``, not today's ``xz`` default: the fixture
pins the format of the days sealed before Wave 5's xz sealing (W5-H), which every later release
must still read.

Usage: ``uv run --frozen python tools/dev/make_vault_fixture.py [--out DIR] [--check]``.
``--check`` writes into a temporary folder and compares it with the committed ``SHA256SUMS``.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core import crl
from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.renewal import renew
from arcilogger_core.retention import run_retention
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider

REPOSITORY = Path(__file__).resolve().parents[2]
FIXTURE = REPOSITORY / "tests" / "fixtures" / "vault-w5"
SAMPLE = REPOSITORY / "tests" / "fixtures" / "format" / "fortigate-sample.log"

SITE = "Örnek A.Ş. Merkez"
HOST = "ORNEK-LOG01"
SENDER = "172.20.0.1"
#: The four sealed days; retention deletes the first.
DAYS = [date(2021, 10, 9) + timedelta(days=n) for n in range(4)]
#: The mock TSA makes its root and certificates: the TSA certificate runs 1825 days from here
#: (to 2026-09-30), the root 3650 days.
KEYS_AT = datetime(2021, 10, 1, 9, 0, tzinfo=SITE_TZ)
#: Retention with the shortest period (730 days): the cutoff is 2021-10-10.
RETENTION_AT = datetime(2023, 10, 10, 3, 0, tzinfo=SITE_TZ)
#: The revocation list is fetched, then the renewal written, inside the 180-day window before
#: the TSA certificate expires.
CRL_AT = datetime(2026, 6, 1, 8, 0, tzinfo=SITE_TZ)
RENEW_AT = datetime(2026, 6, 1, 9, 0, tzinfo=SITE_TZ)
CRL_URL = "http://crl.example.com/arcilogger-mock-root.crl"
#: The one revoked serial number: no certificate of the fixture has it.
REVOKED_SERIAL = 0x5651
#: What ``SHA256SUMS`` leaves out: itself.
UNPINNED = frozenset({"SHA256SUMS"})


def _is_prime(number: int, rng: random.Random) -> bool:
    if number < 2:
        return False
    for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if number % small == 0:
            return number == small
    odd, shifts = number - 1, 0
    while odd % 2 == 0:
        odd, shifts = odd // 2, shifts + 1
    for _ in range(40):  # Miller-Rabin
        witness = pow(rng.randrange(2, number - 1), odd, number)
        if witness in (1, number - 1):
            continue
        for _ in range(shifts - 1):
            witness = pow(witness, 2, number)
            if witness == number - 1:
                break
        else:
            return False
    return True


def _prime(rng: random.Random, bits: int, exponent: int) -> int:
    while True:
        candidate = rng.getrandbits(bits) | (3 << (bits - 2)) | 1  # two top bits: n has 2 * bits
        if (candidate - 1) % exponent and _is_prime(candidate, rng):
            return candidate


def _test_key(label: str) -> rsa.RSAPrivateKey:
    """A 2048-bit RSA key derived from ``label`` alone: TEST ONLY, anyone can derive it."""
    seed = int.from_bytes(
        hashlib.sha256(f"ARCILOGGER vault-w5 TEST ONLY {label}".encode()).digest()
    )
    rng = random.Random(seed)  # noqa: S311 - reproducible on purpose; never a real key
    exponent = 65537
    first = _prime(rng, 1024, exponent)
    second = _prime(rng, 1024, exponent)
    while second == first:
        second = _prime(rng, 1024, exponent)
    private = pow(exponent, -1, (first - 1) * (second - 1))
    return rsa.RSAPrivateNumbers(
        p=first,
        q=second,
        d=private,
        dmp1=rsa.rsa_crt_dmp1(private, first),
        dmq1=rsa.rsa_crt_dmq1(private, second),
        iqmp=rsa.rsa_crt_iqmp(first, second),
        public_numbers=rsa.RSAPublicNumbers(exponent, first * second),
    ).private_key()


class _Clock:
    """The one clock of the sealer, the mock TSA, retention and renewal."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now.astimezone(UTC)


def _revocation_list(tsa: MockTsa, key: rsa.RSAPrivateKey) -> bytes:
    issued = CRL_AT.astimezone(UTC) - timedelta(hours=1)
    revoked = (
        x509.RevokedCertificateBuilder()
        .serial_number(REVOKED_SERIAL)
        .revocation_date(datetime(2026, 5, 15, tzinfo=UTC))
        .add_extension(x509.CRLReason(x509.ReasonFlags.cessation_of_operation), critical=False)
        .build()
    )
    return (
        x509.CertificateRevocationListBuilder()
        .issuer_name(tsa.root_certificate.subject)
        .last_update(issued)
        .next_update(issued + timedelta(days=7))
        .add_revoked_certificate(revoked)
        .sign(key, hashes.SHA256())
        .public_bytes(Encoding.DER)
    )


def _deterministic(stack: ExitStack, keys: Iterator[rsa.RSAPrivateKey]) -> None:
    """Replace every source of randomness and of the machine the run depends on."""
    rng = random.Random(5651)  # noqa: S311 - reproducible serials and nonces, test only
    stack.enter_context(
        mock.patch("arcilogger_core.tsa.mock._new_key", side_effect=lambda _: next(keys))
    )
    stack.enter_context(
        mock.patch.object(x509, "random_serial_number", side_effect=lambda: rng.getrandbits(159))
    )
    stack.enter_context(mock.patch("secrets.randbits", side_effect=rng.getrandbits))
    stack.enter_context(mock.patch("platform.node", return_value=HOST))


def _spool(layout: Layout, day: date, messages: list[bytes]) -> None:
    spool = layout.spool_file("fortigate", day)
    spool.parent.mkdir(parents=True, exist_ok=True)
    with spool.open("wb") as handle:
        for hour in range(24):
            received = datetime.combine(day, time(hour, 0, 30), tzinfo=SITE_TZ)
            handle.write(format_line(received, SENDER, messages[hour % len(messages)]))


def _config(root: Path) -> Config:
    return Config.model_validate(
        {
            "data_dir": str(root / "data"),
            "secrets_dir": str(root),
            "site_name": SITE,
            "sources": [{"name": "fortigate", "type": "fortigate", "address": SENDER}],
            "seal": {
                "quiet_seconds": 0,
                "first_day": DAYS[0].isoformat(),
                "compression": "gzip",  # the days sealed before W5-H (xz), on purpose
            },
            "tsa": {"provider": "mock"},
            "retention": {"enabled": True, "days": 730},
        }
    )


def _make(root: Path) -> tuple[Layout, x509.Certificate]:
    """Run the product on fixed clocks in ``root``: the data directory it leaves, and the mock
    TSA's root."""
    config = _config(root)
    layout = Layout(config.data_dir)
    clock = _Clock(KEYS_AT)
    root_key = _test_key("root")
    keys = iter([root_key, _test_key("tsa"), _test_key("tsa-expired")])
    with ExitStack() as stack:
        _deterministic(stack, keys)
        tsa = MockTsa(layout.state / "mock-tsa", key_type="rsa", clock=clock)
        provider = MockTsaProvider(tsa)
        roots = [tsa.root_certificate]
        sealer = Sealer(
            config,
            provider=provider,
            roots=roots,
            clock=clock,
            clock_status=lambda: "fixture clock",
        )
        messages = SAMPLE.read_bytes().splitlines()
        for day in DAYS:
            _spool(layout, day, messages)
            clock.now = datetime.combine(day + timedelta(days=1), time(0, 30), tzinfo=SITE_TZ)
            outcome = sealer.seal_day(day)
            if outcome.status != "sealed":
                raise SystemExit(f"{day}: sealing failed: {outcome.message}")
        clock.now = RETENTION_AT
        deleted = run_retention(config, provider=provider, roots=roots, clock=clock)
        if deleted.deleted != DAYS[:1] or deleted.error:
            raise SystemExit(f"retention did not delete {DAYS[0]}: {deleted}")
        clock.now = CRL_AT
        served = {CRL_URL: _revocation_list(tsa, root_key)}
        with mock.patch.object(crl, "_download", lambda _opener, url, _options: served[url]):
            fetched = crl.fetch(crl.CrlOptions(urls=(CRL_URL,)), layout, roots, clock())
        if len(fetched.lists) != 1 or fetched.failures:
            raise SystemExit(f"the revocation list was not stored: {fetched}")
        clock.now = RENEW_AT
        run = renew(layout, config, provider, roots, clock())
        if run.reason != "test":
            raise SystemExit(f"the mock renewal must be a test renewal: {run}")
    return layout, tsa.root_certificate


def _copy(layout: Layout, root: x509.Certificate, target: Path) -> None:
    wanted: list[tuple[Path, Path]] = [
        (path, Path("vault") / path.relative_to(layout.vault))
        for path in layout.vault.rglob("*")
        if path.is_file()
    ]
    wanted += [
        (path, Path("state") / "crl" / path.name)
        for path in layout.crl_dir.iterdir()
        if path.name == crl.INDEX_NAME or path.suffix == crl.CRL_SUFFIX
    ]
    for source, name in sorted(wanted, key=lambda pair: pair[1].as_posix()):
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target / name)
    (target / "kokler").mkdir()
    (target / "kokler" / "mock-root.crt").write_bytes(root.public_bytes(Encoding.PEM))


def sums(folder: Path) -> str:
    """``SHA256SUMS`` of ``folder``: every file but itself, sorted by POSIX path."""
    names = sorted(
        path.relative_to(folder).as_posix()
        for path in folder.rglob("*")
        if path.is_file() and path.name not in UNPINNED
    )
    return "".join(
        f"{hashlib.sha256((folder / name).read_bytes()).hexdigest()}  {name}\n" for name in names
    )


def write(target: Path) -> None:
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as folder:
        layout, root = _make(Path(folder))
        try:
            _copy(layout, root, target)
        finally:
            for path in Path(folder).rglob("*"):  # the sealer, retention and renew lock files
                if path.is_file() and is_locked(path):
                    unlock(path)
    (target / "SHA256SUMS").write_text(sums(target), encoding="ascii", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=FIXTURE, help="target folder")
    parser.add_argument(
        "--check", action="store_true", help="write to a temporary folder, compare with --out"
    )
    args = parser.parse_args(argv)
    if not args.check:
        write(args.out)
        print(f"written: {args.out}")
        return 0
    with tempfile.TemporaryDirectory() as folder:
        fresh = Path(folder) / "vault-w5"
        write(fresh)
        same = sums(fresh) == (args.out / "SHA256SUMS").read_text(encoding="ascii")
    print("the same bytes" if same else "DIFFERENT bytes from the committed fixture")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
