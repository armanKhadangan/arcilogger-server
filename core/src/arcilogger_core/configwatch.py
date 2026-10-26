"""Live reload of ``arcilogger.toml`` without a restart (ADR-0014; contract 2.8.2).

A running service keeps the configuration it started with and asks a :class:`ConfigWatcher`
now and then whether the file changed. :meth:`ConfigWatcher.poll` stats the file at most every
``min_interval`` seconds; when its modification time or size changed it reads the bytes, hashes
them and, when the hash differs, validates them exactly as ``load_config`` does
(:func:`~arcilogger_core.config.parse_config`). Only the service's *hot* top-level keys are
taken into the running configuration; any error keeps the configuration in use. A valid file
whose *restart* keys differ (a hand edit of the collector's ``bind``, say: the browser never
changes them) is reported in ``restart_keys`` until the service restarts; the keys a service
does not read are neither taken nor reported.

=========  =========================================  ===========================================
service    hot (taken live)                           restart (read only at start-up)
=========  =========================================  ===========================================
collector  ``sources``                                ``collector``, ``data_dir``
ingest     ``sources``                                ``ingest``, ``database``, ``data_dir``,
                                                      ``secrets_dir``
worker     every other key                            ``database``, ``worker``, ``data_dir``,
                                                      ``secrets_dir``
API        every other key                            ``database``, ``api``, ``data_dir``,
                                                      ``secrets_dir``
=========  =========================================  ===========================================

Each service writes :meth:`ConfigWatch.status_block` as the ``config`` block of its status file
(``state\\<service>-status.json``): the SHA-256 of the bytes its configuration came from tells the
browser administration when a change reached it. :func:`config_status_of` reads the block back;
a status file without one (an older version) gives None, "unknown".
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from arcilogger_core.config import Config, parse_config
from arcilogger_core.timeutil import isoformat_site, utc_now

log = logging.getLogger("arcilogger.configwatch")


def hot_keys_except(*keys: str) -> frozenset[str]:
    """Every top-level key of :class:`Config` except ``keys``."""
    unknown = set(keys) - set(Config.model_fields)
    if unknown:
        raise ValueError(f"not configuration keys: {sorted(unknown)}")
    return frozenset(Config.model_fields) - set(keys)


COLLECTOR_HOT: Final = frozenset({"sources"})
COLLECTOR_RESTART: Final = frozenset({"collector", "data_dir"})
INGEST_HOT: Final = frozenset({"sources"})
INGEST_RESTART: Final = frozenset({"ingest", "database", "data_dir", "secrets_dir"})
WORKER_HOT: Final = hot_keys_except("database", "worker", "data_dir", "secrets_dir")
API_HOT: Final = hot_keys_except("database", "api", "data_dir", "secrets_dir")


@dataclass(frozen=True)
class ConfigWatch:
    """What :meth:`ConfigWatcher.poll` found."""

    #: The configuration to use now (the previous one after an error).
    config: Config
    #: The hot keys changed in this poll: ``config`` is a new object.
    changed: bool
    #: SHA-256 of the bytes ``config`` was loaded from (also after a change of non-hot keys
    #: only: the file is the one the service works from, apart from ``restart_keys``). None
    #: while unknown: the file changed or was unreadable since the service loaded it, and no
    #: valid version of it has been taken since.
    sha256: str | None
    #: When those bytes were loaded.
    loaded_at: datetime
    #: Why the file in place now is not in use (it is logged once per file hash), else None.
    error: str | None
    #: Restart keys of the service that differ from the running configuration: they take
    #: effect only after a restart.
    restart_keys: tuple[str, ...]

    def status_block(self) -> dict[str, Any]:
        """The ``config`` block of a service status file (contract 2.4.12)."""
        return {
            "sha256": self.sha256,
            "loaded_at": isoformat_site(self.loaded_at, "seconds"),
            "error": self.error,
            "restart_keys": list(self.restart_keys),
        }


class ConfigStatus(BaseModel):
    """The ``config`` block of a service status file, as the API reads it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    loaded_at: AwareDatetime
    error: str | None
    restart_keys: tuple[str, ...]


def config_status_of(status: Mapping[str, Any]) -> ConfigStatus | None:
    """The ``config`` block of a parsed status file; None when the file has none (a service of
    an older version) or an invalid one."""
    block = status.get("config")
    if not isinstance(block, Mapping):
        return None
    try:
        return ConfigStatus.model_validate(block)
    except ValidationError:
        return None


