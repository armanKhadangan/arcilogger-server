"""One round trip against an in-process SNMPv3 responder (Wave 5 contract §6.5, W5-N).

pysnmp's own agent side answers on a loopback UDP port, in a thread with its own event loop:
an authPriv user (SHA-256, AES-128), ``sysUpTime`` and the ``ifName``, ``ifHCInOctets``,
``ifHCOutOctets``, ``ifOperStatus`` and ``ifHighSpeed`` objects of two interfaces; the test
moves the counters and ``sysUpTime``. The real :class:`~arcilogger_core.snmp.PysnmpClient` and the
``snmp.poll`` job then run as on a site: walk, GET, rates, the state file; and a wrong key gives
``auth_failed``, a port with no agent ``timeout``.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStore, snmp_auth_key, snmp_priv_key
from arcilogger_core.snmp import (
    IF_HC_IN_OCTETS,
    IF_HC_OUT_OCTETS,
    IF_HIGH_SPEED,
    IF_NAME,
    IF_OPER_STATUS,
    SYS_UPTIME,
    PysnmpClient,
    SnmpError,
    Target,
)
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_worker.snmp import job as snmp_job
from arcilogger_worker.snmp.state import read_state

USER = "arcilogger"
AUTH_KEY = "authkeyauthkey"
PRIV_KEY = "privkeyprivkey"
#: A second user whose keys are outside Latin-1: the device's passphrase is their UTF-8 octets.
WIDE_USER = "ornek"
WIDE_AUTH = "Örnekşifre-ğüı"
WIDE_PRIV = "Gizlişifre-çöğ"
T0 = datetime(2026, 10, 12, 10, 0, tzinfo=SITE_TZ)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _oid(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


@dataclass
class Agent:
    """The responder's objects: OID → value, read at each request (the test moves them)."""

    port: int
    values: dict[str, Any] = field(default_factory=dict)


def _serve(agent: Agent, ready: threading.Event, stop: list[Any]) -> None:
    from pysnmp.carrier.asyncio.dgram import udp
    from pysnmp.entity import config, engine
    from pysnmp.entity.rfc3413 import cmdrsp, context
    from pysnmp.proto import rfc1902

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    snmp = engine.SnmpEngine()
    config.add_transport(
        snmp, udp.DOMAIN_NAME, udp.UdpTransport().open_server_mode(("127.0.0.1", agent.port))
    )
    config.add_v3_user(
        snmp,
        USER,
        config.USM_AUTH_HMAC192_SHA256,
        AUTH_KEY,
        config.USM_PRIV_CFB128_AES,
        PRIV_KEY,
    )
    config.add_v3_user(
        snmp,
        WIDE_USER,
        config.USM_AUTH_HMAC192_SHA256,
        WIDE_AUTH.encode("utf-8"),
        config.USM_PRIV_CFB128_AES,
        WIDE_PRIV.encode("utf-8"),
    )
    config.add_vacm_user(snmp, 3, USER, "authPriv", (1, 3, 6), (1, 3, 6))
    config.add_vacm_user(snmp, 3, WIDE_USER, "authPriv", (1, 3, 6), (1, 3, 6))
    snmp_context = context.SnmpContext(snmp)
    builder = snmp_context.get_mib_instrum().get_mib_builder()
    scalar, instance = builder.import_symbols("SNMPv2-SMI", "MibScalar", "MibScalarInstance")

    class Live(instance):  # type: ignore[misc, valid-type]
        def getValue(self, name: Any, **context: Any) -> Any:  # noqa: N802 - pysnmp's name
            return self.syntax.clone(agent.values[".".join(str(part) for part in name)])

    columns = {
        IF_NAME: rfc1902.OctetString(),
        IF_HC_IN_OCTETS: rfc1902.Counter64(),
        IF_HC_OUT_OCTETS: rfc1902.Counter64(),
        IF_OPER_STATUS: rfc1902.Integer32(),
        IF_HIGH_SPEED: rfc1902.Gauge32(),
    }
    objects = []
    for column, syntax in columns.items():
        objects.append(scalar(_oid(column), syntax))
        # an instance answers only while its syntax has a value: the starting one
        objects += [
            Live(_oid(column), (index,), syntax.clone(agent.values[f"{column}.{index}"]))
            for index in (1, 2)
        ]
    # sysUpTime too, so that the test moves the device's clock with its own (the rate's Δt)
    uptime = _oid(SYS_UPTIME)
    objects.append(Live(uptime[:-1], uptime[-1:], rfc1902.TimeTicks(agent.values[SYS_UPTIME])))
    builder.export_symbols("__ARCILOGGER_TEST", *objects)
    cmdrsp.GetCommandResponder(snmp, snmp_context)
    cmdrsp.NextCommandResponder(snmp, snmp_context)
    snmp.transport_dispatcher.job_started(1)
    stop.append(loop)
    loop.call_soon(ready.set)
    try:
        loop.run_forever()
    finally:
        snmp.transport_dispatcher.job_finished(1)
        snmp.close_dispatcher()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


