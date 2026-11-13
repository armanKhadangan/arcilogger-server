"""Every security rule, in id order (Wave 6 contract §2.1.1).

The rules live in their package's module (:mod:`.network`, :mod:`.access`, :mod:`.threats`,
:mod:`.people`); this module collects them, checks that no id is defined twice and that every
id is one of ``SECURITY_RULE_IDS`` with its texts (:mod:`.texts`), and answers one by id.
"""

from __future__ import annotations

from typing import Final

from arcilogger_db.settings import SECURITY_RULE_IDS
from arcilogger_worker.rules import access, network, people, threats
from arcilogger_worker.rules.model import RuleDef
from arcilogger_worker.rules.texts import TEXTS


def _collect(*groups: tuple[RuleDef, ...]) -> tuple[RuleDef, ...]:
    rules = [rule for group in groups for rule in group]
    ids = [rule.id for rule in rules]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError(f"security rules defined twice: {', '.join(duplicates)}")
    unknown = sorted(set(ids) - set(SECURITY_RULE_IDS))
    if unknown:
        raise ValueError(f"not a security rule id: {', '.join(unknown)}")
    untexted = sorted(set(ids) - set(TEXTS))
    if untexted:
        raise ValueError(f"security rules without texts: {', '.join(untexted)}")
    return tuple(sorted(rules, key=lambda rule: SECURITY_RULE_IDS.index(rule.id)))


#: The registered rules, in id order (``R01`` … ``R19``). A rule package's rules appear here when
#: its module lists them.
RULES: Final[tuple[RuleDef, ...]] = _collect(
    network.RULES, access.RULES, threats.RULES, people.RULES
)
_BY_ID: Final = {item.id: item for item in RULES}


def rule(rule_id: str) -> RuleDef:
    """The registered rule ``rule_id``; ``KeyError`` for an id that is not registered."""
    return _BY_ID[rule_id]


def rule_ids() -> tuple[str, ...]:
    """The ids of the registered rules, in order."""
    return tuple(item.id for item in RULES)
