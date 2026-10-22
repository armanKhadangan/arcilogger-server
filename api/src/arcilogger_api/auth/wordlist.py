"""The bundled list of common and breached passwords (spec 13: "breached-password check against a
bundled list"); no network access is ever needed to check a password.

**File** ``data/common-passwords.txt.gz`` (gzip, UTF-8): a header of ``# `` lines naming the
sources, their SHA-256 checksums and licence, ended by an empty line; then one password per line,
case-folded (``str.casefold``), unique and sorted. The gzip header carries no time stamp, so the
same sources always give the same bytes.

**Shipped content** (built 2026-10-12) from SecLists, https://github.com/danielmiessler/SecLists,
**MIT licence**, © 2018 Daniel Miessler — the licence text is repeated in the file header:

* ``Passwords/Common-Credentials/10k-most-common.txt``: all entries (also used for the "common
  word plus digits" rule of :mod:`.passwords`);
* ``Passwords/Common-Credentials/xato-net-10-million-passwords-1000000.txt``: the entries with at
  least 12 characters (shorter ones can never pass the minimum length).

**Replacing the list** (e.g. with a larger or company-specific one; *verify* that its licence
permits redistribution): put the sources in a directory of their own and run ::

    python -m arcilogger_api.auth.wordlist build --out common-passwords.txt.gz \\
        --notice NOTICE.txt --source 10k-most-common.txt:0 --source other-list.txt:12

``--source PATH:MIN`` takes the entries of ``PATH`` with at least ``MIN`` characters; the notice
file (source URL, licence text) becomes the header. Copy the result over
``api/src/arcilogger_api/auth/data/common-passwords.txt.gz`` and run the tests.

**Turkish base words** ``data/common-passwords-tr.txt``: a short, hand-written list (part of
ARCILOGGER, no third-party source) of words a Turkish-speaking user is likely to build a password
from — *şifre*, *yönetici*, club, city and given names. The SecLists list hardly contains them.
It is plain UTF-8 text, edited directly (one word per line, ``#`` comments); how the words are
matched is described in its header and in :mod:`.passwords`. Its content is a judgement:
*verify on site* and extend it.
"""

from __future__ import annotations

import argparse
import functools
import gzip
import hashlib
import io
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Final

RESOURCE: Final = "common-passwords.txt.gz"
TURKISH_RESOURCE: Final = "common-passwords-tr.txt"


@dataclass(frozen=True)
class Source:
    """One input list: entries shorter than ``min_length`` characters are skipped."""

    path: Path
    min_length: int = 0


def _entries(data: bytes, min_length: int) -> Iterable[str]:
    for raw in data.split(b"\n"):
        text = raw.decode("utf-8", errors="replace").strip("\r")
        if text.strip() and len(text) >= min_length:
            yield text.casefold()


def build_wordlist(sources: Sequence[Source], notice: str) -> bytes:
    """The gzip file for ``sources`` (deterministic: same inputs, same bytes)."""
    words: set[str] = set()
    header = [line.rstrip() for line in notice.strip().splitlines()]
    for source in sources:
        data = source.path.read_bytes()
        before = len(words)
        words.update(_entries(data, source.min_length))
        header.append(
            f"source: {source.path.name} min_length={source.min_length} "
            f"sha256={hashlib.sha256(data).hexdigest()} new_entries={len(words) - before}"
        )
    header.append(f"entries: {len(words)}")
    body = "".join(f"# {line}".rstrip() + "\n" for line in header) + "\n"
    body += "".join(f"{word}\n" for word in sorted(words))
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=9, mtime=0) as handle:
        handle.write(body.encode("utf-8"))
    return buffer.getvalue()


def parse_wordlist(data: bytes) -> tuple[list[str], frozenset[str]]:
    """``(header lines, entries)`` of a gzip word list."""
    text = gzip.decompress(data).decode("utf-8")
    head, separator, body = text.partition("\n\n")
    if not separator:
        raise ValueError("word list without header separator")
    header = [line.removeprefix("#").removeprefix(" ").rstrip() for line in head.splitlines()]
    entries = frozenset(line for line in body.split("\n") if line)
    return header, entries


@functools.cache
def bundled() -> tuple[tuple[str, ...], frozenset[str]]:
    """Header and entries of the shipped list (read once per process)."""
    data = resources.files("arcilogger_api.auth").joinpath("data", RESOURCE).read_bytes()
    header, entries = parse_wordlist(data)
    return tuple(header), entries


def common_passwords() -> frozenset[str]:
    """The case-folded entries of the shipped list."""
    return bundled()[1]


def parse_base_words(text: str) -> tuple[str, ...]:
    """Entries of a hand-written word file: one per line, ``#`` comment lines, blanks ignored."""
    words = (line.strip() for line in text.splitlines())
    return tuple(word for word in words if word and not word.startswith("#"))


@functools.cache
def turkish_base_words() -> tuple[str, ...]:
    """The words of ``data/common-passwords-tr.txt`` as written (the caller folds them)."""
    path = resources.files("arcilogger_api.auth").joinpath("data", TURKISH_RESOURCE)
    return parse_base_words(path.read_text(encoding="utf-8"))


def _source(text: str) -> Source:
    """``PATH`` or ``PATH:MIN`` (a Windows drive letter such as ``C:\\`` is part of the path)."""
    path, separator, minimum = text.rpartition(":")
    if separator and path and minimum.isdigit():
        return Source(Path(path), int(minimum))
    return Source(Path(text))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m arcilogger_api.auth.wordlist")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build the gzip word list")
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--notice", type=Path, required=True, help="header text (source, licence)")
    build.add_argument("--source", type=_source, action="append", required=True)
    show = commands.add_parser("info", help="print the header of the shipped list")
    show.set_defaults(command="info")
    args = parser.parse_args(argv)
    if args.command == "info":
        header, entries = bundled()
        print("\n".join(header))
        print(f"loaded entries: {len(entries)}")
        print(f"Turkish base words ({TURKISH_RESOURCE}): {len(turkish_base_words())}")
        return 0
    data = build_wordlist(args.source, args.notice.read_text(encoding="utf-8"))
    args.out.write_bytes(data)
    print(f"{args.out}: {len(parse_wordlist(data)[1])} entries, {len(data)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
