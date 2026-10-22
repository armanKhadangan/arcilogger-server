"""Evidence packages ("Kanıt Paketi") and the standalone verifier (build specification 9.6).

* :func:`build_package` — a ZIP with the sealed days, their manifests and time stamps, the
  root and TSA certificates, an optional extract of raw lines and report, the standalone
  verifier and the auditor's guide (``DOGRULAMA.txt``); optionally AES-256 encrypted.
* :func:`run_verifier` — runs the standalone ``verify.py`` in an isolated Python process.
* :func:`purge_expired` — deletes packages older than 30 days from ``exports/``.

See ``docs/DENETCI-KILAVUZU.md`` and ADR-0006.
"""

from __future__ import annotations

from arcilogger_core.evidence.archive import extract_archive, is_encrypted
from arcilogger_core.evidence.builder import (
    build_package,
    generate_password,
    package_days,
    requires_critical_eku,
    verifier_sha256,
)
from arcilogger_core.evidence.exports import DEFAULT_KEEP_DAYS, purge_expired
from arcilogger_core.evidence.model import EvidenceError, PackageInfo, RawRef, load_refs
from arcilogger_core.evidence.runner import run_verifier, verifier_command

__all__ = [
    "DEFAULT_KEEP_DAYS",
    "EvidenceError",
    "PackageInfo",
    "RawRef",
    "build_package",
    "extract_archive",
    "generate_password",
    "is_encrypted",
    "load_refs",
    "package_days",
    "purge_expired",
    "requires_critical_eku",
    "run_verifier",
    "verifier_command",
    "verifier_sha256",
]
