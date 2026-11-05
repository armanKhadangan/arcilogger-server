"""Which log sources are polled, and how (Wave 5 contract §6, N1).

``[sources.snmp]`` is read with the N1 defaults through ``getattr``: the model itself goes into
``config.py`` with W5-T's merge (§6.6), and then this module reads its attributes unchanged.
Only an enabled single-address source with ``snmp.enabled`` is polled; the target is always the
source's own ``address`` (N3): nothing else is ever asked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from arcilogger_core.config import Config
from arcilogger_core.secretstore import SecretStore, SecretStoreError
from arcilogger_core.snmp import Auth, Priv, Target

DEFAULT_PORT: Final = 161
DEFAULT_AUTH: Final[Auth] = "sha256"
DEFAULT_PRIV: Final[Priv] = "aes128"
DEFAULT_INTERVAL_SECONDS: Final = 15


def auth_key_name(source: str) -> str:
    """The secret store name of a source's SNMPv3 authentication key (N1)."""
    return f"snmp.{source}.auth_key"


def priv_key_name(source: str) -> str:
    """The secret store name of a source's SNMPv3 privacy key (N1)."""
    return f"snmp.{source}.priv_key"


@dataclass(frozen=True)
class SnmpSource:
    """An SNMP-enabled log source as the poller sees it (no secret in it)."""

    name: str
    host: str
    port: int
    user: str
    auth: Auth
    priv: Priv
    #: ``ifName`` of each WAN line (1 to 4).
    interfaces: tuple[str, ...]
    #: ``ifName`` → (down, up) in Mbit/s, as configured.
    capacity: Mapping[str, tuple[float, float]]
    interval_seconds: int


def _capacity(value: Any) -> dict[str, tuple[float, float]]:
    found: dict[str, tuple[float, float]] = {}
    for name, entry in dict(value or {}).items():
        down = entry.get("down") if isinstance(entry, Mapping) else getattr(entry, "down", None)
        up = entry.get("up") if isinstance(entry, Mapping) else getattr(entry, "up", None)
        if down is not None and up is not None:
            found[str(name)] = (float(down), float(up))
    return found


def snmp_sources(config: Config) -> list[SnmpSource]:
    """The sources to poll: enabled, a single address, ``snmp.enabled`` and 1 to 4 interfaces."""
    found = []
    for source in config.sources:
        snmp = getattr(source, "snmp", None)
        if snmp is None or not getattr(snmp, "enabled", False):
            continue
        if not source.enabled or source.is_network:
            continue  # a network is refused when the configuration is read (N1)
        interfaces = tuple(str(name) for name in getattr(snmp, "interfaces", ()) or ())
        if not interfaces:
            continue
        found.append(
            SnmpSource(
                name=source.name,
                host=source.address,
                port=int(getattr(snmp, "port", DEFAULT_PORT)),
                user=str(getattr(snmp, "user", "")),
                auth=getattr(snmp, "auth", DEFAULT_AUTH),
                priv=getattr(snmp, "priv", DEFAULT_PRIV),
                interfaces=interfaces[:4],
                capacity=_capacity(getattr(snmp, "capacity_mbps", None)),
                interval_seconds=int(getattr(snmp, "interval_seconds", DEFAULT_INTERVAL_SECONDS)),
            )
        )
    return found


def secret_store(config: Config) -> SecretStore:
    return SecretStore(config.secrets_dir / "secret.key", config.secrets_dir / "secrets.json")


def target(source: SnmpSource, store: SecretStore) -> Target | None:
    """The request target with both keys from the secret store; ``None`` while one is missing
    or unreadable (the poll then reports ``auth_failed``)."""
    try:
        auth_key = store.get(auth_key_name(source.name))
        priv_key = store.get(priv_key_name(source.name))
    except SecretStoreError:
        return None
    if not auth_key or not priv_key:
        return None
    return Target(
        host=source.host,
        port=source.port,
        user=source.user,
        auth=source.auth,
        priv=source.priv,
        auth_key=auth_key,
        priv_key=priv_key,
    )
