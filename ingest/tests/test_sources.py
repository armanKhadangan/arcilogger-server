"""Spool folder → configured parser type (exact sources, network members, unknown senders)."""

from __future__ import annotations

from pathlib import Path

import pytest

from arcilogger_ingest.sources import resolve_source
from arcilogger_ingest.testing import make_config


@pytest.mark.parametrize(
    ("folder", "configured_type", "unknown", "expected", "heartbeat"),
    [
        ("fortigate", "fortigate", False, True, 10),
        ("core-r1", "mikrotik", False, True, 15),
        # a member of the network source "ap" (172.20.10.0/24) inherits its type
        ("ap-172.20.10.23", "generic", False, False, 15),
        # outside the network: not a member, no configured type
        ("ap-172.20.12.23", None, False, False, 15),
        ("ap-not-an-ip", None, False, False, 15),
        # the network entry's own name is not a spool folder of a configured exact source
        ("ap", None, False, False, 15),
        ("unknown-10.9.9.9", None, True, False, 15),
        ("unknown-fe80--1", None, True, False, 15),
        # removed from the configuration: content detection, but not a "new sender"
        ("old-switch", None, False, False, 15),
    ],
)
def test_resolve_source(
    tmp_path: Path,
    folder: str,
    configured_type: str | None,
    unknown: bool,
    expected: bool,
    heartbeat: int,
) -> None:
    info = resolve_source(make_config(tmp_path), folder)
    assert info.name == folder
    assert info.configured_type == configured_type
    assert info.unknown is unknown
    assert info.expected is expected
    assert info.heartbeat_minutes == heartbeat


def test_exact_source_wins_over_a_network_prefix(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        sources=[
            {"name": "ap", "type": "generic", "address": "172.20.10.0/24"},
            {"name": "ap-core", "type": "mikrotik", "address": "172.20.10.4"},
        ],
    )
    assert resolve_source(config, "ap-core").configured_type == "mikrotik"
    assert resolve_source(config, "ap-core").config is not None
    assert resolve_source(config, "ap-172.20.10.204").configured_type == "generic"
