"""The people and usage rules (Wave 6 contract §2.2.4, package W6-E): R09 (repeated blocked
categories), R13 (large upload) and R14 (night activity), all person-level.

Each rule is a :class:`~arcilogger_worker.rules.model.RuleDef` in :data:`RULES`, in id order; the
registry (:mod:`arcilogger_worker.rules.registry`) collects them. Empty until W6-E lands.
"""

from __future__ import annotations

from typing import Final

from arcilogger_worker.rules.model import RuleDef

RULES: Final[tuple[RuleDef, ...]] = ()
