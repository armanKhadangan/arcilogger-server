"""Run the standalone verifier the way an auditor does: a separate, isolated Python process.

``python -I -S verify.py``: ``-I`` ignores ``PYTHON*`` environment variables and the user's site
folder, ``-S`` does not load site-packages at all. So ARCILOGGER's own installed libraries
(``cryptography``, ``asn1crypto``) are not available to it, and a passing run proves the
verifier needs nothing but the standard library. ARCILOGGER always runs its *own* copy of
``verify.py``, never the copy inside a package (that one could have been altered).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from arcilogger_core.evidence.standalone import VERIFY_PY


def verifier_command(target: Path, *args: str, python: str | None = None) -> list[str]:
    return [python or sys.executable or "python", "-I", "-S", str(VERIFY_PY), str(target), *args]


def run_verifier(
    target: Path,
    *args: str,
    python: str | None = None,
    capture: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Verify ``target`` (a package folder or an unencrypted ZIP); exit code 0 = verified."""
    return subprocess.run(  # noqa: S603 - fixed program, arguments are paths
        verifier_command(target, *args, python=python),
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
