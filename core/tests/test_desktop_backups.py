"""The status window's Backups list and its verified copy (``arcilogger_core.desktop.backups``)
and the texts of the notification-area icon (``arcilogger_core.desktop.tray``)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from arcilogger_core.desktop import backups, tray
from arcilogger_core.desktop.status import LEVEL_TEXTS


def _dump(folder: Path, name: str, content: bytes, *, sidecar: str | None = "match") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(content)
    if sidecar is not None:
        digest = hashlib.sha256(content if sidecar == "match" else b"other").hexdigest()
        (folder / (name + ".json")).write_text(json.dumps({"sha256": digest}), encoding="utf-8")
    return path


def _folders(tmp_path: Path) -> list[backups.Folder]:
    return backups.folders(tmp_path / "data", tmp_path / "scheduled")


def test_folders_are_the_scheduled_and_the_installer_dumps(tmp_path: Path) -> None:
    places = _folders(tmp_path)
    assert [folder.path for folder in places] == [
        tmp_path / "scheduled",
        tmp_path / "data" / "backups" / "db",
    ]
    assert backups.folders(None, None) == []


def test_list_dumps_newest_first_with_recorded_hashes(tmp_path: Path) -> None:
    old = _dump(tmp_path / "scheduled", "arcilogger-20261101-014000.dump", b"one")
    new = _dump(
        tmp_path / "data" / "backups" / "db",
        "arci-20261102T0900Z-before-0.3.0.dump",
        b"2",
        sidecar=None,
    )
    (tmp_path / "scheduled" / "notes.txt").write_text("not a dump", encoding="utf-8")
    (tmp_path / "scheduled" / "sub.dump").mkdir()
    os.utime(old, (1_700_000_000, 1_700_000_000))
    os.utime(new, (1_800_000_000, 1_800_000_000))
    found = backups.list_dumps(_folders(tmp_path))
    assert [dump.path for dump in found] == [new, old]
    assert found[1].sha256 == hashlib.sha256(b"one").hexdigest()
    assert found[0].sha256 is None
    assert found[0].folder.title_en == "Before an upgrade"
    assert backups.list_dumps([backups.Folder(tmp_path / "missing", "a", "b")]) == []


def test_save_copy_is_verified_and_never_overwrites(tmp_path: Path) -> None:
    _dump(tmp_path / "scheduled", "arcilogger-20261101-014000.dump", b"PGDMP data")
    (dump,) = backups.list_dumps(_folders(tmp_path))
    target = tmp_path / "usb"
    target.mkdir()
    seen: list[int] = []
    copied = backups.save_copy(dump, target, progress=seen.append)
    assert copied.path.read_bytes() == b"PGDMP data"
    assert copied.recorded is True
    assert copied.sha256 == hashlib.sha256(b"PGDMP data").hexdigest()
    assert (target / (dump.path.name + ".json")).is_file()
    assert seen[-1] == len(b"PGDMP data")
    assert not list(target.glob("*.partial"))
    with pytest.raises(backups.CopyError, match="zaten var"):
        backups.save_copy(dump, target)
    with pytest.raises(backups.CopyError, match="Klasör yok"):
        backups.save_copy(dump, tmp_path / "nowhere")


def test_a_damaged_dump_is_not_copied(tmp_path: Path) -> None:
    _dump(tmp_path / "scheduled", "arcilogger-20261101-014000.dump", b"bad", sidecar="other")
    (dump,) = backups.list_dumps(_folders(tmp_path))
    target = tmp_path / "usb"
    target.mkdir()
    with pytest.raises(backups.CopyError, match="bozulmuş"):
        backups.save_copy(dump, target)
    assert list(target.iterdir()) == []


def test_without_a_sidecar_the_copy_is_compared_with_the_source(tmp_path: Path) -> None:
    _dump(tmp_path / "data" / "backups" / "db", "arci-before-0.3.0.dump", b"x" * 5, sidecar=None)
    (dump,) = backups.list_dumps(_folders(tmp_path))
    target = tmp_path / "usb"
    target.mkdir()
    copied = backups.save_copy(dump, target)
    assert copied.recorded is None
    assert copied.sha256 == hashlib.sha256(b"x" * 5).hexdigest()


def test_a_left_partial_copy_is_named(tmp_path: Path) -> None:
    _dump(tmp_path / "scheduled", "arcilogger-20261101-014000.dump", b"data")
    (dump,) = backups.list_dumps(_folders(tmp_path))
    target = tmp_path / "usb"
    target.mkdir()
    (target / (dump.path.name + ".partial")).write_bytes(b"half")
    with pytest.raises(backups.CopyError, match="Yarım kalmış"):
        backups.save_copy(dump, target)
    assert (target / (dump.path.name + ".partial")).read_bytes() == b"half"


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (900, "900 B"),
        (2048, "2,0 KB"),
        (5 * 1024**2, "5,0 MB"),
        (3 * 1024**4, "3072,0 GB"),
    ],
)
def test_size_text(size: int, text: str) -> None:
    assert backups.size_text(size) == text


def test_tray_texts_fit_what_windows_shows() -> None:
    for level, (tr, en) in LEVEL_TEXTS.items():
        tip = tray.tooltip(level)
        assert tr in tip
        assert en in tip
        assert len(tip) <= tray.TIP_LIMIT
    title, text = tray.critical_notice("x" * 300, "y" * 300)
    assert len(title) <= tray.TITLE_LIMIT
    assert len(text) <= tray.TEXT_LIMIT
    assert text.endswith("…")


def test_tray_menu_offers_only_harmless_commands() -> None:
    commands = [item[0] for item in tray.MENU if item is not None]
    assert commands == ["show", "web", "backups", "quit"]
    for item in tray.MENU:
        if item is not None:
            assert " / " in item[1]  # Turkish first, English beside it


def test_tray_icon_files_are_the_programs() -> None:
    icons = Path(__file__).parents[2] / "deploy" / "windows" / "desktop" / "icons"
    for level in LEVEL_TEXTS:
        assert tray.icon_file(icons, level).is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="the real icon is tested on Windows below")
def test_no_icon_off_windows() -> None:
    assert tray.start(lambda command: None) is None
    assert tray.show_running() is False


@pytest.mark.skipif(sys.platform != "win32", reason="the notification area is Windows-only")
def test_real_icon_comes_and_goes() -> None:
    """Add the icon, change it, show a notification and remove it (a runner without a taskbar
    answers None: then nothing is left behind either)."""
    icons = Path(__file__).parents[2] / "deploy" / "windows" / "desktop" / "icons"
    icon = tray.start(lambda command: None, icons)
    if icon is not None:
        assert tray.show_running() is True
        icon.set("warn", tray.tooltip("warn"))
        icon.notify(*tray.critical_notice("Test", "Test"))
        icon.close()
    assert tray.show_running() is False
