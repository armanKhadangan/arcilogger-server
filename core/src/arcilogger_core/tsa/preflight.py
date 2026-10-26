"""Whether the Seal task could start sealing with a configuration (ADR-0014; contract 2.4.7, 2.6).

The Seal task stops before sealing when it cannot build its time-stamp provider or find a pinned
root (``R20-seal-config``, one of the causes of :data:`arcilogger_core.seal.CONFIG_CAUSES`).
:func:`seal_preflight` answers the same question without stamping anything, without decrypting
more than the stored Kamu SM credentials and without creating a file:

* the agent reports it in its heartbeat (``seal_ready``) and refuses a change that would stop a
  sealer that works now (``tsa_would_break``);
* the API shows it on the Kamu SM page; the CLI's own start-up check follows the same order.

Causes, in this order: ``zamane-jar`` (no client configured), ``secret-store`` (the key does not
open the store), ``credentials`` (customer number or password not stored), ``executable`` (the
configured Java or client is not trusted, :mod:`arcilogger_core.execpolicy`), ``roots`` (no pinned
root certificate). :func:`production_missing` lists what the switch to the Kamu SM production
server still needs (contract 2.6, ``tsa_not_ready``).
"""

from __future__ import annotations

import shutil
from collections.abc import Collection
from typing import Final

from arcilogger_core.config import Config
from arcilogger_core.execpolicy import (
    ExecutableRefusedError,
    TrustAnchors,
    check_java,
    check_zamane_jar,
)
from arcilogger_core.secretstore import (
    KAMUSM_CUSTOMER,
    KAMUSM_PASSWORD,
    SecretStore,
    SecretStoreError,
)
from arcilogger_core.tsa.base import TsaError
from arcilogger_core.tsa.verify import load_roots_dir

CREDENTIALS: Final = frozenset({KAMUSM_CUSTOMER, KAMUSM_PASSWORD})
#: Cause of a configured program that ``execpolicy`` refuses (its text: ``seal.CONFIG_CAUSES``).
EXECUTABLE_CAUSE: Final = "executable"


def executables_trusted(config: Config, anchors: TrustAnchors) -> bool:
    """``tsa.java`` and ``tsa.zamane_jar`` pass ``execpolicy`` (a configured jar is needed)."""
    if config.tsa.zamane_jar is None:
        return False
    try:
        check_java(config.tsa.java, anchors)
        check_zamane_jar(config.tsa.zamane_jar, anchors)
    except ExecutableRefusedError:
        return False
    return True


def java_present(config: Config, anchors: TrustAnchors) -> bool:
    """``tsa.java`` is trusted and exists (a bare name is looked up in ``PATH``)."""
    try:
        program = check_java(config.tsa.java, anchors)[0]
    except ExecutableRefusedError:
        return False
    return "/" in program or "\\" in program or shutil.which(program) is not None


def _provider_cause(
    config: Config, anchors: TrustAnchors, secrets: SecretStore, stored: Collection[str] | None
) -> str | None:
    if config.tsa.provider != "zamane":
        return None  # the mock and RFC 3161 providers need neither credentials nor a client
    if config.tsa.zamane_jar is None:
        return "zamane-jar"
    if stored is None:
        try:
            names = set(secrets.names())
        except (OSError, ValueError, SecretStoreError):
            return "secret-store"
        if names >= CREDENTIALS and not all(secrets.readable(name) for name in CREDENTIALS):
            return "secret-store"
    else:
        names = set(stored)
    if not names >= CREDENTIALS:
        return "credentials"
    if not executables_trusted(config, anchors):
        return EXECUTABLE_CAUSE
    return None


def roots_present(config: Config) -> bool:
    """A pinned root in ``tsa.roots_dir`` (the mock provider brings its own)."""
    if config.tsa.provider == "mock":
        return True
    directory = config.tsa.roots_dir
    try:  # Path.is_dir raises "access denied" (Python 3.12) instead of answering False
        return directory is not None and directory.is_dir() and bool(load_roots_dir(directory))
    except (OSError, ValueError, TsaError):
        return False


def seal_preflight(
    config: Config,
    *,
    anchors: TrustAnchors,
    secrets: SecretStore,
    stored: Collection[str] | None = None,
) -> list[str]:
    """The causes that would stop the Seal task now; empty when it could start.

    ``stored``: the secret names to assume (a candidate configuration of the browser
    administration, whose new credentials are not stored yet); None reads the store."""
    causes = []
    cause = _provider_cause(config, anchors, secrets, stored)
    if cause is not None:
        causes.append(cause)
    if not roots_present(config):
        causes.append("roots")
    return causes


def production_missing(
    config: Config, *, anchors: TrustAnchors, stored: Collection[str], roots: int
) -> list[str]:
    """What the Kamu SM production server still needs: ``credentials``, ``zamane_jar`` (a
    trusted client), ``java`` and ``roots`` (``roots``: the number of installed roots)."""
    missing = []
    if not set(stored) >= CREDENTIALS:
        missing.append("credentials")
    jar = config.tsa.zamane_jar
    try:
        if jar is None:
            raise ExecutableRefusedError("tsa.zamane_jar", "not configured")
        check_zamane_jar(jar, anchors)
    except ExecutableRefusedError:
        missing.append("zamane_jar")
    if not java_present(config, anchors):
        missing.append("java")
    if roots < 1:
        missing.append("roots")
    return missing
