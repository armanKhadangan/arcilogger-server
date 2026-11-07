"""The second copy of the database backups on the replica's share (Wave 5 addendum C, C6;
``arcilogger_core.backupmirror``)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arcilogger_core import backupmirror
from arcilogger_core.backupmirror import (
    MirrorOutcome,
    backup_folder,
    mirror_backups,
    mirror_state_path,
    summary,
    write_state,
)
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout


def _dump(folder: Path, name: str, content: bytes, *, recorded: bytes | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(content)
    digest = hashlib.sha256(content if recorded is None else recorded).hexdigest()
    (folder / (name + ".json")).write_text(json.dumps({"sha256": digest}), encoding="utf-8")
    return path


def test_backup_folder_is_below_the_data_folder_unless_absolute(tmp_path: Path) -> None:
    config = Config.model_validate(
        {"data_dir": str(tmp_path / "data"), "secrets_dir": str(tmp_path)}
    )
    assert backup_folder(config) == tmp_path / "data" / "backups" / "scheduled"
    other = tmp_path / "elsewhere"
    config = Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "backup": {"dir": str(other), "mirror_target": "//NAS-01/arcilogger-yedek/"},
        }
    )
    assert backup_folder(config) == other
    assert config.backup.mirror_text == "\\\\NAS-01\\arcilogger-yedek"


def test_dumps_and_sidecars_are_copied_once(tmp_path: Path) -> None:
    source, share = tmp_path / "scheduled", tmp_path / "share"
    share.mkdir()
    _dump(source, "arcilogger-20261106-014000.dump", b"one")
    _dump(source, "arcilogger-20261107-014000.dump", b"two")
    (source / "notes.txt").write_text("not a dump", encoding="utf-8")
    (source / ".arcilogger-20261107-014000.dump.partial").write_bytes(b"half")
    first = mirror_backups(source, share)
    assert first.ok
    assert first.copied == ["arcilogger-20261106-014000.dump", "arcilogger-20261107-014000.dump"]
    target = share
    assert sorted(p.name for p in target.iterdir()) == [
        ".arcilogger-staging",
        "arcilogger-20261106-014000.dump",
        "arcilogger-20261106-014000.dump.json",
        "arcilogger-20261107-014000.dump",
        "arcilogger-20261107-014000.dump.json",
    ]
    assert list((target / ".arcilogger-staging").iterdir()) == []
    assert (target / "arcilogger-20261107-014000.dump").read_bytes() == b"two"
    # The next hour: nothing to copy, nothing overwritten.
    second = mirror_backups(source, share)
    assert second.ok
    assert second.copied == []
    assert len(second.present) == 2


def test_the_copy_keeps_what_the_folder_keeps(tmp_path: Path) -> None:
    """Once every dump is copied, the copies the retention pruned at the source go too; other
    files and other names on the share are never touched."""
    source, share = tmp_path / "scheduled", tmp_path / "share"
    share.mkdir()
    (share / "arcilogger-20261101-014000.dump").write_bytes(b"pruned at the source")
    (share / "arcilogger-20261101-014000.dump.json").write_text("{}", encoding="utf-8")
    (share / "notes.txt").write_text("the NAS administrator's", encoding="utf-8")
    (share / "arcilogger-before-0.3.0.dump").write_bytes(b"not a scheduled name")
    _dump(source, "arcilogger-20261107-014000.dump", b"kept")
    outcome = mirror_backups(source, share)
    assert outcome.ok
    assert outcome.pruned == ["arcilogger-20261101-014000.dump"]
    assert sorted(p.name for p in share.iterdir() if p.is_file()) == [
        "arcilogger-20261107-014000.dump",
        "arcilogger-20261107-014000.dump.json",
        "arcilogger-before-0.3.0.dump",
        "notes.txt",
    ]
    # An empty backup folder (a new disk, a failed listing) deletes nothing on the share.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert mirror_backups(empty, share).pruned == []
    assert (share / "arcilogger-20261107-014000.dump").exists()


def test_the_mirror_never_overwrites_nor_deletes(tmp_path: Path) -> None:
    source, share = tmp_path / "scheduled", tmp_path / "share"
    (share).mkdir(parents=True)
    (share / "arcilogger-20261101-014000.dump").write_bytes(b"older copy")
    (share / "arcilogger-20261106-014000.dump").write_bytes(b"different!")
    _dump(source, "arcilogger-20261106-014000.dump", b"one")
    outcome = mirror_backups(source, share)
    assert not outcome.ok
    assert outcome.failed == {
        "arcilogger-20261106-014000.dump": "a different file of this name is on the share"
    }
    # The dump pruned at the source stays on the share; the conflicting one is untouched.
    assert (share / "arcilogger-20261101-014000.dump").read_bytes() == b"older copy"
    assert (share / "arcilogger-20261106-014000.dump").read_bytes() == b"different!"


def test_a_damaged_dump_or_one_without_its_record_is_not_copied(tmp_path: Path) -> None:
    source, share = tmp_path / "scheduled", tmp_path / "share"
    share.mkdir()
    _dump(source, "arcilogger-20261106-014000.dump", b"changed", recorded=b"original")
    (source / "arcilogger-20261107-014000.dump").write_bytes(b"no sidecar")
    outcome = mirror_backups(source, share)
    assert outcome.copied == []
    assert outcome.failed == {
        "arcilogger-20261106-014000.dump": (
            "the dump does not match its recorded SHA-256 (it may be damaged)"
        ),
        "arcilogger-20261107-014000.dump": "no recorded SHA-256 (sidecar missing or unreadable)",
    }
    assert not (share / "arcilogger-20261106-014000.dump").exists()
    assert list((share / ".arcilogger-staging").iterdir()) == []


def test_a_copy_that_reads_back_differently_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, share = tmp_path / "scheduled", tmp_path / "share"
    share.mkdir()
    _dump(source, "arcilogger-20261106-014000.dump", b"one")
    monkeypatch.setattr(backupmirror, "_hash", lambda path: "0" * 64)
    outcome = mirror_backups(source, share)
    assert outcome.failed == {
        "arcilogger-20261106-014000.dump": "the copy read back from the share differs"
    }
    assert not (share / "arcilogger-20261106-014000.dump").exists()


def test_an_unreadable_backup_folder_is_an_error(tmp_path: Path) -> None:
    outcome = mirror_backups(tmp_path / "missing", tmp_path / "share")
    assert outcome.error is not None
    assert outcome.error.startswith("the backup folder cannot be read")
    assert str(tmp_path) not in outcome.error  # no path in the state file
    assert not outcome.ok


def test_state_file_and_summary(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    outcome = MirrorOutcome(
        copied=["arcilogger-20261107-014000.dump"],
        present=["arcilogger-20261106-014000.dump"],
        failed={"arcilogger-20261105-014000.dump": "Access is denied"},
    )
    write_state(layout, outcome, finished=datetime(2026, 11, 7, 0, 40, tzinfo=UTC))
    state = json.loads(mirror_state_path(layout).read_text(encoding="utf-8"))
    assert state == {
        "schema": 1,
        "finished": "2026-11-07T03:40:00+03:00",
        "ok": False,
        "copied": 1,
        "present": 1,
        "pruned": 0,
        "prune_refused": 0,
        "failed": {"arcilogger-20261105-014000.dump": "Access is denied"},
        "error": None,
    }
    assert summary(outcome) == (
        "Yedek kopyası: 1 yeni, 1 zaten var, 1 kopyalanamadı (arcilogger-20261105-014000.dump)"
    )
    assert summary(MirrorOutcome(error="x")) == "Yedek kopyası yapılamadı: x"
