"""Which log sources are polled, and how (Wave 5 contract §6, N1).

``[sources.snmp]`` is :class:`arcilogger_core.config.SnmpConfig`: the configuration refuses what
N1 refuses when it is read (a network address, anything but authPriv, values out of bounds).
Only an enabled single-address source with ``snmp.enabled`` is polled; the target is always the
source's own ``address`` (N3): nothing else is ever asked. The two keys come from the secret
store at each poll; a missing one is ``snmp_secret_missing``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from arcilogger_core.config import Config
from arcilogger_core.secretstore import (
    SNMP_KEY_LENGTHS,
    SecretStore,
    SecretStoreError,
    snmp_auth_key,
    snmp_priv_key,
)
from arcilogger_core.snmp import Auth, Priv, Target

#: Why a polled source is asked without a request (the job's details name it).
SECRET_MISSING: Final = "snmp_secret_missing"  # noqa: S105 - a refusal code, not a secret


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


def snmp_sources(config: Config) -> list[SnmpSource]:
    """The sources to poll: enabled, with ``snmp.enabled`` (the configuration has accepted it)."""
    found = []
    for source in config.sources:
        snmp = source.snmp
        if snmp is None or not snmp.enabled or not source.enabled or source.is_network:
            continue
        found.append(
            SnmpSource(
                name=source.name,
                host=source.address,
                port=snmp.port,
                user=snmp.user,
                auth=snmp.auth,
                priv=snmp.priv,
                interfaces=tuple(name.strip() for name in snmp.interfaces),
                capacity={
                    name: (float(value.down), float(value.up))
                    for name, value in snmp.capacity_mbps.items()
                },
                interval_seconds=snmp.interval_seconds,
            )
        )
    return found


def secret_store(config: Config) -> SecretStore:
    return SecretStore(config.secrets_dir / "secret.key", config.secrets_dir / "secrets.json")


def target(source: SnmpSource, store: SecretStore) -> Target | None:
    """The request target with both keys from the secret store; ``None`` while one is missing,
    unreadable or not 8 to 64 characters (``snmp_secret_missing``: the poll then reports
    ``auth_failed`` without sending a request)."""
    try:
        auth_key = store.get(snmp_auth_key(source.name))
        priv_key = store.get(snmp_priv_key(source.name))
    except SecretStoreError:
        return None
    if auth_key is None or priv_key is None:
        return None
    if len(auth_key) not in SNMP_KEY_LENGTHS or len(priv_key) not in SNMP_KEY_LENGTHS:
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
