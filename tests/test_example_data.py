"""Example and test data use the fictional world's MAC addresses (CLAUDE.md, #303).

The repository's example world is Örnek A.Ş.: its devices have MAC addresses from the IANA
documentation block ``00:00:5E:00:53:xx`` (RFC 7042), and a phone's private (randomised) address is
the same block with the locally administered bit set, ``02:00:5E:00:53:xx``. A real
manufacturer's prefix in example data names a real vendor's device, which the rule forbids; a
narrow grep for one prefix (#14) let others through, so this test reads every tracked text file.

Allowed besides the two blocks, each for a stated reason: the protocol-defined addresses that
tests use as such (all zeros, broadcast, multicast), and the IEEE registry excerpts of the OUI
lookup's own test, which needs several distinct vendor prefixes and a 36-bit block. ``design/``
(the contracts, frozen per wave) and lock files are not read.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]

#: Six pairs of hex digits with one separator throughout, found at every position (overlapping),
#: so that a MAC after a word and a separator (``device:…``, ``mac:…``) is seen as well.
_WINDOW: Final = re.compile(r"(?=([0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\2[0-9A-Fa-f]{2}){4}))")
#: The dotted (Cisco) form, three groups of four hex digits.
_DOTTED: Final = re.compile(
    r"(?<![0-9A-Fa-f.])[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}(?![0-9A-Fa-f.])"
)
#: The word next to a window beyond a separator: a lone pair of hex digits means the window is part
#: of a longer run (a DHCP client id, the inside of another MAC), a time span has two separators.
_WORD: Final = re.compile(r"[0-9A-Za-z_.]+")


def macs(line: str) -> list[str]:
    """The MAC addresses written in ``line``: six pairs with one separator, not part of a longer
    run of pairs (a DHCP client id, an alert key's two MACs joined by another pair)."""
    found = []
    for match in _WINDOW.finditer(line):
        value, separator = match.group(1), match.group(2)
        start, end = match.start(1), match.start(1) + len(value)
        if start and line[start - 1].isalnum():
            continue  # starts inside a word (``mac:`` is a word, not a pair)
        if start and line[start - 1] in ":-":
            before = _WORD.search(line[: start - 1][::-1])
            if line[start - 1] == separator and before and before.start() == 0:
                word = before.group(0)[::-1]
                if re.fullmatch(r"[0-9A-Fa-f]{2}", word):
                    continue  # one more pair before it
        if end < len(line) and line[end] in ":-":
            after = _WORD.match(line, end + 1)
            if line[end] == separator and after and re.fullmatch(r"[0-9A-Fa-f]{2}", after.group(0)):
                continue  # one more pair after it
        elif end < len(line) and line[end].isalnum():
            continue  # ends inside a word
        found.append(value)
    found += [match.group(0) for match in _DOTTED.finditer(line)]
    return found


def normal(written: str) -> str:
    """``written`` as six lower-case pairs joined by colons."""
    digits = re.sub(r"[^0-9a-f]", "", written.lower())
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


#: The documentation block (RFC 7042) and its locally administered twin.
FICTIONAL: Final = re.compile(r"0[02]:00:5e:00:53:[0-9a-f]{2}")
#: Addresses a protocol defines, used by the tests as what they are.
PROTOCOL: Final = {
    "00:00:00:00:00:00": "no address (a refused or empty MAC)",
    "ff:ff:ff:ff:ff:ff": "broadcast",
    "01:00:5e:00:00:01": "IPv4 multicast (all hosts)",
    "01:00:5e:00:00:fb": "IPv4 multicast (mDNS)",
    "33:33:00:00:00:01": "IPv6 multicast (all nodes)",
    "00:00:5e:00:00:00": "the IANA block's first address (a vendor-prefix search bound)",
    "00:00:5e:ff:ff:ff": "the IANA block's last address (a vendor-prefix search bound)",
}
#: Files whose MAC addresses are allowed as they are, and why.
ALLOWED_FILES: Final = {
    "worker/tests/test_devices_oui.py": (
        "the OUI lookup's test: IEEE registry excerpts (MA-L and a 36-bit MA-S block) and the "
        "unicast/multicast and universal/local bit cases need several distinct vendor prefixes"
    ),
    "tests/test_example_data.py": "this test",
}
#: Paths not read: the frozen contracts, lock files and binary assets.
SKIPPED: Final = re.compile(
    r"^design/|(^|/)(uv\.lock|package-lock\.json)$|\.(png|ico|pdf|xlsx|woff2?|ttf|gz|xz|zip|der|p12|pfx)$"
)


def _tracked() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [path for path in listing.split("\0") if path and not SKIPPED.search(path)]


def _offenders() -> list[str]:
    found = []
    for path in _tracked():
        if path in ALLOWED_FILES:
            continue
        file = ROOT / path
        if not file.is_file():  # a tracked link, or a file removed in the working tree
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for written in macs(line):
                value = normal(written)
                if not FICTIONAL.fullmatch(value) and value not in PROTOCOL:
                    found.append(f"{path}:{number}: {written}")
    return found


def test_example_macs_are_the_fictional_worlds() -> None:
    """Every MAC address in a tracked text file is ``00:00:5E:00:53:xx`` (or its locally
    administered twin ``02:…``), a protocol-defined one, or in an allowed file."""
    assert _offenders() == []


def test_the_pattern_finds_a_mac_and_skips_what_only_looks_like_one() -> None:
    """The pattern behind the check: one separator, no longer run around it."""
    line = (
        'mac="3C-52-82-00-00-05" srcmac=3c:52:82:00:00:05 subject device:a4:83:e7:00:00:44 '
        "duid 00:01:00:01:2c:00:00:00:00:00:5e:00:53:57 18:13:00-18:14:00 "
        "ip_conflict:172.20.42.57:00:00:5e:00:53:57,00:00:5e:00:53:9a:2026-10-09 "
        "mac:00:00:5e:00:53:* cisco f01f.af00.0001 version 1.2.3"
    )
    assert macs(line) == [
        "3C-52-82-00-00-05",
        "3c:52:82:00:00:05",
        "a4:83:e7:00:00:44",
        "00:00:5e:00:53:57",
        "00:00:5e:00:53:9a",
        "f01f.af00.0001",
    ]
    assert normal("F01F.AF00.0001") == normal("f0-1f-af-00-00-01") == "f0:1f:af:00:00:01"
    assert FICTIONAL.fullmatch("02:00:5e:00:53:46")
    assert not FICTIONAL.fullmatch("00:00:5e:00:54:01")
