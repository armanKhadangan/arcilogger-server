"""Host names → registrable domains (Public Suffix List) and the background traffic rules.

**Registrable domain** (spec 10.2: ``www.youtube.com`` → ``youtube.com``): the Public Suffix
List decides where the public part ends (``bbc.co.uk``, ``gov.tr``). The list comes from the
``publicsuffixlist`` package (MPL-2.0), which bundles a copy in its wheel and reads it from the
package directory — ARCILOGGER never downloads it (the server has no general internet access).
A newer list can be placed on the server and named in ``usage.psl_file``; when that file is
missing or unreadable the bundled list is used and the rollup records why. By default only the
ICANN section counts (``cdn.cloudfront.net`` style private suffixes would split one service into
thousands of "sites"); ``usage.psl_private_suffixes`` switches the private section on.

**Background traffic** (spec 10.3, settings ``usage.background_*``): a session is background
when its host name matches a pattern of ``usage.background_domains`` (``fnmatch`` wildcards,
case-insensitive; ``*.example.com`` also matches ``example.com`` itself), when it goes to a port
of ``usage.background_ports`` (DNS, NTP, DNS over TLS), when its total traffic is below
``usage.background_session_bytes``, or — only when no host name is known for it — when its
FortiGate application category is in ``usage.background_appcats``. The last rule needs the host
name exception: on the real FortiGate capture of 2026-10-12 ordinary HTTPS browsing is logged
as ``SSL_TLSv1.3.PQC`` in the category ``Network.Service`` (ASSUMPTIONS U4).
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Final

from publicsuffixlist import PublicSuffixList

log = logging.getLogger("arcilogger.worker.usage")

#: Longest host name kept (DNS limit).
MAX_HOSTNAME: Final = 253


def normalize_hostname(value: str | None) -> str | None:
    """Lower case, without a trailing dot, brackets or ``:port``; ``None`` when empty."""
    if value is None:
        return None
    return _normalized(value)


@lru_cache(maxsize=65536)
def _normalized(value: str) -> str | None:
    host = value.strip().lower().rstrip(".")
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    elif host.count(":") == 1:
        name, _, port = host.partition(":")
        if port.isdigit():
            host = name
    if not host or len(host) > MAX_HOSTNAME or len(host.split()) != 1:
        return None
    return host


@lru_cache(maxsize=65536)
def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _package_version() -> str:
    try:
        return version("publicsuffixlist")
    except PackageNotFoundError:  # pragma: no cover - the package is a declared dependency
        return "unknown"


@dataclass
class DomainNamer:
    """Registrable domain of a host name, cached (one instance per rollup run)."""

    psl_file: str = ""
    private_suffixes: bool = False
    #: Where the list came from: ``bundled <version>`` or the file with its size and time.
    source: str = field(init=False, default="")
    #: Why ``psl_file`` was not used ("" when it was, or none was configured).
    warning: str = field(init=False, default="")
    _psl: Any = field(init=False, repr=False, default=None)
    _cache: dict[str, str] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        only_icann = not self.private_suffixes
        if self.psl_file:
            path = Path(self.psl_file)
            try:
                data = path.read_bytes()
                if b"===BEGIN ICANN DOMAINS===" not in data:
                    raise ValueError("not a public_suffix_list.dat (no ICANN section)")
                self._psl = PublicSuffixList(data.splitlines(), only_icann=only_icann)
                stat = path.stat()
                self.source = f"{path} ({stat.st_size} bytes, mtime {int(stat.st_mtime)})"
            except (OSError, ValueError, UnicodeError) as exc:
                self.warning = f"usage.psl_file not used: {type(exc).__name__}: {exc}"[:300]
                log.warning("%s", self.warning)
        if self._psl is None:
            self._psl = PublicSuffixList(only_icann=only_icann)
            self.source = f"bundled publicsuffixlist {_package_version()}"

    def registrable(self, host: str) -> str:
        """``www.youtube.com`` → ``youtube.com``; an address or a bare suffix stays as it is."""
        known = self._cache.get(host)
        if known is not None:
            return known
        if is_ip_literal(host) or "." not in host:
            found = host
        else:
            found = self._psl.privatesuffix(host) or host
        self._cache[host] = found
        return str(found)


@dataclass
class BackgroundRules:
    """The background patterns, application categories and ports of the settings."""

    patterns: tuple[str, ...]
    appcats: frozenset[str]
    ports: frozenset[int]
    _cache: dict[str, bool] = field(init=False, repr=False, default_factory=dict)

    @classmethod
    def create(
        cls, patterns: tuple[str, ...], appcats: tuple[str, ...], ports: tuple[int, ...]
    ) -> BackgroundRules:
        return cls(
            tuple(p.lower() for p in patterns),
            frozenset(a.casefold() for a in appcats),
            frozenset(ports),
        )

    def host(self, host: str) -> bool:
        """``host`` (normalised) matches a background pattern."""
        known = self._cache.get(host)
        if known is None:
            known = any(
                fnmatchcase(host, pattern) or (pattern.startswith("*.") and host == pattern[2:])
                for pattern in self.patterns
            )
            self._cache[host] = known
        return known

    def appcat(self, appcat: str | None) -> bool:
        return appcat is not None and appcat.casefold() in self.appcats

    def port(self, port: int | None) -> bool:
        return port is not None and port in self.ports
