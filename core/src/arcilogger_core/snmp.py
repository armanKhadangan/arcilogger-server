"""The SNMP library boundary (Wave 5 contract §6, N1/N2/N5): read-only SNMPv3 GETs and walks.

Only SNMPv3 with authentication and privacy (authPriv) exists here: there is no community
string, so SNMPv1/v2c cannot be asked for. The worker's poller (``arcilogger_worker.snmp``) and
the administration probe talk to :class:`SnmpClient`; :class:`PysnmpClient` is the real one,
tests use a double at this boundary. Every failure is an :class:`SnmpError` whose ``status`` is
one of the codes of N4. It lives in core, as ``smtp`` does, so the probe's child process needs
nothing from the worker; pysnmp is imported only inside the methods here, so nothing the
collector or ingest load ever loads it.

The keys of a :class:`Target` never appear in its ``repr``, in a log line or in an error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol

Auth = Literal["sha1", "sha256", "sha512"]
Priv = Literal["aes128", "aes256"]
#: ``ok`` and the failures of a poll (N4); ``disabled`` is a source whose SNMP was switched off.
Status = Literal[
    "ok",
    "timeout",
    "auth_failed",
    "no_such_interface",
    "snmp_no_hc_counters",
    "disabled",
]

SYS_UPTIME: Final = "1.3.6.1.2.1.1.3.0"
IF_NAME: Final = "1.3.6.1.2.1.31.1.1.1.1"
IF_HC_IN_OCTETS: Final = "1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT_OCTETS: Final = "1.3.6.1.2.1.31.1.1.1.10"
IF_OPER_STATUS: Final = "1.3.6.1.2.1.2.2.1.8"
IF_HIGH_SPEED: Final = "1.3.6.1.2.1.31.1.1.1.15"

#: How long one request waits for an answer, and how often it is sent again: a device that does
#: not answer costs the job at most 2 s per request (the sources are polled concurrently).
TIMEOUT_SECONDS: Final = 1.0
RETRIES: Final = 1

#: ``ifOperStatus`` (RFC 2863) by name, as ``/activity/wan`` gives it.
OPER_STATUS: Final[Mapping[int, str]] = {
    1: "up",
    2: "down",
    3: "testing",
    4: "unknown",
    5: "dormant",
    6: "not_present",
    7: "lower_layer_down",
}


@dataclass(frozen=True)
class Target:
    """One device: the log source's own address (N1), the SNMPv3 user and its two keys."""

    host: str
    port: int
    user: str
    auth: Auth
    priv: Priv
    auth_key: str = field(repr=False)
    priv_key: str = field(repr=False)


