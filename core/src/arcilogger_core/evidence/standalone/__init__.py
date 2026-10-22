"""The standalone verifier that is copied, unchanged, into every evidence package.

* ``verify.py`` — one file, Python 3.9+ standard library only (``python -I -S verify.py``). It is
  also importable here (``arcilogger_core.evidence.standalone.verify``) so the tests and
  ``arcilogger-core evidence verify`` exercise exactly the code an auditor receives.
* ``verify.ps1`` / ``verify.sh`` — thin wrappers that find a Python 3 interpreter and run
  ``verify.py`` on the folder they are in.

Nothing in this folder may import ARCILOGGER or any third-party package (ADR-0006).
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
VERIFY_PY = HERE / "verify.py"
VERIFY_PS1 = HERE / "verify.ps1"
VERIFY_SH = HERE / "verify.sh"

#: Package path → source file of the verifier files placed at the root of every package.
VERIFIER_FILES = {"verify.py": VERIFY_PY, "verify.ps1": VERIFY_PS1, "verify.sh": VERIFY_SH}
