"""Credentials for the replica's SMB share (a WORM folder on a NAS, build specification 8.3).

The Replica task runs as ``LOCAL SERVICE``, which reaches the network anonymously: a share that
allows only the replica user ("read-write for the replica user only") refuses it. So before
copying, the replica connects to ``\\\\host\\share`` with ``replica.user`` and the stored secret
``replica.password`` — a connection without a drive letter, for this process only
(``WNetAddConnection2W``, never remembered in a profile) — and cancels it afterwards. That works
whatever account the task runs as and survives upgrades that register the task again.

The password is never logged, printed or put on a command line (``net use`` would show it in
the process list).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import Protocol


class ShareError(OSError):
    """The share could not be connected (the message never contains the password)."""


#: Win32 error codes of WNetAddConnection2W, with what to tell the operator.
MESSAGES = {
    5: "erişim reddedildi (kullanıcının paylaşımda yazma hakkı yok)",
    53: "ağ yolu bulunamadı (sunucu adı ya da ağ)",
    67: "paylaşım adı bulunamadı",
    86: "parola yanlış",
    1219: "bu sunucuya başka kimlik bilgileriyle açık bir bağlantı var",
    1326: "kullanıcı adı ya da parola yanlış",
}


class ShareApi(Protocol):
    def add(self, remote: str, user: str, password: str) -> None: ...

    def cancel(self, remote: str) -> None: ...


def unc_root(target: Path | str) -> str | None:
    """``\\\\host\\share`` of a UNC target (``\\\\host\\share\\folder`` or ``//host/share``)."""
    anchor = PureWindowsPath(str(target)).anchor.rstrip("\\")
    return anchor if anchor.startswith("\\\\") and anchor.count("\\") >= 3 else None


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _NetResource(ctypes.Structure):
        _fields_ = (
            ("dwScope", wintypes.DWORD),
            ("dwType", wintypes.DWORD),
            ("dwDisplayType", wintypes.DWORD),
            ("dwUsage", wintypes.DWORD),
            ("lpLocalName", wintypes.LPWSTR),
            ("lpRemoteName", wintypes.LPWSTR),
            ("lpComment", wintypes.LPWSTR),
            ("lpProvider", wintypes.LPWSTR),
        )

    class WindowsShareApi:
        """``mpr.dll``: a deviceless connection for this logon session, never remembered."""

        RESOURCETYPE_DISK = 1

        def __init__(self) -> None:
            self._mpr = ctypes.WinDLL("mpr")

        def add(self, remote: str, user: str, password: str) -> None:
            resource = _NetResource()
            resource.dwType = self.RESOURCETYPE_DISK
            resource.lpRemoteName = remote
            code = self._mpr.WNetAddConnection2W(ctypes.byref(resource), password, user, 0)
            if code != 0:
                reason = MESSAGES.get(code, f"Windows hata kodu {code}")
                raise ShareError(f"{remote} paylaşımına bağlanılamadı: {reason}")

        def cancel(self, remote: str) -> None:
            self._mpr.WNetCancelConnection2W(remote, 0, True)

    def default_api() -> ShareApi | None:
        return WindowsShareApi()

else:

    def default_api() -> ShareApi | None:
        return None  # credentials for an SMB share are a Windows matter


@contextmanager
def connected(
    target: Path,
    user: str | None,
    password: str | None,
    *,
    api: ShareApi | None = None,
) -> Iterator[None]:
    """Connect to the target's share with ``user``/``password`` for the duration of the block.

    A local target, or no ``replica.user``, needs no connection. A UNC target with a user but no
    stored password is refused with a clear message.
    """
    remote = unc_root(target)
    if remote is None or not user:
        yield
        return
    if not password:
        raise ShareError(
            "replica.user ayarlı ama replica.password kayıtlı değil: "
            "arcilogger-core secret set replica.password"
        )
    share = api if api is not None else default_api()
    if share is None:
        raise ShareError(
            f"{remote}: SMB paylaşımına kimlik bilgisiyle bağlanmak Windows gerektirir"
        )
    share.add(remote, user, password)
    try:
        yield
    finally:
        share.cancel(remote)
