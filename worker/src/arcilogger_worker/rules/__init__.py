"""The security rules R01-R19 and the engine that runs them (Wave 6 contract §2.1, spec 11).

* :mod:`.model` — what a rule is (:class:`~.model.RuleDef` with its parameters), what it finds
  (:class:`~.model.Hit` with its :class:`~.model.EvidenceRef` rows) and what it is given
  (:class:`~.model.RuleContext`);
* :mod:`.registry` — every rule, in id order (:data:`~.registry.RULES`);
* :mod:`.texts` — the names, title templates and advice of every rule, Turkish and English;
* :mod:`.network`, :mod:`.access`, :mod:`.threats`, :mod:`.people` — the rules themselves
  (W6-B … W6-E), one module per package.

A rule is Python code in the registry; an administrator changes only the values of its
parameters, which reach SQL as bound parameters (contract §0.1 rule 3). The rules read the parsed
tables, ``devices``, ``leases``, ``persons`` and ``settings``, and write nothing but alerts, their
evidence and their state (rule 2).
"""

from __future__ import annotations
