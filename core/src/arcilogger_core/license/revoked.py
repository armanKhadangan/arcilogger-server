"""Licence ids the owner revoked (licensing contract 2.1, [C-13]).

Same rules as :mod:`arcilogger_core.license.keys`: committed by the owner, never changed at run
time (no environment variable, configuration file, database row or API), read by the verifier
at call time, swapped by tests. A revoked licence is ``invalid`` (``license_revoked``) and
refused on upload. Revocation reaches only installations that upgrade to a release containing
the id.
"""

from __future__ import annotations

from typing import Final

#: Licence ids the owner revoked (a replaced server, a leaked or mistaken licence). Committed by
#: the owner, like keys.py; empty today.
REVOKED_LICENSE_IDS: Final[frozenset[str]] = frozenset()
