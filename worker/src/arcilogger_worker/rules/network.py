"""The network rules (Wave 6 contract §2.2.1, package W6-B): R01 (internal port scan), R02
(external scan), R08 (DNS bypass), R10 (proxy, VPN, Tor) and R19 (suspicious DNS, DGA).

Each rule is a :class:`~arcilogger_worker.rules.model.RuleDef` in :data:`RULES`, in id order; the
registry (:mod:`arcilogger_worker.rules.registry`) collects them. Empty until W6-B lands.
"""

from __future__ import annotations

from typing import Final

from arcilogger_worker.rules.model import RuleDef

RULES: Final[tuple[RuleDef, ...]] = ()
