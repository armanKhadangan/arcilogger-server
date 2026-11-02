"""Known releases of the Kamu SM Zamane console client (``tss-client-console-<version>.jar``).

A client whose SHA-256 is listed here is trusted on first sight: the browser administration
promotes an uploaded copy at once and the tasks run it (ADR-0014; ``arcilogger_core.execpolicy``).
Any other client waits for the hold of ``policy.json`` (``jar_hold_hours``) or a console approval.

Rules:

* Only releases the owner checked: downloaded from Kamu SM and compared with the SHA-256 Kamu SM
  publishes. Empty until the owner does so; nothing at run time adds an entry (no environment
  variable, configuration file or API). Readers look the attribute up at call time
  (:func:`find_release`), so tests swap it.
* The key is the SHA-256 of the jar itself (the file that runs); ``archive_sha256`` is the
  SHA-256 of the ``.zip`` Kamu SM distributes it in, when it does.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True)
class ZamaneRelease:
    #: The client's version, e.g. ``"3.1.33"`` (from the file name Kamu SM gives it).
    version: str
    #: SHA-256 of the distributed ``.zip`` holding the jar, or None for a bare jar.
    archive_sha256: str | None = None


#: SHA-256 of the jar (64 lower-case hex digits) -> release. Empty until the owner fills it.
KNOWN_RELEASES: Final[Mapping[str, ZamaneRelease]] = MappingProxyType({})


def find_release(sha256: str) -> ZamaneRelease | None:
    """The known release whose jar or distributed archive has this SHA-256."""
    digest = sha256.lower()
    found = KNOWN_RELEASES.get(digest)
    if found is not None:
        return found
    return next((r for r in KNOWN_RELEASES.values() if r.archive_sha256 == digest), None)
