"""Time-stamp providers (build specification section 6).

* ``mock`` — :class:`~arcilogger_core.tsa.mock.MockTsaProvider`, tests and demo, not legal;
* ``rfc3161`` — :class:`~arcilogger_core.tsa.rfc3161.Rfc3161HttpProvider`, development, not legal;
* ``zamane`` — :class:`~arcilogger_core.tsa.zamane.ZamaneCliProvider`, the official Kamu SM
  console client; the provider for production stamps (``zd.kamusm.gov.tr``). Whether a day is
  shown as legal grade is decided by
  :func:`arcilogger_core.tsa.verify.confirmed_legal_grade` from the verified chain.
"""

from __future__ import annotations

from cryptography import x509

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import KAMUSM_CUSTOMER, KAMUSM_PASSWORD, SecretStore
from arcilogger_core.tsa.base import TimestampProvider, TsaConfigurationError
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_core.tsa.rfc3161 import Rfc3161HttpProvider
from arcilogger_core.tsa.verify import load_roots_dir
from arcilogger_core.tsa.zamane import ZamaneCliProvider


def mock_tsa(layout: Layout) -> MockTsa:
    return MockTsa(layout.state / "mock-tsa")


def make_provider(config: Config, layout: Layout, secrets: SecretStore) -> TimestampProvider:
    settings = config.tsa
    if settings.provider == "mock":
        return MockTsaProvider(mock_tsa(layout), digest=settings.digest)
    if settings.provider == "rfc3161":
        url = settings.url if settings.port in (80, 443) else f"{settings.url}:{settings.port}"
        return Rfc3161HttpProvider(
            url, digest=settings.digest, timeout_seconds=settings.timeout_seconds
        )
    if settings.zamane_jar is None:
        raise TsaConfigurationError("tsa.zamane_jar is not configured")
    customer = secrets.get(KAMUSM_CUSTOMER)
    password = secrets.get(KAMUSM_PASSWORD)
    if not customer or not password:
        raise TsaConfigurationError("Kamu SM customer number / password not stored")
    return ZamaneCliProvider(
        java=settings.java,
        jar=settings.zamane_jar,
        url=settings.url,
        port=settings.port,
        customer=customer,
        password=password,
        legal_grade=settings.legal_grade,
        digest=settings.digest,
        timeout_seconds=settings.timeout_seconds,
        verify_flag=settings.zamane_verify_flag,
        credit_regex=settings.credit_regex,
    )


def trusted_roots(config: Config, layout: Layout) -> list[x509.Certificate]:
    """Root certificates a token may chain to.

    The pinned roots of ``tsa.roots_dir`` — and the mock TSA's test root **only while
    ``tsa.provider`` is ``mock``** (tests, demo, trial). The mock's private keys lie next to its
    root in ``state/mock-tsa``; whoever can read them could stamp anything, so once the site
    uses a real provider that root is never trusted again, whether or not its files are still
    there. Days stamped by the mock during a trial then no longer verify (by design).
    """
    roots: list[x509.Certificate] = []
    if config.tsa.roots_dir is not None and config.tsa.roots_dir.is_dir():
        roots += load_roots_dir(config.tsa.roots_dir)
    if config.tsa.provider == "mock":
        roots.append(mock_tsa(layout).root_certificate)
    if not roots:
        raise TsaConfigurationError("no pinned root certificates (tsa.roots_dir)")
    return roots
