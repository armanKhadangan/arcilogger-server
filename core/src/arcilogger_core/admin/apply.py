"""Applying a change to ``arcilogger.toml`` and ``secrets.json`` as one transaction
(ADR-0014; contract 2.7.1, 2.7.2).

:meth:`Applier.apply` runs steps 6-12 of contract 2.7.1 for a :class:`ChangePlan` whose new
bytes were built and validated already (steps 1-5, the agent):

6. the write-ahead record ``admin\\state\\txn.json`` (``prepared``), fsync;
7. the current pair saved as the next version (``admin\\versions\\<n>\\``);
8. the new files written as ``.<name>.<id>.tmp`` next to them (same volume), fsync;
9. roots and CA files installed under new names (``.tmp``, then rename);
10. ``txn.json`` stage ``written``, then ``secrets.json`` replaced, then ``arcilogger.toml``
    (:func:`~arcilogger_core.admin.winapi.replace_file`; a sharing violation is retried for up to
    10 seconds). ``arcilogger.toml`` is the commit point;
11. verified: both hashes as planned, ``load_config`` succeeds, every changed secret decrypts;
12. ``txn.json`` removed (the caller writes the journal line first: :meth:`Applier.commit`).

A failure in steps 8-11 restores both files from the version of step 7 (replaced again),
removes the files step 9 installed and verifies the old pair: ``failed`` with
``rolled_back``; when that restore fails too: ``needs_attention`` (``rollback_failed``) and the
version folder for ``versions restore``. The services never see a partial file: every replace is
atomic and the watchers keep their last valid configuration. After a successful apply the agent
checks that it can still open both files for writing (``acl_drift``).

:meth:`Applier.recover` runs at start-up before any new request: stage ``prepared`` — the
temporary files are discarded (``interrupted``); stage ``written`` — the file hashes decide:
the new configuration finishes steps 11-12, the old one is kept (and ``secrets.json`` restored
if it was already replaced), anything else needs attention.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import JsonValue

from arcilogger_core.admin import winapi
from arcilogger_core.admin.contract import VersionEntry
from arcilogger_core.admin.rules import Install
from arcilogger_core.admin.store import (
    Digests,
    Installation,
    TxnFile,
    VersionStore,
    read_bytes_or_none,
    read_txn,
    remove_txn,
    write_txn,
)
from arcilogger_core.config import parse_config
from arcilogger_core.fsutil import fsync_dir
from arcilogger_core.secretstore import SecretStore, SecretStoreError

log = logging.getLogger("arcilogger.admin.apply")

#: How long a replace that another process blocks is retried (contract 2.7.1 step 10).
REPLACE_RETRY_SECONDS: Final = 10.0
#: Points :class:`Applier` calls its ``checkpoint`` at (failure injection in tests).
CHECKPOINTS: Final = (
    "versions",
    "write_temp",
    "install_files",
    "replace_secrets",
    "replace_config",
    "verify",
    "restore",
)

Outcome = Literal["applied", "failed", "needs_attention"]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ApplyError(RuntimeError):
    """A step of the transaction failed (``code``: ``write_failed`` or ``verify_failed``)."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class ChangePlan:
    """What one apply writes. ``new_secrets`` None: ``secrets.json`` is not rewritten."""

    #: The transaction's id (the request's, or a new one for a console restore).
    id: str
    request: str | None
    actor: str
    paths: tuple[str, ...]
    old_toml: bytes = field(repr=False)
    old_secrets: bytes | None = field(repr=False)
    new_toml: bytes = field(repr=False)
    new_secrets: bytes | None = field(default=None, repr=False)
    installs: tuple[Install, ...] = ()
    #: The secrets the change sets (each must decrypt afterwards).
    secret_names: tuple[str, ...] = ()
    #: The journal line to write when it completes (kept in ``txn.json`` for a recovery).
    journal: dict[str, JsonValue] | None = None

    @property
    def digests(self) -> tuple[Digests, Digests]:
        old = Digests(
            toml_sha256=sha256(self.old_toml),
            secrets_sha256=sha256(self.old_secrets) if self.old_secrets is not None else None,
        )
        new = Digests(
            toml_sha256=sha256(self.new_toml),
            secrets_sha256=(
                sha256(self.new_secrets) if self.new_secrets is not None else old.secrets_sha256
            ),
        )
        return old, new