class SnmpError(Exception):
    """A request that gave no usable answer; ``status`` is its N4 code."""

    def __init__(self, status: Status, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status: Status = status
        self.detail = detail


#: An object the agent does not have (``noSuchObject``, ``noSuchInstance``, ``endOfMibView``).
MISSING: Final = None
Value = int | str | None


class SnmpClient(Protocol):
    async def get(self, target: Target, oids: Sequence[str]) -> dict[str, Value]:
        """One GET of ``oids``: each OID's integer or text value, or :data:`MISSING`."""
        ...

    async def walk(self, target: Target, oid: str) -> dict[str, str]:
        """A walk under ``oid``: the full OID of each object and its text."""
        ...


#: Error indications of the user-based security model: the user, a key or a protocol is wrong.
_AUTH_ERRORS: Final = frozenset(
    {
        "AuthenticationError",
        "AuthenticationFailure",
        "DecryptionError",
        "NotInTimeWindow",
        "UnknownSecurityName",
        "UnknownUserName",
        "UnsupportedAuthProtocol",
        "UnsupportedPrivProtocol",
        "UnsupportedSecurityLevel",
        "WrongDigest",
    }
)


def _status_of(indication: object) -> Status:
    """The N4 code of a pysnmp error indication: a key or protocol the agent refused is
    ``auth_failed``; anything else (no answer, a malformed one) is ``timeout``."""
    return "auth_failed" if type(indication).__name__ in _AUTH_ERRORS else "timeout"


class PysnmpClient:
    """:class:`SnmpClient` over pysnmp's asyncio API (a new engine for each request)."""

    def _session(self, target: Target) -> tuple[Any, Any]:
        from pysnmp.hlapi.v3arch import asyncio as hlapi

        auth = {
            "sha1": hlapi.usmHMACSHAAuthProtocol,
            "sha256": hlapi.usmHMAC192SHA256AuthProtocol,
            "sha512": hlapi.usmHMAC384SHA512AuthProtocol,
        }[target.auth]
        priv = {"aes128": hlapi.usmAesCfb128Protocol, "aes256": hlapi.usmAesCfb256Protocol}[
            target.priv
        ]
        user = hlapi.UsmUserData(
            target.user,
            authKey=target.auth_key,
            privKey=target.priv_key,
            authProtocol=auth,
            privProtocol=priv,
        )
        return hlapi.SnmpEngine(), user

    async def get(self, target: Target, oids: Sequence[str]) -> dict[str, Value]:
        from pysnmp.hlapi.v3arch import asyncio as hlapi

        engine, user = self._session(target)
        try:
            transport = await hlapi.UdpTransportTarget.create(
                (target.host, target.port), timeout=TIMEOUT_SECONDS, retries=RETRIES
            )
            indication, status, _index, binds = await hlapi.get_cmd(
                engine,
                user,
                transport,
                hlapi.ContextData(),
                *(hlapi.ObjectType(hlapi.ObjectIdentity(oid)) for oid in oids),
                lookupMib=False,
            )
        finally:
            engine.close_dispatcher()
        if indication is not None:
            raise SnmpError(_status_of(indication), type(indication).__name__)
        if status:
            raise SnmpError("timeout", f"error-status {int(status)}")
        return {str(oid): _value(value) for oid, value in binds}

    async def walk(self, target: Target, oid: str) -> dict[str, str]:
        from pysnmp.hlapi.v3arch import asyncio as hlapi

        engine, user = self._session(target)
        found: dict[str, str] = {}
        try:
            transport = await hlapi.UdpTransportTarget.create(
                (target.host, target.port), timeout=TIMEOUT_SECONDS, retries=RETRIES
            )
            async for indication, status, _index, binds in hlapi.walk_cmd(
                engine,
                user,
                transport,
                hlapi.ContextData(),
                hlapi.ObjectType(hlapi.ObjectIdentity(oid)),
                lexicographicMode=False,
                lookupMib=False,
            ):
                if indication is not None:
                    raise SnmpError(_status_of(indication), type(indication).__name__)
                if status:
                    raise SnmpError("timeout", f"error-status {int(status)}")
                for name, value in binds:
                    text = _value(value)
                    if text is not MISSING:
                        found[str(name)] = str(text)
        finally:
            engine.close_dispatcher()
        return found


def _value(value: Any) -> Value:
    """An integer (counters, gauges, enumerations, ``TimeTicks``), a text, or :data:`MISSING`."""
    from pyasn1.type import univ
    from pysnmp.proto import rfc1905

    if isinstance(value, rfc1905.NoSuchObject | rfc1905.NoSuchInstance | rfc1905.EndOfMibView):
        return MISSING
    if isinstance(value, univ.OctetString):
        return bytes(value.asOctets()).decode("utf-8", "replace")
    return int(value)


@dataclass(frozen=True)
class Interface:
    """One interface as the device reports it (the probe's list, N5)."""

    name: str
    index: int
    oper_status: str | None
    speed_mbps: int | None
    #: Whether ``ifHCInOctets`` answers (asked only for the interfaces looked for).
    counters: bool


#: Interfaces listed by the probe at most, and objects asked in one GET.
MAX_LISTED: Final = 64
GET_BATCH: Final = 24


async def list_interfaces(
    client: SnmpClient, target: Target, wanted: Sequence[str] = ()
) -> list[Interface]:
    """``ifName`` walked, then each interface's ``ifOperStatus`` and ``ifHighSpeed``, and for the
    ``wanted`` names whether ``ifHCInOctets`` answers (the first index of a repeated name)."""
    indexes: dict[str, int] = {}
    for oid, name in (await client.walk(target, IF_NAME)).items():
        last = oid.rsplit(".", 1)[-1]
        if last.isdigit():
            indexes.setdefault(name, int(last))
    listed = sorted(indexes.items(), key=lambda item: item[1])[:MAX_LISTED]
    oids = [
        f"{column}.{index}" for _, index in listed for column in (IF_OPER_STATUS, IF_HIGH_SPEED)
    ]
    oids += [f"{IF_HC_IN_OCTETS}.{indexes[name]}" for name in wanted if name in indexes]
    values: dict[str, Value] = {}
    for start in range(0, len(oids), GET_BATCH):
        values.update(await client.get(target, oids[start : start + GET_BATCH]))
    found = []
    for name, index in listed:
        oper = values.get(f"{IF_OPER_STATUS}.{index}")
        speed = values.get(f"{IF_HIGH_SPEED}.{index}")
        found.append(
            Interface(
                name=name,
                index=index,
                oper_status=OPER_STATUS.get(oper) if isinstance(oper, int) else None,
                speed_mbps=speed if isinstance(speed, int) else None,
                counters=isinstance(values.get(f"{IF_HC_IN_OCTETS}.{index}"), int),
            )
        )
    return found
