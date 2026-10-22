"""Make sealed files read-only (ADR-0003).

Windows
    The read-only attribute plus an NTFS **deny** entry for Everyone (``S-1-1-0``) on delete, write
    data, append data, write attributes and write extended attributes. The owner (the sealing
    account) can still change the ACL, which is how the retention job unlocks a day it deletes.
    Administrators can do the same — tampering is still detected by the hash chain and the time
    stamps, and the WORM copy on the NAS cannot be changed.

Linux / other POSIX (development, CI)
    ``chmod 0444`` and, when running as root on a file system that supports it, ``chattr +i``.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

EVERYONE_SID = "*S-1-1-0"
DENY_RIGHTS = "(DE,WD,AD,WA,WEA)"


def _icacls(*args: str) -> None:
    subprocess.run(["icacls", *args], check=True, capture_output=True)  # noqa: S603, S607


def _chattr(flag: str, path: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0 and shutil.which("chattr"):
        # Not every file system supports it (tmpfs, overlayfs); chmod still applies.
        subprocess.run(["chattr", flag, str(path)], check=False, capture_output=True)  # noqa: S603, S607


def lock(path: Path) -> None:
    """Make ``path`` read-only and protected against deletion."""
    if sys.platform == "win32":
        path.chmod(stat.S_IREAD)
        _icacls(str(path), "/deny", f"{EVERYONE_SID}:{DENY_RIGHTS}")
    else:
        path.chmod(0o444)
        _chattr("+i", path)


def unlock(path: Path) -> None:
    """Undo :func:`lock` (retention only)."""
    if sys.platform == "win32":
        _icacls(str(path), "/remove:d", EVERYONE_SID)
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
    else:
        _chattr("-i", path)
        path.chmod(0o644)


def is_locked(path: Path) -> bool:
    mode = path.stat().st_mode
    return not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
