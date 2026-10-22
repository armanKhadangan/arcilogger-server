"""Locked evidence files cannot be written; unlock restores normal access."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from arcilogger_core.immutability import is_locked, lock, unlock

RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def test_lock_blocks_writes_and_unlock_restores(tmp_path: Path) -> None:
    path = tmp_path / "fortigate.log.gz"
    path.write_bytes(b"evidence")
    lock(path)
    assert is_locked(path)
    try:
        with path.open("ab") as handle:
            handle.write(b"tamper")
    except PermissionError:
        pass
    else:
        # Only root on a file system without chattr support can still write (Linux dev boxes).
        assert RUNNING_AS_ROOT
        assert sys.platform != "win32"
        pytest.skip("root without chattr support: permission bits do not bind root")
    unlock(path)
    assert not is_locked(path)
    path.write_bytes(b"replaced after unlock")
    path.unlink()


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS deny ACL is Windows-only")
def test_windows_lock_also_blocks_delete(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(b"{}")
    lock(path)
    with pytest.raises(PermissionError):
        path.unlink()
    unlock(path)
    path.unlink()