@dataclass(frozen=True)
class ApplyOutcome:
    state: Outcome
    #: ``write_failed``, ``verify_failed``, ``rollback_failed``, ``acl_drift``, ``interrupted``.
    code: str | None
    version: VersionEntry | None
    rolled_back: bool
    from_sha256: str
    to_sha256: str
    #: Why, for the log (never a value of the files).
    reason: str | None = None
    #: The transaction record of a recovered apply (its journal line).
    txn: TxnFile | None = None
    #: Recovery only: the apply completed after a crash (the journal line says ``recovered``).
    recovered: bool = False


class Applier:
    """The transaction on the files of one installation."""

    def __init__(
        self,
        installation: Installation,
        *,
        now: Callable[[], datetime],
        replace: Callable[[Path, Path], None] = winapi.replace_file,
        writable: Callable[[Path], bool] = winapi.can_write,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        self.installation = installation
        self.layout = installation.admin
        self.versions = VersionStore(self.layout)
        self._now = now
        self._replace_file = replace
        self._writable = writable
        self._sleep = sleep
        self._monotonic = monotonic
        self._checkpoint = checkpoint or (lambda step: None)

    # -- file steps --------------------------------------------------------------------------------

    def _write_new(self, path: Path, data: bytes) -> None:
        with path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

    def _replace(self, source: Path, target: Path) -> None:
        """Replace ``target`` by ``source``, retrying while another process holds ``target``."""
        deadline = self._monotonic() + REPLACE_RETRY_SECONDS
        wait = 0.05
        while True:
            try:
                self._replace_file(source, target)
            except winapi.SharingViolationError:
                if self._monotonic() >= deadline:
                    raise
                self._sleep(wait)
                wait = min(wait * 2, 1.0)
            else:
                fsync_dir(target.parent)
                return

    def _put(self, target: Path, data: bytes | None, txn_id: str) -> None:
        """``target`` holds ``data`` afterwards (None: it does not exist)."""
        if data is None:
            target.unlink(missing_ok=True)
            return
        temp = self.installation.temp_file(target, txn_id)
        self._write_new(temp, data)
        try:
            self._replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    def _install(self, installs: Sequence[Install], txn_id: str) -> list[str]:
        created: list[str] = []
        for item in installs:
            if item.target.exists():
                if item.target.read_bytes() == item.data:
                    continue
                raise ApplyError("write_failed", f"{item.target.name} exists with other content")
            item.target.parent.mkdir(parents=True, exist_ok=True)
            temp = item.target.with_name(f".{item.target.name}.{txn_id}.tmp")
            self._write_new(temp, item.data)
            temp.replace(item.target)
            fsync_dir(item.target.parent)
            created.append(str(item.target))
        return created

    def _discard_temps(self, txn_id: str) -> None:
        for target in (self.installation.toml, self.installation.secrets):
            self.installation.temp_file(target, txn_id).unlink(missing_ok=True)

    @staticmethod
    def _remove_files(paths: Sequence[str]) -> None:
        for path in paths:
            Path(path).unlink(missing_ok=True)

    def _digest_on_disk(self, path: Path) -> str | None:
        data = read_bytes_or_none(path)
        return sha256(data) if data is not None else None

    def verify(self, new: Digests, secret_names: Sequence[str]) -> None:
        """Step 11: the files are the planned ones, load and decrypt (:class:`ApplyError`)."""
        toml = self.installation.toml.read_bytes()
        if sha256(toml) != new.toml_sha256:
            raise ApplyError("verify_failed", "arcilogger.toml is not the planned file")
        if self._digest_on_disk(self.installation.secrets) != new.secrets_sha256:
            raise ApplyError("verify_failed", "secrets.json is not the planned file")
        try:
            parse_config(toml, self.installation.toml)
        except ValueError as exc:
            raise ApplyError("verify_failed", "arcilogger.toml does not load") from exc
        store = SecretStore(self.installation.secret_key, self.installation.secrets)
        for name in secret_names:
            try:
                if store.get(name) is None:
                    raise ApplyError("verify_failed", f"{name} is not stored")
            except (SecretStoreError, ValueError, OSError) as exc:
                raise ApplyError("verify_failed", f"{name} does not decrypt") from exc

    def writable(self) -> bool:
        return all(
            self._writable(path)
            for path in (self.installation.toml, self.installation.secrets)
            if path.exists()
        )

    # -- the transaction ---------------------------------------------------------------------------

    def apply(self, plan: ChangePlan) -> ApplyOutcome:
        """Steps 6-11; the caller journals the outcome and then calls :meth:`commit`."""
        old, new = plan.digests
        number = max((v.n for v in self.versions.index().versions), default=0) + 1
        txn = TxnFile(
            id=plan.id,
            request=plan.request,
            version=number,
            old=old,
            new=new,
            stage="prepared",
            secret_names=plan.secret_names,
            journal=plan.journal,
            started_at=self._now(),
        )
        write_txn(self.layout, txn)
        entry: VersionEntry | None = None
        replaced = False
        try:
            self._checkpoint("versions")
            entry = self.versions.save(
                toml=plan.old_toml,
                secrets=plan.old_secrets,
                request=plan.request,
                actor=plan.actor,
                paths=plan.paths,
                at=txn.started_at,
                toml_sha256=old.toml_sha256,
                secrets_sha256=old.secrets_sha256,
            )
            if entry.n != number:  # another writer of the version store: nothing is replaced
                raise ApplyError("write_failed", f"version {number} became {entry.n}")
            self._checkpoint("write_temp")
            temp_toml = self.installation.temp_file(self.installation.toml, plan.id)
            self._write_new(temp_toml, plan.new_toml)
            temp_secrets = self.installation.temp_file(self.installation.secrets, plan.id)
            if plan.new_secrets is not None:
                self._write_new(temp_secrets, plan.new_secrets)
            self._checkpoint("install_files")
            txn = txn.model_copy(update={"files": tuple(self._install(plan.installs, plan.id))})
            if txn.files:
                write_txn(self.layout, txn)  # a crash from here removes them again
            txn = txn.model_copy(update={"stage": "written"})
            write_txn(self.layout, txn)
            replaced = True
            if plan.new_secrets is not None:
                self._checkpoint("replace_secrets")
                self._replace(temp_secrets, self.installation.secrets)
            self._checkpoint("replace_config")
            self._replace(temp_toml, self.installation.toml)
            self._checkpoint("verify")
            self.verify(new, plan.secret_names)
        except (OSError, ApplyError, ValueError) as exc:
            code = exc.code if isinstance(exc, ApplyError) else "write_failed"
            log.error("apply %s failed (%s): %s", plan.id, code, exc)
            return self._rollback(txn, entry, code, str(exc), replaced=replaced)
        finally:
            self._discard_temps(plan.id)
        if not self.writable():
            return ApplyOutcome(
                "needs_attention",
                "acl_drift",
                entry,
                False,
                old.toml_sha256,
                new.toml_sha256,
                reason="a configuration file is no longer writable",
                txn=txn,
            )
        return ApplyOutcome(
            "applied", None, entry, False, old.toml_sha256, new.toml_sha256, txn=txn
        )

    def commit(self) -> None:
        """Step 12: the apply is complete (after its journal line was written)."""
        remove_txn(self.layout)

    def _rollback(
        self,
        txn: TxnFile,
        entry: VersionEntry | None,
        code: str,
        reason: str,
        *,
        replaced: bool,
    ) -> ApplyOutcome:
        try:
            if replaced:
                self._restore(txn)
            self._remove_files(txn.files)
        except (OSError, ApplyError, ValueError) as exc:
            log.critical("restoring the previous configuration (%s) failed: %s", txn.id, exc)
            remove_txn(self.layout)
            return ApplyOutcome(
                "needs_attention",
                "rollback_failed",
                entry,
                False,
                txn.old.toml_sha256,
                txn.new.toml_sha256,
                reason=f"{reason}; restore: {exc}",
                txn=txn,
            )
        remove_txn(self.layout)
        return ApplyOutcome(
            "failed",
            code,
            entry,
            replaced,
            txn.old.toml_sha256,
            txn.new.toml_sha256,
            reason=reason,
            txn=txn,
        )

    def _restore(self, txn: TxnFile) -> None:
        """Put the pair of version ``txn.version`` back where it differs, and check it."""
        self._checkpoint("restore")
        toml, secrets = self.versions.read(txn.version)
        if sha256(toml) != txn.old.toml_sha256:
            raise ApplyError("rollback_failed", f"version {txn.version} is not the saved file")
        if self._digest_on_disk(self.installation.secrets) != txn.old.secrets_sha256:
            if secrets is None and txn.old.secrets_sha256 is not None:
                raise ApplyError("rollback_failed", f"version {txn.version} has no secrets.json")
            self._put(self.installation.secrets, secrets, txn.id)
        if self._digest_on_disk(self.installation.toml) != txn.old.toml_sha256:
            self._put(self.installation.toml, toml, txn.id)
        if (
            self._digest_on_disk(self.installation.toml) != txn.old.toml_sha256
            or self._digest_on_disk(self.installation.secrets) != txn.old.secrets_sha256
        ):
            raise ApplyError("rollback_failed", "the restored files are not the saved ones")

    # -- recovery ----------------------------------------------------------------------------------

    def recover(self) -> ApplyOutcome | None:
        """Finish or undo an apply a crash interrupted (None: there was none)."""
        txn = read_txn(self.layout)
        if txn is None:
            return None
        self._discard_temps(txn.id)
        entry = self.versions.entry(txn.version)
        if entry is not None and entry.request != txn.request:
            entry = None  # step 7 had not run: the number belongs to nothing yet
        old, new = txn.old.toml_sha256, txn.new.toml_sha256
        if txn.stage == "prepared":
            self._remove_files(txn.files)
            remove_txn(self.layout)
            return ApplyOutcome("failed", "interrupted", entry, False, old, new, txn=txn)
        on_disk = self._digest_on_disk(self.installation.toml)
        if on_disk == new:
            try:
                self.verify(txn.new, txn.secret_names)
            except (ApplyError, OSError) as exc:
                return self._rollback(txn, entry, "verify_failed", str(exc), replaced=True)
            return ApplyOutcome("applied", None, entry, False, old, new, txn=txn, recovered=True)
        if on_disk == old:
            try:
                self._restore(txn)
                self._remove_files(txn.files)
            except (OSError, ApplyError, ValueError) as exc:
                remove_txn(self.layout)
                return ApplyOutcome(
                    "needs_attention",
                    "rollback_failed",
                    entry,
                    False,
                    old,
                    new,
                    reason=str(exc),
                    txn=txn,
                )
            remove_txn(self.layout)
            return ApplyOutcome("failed", "interrupted", entry, True, old, new, txn=txn)
        remove_txn(self.layout)
        return ApplyOutcome(
            "needs_attention",
            "rollback_failed",
            entry,
            False,
            old,
            new,
            reason="arcilogger.toml is neither the old nor the new file",
            txn=txn,
        )
