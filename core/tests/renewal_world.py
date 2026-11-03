"""A data directory for the re-stamping tests, with one clock for everything that reads time.

The sealer, the mock TSA that stamps the days and a second, "legal" TSA all read
:class:`Clock`, so a test can stamp a day, move years ahead and stamp a renewal there. Each
stamp's TSA certificate is issued with the validity the test asks for (:func:`issue_tsa`), so
the certificate's ``notAfter`` (the date a renewal is measured against) is exact.

The legal CA is made up (``Örnek Simulated CA (test)``, a policy outside the test arc 2.999):
its stamps count once its root is listed in ``tsa.legal_root_fingerprints``, as a production
stamp does (rule 4). Nothing about a real Kamu SM or TÜRKTRUST certificate is assumed, and no
real server is called.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from arcilogger_core.canonical import canonical_json
from arcilogger_core.layout import Layout
from arcilogger_core.renewal import (
    RecordEntry,
    RenewalReport,
    TokenStatus,
    build_record,
    renewal_status,
    system_block,
    tsa_block,
)
from arcilogger_core.seal import Sealer, sha256_file
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa.base import TimestampProvider
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_core.tsa.verify import fingerprint, inspect_token

from .conftest import World, make_config
from .test_legal_grade import SIMULATED_POLICY, ProductionLikeProvider

PrivateKey = ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey

#: The day sealed by :func:`make_world`, and when its TSA certificate expires.
DAY = date(2026, 10, 12)
CNA = datetime(2031, 10, 1, tzinfo=UTC)
WINDOW = timedelta(days=180)
REPORT_NAME = "ARCILOGGER-5651-rapor-7.pdf"


class Clock:
    """The one clock of a renewal world."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _name(organization: str, common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "TR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def issue_tsa(
    issuer: tuple[x509.Certificate, PrivateKey],
    *,
    not_before: datetime,
    not_after: datetime,
    common_name: str = "Örnek TSA (test)",
) -> tuple[x509.Certificate, PrivateKey]:
    """A TSA certificate of ``issuer``, valid from ``not_before`` to ``not_after``."""
    key = ec.generate_private_key(ec.SECP256R1())
    organization = issuer[0].subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name(str(organization), common_name))
        .issuer_name(issuer[0].subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
        .sign(issuer[1], hashes.SHA256())
    )
    return certificate, key