@pytest.fixture
def agent() -> Iterator[Agent]:
    responder = Agent(_free_port())
    responder.values = {
        SYS_UPTIME: 815_220_100,
        f"{IF_NAME}.1": "wan1",
        f"{IF_NAME}.2": "lan",
        f"{IF_HC_IN_OCTETS}.1": 10_000_000_000,
        f"{IF_HC_OUT_OCTETS}.1": 2_000_000_000,
        f"{IF_OPER_STATUS}.1": 1,
        f"{IF_HIGH_SPEED}.1": 1000,
        f"{IF_HC_IN_OCTETS}.2": 5,
        f"{IF_HC_OUT_OCTETS}.2": 6,
        f"{IF_OPER_STATUS}.2": 2,
        f"{IF_HIGH_SPEED}.2": 100,
    }
    ready, stop = threading.Event(), list[asyncio.AbstractEventLoop]()
    thread = threading.Thread(target=_serve, args=(responder, ready, stop), daemon=True)
    thread.start()
    assert ready.wait(10), "the SNMP responder did not start"
    yield responder
    stop[0].call_soon_threadsafe(stop[0].stop)
    thread.join(10)


def _target(port: int, auth_key: str = AUTH_KEY) -> Target:
    return Target("127.0.0.1", port, USER, "sha256", "aes128", auth_key, PRIV_KEY)


def test_the_client_walks_and_gets_with_authpriv(agent: Agent) -> None:
    client = PysnmpClient()
    names = asyncio.run(client.walk(_target(agent.port), IF_NAME))
    assert names == {f"{IF_NAME}.1": "wan1", f"{IF_NAME}.2": "lan"}
    oids = [SYS_UPTIME, f"{IF_HC_IN_OCTETS}.1", f"{IF_OPER_STATUS}.2", f"{IF_HC_IN_OCTETS}.9"]
    values = asyncio.run(client.get(_target(agent.port), oids))
    assert isinstance(values[SYS_UPTIME], int)
    assert values[f"{IF_HC_IN_OCTETS}.1"] == 10_000_000_000  # a Counter64 above 2**32
    assert values[f"{IF_OPER_STATUS}.2"] == 2
    assert values[f"{IF_HC_IN_OCTETS}.9"] is None  # noSuchInstance


def test_a_wrong_key_is_auth_failed_and_no_agent_is_timeout(agent: Agent) -> None:
    client = PysnmpClient()
    with pytest.raises(SnmpError) as wrong:
        asyncio.run(client.get(_target(agent.port, auth_key="wrongwrongwrong"), [SYS_UPTIME]))
    assert wrong.value.status == "auth_failed"
    with pytest.raises(SnmpError) as silent:
        asyncio.run(client.get(_target(_free_port()), [SYS_UPTIME]))
    assert silent.value.status == "timeout"