def _describe(exc: Exception) -> str:
    """One line about why a file is not usable: where and what for each validation error
    (pydantic's messages, not its echo of the whole input)."""
    if isinstance(exc, ValidationError):
        problems = [
            f"{'.'.join(str(part) for part in error['loc']) or '(file)'}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        ]
        return "; ".join(problems)
    return f"{type(exc).__name__}: {exc}".splitlines()[0]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ConfigWatcher:
    """Watches one configuration file for one service: ``hot`` are the keys it takes live,
    ``restart`` those it reads only at start-up (None: every key that is not hot)."""

    def __init__(
        self,
        path: Path,
        current: Config,
        *,
        hot: frozenset[str],
        min_interval: float,
        restart: frozenset[str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._path = path
        self._hot = hot
        self._restart = frozenset(Config.model_fields) - hot if restart is None else restart
        if (hot | self._restart) - set(Config.model_fields) or hot & self._restart:
            raise ValueError("hot and restart keys are distinct top-level configuration keys")
        self._min_interval = min_interval
        self._monotonic = monotonic
        self._now = now
        self._config = current
        self._loaded_at = now()
        self._error: str | None = None
        self._logged_error: str | None = None  # hash of the bad file last logged
        self._restart_keys: tuple[str, ...] = ()
        self._next_check = monotonic() + min_interval
        # The bytes in place now are taken as the ones ``current`` came from. When they say
        # something else (the file changed since it was loaded), the hash is unknown and the
        # first poll takes them.
        self._stat = self._stat_of()
        self._sha256 = self._origin(current)
        if self._sha256 is None:
            self._stat = None
            self._next_check = monotonic()

    def _origin(self, current: Config) -> str | None:
        """The hash of the file when it holds ``current``, else None."""
        try:
            data = self._path.read_bytes()
            return _sha256(data) if parse_config(data, self._path) == current else None
        except (OSError, ValueError):  # ValueError: TOMLDecodeError, ValidationError, UTF-8
            return None

    def _stat_of(self) -> tuple[int, int] | None:
        try:
            stat = self._path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _watch(self, *, changed: bool) -> ConfigWatch:
        return ConfigWatch(
            config=self._config,
            changed=changed,
            sha256=self._sha256,
            loaded_at=self._loaded_at,
            error=self._error,
            restart_keys=self._restart_keys,
        )

    def poll(self) -> ConfigWatch:
        """The configuration to use now; never raises."""
        try:
            return self._poll()
        except Exception as exc:  # a broken watch must never stop the service
            self._error = _describe(exc)
            log.exception("configuration watch failed: %s", self._path)
            return self._watch(changed=False)

    def _poll(self) -> ConfigWatch:
        if self._monotonic() < self._next_check:
            return self._watch(changed=False)
        self._next_check = self._monotonic() + self._min_interval
        stat = self._stat_of()
        if stat is not None and stat == self._stat:
            return self._watch(changed=False)
        try:
            data = self._path.read_bytes()
        except OSError as exc:  # removed or locked for a moment while an editor saves it
            self._error = _describe(exc)
            return self._watch(changed=False)
        self._stat = stat
        digest = _sha256(data)
        if digest == self._sha256:  # touched, not changed (or changed back)
            self._error = None
            return self._watch(changed=False)
        try:
            new = parse_config(data, self._path)
        except ValueError as exc:  # TOMLDecodeError, ValidationError, UnicodeDecodeError
            self._error = _describe(exc)
            if self._logged_error != digest:
                self._logged_error = digest
                log.error(
                    "configuration %s not loaded, the previous one stays in use: %s",
                    self._path,
                    self._error,
                )
            return self._watch(changed=False)
        update = {key: getattr(new, key) for key in self._hot}
        running = self._config
        changed = any(getattr(running, key) != value for key, value in update.items())
        if changed:
            self._config = running.model_copy(update=update)
        self._sha256 = digest
        self._loaded_at = self._now()
        self._error = None
        self._restart_keys = tuple(
            key
            for key in Config.model_fields
            if key in self._restart and getattr(new, key) != getattr(self._config, key)
        )
        if changed:
            log.info("configuration %s reloaded (%s)", self._path, digest[:12])
        if self._restart_keys:
            log.warning(
                "configuration %s: %s take effect only after a restart",
                self._path,
                ", ".join(self._restart_keys),
            )
        return self._watch(changed=changed)
