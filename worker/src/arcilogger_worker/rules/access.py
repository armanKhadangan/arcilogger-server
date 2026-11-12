"""The access and device rules (Wave 6 contract §2.2.2, package W6-C): R03 (brute-force login),
R04 (admin login off hours or from an unexpected address), R11 (new device), R12 (MAC change) and
R16 (device configuration change).

Each rule is a :class:`~arcilogger_worker.rules.model.RuleDef` in :data:`RULES`, in id order; the
registry (:mod:`arcilogger_worker.rules.registry`) collects them. Empty until W6-C lands.
"""

from __future__ import annotations

from typing import Final

from arcilogger_worker.rules.model import RuleDef

RULES: Final[tuple[RuleDef, ...]] = ()
