"""Time-stamp providers (build specification section 6).

* ``mock`` — :class:`~arcilogger_core.tsa.mock.MockTsaProvider`, tests and demo, not legal;
* ``rfc3161`` — :class:`~arcilogger_core.tsa.rfc3161.Rfc3161HttpProvider`, development, not legal;
* ``zamane`` — :class:`~arcilogger_core.tsa.zamane.ZamaneCliProvider`, the official Kamu SM
  console client; the provider for production stamps (``zd.kamusm.gov.tr``);
* ``turktrust`` — :class:`~arcilogger_core.tsa.turktrust.TurktrustHttpsProvider`, TÜRKTRUST's
  RFC 3161 server with HTTP Basic authentication; its production stamps (``zd.turktrust.com.tr``)
  claim legal grade too (D12).

Whether a day is shown as legal grade is decided by
:func:`arcilogger_core.tsa.verify.confirmed_legal_grade` from the verified chain.

The Zamane client is a program (``tsa.java -jar tsa.zamane_jar``): :func:`make_provider` runs
only what :mod:`arcilogger_core.execpolicy` accepts (ADR-0014), every time it builds one.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from cryptography import x509

from arcilogger_core.config import Config, default_config_path
from arcilogger_core.execpolicy import (
    ExecutableRefusedError,
    TrustAnchors,
    check_java,
    check_zamane_jar,
)
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import (
    KAMUSM_CUSTOMER,
    KAMUSM_PASSWORD,
    TURKTRUST_PASSWORD,
    TURKTRUST_USERNAME,
    SecretStore,
)
from arcilogger_core.tsa.base import TimestampProvider, TsaConfigurationError
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_core.tsa.rfc3161 import Rfc3161HttpProvider
from arcilogger_core.tsa.turktrust import TurktrustHttpsProvider
from arcilogger_core.tsa.verify import load_roots_dir
from arcilogger_core.tsa.zamane import ZamaneCliProvider


class TsaExecutableError(TsaConfigurationError):
    """The executable policy refused ``tsa.java`` or ``tsa.zamane_jar``: no Zamane client
    runs (the sealing cause ``executable``, the task code ``executable_refused``).
    ``refusal`` is the policy's own error."""

    def __init__(self, refusal: ExecutableRefusedError) -> None:
        super().__init__(str(refusal))
        self.refusal = refusal


def url_with_port(url: str, port: int) -> str:
    """``tsa.url`` with ``tsa.port``: unchanged when the URL names a port itself or the port is
    80 or 443 (the URL's scheme then decides, as for ``rfc3161``: ``tsa.port`` defaults to 80,
    so an https URL without a port is not sent to port 80), else the port inserted after the
    host."""
    parts = urlsplit(url)
    if parts.port is not None or port in (80, 443) or not parts.hostname:
        return url
    return urlunsplit(parts._replace(netloc=f"{parts.netloc}:{port}"))


def mock_tsa(layout: Layout) -> MockTsa:
    return MockTsa(layout.state / "mock-tsa")


def make_provider(
    config: Config, layout: Layout, secrets: SecretStore, *, anchors: TrustAnchors | None = None
) -> TimestampProvider:
    """The configured provider. ``anchors``: what the executable policy trusts, built from the
    caller's fixed configuration path (``TrustAnchors.of(--config)``), never from a key of the
    file; by default from :func:`~arcilogger_core.config.default_config_path` (the path every
    service and task is given by ``ARCILOGGER_CONFIG`` or its default).

    Raises :class:`~arcilogger_core.tsa.base.TsaConfigurationError` (also
    :class:`TsaExecutableError`) or ``SecretStoreError``."""
    settings = config.tsa
    if settings.provider == "mock":
        return MockTsaProvider(mock_tsa(layout), digest=settings.digest)
    if settings.provider == "rfc3161":
        url = settings.url if settings.port in (80, 443) else f"{settings.url}:{settings.port}"
        return Rfc3161HttpProvider(
            url,
            digest=settings.digest,
            timeout_seconds=settings.timeout_seconds,
            https_proxy=config.network.https_proxy,
        )
    if settings.provider == "turktrust":
        username = secrets.get(TURKTRUST_USERNAME)
        password = secrets.get(TURKTRUST_PASSWORD)
        if not username or not password:
            raise TsaConfigurationError("TÜRKTRUST user name / password not stored")
        return TurktrustHttpsProvider(
            url_with_port(settings.url, settings.port),
            username=username,
            password=password,
            legal_grade=settings.legal_grade,
            digest=settings.digest,
            timeout_seconds=settings.timeout_seconds,
            https_proxy=config.network.https_proxy,
            allow_plain_http=settings.allow_plain_http,
        )
    if settings.zamane_jar is None:
        raise TsaConfigurationError("tsa.zamane_jar is not configured")
    customer = secrets.get(KAMUSM_CUSTOMER)
    password = secrets.get(KAMUSM_PASSWORD)
    if not customer or not password:
        raise TsaConfigurationError("Kamu SM customer number / password not stored")
    if anchors is None:
        anchors = TrustAnchors.of(default_config_path())
    try:
        java = check_java(settings.java, anchors)
        jar = check_zamane_jar(settings.zamane_jar, anchors)
    except ExecutableRefusedError as exc:
        raise TsaExecutableError(exc) from exc
    return ZamaneCliProvider(
        java=java,
        jar=jar.path,
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
