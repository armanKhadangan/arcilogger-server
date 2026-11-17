"""Example and test data use the fictional world's IPv4 addresses only (CLAUDE.md, issue #313).

The fictional world is 172.20.x (and the other private and shared ranges a site has), and the
RFC 5737 documentation networks 192.0.2.0/24, 198.51.100.0/24 and 203.0.113.0/24 for anything
on the internet. A real public address (a well-known resolver, a CDN, a cloud service) must not
come back into the repository's examples: #313 replaced the last ones (8.8.8.8, 1.1.1.1, …).

The finder looks at IPv4 literals in **known contexts** only: after a key that names an address
(``ip=``, ``srcip=``, ``"dstip": "…"``, ``public_ip=``, ``answers=[…``, ``hosts: ['…``,
``address = "…"`` and the like). Dotted numbers elsewhere are mostly not addresses (X.509 and
SNMP OIDs, RFC section numbers, version strings), so they are left alone. An exception needs an
entry in :data:`ALLOWED` with its reason. #308's ``tests/test_example_data.py`` pins the MACs.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Final, NamedTuple

import pytest

ROOT: Final = Path(__file__).resolve().parents[1]
#: The design contracts quote real-world facts, lock files hold no example data, and this file
#: plants public addresses on purpose to pin the finder.
SKIPPED: Final = re.compile(
    r"^design/|(^|/)(uv\.lock|package-lock\.json)$|^tests/test_example_ipv4\.py$"
)
#: Real public addresses that may stay, each with its reason.
ALLOWED: Final[dict[str, str]] = {
    "172.0.0.0": (
        "the network 172.0.0.0/8, not a host: what the search grammar expands `srcip:172.*` to "
        "(api/tests/test_search_grammar.py), and a sender range wider than the installer "
        "allows (tests/test_installer_scripts.py)"
    ),
}
#: An IPv4 literal right after a key that names an address. ``ip`` covers ``srcip``, ``dstip``,
#: ``transip``, ``public_ip``, ``dest_ip``; the key may be quoted (JSON, TOML, Python) and the
#: value quoted, a bytes literal, or the first item of a list or tuple.
CONTEXT: Final = re.compile(
    r"""(?ix)
    (?<![a-z0-9_])
    (?P<key>[a-z_]*(?:ip|ips|addr|address|addresses|host|hosts|sender|senders|answers|server
        |servers|gateway|destination|domain))
    ["']?\s*[:=]\s*[\[(]?\s*b?["']?
    (?P<ip>\d{1,3}(?:\.\d{1,3}){3})(?![\d.]*\d)
    """
)


class Found(NamedTuple):
    path: str
    line: int
    key: str
    address: str


def public(address: str) -> bool:
    """A real internet address: global, not multicast, not RFC 5737 (which Python already
    counts as not global)."""
    try:
        parsed = ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return parsed.is_global and not parsed.is_multicast


def find(path: str, text: str) -> Iterator[Found]:
    """The public IPv4 addresses of ``text`` in known contexts."""
    for number, line in enumerate(text.splitlines(), 1):
        for match in CONTEXT.finditer(line):
            if public(match["ip"]):
                yield Found(path, number, match["key"], match["ip"])


def tracked_texts() -> Iterable[tuple[str, str]]:
    """Every tracked UTF-8 text file of the repository but :data:`SKIPPED`."""
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    for name in filter(None, listed.split("\0")):
        if SKIPPED.search(name):
            continue
        try:
            yield name, (ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue


def test_no_public_example_address_in_a_known_context() -> None:
    found = [item for name, text in tracked_texts() for item in find(name, text)]
    unexplained = [item for item in found if item.address not in ALLOWED]
    assert unexplained == [], "\n".join(
        f"{f.path}:{f.line}: {f.key} {f.address} is a real public address; use 192.0.2.x, "
        "198.51.100.x or 203.0.113.x (RFC 5737), or add it to ALLOWED with the reason"
        for f in unexplained
    )
    stale = sorted(set(ALLOWED) - {item.address for item in found})
    assert stale == [], f"ALLOWED names addresses no longer found: {stale}"


@pytest.mark.parametrize(
    "line",
    [
        "srcip=8.8.8.8 dstip=172.20.41.5",
        'outcome = parse(b\'<189>logid="0000000013" srcip=8.8.8.8 devname="X"\')',
        '"dstip": "8.8.8.8",',
        '{"ip":"8.8.8.8"}',
        'lookup(db_session, public_ip="192.0.2.44", dest_ip="8.8.8.8")',
        'FgDns(qname="example.com", answers=["8.8.8.8", "2606:2800::1"])',
        "hosts: ['8.8.8.8'],",
        'address = "8.8.8.8"',
        "destinations = [(\"198.51.100.69\", 443)]; gateway='8.8.8.8'",
        '"domain": "8.8.8.8",',
    ],
)
def test_the_finder_catches_a_public_address_in_a_known_context(line: str) -> None:
    assert [item.address for item in find("planted.py", line)] == ["8.8.8.8"]


@pytest.mark.parametrize(
    "line",
    [
        'sv.encode(0x30, sv.encode_oid("1.2.3.4"))',  # an OID
        '"version": "2.12.0.0"',  # a version, and not after an address key
        '"""keyUsage allows cRLSign (RFC 5280 4.2.1.3)."""',  # a section number
        "ifHighSpeed = 1.3.6.1.2.1.31.1.1.1.15",  # an SNMP OID
        "srcip=198.51.100.7 dstip=203.0.113.9 transip=192.0.2.1",  # RFC 5737
        'ip=172.20.41.5 "sender": "172.31.255.1" address = "10.0.0.1"',  # private
        'transip = "100.64.12.9"',  # shared address space (RFC 6598), not public
        'host = "224.0.0.5"',  # multicast
        '("8.8.8.8", 53)',  # a public address outside a known context: not this test's
    ],
)
def test_the_finder_leaves_other_dotted_numbers(line: str) -> None:
    assert list(find("planted.py", line)) == []


def test_a_planted_public_address_fails_the_tree_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tree test itself fails on a planted ``8.8.8.8`` and names where it is."""
    planted = [("web/e2e/mock-planted.ts", "export const row = { srcip: '8.8.8.8' };\n")]
    tree = list(tracked_texts())
    monkeypatch.setattr("tests.test_example_ipv4.tracked_texts", lambda: [*tree, *planted])
    with pytest.raises(AssertionError, match=r"web/e2e/mock-planted\.ts:1: srcip 8\.8\.8\.8"):
        test_no_public_example_address_in_a_known_context()
