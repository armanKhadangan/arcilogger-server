"""Which source a spool folder belongs to, and what the ``sources`` table says about it.

Spool folder names come from the collector (``arcilogger_core.config.Config.source_name_for``):

=========================  ====================================================================
Folder                     Meaning
=========================  ====================================================================
``fortigate``              a configured source with one address: its configured type
``ap-172.20.10.23``        a sender inside a configured **network** (``ap`` = 172.20.10.0/24):
                           inherits the network entry's type
``unknown-172.20.19.9``    a sender that is not configured: the parser is chosen by content
                           (``devname=``/``logid=`` → FortiGate, else generic) and the source is
                           flagged ``unknown`` (alert "yeni log kaynağı")
anything else              a folder whose source was removed from the configuration: content
                           detection as well, but it is not flagged as a new sender
=========================  ====================================================================
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Final

from arcilogger_core.config import Config, SourceConfig
from arcilogger_ingest.parsers import SourceType

UNKNOWN_PREFIX: Final = "unknown-"


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """How ingest treats one spool folder."""

    name: str
    #: Parser type from the configuration; ``None`` = detect by content.
    configured_type: SourceType | None
    #: Folder named ``unknown-<ip>``: a sender that is not in the configuration.
    unknown: bool
    #: The configured exact source (``None`` for network members and unknown senders).
    config: SourceConfig | None = None

    @property
    def expected(self) -> bool:
        """Only exact configured sources are expected every day (like the sealer's gap check)."""
        return self.config is not None and self.config.expected

    @property
    def heartbeat_minutes(self) -> int:
        return self.config.heartbeat_minutes if self.config is not None else 15


def resolve_source(config: Config, name: str) -> SourceInfo:
    """Map a spool folder name to its configured type (see the module docstring)."""
    for source in config.sources:
        if source.name == name and not source.is_network:
            return SourceInfo(name, source.type, unknown=False, config=source)
    if name.startswith(UNKNOWN_PREFIX):
        return SourceInfo(name, None, unknown=True)
    for source in config.sources:
        prefix = source.name + "-"
        if source.is_network and name.startswith(prefix):
            try:
                address = ipaddress.ip_address(name[len(prefix) :])
            except ValueError:
                continue
            if address in ipaddress.ip_network(source.address):
                return SourceInfo(name, source.type, unknown=False)
    return SourceInfo(name, None, unknown=False)