class LegalCa:
    """A made-up CA whose root the site lists as legal: valid from 2020 to 2070, so every
    renewal a test stamps years ahead still chains to it."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        name = _name("Örnek Simulated CA (test)", "Örnek Simulated Root (test)")
        self.root = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime(2020, 1, 1, tzinfo=UTC))
            .not_valid_after(datetime(2070, 1, 1, tzinfo=UTC))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(self.key, hashes.SHA256())
        )

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.root)


@dataclass
class RenewalWorld:
    world: World
    clock: Clock
    #: Stamps the days, the reports and the deletion records (the mock root).
    tsa: MockTsa
    legal: LegalCa
    #: Stamps renewals with the legal CA's certificates (policy outside 2.999).
    legal_tsa: MockTsa

    @property
    def layout(self) -> Layout:
        return self.world.layout

    @property
    def roots(self) -> list[x509.Certificate]:
        return [self.tsa.root_certificate, self.legal.root]

    def set_now(self, now: datetime) -> None:
        self.clock.now = now
        self.world.now = now

    def _mock_root(self) -> tuple[x509.Certificate, PrivateKey]:
        key = serialization.load_pem_private_key(
            (self.tsa.state_dir / "root.key").read_bytes(), None
        )
        assert isinstance(key, ec.EllipticCurvePrivateKey)
        return self.tsa.root_certificate, key

    def mock_signer(self, cert_not_after: datetime) -> None:
        """The mock TSA signs from now on with a certificate expiring at ``cert_not_after``."""
        self.tsa.signer = issue_tsa(
            self._mock_root(),
            not_before=self.clock.now - timedelta(days=1),
            not_after=cert_not_after,
            common_name="ARCILOGGER Mock TSA (TEST ONLY)",
        )

    def mock_provider(self, cert_not_after: datetime) -> MockTsaProvider:
        """The plain mock provider (claim: not legal grade), its certificate expiring then."""
        self.mock_signer(cert_not_after)
        return MockTsaProvider(self.tsa)

    def legal_provider(
        self, cert_not_after: datetime, *, claim: bool = True, not_before: datetime | None = None
    ) -> MockTsaProvider:
        """A provider stamping with a new certificate of the legal CA (from ``not_before``, a
        day before now by default, to ``cert_not_after``); ``claim`` false: the plain mock
        provider's claim on that CA."""
        self.legal_tsa.signer = issue_tsa(
            (self.legal.root, self.legal.key),
            not_before=not_before or self.clock.now - timedelta(days=1),
            not_after=cert_not_after,
        )
        if claim:
            return ProductionLikeProvider(self.legal_tsa)
        return MockTsaProvider(self.legal_tsa)

    def seal(self, day: date, cert_not_after: datetime = CNA) -> None:
        """Seal ``day`` (the mock provider, a certificate expiring at ``cert_not_after``); the
        clock is then at 00:30 site time of the next day."""
        self.world.busy_day(day)
        self.set_now(
            datetime.combine(day + timedelta(days=1), time(0, 30), tzinfo=SITE_TZ).astimezone(UTC)
        )
        self.mock_signer(cert_not_after)
        sealer = Sealer(
            self.world.config,
            provider=MockTsaProvider(self.tsa),
            roots=[self.tsa.root_certificate],
            clock=self.clock,
            clock_status=lambda: "renewal test clock",
        )
        assert [outcome.status for outcome in sealer.seal_due()] == ["sealed"]

    def add_report(
        self, cert_not_after: datetime = CNA, name: str = REPORT_NAME
    ) -> tuple[Path, Path]:
        """A stored official report and its token, stamped as the report store stamps."""
        pdf = self.layout.exports / "reports" / name
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.7\n%Ornek A.S. stored report\n%%EOF\n")
        return pdf, self.mock_provider(cert_not_after).stamp(pdf).token_path

    def add_deletion(
        self, name: str = "2026-10-20-imha.json", cert_not_after: datetime = CNA
    ) -> tuple[Path, Path]:
        """A stamped deletion record under its final name (its content is not what renewal
        reads: only its bytes and its token)."""
        record = self.layout.deletions / name
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_bytes(
            canonical_json({"schema": "arcilogger.deletion/1", "days": [], "site": "Örnek A.Ş."})
        )
        return record, self.mock_provider(cert_not_after).stamp(record).token_path

    def day_entry(self, day: date = DAY) -> RecordEntry:
        """What a renewal record lists for ``day``'s token."""
        token = self.layout.token(day)
        return RecordEntry(
            "day",
            token.relative_to(self.layout.vault).as_posix(),
            sha256_file(token),
            inspect_token(token.read_bytes()).tsa_cert_not_after or CNA,
            day=day,
            manifest_sha256=sha256_file(self.layout.manifest(day)),
        )

    def write_renewal(
        self,
        name: str,
        entries: list[RecordEntry],
        provider: TimestampProvider,
        *,
        reason: Any = None,
    ) -> Path:
        """A renewal record ``renewals/<name>.json`` of ``entries``, stamped by ``provider`` now
        (without :func:`arcilogger_core.renewal.renew`)."""
        tsa = tsa_block(provider, self.world.config)
        record = build_record(
            entries,
            created_at=self.clock.now,
            system=system_block(self.world.config),
            tsa=tsa,
            reason=reason or ("due" if provider.legal_grade else "test"),
        )
        path = self.layout.renewals_dir / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json(record))
        provider.stamp(path)
        return path

    def status(self, now: datetime | None = None) -> RenewalReport:
        return renewal_status(self.layout, self.world.config, self.roots, now or self.clock.now)

    def of(self, report: RenewalReport, path: str) -> TokenStatus:
        (found,) = [token for token in report.tokens if token.path == path]
        return found


def day_token_path(day: date = DAY) -> str:
    return f"{day:%Y/%m/%d}/manifest-{day.isoformat()}.json.zd"


def make_world(tmp_path: Path, *, legal_listed: bool = True) -> RenewalWorld:
    """A data directory with :data:`DAY` sealed (its certificate expiring at :data:`CNA`) and
    the legal CA's root listed in ``tsa.legal_root_fingerprints`` (unless ``legal_listed`` is
    false)."""
    start = datetime.combine(DAY, time(12, 0), tzinfo=SITE_TZ).astimezone(UTC)
    clock = Clock(start)
    legal = LegalCa()
    config = make_config(
        tmp_path, tsa={"legal_root_fingerprints": [legal.fingerprint] if legal_listed else []}
    )
    state = Layout(config.data_dir).state
    tsa = MockTsa(state / "mock-tsa", key_type="ec", clock=clock)
    legal_tsa = MockTsa(state / "legal-tsa", key_type="ec", clock=clock)
    legal_tsa.policy = SIMULATED_POLICY
    world = World(root=tmp_path, config=config, tsa=tsa, now=start)
    renewal_world = RenewalWorld(world, clock, tsa, legal, legal_tsa)
    renewal_world.seal(DAY)
    return renewal_world
