"""Which log sources are polled, and how (Wave 5 contract §6, N1).

``[sources.snmp]`` is read through ``getattr`` with the N1 defaults, and N1's rules are applied
here: the model itself goes into ``config.py`` with W5-T's merge (§6.6, Q3 on #202), and then
refuses the same values when the configuration is read. Only an enabled single-address source
with ``snmp.enabled`` is polled; the target is always the source's own ``address`` (N3): nothing
else is ever asked. Only SNMPv3 with authentication and privacy exists: a community string, a
version other than 3 or a missing ``auth``/``priv`` protocol is refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, get_args

from arcilogger_core.config import Config
from arcilogger_core.secretstore import SecretStore, SecretStoreError
from arcilogger_core.snmp import Auth, Priv, Target

DEFAULT_PORT: Final = 161
DEFAULT_AUTH: Final[Auth] = "sha256"
DEFAULT_PRIV: Final[Priv] = "aes128"
DEFAULT_INTERVAL_SECONDS: Final = 15
PORTS: Final = range(1, 65536)
INTERVALS: Final = range(10, 61)
MAX_INTERFACES: Final = 4
#: The keys' lengths (N1).
KEY_LENGTHS: Final = range(8, 65)

#: Why a source with ``snmp.enabled`` is not polled (the job's details name it; the model of
#: W5-T refuses the same values with its own error when the configuration is read).
NETWORK: Final = "snmp_source_is_network"
SECRET_MISSING: Final = "snmp_secret_missing"  # noqa: S105 - a refusal code, not a secret
AUTH_PRIV_ONLY: Final = "snmp_auth_priv_required"
INVALID: Final = "snmp_setting_invalid"


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


def refusal(source: Any) -> str | None:
    """Why N1 refuses the ``[sources.snmp]`` of ``source``, or ``None``."""
    snmp = source.snmp
    if source.is_network:
        return NETWORK
    if (
        getattr(snmp, "community", None) is not None
        or getattr(snmp, "version", 3) != 3
        or getattr(snmp, "auth", DEFAULT_AUTH) not in get_args(Auth)
        or getattr(snmp, "priv", DEFAULT_PRIV) not in get_args(Priv)
    ):
        return AUTH_PRIV_ONLY  # no v1/v2c, no noAuthNoPriv or authNoPriv
    interfaces = list(getattr(snmp, "interfaces", ()) or ())
    capacity = _capacity(getattr(snmp, "capacity_mbps", None))
    if (
        getattr(snmp, "port", DEFAULT_PORT) not in PORTS
        or getattr(snmp, "interval_seconds", DEFAULT_INTERVAL_SECONDS) not in INTERVALS
        or not str(getattr(snmp, "user", "") or "").strip()
        or not 1 <= len(interfaces) <= MAX_INTERFACES
        or len(set(interfaces)) != len(interfaces)
        or not all(isinstance(name, str) and name.strip() for name in interfaces)
        or not set(capacity) <= set(interfaces)
        or any(value <= 0 for pair in capacity.values() for value in pair)
    ):
        return INVALID
    return None


def snmp_sources(config: Config, refused: dict[str, str] | None = None) -> list[SnmpSource]:
    """The sources to poll: enabled, ``snmp.enabled`` and accepted by N1 (:func:`refusal`);
    ``refused`` gets each SNMP-enabled source that is not, with its code."""
    found = []
    for source in config.sources:
        snmp = getattr(source, "snmp", None)
        if snmp is None or not getattr(snmp, "enabled", False) or not source.enabled:
            continue
        code = refusal(source)
        if code is not None:
            if refused is not None:
                refused[source.name] = code
            continue
        found.append(
            SnmpSource(
                name=source.name,
                host=source.address,
                port=int(getattr(snmp, "port", DEFAULT_PORT)),
                user=str(getattr(snmp, "user", "")),
                auth=getattr(snmp, "auth", DEFAULT_AUTH),
                priv=getattr(snmp, "priv", DEFAULT_PRIV),
                interfaces=tuple(str(name) for name in snmp.interfaces),
                capacity=_capacity(getattr(snmp, "capacity_mbps", None)),
                interval_seconds=int(getattr(snmp, "interval_seconds", DEFAULT_INTERVAL_SECONDS)),
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
        auth_key = store.get(auth_key_name(source.name))
        priv_key = store.get(priv_key_name(source.name))
    except SecretStoreError:
        return None
    if auth_key is None or priv_key is None:
        return None
    if len(auth_key) not in KEY_LENGTHS or len(priv_key) not in KEY_LENGTHS:
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
