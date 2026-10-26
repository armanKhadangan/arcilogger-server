"""No secret leaves the secret store in plain text (ADR-0014; contract 3, the canary test).

A canary value goes into every secret the browser may set — the Kamu SM customer number and
password, the SMTP password, the replica password — through applies that succeed, a probe that
fails and echoes it, a held change, a rejected request and a crash recovery. Afterwards no file
under the installation folder or the data root (inbox, work, pending, status, journal, versions,
system, task requests, logs) and no log record contains the plaintext.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue

from arcilogger_core.admin.probe import ProbeKind, ProbeResult
from arcilogger_core.admin.testing import FakeAgent, FakeProber, Installed, make_installation
from arcilogger_core.timeutil import utc_now

CANARIES = {
    "kamusm.customer": "KANARYA7731",
    "kamusm.password": "kanarya-kamu-parola-11",
    "smtp.password": "kanarya-smtp-parola-22",
    "replica.password": "kanarya-replika-parola-33",
}


class EchoingProber(FakeProber):
    """A probe that fails and puts every secret it was given into its message."""

    def run(
        self, kind: ProbeKind, params: Mapping[str, JsonValue], secrets: Mapping[str, str]
    ) -> ProbeResult:
        super().run(kind, params, secrets)
        if kind == "replica":
            return ProbeResult(
                False, "replica_test_failed", "refused: " + " ".join(secrets.values())
            )
        return ProbeResult(True, None, "ok " + " ".join(secrets.values()))


def every_file(*roots: Path) -> Iterator[Path]:
    for root in roots:
        yield from (path for path in root.rglob("*") if path.is_file())


def token(installed: Installed, name: str) -> dict[str, Any]:
    return {
        "op": "secret_set",
        "name": name,
        "expect": "unset",
        "token": installed.store.encrypt_value(CANARIES[name]),
    }


@pytest.fixture
def installed(tmp_path: Path) -> Installed:
    return make_installation(tmp_path / "ProgramData")


def test_no_plaintext_secret_reaches_a_file_or_a_log(
    installed: Installed, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    prober = EchoingProber()
    agent = FakeAgent(installed, prober=prober)
    smtp: list[dict[str, Any]] = [
        {"op": "set", "path": "smtp.enabled", "expect": False, "value": True},
        {"op": "set", "path": "smtp.host", "expect": None, "value": "smtp.ornek.example"},
        {"op": "set", "path": "smtp.username", "expect": None, "value": "arcilogger"},
        {"op": "set", "path": "smtp.from_address", "expect": None,
         "value": "arcilogger@ornek.example"},
        token(installed, "smtp.password"),
    ]  # fmt: skip
    replica: list[dict[str, Any]] = [
        {"op": "set", "path": "replica.enabled", "expect": False, "value": True},
        {"op": "set", "path": "replica.target", "expect": None, "value": "\\\\NAS-01\\worm"},
        {"op": "set", "path": "replica.user", "expect": None, "value": "NAS-01\\arcireplica"},
        token(installed, "replica.password"),
    ]
    requests = [
        installed.request(
            section="tsa",
            ops=[token(installed, "kamusm.customer"), token(installed, "kamusm.password")],
        ),
        installed.request(section="smtp", ops=smtp, options={"test_to": "bt@ornek.example"}),
        installed.request(section="replica", ops=replica),  # the probe fails and echoes
        installed.request(section="replica", ops=replica, probe=False),
        installed.request(  # expired: rejected after its signature verified
            section="tsa",
            ops=[token(installed, "kamusm.password")],
            now=utc_now() - timedelta(minutes=20),
        ),
    ]
    for request in requests:
        installed.submit(request)
        agent.process()
    states = [installed.status(r.id).state for r in requests]  # type: ignore[union-attr]
    assert states == ["applied", "applied", "probe_failed", "applied", "rejected"]
    assert installed.store.get("smtp.password") == CANARIES["smtp.password"]
    assert {call.kind for call in prober.calls} == {"smtp", "replica"}
    # A held change keeps its token in pending\ until it is due.
    installed.write_policy(hold_hours=1)
    held = installed.request(
        section="replica",
        ops=[
            {"op": "set", "path": "replica.enabled", "expect": True, "value": False},
        ],
    )
    installed.submit(held)
    agent.process()
    assert installed.status(held.id).state == "pending"  # type: ignore[union-attr]
    found = [
        str(path)
        for path in every_file(installed.root, installed.data_dir)
        for value in CANARIES.values()
        if value.encode() in path.read_bytes()
    ]
    assert found == []
    for value in CANARIES.values():
        assert value not in caplog.text
        assert all(value not in message for _, _, message in agent.events.events)
