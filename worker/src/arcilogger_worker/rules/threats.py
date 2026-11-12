"""The threat and health rules (Wave 6 contract §2.2.3, package W6-D): R05 (IPS attack), R06
(botnet or threat IP), R07 (malware), R17 (firewall health) and R18 (DHCP pool exhausted).

Each rule is a :class:`~arcilogger_worker.rules.model.RuleDef` in :data:`RULES`, in id order; the
registry (:mod:`arcilogger_worker.rules.registry`) collects them. Empty until W6-D lands.
"""

from __future__ import annotations

from typing import Final

from arcilogger_worker.rules.model import RuleDef

RULES: Final[tuple[RuleDef, ...]] = ()