def test_a_key_outside_latin_1_is_sent_as_utf_8(agent: Agent) -> None:
    wide = Target("127.0.0.1", agent.port, WIDE_USER, "sha256", "aes128", WIDE_AUTH, WIDE_PRIV)
    values = asyncio.run(PysnmpClient().get(wide, [f"{IF_OPER_STATUS}.1"]))
    assert values == {f"{IF_OPER_STATUS}.1": 1}


def test_a_failure_pysnmp_raises_is_an_snmp_error_without_its_message() -> None:
    """An IPv6 address (only a hand-edited configuration can carry one) is not a crash."""
    ipv6 = Target("2001:db8::1", 161, USER, "sha256", "aes128", AUTH_KEY, PRIV_KEY)
    with pytest.raises(SnmpError) as error:
        asyncio.run(PysnmpClient().get(ipv6, [SYS_UPTIME]))
    assert error.value.status == "timeout"
    assert error.value.detail.isidentifier()  # the exception's type only
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def test_the_probe_child_lists_the_device_s_interfaces(agent: Agent) -> None:
    """The ``snmp`` probe in its child process against the responder: a name the device does
    not have is refused with what it reports (W5-N-9)."""
    from arcilogger_core.admin.contract import ProbeInterface
    from arcilogger_core.admin.probe import ChildProber

    params: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": agent.port,
        "user": USER,
        "auth": "sha256",
        "priv": "aes128",
        "interfaces": ["wan9"],
    }
    keys = {"auth_key": AUTH_KEY, "priv_key": PRIV_KEY}
    result = ChildProber().run("snmp", params, keys)
    assert (result.ok, result.code) == (False, "snmp_no_such_interface")
    assert result.interfaces == (
        ProbeInterface(name="wan1", oper_status="up", speed_mbps=1000),
        ProbeInterface(name="lan", oper_status="down", speed_mbps=100),
    )


def test_the_job_round_trip(agent: Agent, tmp_path: Path, layout: Layout) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    SecretStore.create_key(secrets / "secret.key")
    store = SecretStore(secrets / "secret.key", secrets / "secrets.json")
    store.set(snmp_auth_key("fw-1"), AUTH_KEY)
    store.set(snmp_priv_key("fw-1"), PRIV_KEY)
    config = Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(secrets),
            "sources": [
                {
                    "name": "fw-1",
                    "type": "fortigate",
                    "address": "127.0.0.1",
                    "snmp": {
                        "enabled": True,
                        "port": agent.port,
                        "user": USER,
                        "interfaces": ["wan1"],
                        "capacity_mbps": {"wan1": {"down": 1000, "up": 100}},
                        "interval_seconds": 10,
                    },
                }
            ],
        }
    )
    assert snmp_job.poll_due(layout, config, now=T0, sessions=None) is not None
    agent.values[SYS_UPTIME] += 1000  # 10 s pass on the device
    agent.values[f"{IF_HC_IN_OCTETS}.1"] += 12_500_000  # 10 Mbit/s down over 10 s
    agent.values[f"{IF_HC_OUT_OCTETS}.1"] += 1_250_000  # 1 Mbit/s up
    later = T0 + timedelta(seconds=10)
    report = snmp_job.poll_due(layout, config, now=later, sessions=None)
    assert report is not None
    assert report.polled == ["fw-1"]
    line = read_state(layout.wan_rates).sources["fw-1"].lines["wan1"]
    assert (line.status, line.if_index, line.oper_status, line.high_speed_mbps) == (
        "ok",
        1,
        "up",
        1000,
    )
    assert line.samples == [(later, 10_000_000.0, 1_000_000.0)]
    assert line.capacity == (1000.0, 100.0)
    text = layout.wan_rates.read_text(encoding="utf-8")
    assert AUTH_KEY not in text
    assert PRIV_KEY not in text
    assert json.loads(text)["sources"]["fw-1"]["lines"]["wan1"]["status"] == "ok"
